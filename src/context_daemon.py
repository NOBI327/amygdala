"""自動コンテキスト更新デーモン。

memoriesテーブルをポーリング監視し、新規INSERTを検知したら
感情ベクトル＋シーンタグで検索して結果を一時ファイルに書き出す。

MCPサーバーからサブプロセスとして起動される。
単体起動: python -m src.context_daemon --db-path memory.db
"""

import argparse
import json
import logging
import os
import getpass
import sys
import signal
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

from .config import Config
from .db import DatabaseManager
from .search_engine import SearchEngine

logger = logging.getLogger(__name__)


def create_secure_tmpdir() -> str:
    """ユーザー固有の安全な一時ディレクトリを作成する。"""
    base = os.path.join(tempfile.gettempdir(), f"amygdala_{getpass.getuser()}")
    os.makedirs(base, mode=0o700, exist_ok=True)

    if os.path.islink(base):
        raise RuntimeError(f"Symlink detected at {base}, refusing to use")

    if sys.platform != "win32":
        stat = os.stat(base)
        if stat.st_mode & 0o077:
            os.chmod(base, 0o700)

    return base


def is_parent_alive(original_ppid: int) -> bool:
    """親プロセスの生存確認。プラットフォーム別に実装。"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(0x1000, False, original_ppid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            try:
                os.kill(original_ppid, 0)
                return True
            except OSError:
                return False
    else:
        return os.getppid() == original_ppid


class ContextDaemon:
    """自動コンテキスト更新デーモン本体。"""

    def __init__(self, config: Config, db: DatabaseManager) -> None:
        self.config = config
        self.db = db
        self._engine = SearchEngine(config, db)
        self._last_memory_id: int = 0
        self._error_count: int = 0
        self._running: bool = True
        self._tmpdir: str = ""
        self._context_file: str = ""
        self._original_ppid: int = os.getppid()

    def recall_for_context(
        self,
        emotion_vec: Dict[str, float],
        scenes: List[str],
        top_k: int,
    ) -> List[Dict]:
        """デーモン用の検索インターフェース。"""
        return self._engine.search_memories(emotion_vec, scenes, top_k=top_k)

    def _init_tmpdir(self) -> None:
        """一時ディレクトリとファイルパスを初期化する。"""
        self._tmpdir = create_secure_tmpdir()
        self._context_file = os.path.join(self._tmpdir, "context.json")

    def _get_latest_memory_id(self) -> int:
        """memoriesテーブルの最新ID（主キー）を取得する。"""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT MAX(id) FROM memories WHERE archived = FALSE"
        ).fetchone()
        return row[0] if row and row[0] is not None else 0

    def _get_memory_by_id(self, memory_id: int) -> Optional[Dict]:
        """指定IDのmemoryレコードを取得する。"""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def _extract_emotion_vec(self, memory: Dict) -> Dict[str, float]:
        """memoryレコードから感情ベクトルを抽出する。"""
        all_axes = list(self.config.EMOTION_AXES) + list(self.config.META_AXES)
        return {ax: float(memory.get(ax, 0.0)) for ax in all_axes}

    def _extract_scenes(self, memory: Dict) -> List[str]:
        """memoryレコードからシーンタグリストを抽出する。"""
        scenes_raw = memory.get("scenes", "[]")
        if not scenes_raw:
            return []
        try:
            scenes = json.loads(scenes_raw)
            return scenes if isinstance(scenes, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def _write_context_file(
        self,
        source_memory_id: int,
        trigger_emotion: Dict[str, float],
        trigger_scenes: List[str],
        recalled_memories: List[Dict],
    ) -> None:
        """検索結果をアトミックリネームで一時ファイルに書き出す。"""
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source_memory_id": source_memory_id,
            "trigger_emotion": trigger_emotion,
            "trigger_scenes": trigger_scenes,
            "recalled_memories": recalled_memories,
        }
        tmp_path = self._context_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._context_file)

    def _cleanup(self) -> None:
        """一時ファイルを削除する（best effort）。"""
        try:
            if self._context_file and os.path.exists(self._context_file):
                os.remove(self._context_file)
        except OSError:
            pass
        try:
            tmp_path = self._context_file + ".tmp"
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

    def _calculate_sleep_interval(self) -> float:
        """エラーカウントに基づくExponential Backoff付きスリープ間隔を計算する。"""
        if self._error_count == 0:
            return self.config.DAEMON_POLL_INTERVAL_SEC
        interval = self.config.DAEMON_POLL_INTERVAL_SEC * (2 ** self._error_count)
        return min(interval, self.config.DAEMON_MAX_BACKOFF_SEC)

    def stop(self) -> None:
        """ポーリングループを停止する。"""
        self._running = False

    def run(self) -> None:
        """ポーリングループを実行する。"""
        self._init_tmpdir()

        # シグナルハンドラ設定（POSIX環境のみ）
        if sys.platform != "win32":
            def _handle_sigterm(signum, frame):
                self.stop()
            signal.signal(signal.SIGTERM, _handle_sigterm)

        # 起動時の最新IDを取得（起動前の既存レコードはスキップ）
        try:
            self._last_memory_id = self._get_latest_memory_id()
            logger.info(
                f"Context daemon started. tmpdir={self._tmpdir}, "
                f"last_memory_id={self._last_memory_id}, "
                f"poll_interval={self.config.DAEMON_POLL_INTERVAL_SEC}s"
            )
        except Exception as e:
            logger.warning(f"Failed to get initial memory ID: {e}")

        try:
            while self._running:
                # 親プロセス生存確認
                if not is_parent_alive(self._original_ppid):
                    logger.info("Parent process is gone. Shutting down daemon.")
                    break

                try:
                    current_max_id = self._get_latest_memory_id()

                    if current_max_id > self._last_memory_id:
                        memory = self._get_memory_by_id(current_max_id)
                        if memory:
                            emotion_vec = self._extract_emotion_vec(memory)
                            scenes = self._extract_scenes(memory)
                            results = self.recall_for_context(
                                emotion_vec, scenes, self.config.DAEMON_RECALL_TOP_K
                            )
                            self._write_context_file(
                                current_max_id, emotion_vec, scenes, results
                            )
                        self._last_memory_id = current_max_id

                    # 正常復帰
                    self._error_count = 0

                except Exception as e:
                    self._error_count += 1
                    logger.warning(
                        f"Daemon poll error (count={self._error_count}): {e}"
                    )

                sleep_time = self._calculate_sleep_interval()
                time.sleep(sleep_time)

        finally:
            self._cleanup()
            logger.info("Context daemon stopped.")

    @property
    def context_file_path(self) -> str:
        """コンテキストファイルのパスを返す（テスト用）。"""
        return self._context_file

    @property
    def tmpdir(self) -> str:
        """一時ディレクトリのパスを返す。"""
        return self._tmpdir


def main(db_path: Optional[str] = None) -> None:
    """デーモンのエントリーポイント。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="amygdala context daemon")
    parser.add_argument("--db-path", type=str, default=None)
    args = parser.parse_args()

    config = Config.from_env()
    effective_db_path = args.db_path or db_path or config.DB_PATH
    db = DatabaseManager(effective_db_path)
    db.init()
    daemon = ContextDaemon(config, db)
    daemon.run()


if __name__ == "__main__":
    main()
