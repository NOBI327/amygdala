"""SessionStart hook スクリプト。

Claude Code の SessionStart hook から呼び出され、
amygdala の記憶コンテキストを stdout に出力する。

依存: stdlib のみ（json, sqlite3, tempfile, getpass, argparse）。
amygdala の src パッケージには一切依存しない。

使用例:
  python C:/claude_pj/amygdala/src/session_hook.py --db-path C:/claude_pj/amygdala/memory.db
"""

import argparse
import getpass
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone


# Windows で stdout が CP932 になる問題を回避
sys.stdout.reconfigure(encoding="utf-8")


def get_context_file_path() -> str:
    """context.json のパスを算出する（create_secure_tmpdir と同じロジック）。"""
    return os.path.join(
        tempfile.gettempdir(),
        f"amygdala_{getpass.getuser()}",
        "context.json",
    )


def resolve_db_path(cli_arg: str | None) -> str:
    """DB_PATH を優先順位付きで解決する。

    1. --db-path コマンドライン引数
    2. 環境変数 EMS_DB_PATH
    3. session_hook.py の __file__ から逆算したプロジェクトルート/memory.db
    """
    if cli_arg:
        return cli_arg
    env = os.environ.get("EMS_DB_PATH")
    if env:
        return env
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "memory.db")


def read_context_file(path: str, max_age_hours: float) -> dict | None:
    """context.json を読み込み、鮮度チェックする。

    Returns:
        鮮度OK なら context dict、それ以外は None。
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    updated_at = data.get("updated_at")
    if not updated_at:
        return None

    try:
        ts = datetime.fromisoformat(updated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
    except (ValueError, TypeError):
        return None

    return data


def fetch_from_db(db_path: str, max_memories: int) -> list[dict]:
    """DB から最新 N 件のメモリを取得する。

    Returns:
        メモリのリスト。接続失敗時は空リスト。
    """
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path, timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, content, timestamp,
                      joy, sadness, anger, fear, surprise, disgust,
                      trust, anticipation, importance, urgency
               FROM memories
               WHERE archived = FALSE
               ORDER BY id DESC
               LIMIT ?""",
            (max_memories,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except (sqlite3.Error, OSError):
        return []


def format_emotions(memory: dict) -> str:
    """メモリの感情ベクトルからスコア > 0.3 の軸をフォーマットする。"""
    axes = [
        "joy", "sadness", "anger", "fear", "surprise",
        "disgust", "trust", "anticipation", "importance", "urgency",
    ]
    parts = []
    for ax in axes:
        val = memory.get(ax, 0.0)
        if val is not None and float(val) > 0.3:
            parts.append(f"{ax}={float(val):.1f}")
    return ", ".join(parts) if parts else "neutral"


def format_context_json(data: dict) -> str:
    """context.json の内容を人間可読テキストに整形する。"""
    updated_at = data.get("updated_at", "unknown")
    # ISO format を読みやすく変換
    try:
        ts = datetime.fromisoformat(updated_at)
        updated_str = ts.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        updated_str = updated_at

    lines = [
        "[amygdala: 前回の記憶コンテキスト]",
        "",
        f"最終更新: {updated_str}",
        "データソース: context.json（感情ベース検索）",
        "",
        "関連する記憶:",
    ]

    memories = data.get("recalled_memories", [])
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        # タイムスタンプがあれば使う
        ts_str = ""
        timestamp = mem.get("timestamp")
        if timestamp:
            try:
                ts_str = f"({datetime.fromisoformat(timestamp).strftime('%Y-%m-%d')}) "
            except (ValueError, TypeError):
                pass

        # 感情情報
        emotion_str = format_emotions(mem)

        lines.append(f"{i}. {ts_str}{content}")
        lines.append(f"   [感情: {emotion_str}]")
        lines.append("")

    lines.append("このコンテキストはamygdala感情記憶システムにより自動生成されました。")
    lines.append(
        "ユーザーの過去の会話や記憶に関する発言には、recall_memoriesで追加検索してください。"
    )
    return "\n".join(lines)


def format_db_memories(memories: list[dict]) -> str:
    """DB から取得したメモリを人間可読テキストに整形する。"""
    if not memories:
        return ""

    lines = [
        "[amygdala: 前回の記憶コンテキスト]",
        "",
        f"最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "データソース: DB直接検索（最新N件）",
        "",
        "関連する記憶:",
    ]

    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        ts_str = ""
        timestamp = mem.get("timestamp")
        if timestamp:
            try:
                ts_str = f"({datetime.fromisoformat(timestamp).strftime('%Y-%m-%d')}) "
            except (ValueError, TypeError):
                pass

        emotion_str = format_emotions(mem)

        lines.append(f"{i}. {ts_str}{content}")
        lines.append(f"   [感情: {emotion_str}]")
        lines.append("")

    lines.append("このコンテキストはamygdala感情記憶システムにより自動生成されました。")
    lines.append(
        "ユーザーの過去の会話や記憶に関する発言には、recall_memoriesで追加検索してください。"
    )
    return "\n".join(lines)


def main() -> None:
    """エントリーポイント。常に exit 0 で終了する。"""
    try:
        parser = argparse.ArgumentParser(
            description="amygdala SessionStart hook script"
        )
        parser.add_argument("--db-path", type=str, default=None)
        parser.add_argument("--max-memories", type=int, default=5)
        parser.add_argument("--max-age-hours", type=float, default=24.0)
        args = parser.parse_args()

        # 1. context.json を試す（ゼロベクトル検索結果はスキップ）
        context_path = get_context_file_path()
        data = read_context_file(context_path, args.max_age_hours)
        if data and data.get("recalled_memories"):
            trigger = data.get("trigger_emotion", {})
            is_zero_vector = all(
                float(v) == 0.0 for v in trigger.values()
            ) if trigger else True
            if not is_zero_vector:
                print(format_context_json(data))
                return

        # 2. DB フォールバック
        db_path = resolve_db_path(args.db_path)
        memories = fetch_from_db(db_path, args.max_memories)
        if memories:
            print(format_db_memories(memories))
            return

        # 3. 何も取れなかった場合 — 空出力
    except Exception as e:
        # エラーは stderr に出力。stdout は空のまま。
        print(f"[amygdala hook error] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
