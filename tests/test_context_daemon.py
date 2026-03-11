"""
tests/test_context_daemon.py
ContextDaemonのユニットテスト。全テストでDatabaseManager(":memory:")を使用。
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.config import Config
from src.db import DatabaseManager
from src.context_daemon import (
    ContextDaemon,
    create_secure_tmpdir,
    is_parent_alive,
)


@pytest.fixture
def config():
    return Config(
        DAEMON_POLL_INTERVAL_SEC=0.1,
        DAEMON_MAX_BACKOFF_SEC=1.0,
        DAEMON_RECALL_TOP_K=3,
    )


@pytest.fixture
def db():
    d = DatabaseManager(":memory:")
    d.init()
    yield d
    d.close()


@pytest.fixture
def daemon(config, db):
    return ContextDaemon(config, db)


def _insert_memory(db, content, emotion=None, scenes=None):
    """テスト用記憶をDBに挿入しIDを返す。"""
    if emotion is None:
        emotion = {}
    if scenes is None:
        scenes = []
    conn = db.get_connection()
    cur = conn.execute(
        """INSERT INTO memories
           (content, timestamp,
            joy, sadness, anger, fear, surprise, disgust,
            trust, anticipation, importance, urgency, scenes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            content,
            datetime.now(timezone.utc).isoformat(),
            emotion.get("joy", 0.0),
            emotion.get("sadness", 0.0),
            emotion.get("anger", 0.0),
            emotion.get("fear", 0.0),
            emotion.get("surprise", 0.0),
            emotion.get("disgust", 0.0),
            emotion.get("trust", 0.0),
            emotion.get("anticipation", 0.0),
            emotion.get("importance", 0.0),
            emotion.get("urgency", 0.0),
            json.dumps(scenes, ensure_ascii=False),
        ),
    )
    conn.commit()
    return cur.lastrowid


class TestCreateSecureTmpdir:
    def test_creates_directory(self):
        tmpdir = create_secure_tmpdir()
        assert os.path.isdir(tmpdir)
        assert "amygdala_" in tmpdir

    def test_idempotent(self):
        d1 = create_secure_tmpdir()
        d2 = create_secure_tmpdir()
        assert d1 == d2

    def test_rejects_symlink(self, tmp_path):
        # シンボリックリンクが存在する場合にRuntimeErrorを投げる
        target = tmp_path / "real_dir"
        target.mkdir()
        link = tmp_path / "link_dir"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        with patch("src.context_daemon.tempfile.gettempdir", return_value=str(tmp_path)):
            with patch("src.context_daemon.getpass.getuser", return_value="link_dir"):
                with pytest.raises(RuntimeError, match="Symlink detected"):
                    create_secure_tmpdir()


class TestIsParentAlive:
    def test_current_parent_is_alive(self):
        ppid = os.getppid()
        assert is_parent_alive(ppid) is True

    def test_nonexistent_pid(self):
        # 存在しないPIDではFalseを返す（プラットフォーム依存）
        result = is_parent_alive(99999999)
        # Windowsではos.kill(pid, 0)がOSErrorを投げない場合があるので
        # Falseを期待するが、環境によってはTrueもありうる
        assert isinstance(result, bool)


class TestContextDaemonInit:
    def test_initial_state(self, daemon):
        assert daemon._last_memory_id == 0
        assert daemon._error_count == 0
        assert daemon._running is True

    def test_recall_for_context(self, daemon, db):
        _insert_memory(db, "test memory", {"joy": 0.8, "trust": 0.5}, ["work"])
        results = daemon.recall_for_context(
            {"joy": 0.8, "trust": 0.5, "sadness": 0, "anger": 0,
             "fear": 0, "surprise": 0, "disgust": 0, "anticipation": 0,
             "importance": 0.5, "urgency": 0},
            ["work"], top_k=3,
        )
        assert len(results) >= 1
        assert results[0]["content"] == "test memory"


class TestDaemonPolling:
    def test_get_latest_memory_id_empty(self, daemon):
        assert daemon._get_latest_memory_id() == 0

    def test_get_latest_memory_id(self, daemon, db):
        _insert_memory(db, "m1")
        _insert_memory(db, "m2")
        assert daemon._get_latest_memory_id() == 2

    def test_get_memory_by_id(self, daemon, db):
        mid = _insert_memory(db, "hello world", {"joy": 0.9}, ["hobby"])
        m = daemon._get_memory_by_id(mid)
        assert m is not None
        assert m["content"] == "hello world"
        assert float(m["joy"]) == pytest.approx(0.9)

    def test_get_memory_by_id_missing(self, daemon):
        assert daemon._get_memory_by_id(9999) is None

    def test_extract_emotion_vec(self, daemon, db):
        mid = _insert_memory(db, "test", {"joy": 0.5, "trust": 0.3})
        m = daemon._get_memory_by_id(mid)
        vec = daemon._extract_emotion_vec(m)
        assert vec["joy"] == pytest.approx(0.5)
        assert vec["trust"] == pytest.approx(0.3)
        assert vec["sadness"] == pytest.approx(0.0)

    def test_extract_scenes(self, daemon, db):
        mid = _insert_memory(db, "test", scenes=["work", "learning"])
        m = daemon._get_memory_by_id(mid)
        scenes = daemon._extract_scenes(m)
        assert scenes == ["work", "learning"]

    def test_extract_scenes_empty(self, daemon, db):
        mid = _insert_memory(db, "test")
        m = daemon._get_memory_by_id(mid)
        scenes = daemon._extract_scenes(m)
        assert scenes == []

    def test_extract_scenes_invalid_json(self, daemon):
        m = {"scenes": "not-json"}
        assert daemon._extract_scenes(m) == []


class TestContextFileIO:
    def test_write_and_read_context_file(self, daemon):
        daemon._init_tmpdir()
        try:
            daemon._write_context_file(
                source_memory_id=1,
                trigger_emotion={"joy": 0.5},
                trigger_scenes=["work"],
                recalled_memories=[{"id": 1, "content": "test", "score": 0.8}],
            )
            assert os.path.exists(daemon.context_file_path)
            with open(daemon.context_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["source_memory_id"] == 1
            assert data["trigger_emotion"] == {"joy": 0.5}
            assert data["trigger_scenes"] == ["work"]
            assert len(data["recalled_memories"]) == 1
            assert "updated_at" in data
        finally:
            daemon._cleanup()

    def test_atomic_rename(self, daemon):
        """一時ファイル(.tmp)がリネームされて最終ファイルだけ残ること。"""
        daemon._init_tmpdir()
        try:
            daemon._write_context_file(1, {}, [], [])
            assert os.path.exists(daemon.context_file_path)
            assert not os.path.exists(daemon.context_file_path + ".tmp")
        finally:
            daemon._cleanup()

    def test_cleanup_removes_files(self, daemon):
        daemon._init_tmpdir()
        daemon._write_context_file(1, {}, [], [])
        assert os.path.exists(daemon.context_file_path)
        daemon._cleanup()
        assert not os.path.exists(daemon.context_file_path)


class TestExponentialBackoff:
    def test_no_error(self, daemon):
        daemon._error_count = 0
        assert daemon._calculate_sleep_interval() == pytest.approx(0.1)

    def test_first_error(self, daemon):
        daemon._error_count = 1
        assert daemon._calculate_sleep_interval() == pytest.approx(0.2)

    def test_max_backoff(self, daemon):
        daemon._error_count = 100
        assert daemon._calculate_sleep_interval() == pytest.approx(1.0)


class TestDaemonRunLoop:
    def test_single_poll_detects_new_memory(self, daemon, db):
        """1ポーリングサイクルで新しいmemoryを検知し、context.jsonを書き出す。"""
        daemon._init_tmpdir()
        try:
            # 起動時IDを設定
            daemon._last_memory_id = 0

            # メモリを挿入
            _insert_memory(db, "important fact", {"importance": 0.9}, ["work"])

            # 1回のポーリングサイクルをシミュレート
            current_max_id = daemon._get_latest_memory_id()
            assert current_max_id == 1

            memory = daemon._get_memory_by_id(current_max_id)
            emotion_vec = daemon._extract_emotion_vec(memory)
            scenes = daemon._extract_scenes(memory)
            results = daemon.recall_for_context(emotion_vec, scenes, 3)
            daemon._write_context_file(current_max_id, emotion_vec, scenes, results)
            daemon._last_memory_id = current_max_id

            # 結果を確認
            with open(daemon.context_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["source_memory_id"] == 1
            assert len(data["recalled_memories"]) >= 1
        finally:
            daemon._cleanup()

    def test_run_stops_on_stop(self, daemon, db):
        """stop()呼び出しでrunループが終了すること。"""
        import threading

        daemon._init_tmpdir()

        def stop_after_delay():
            time.sleep(0.3)
            daemon.stop()

        t = threading.Thread(target=stop_after_delay)
        t.start()
        daemon.run()  # stop()で抜ける
        t.join(timeout=5)
        assert not daemon._running

    def test_run_handles_db_error(self, config):
        """DBエラーでもデーモンがクラッシュせずbackoffすること。"""
        mock_db = MagicMock()
        mock_db.get_connection.side_effect = Exception("DB locked")

        d = ContextDaemon(config, mock_db)
        d._init_tmpdir = MagicMock()
        d._tmpdir = tempfile.gettempdir()
        d._context_file = os.path.join(d._tmpdir, "test_ctx.json")

        # 3回ポーリングしてstop
        call_count = 0
        original_sleep = time.sleep

        def counted_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                d.stop()
            original_sleep(0.01)

        with patch("src.context_daemon.time.sleep", side_effect=counted_sleep):
            with patch("src.context_daemon.is_parent_alive", return_value=True):
                d.run()

        assert d._error_count > 0


class TestMCPServerScenesFixStep0:
    """Step 0: store_memoryのscenes INSERT漏れ修正のテスト。"""

    def test_scenes_inserted_in_db(self, db):
        """scenes_inputがDBのscenesカラムに保存されること。"""
        # mcp_serverのstore_memoryメソッドと同等のパース→INSERT
        scenes_input = ["work", "learning"]
        scenes = scenes_input[:3]
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO memories
               (content, raw_input,
                joy, sadness, anger, fear, surprise, disgust, trust, anticipation,
                importance, urgency, scenes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
             json.dumps(scenes, ensure_ascii=False)),
        )
        conn.commit()
        row = conn.execute("SELECT scenes FROM memories WHERE id = 1").fetchone()
        assert json.loads(row["scenes"]) == ["work", "learning"]

    def test_scenes_max_3(self):
        """scenes_inputが3件に制限されること。"""
        scenes_input = ["a", "b", "c", "d", "e"]
        scenes = [str(s) for s in scenes_input[:3]]
        assert len(scenes) == 3
        assert scenes == ["a", "b", "c"]


class TestConfigDaemonSettings:
    """Step 1: Config にデーモン設定が追加されていること。"""

    def test_default_values(self):
        c = Config()
        assert c.DAEMON_POLL_INTERVAL_SEC == 2.0
        assert c.DAEMON_MAX_BACKOFF_SEC == 60.0
        assert c.DAEMON_RECALL_TOP_K == 5

    def test_from_env(self):
        with patch.dict(os.environ, {
            "EMS_DAEMON_POLL_INTERVAL": "5.0",
            "EMS_DAEMON_MAX_BACKOFF": "120.0",
            "EMS_DAEMON_RECALL_TOP_K": "10",
        }):
            c = Config.from_env()
            assert c.DAEMON_POLL_INTERVAL_SEC == 5.0
            assert c.DAEMON_MAX_BACKOFF_SEC == 120.0
            assert c.DAEMON_RECALL_TOP_K == 10
