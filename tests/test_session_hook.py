"""session_hook.py の単体テスト。"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest

from src.session_hook import (
    fetch_from_db,
    format_context_json,
    format_db_memories,
    format_emotions,
    get_context_file_path,
    main,
    read_context_file,
    resolve_db_path,
)


# ── fixtures ──


@pytest.fixture
def tmp_context_dir(tmp_path):
    """一時的な context.json 用ディレクトリ。"""
    return tmp_path


@pytest.fixture
def fresh_context_data():
    """鮮度OK な context.json データ。"""
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_memory_id": 42,
        "trigger_emotion": {"joy": 0.5, "trust": 0.7},
        "trigger_scenes": ["work"],
        "recalled_memories": [
            {
                "content": "amygdalaの設計会議を行った",
                "timestamp": "2026-03-11T10:00:00+09:00",
                "joy": 0.4,
                "trust": 0.7,
                "importance": 0.8,
                "sadness": 0.0,
                "anger": 0.0,
                "fear": 0.0,
                "surprise": 0.1,
                "disgust": 0.0,
                "anticipation": 0.3,
                "urgency": 0.2,
            },
            {
                "content": "デーモンの実機テストを実施",
                "timestamp": "2026-03-11T14:00:00+09:00",
                "joy": 0.2,
                "trust": 0.5,
                "importance": 0.6,
                "sadness": 0.0,
                "anger": 0.0,
                "fear": 0.0,
                "surprise": 0.0,
                "disgust": 0.0,
                "anticipation": 0.4,
                "urgency": 0.1,
            },
        ],
    }


@pytest.fixture
def test_db(tmp_path):
    """テスト用 SQLite DB。"""
    db_path = str(tmp_path / "test_memory.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            raw_input TEXT DEFAULT '',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            joy REAL DEFAULT 0, sadness REAL DEFAULT 0,
            anger REAL DEFAULT 0, fear REAL DEFAULT 0,
            surprise REAL DEFAULT 0, disgust REAL DEFAULT 0,
            trust REAL DEFAULT 0, anticipation REAL DEFAULT 0,
            importance REAL DEFAULT 0, urgency REAL DEFAULT 0,
            scenes TEXT DEFAULT '[]',
            archived BOOLEAN DEFAULT FALSE
        )"""
    )
    # テストデータ挿入
    for i in range(7):
        conn.execute(
            """INSERT INTO memories (content, joy, trust, importance)
               VALUES (?, ?, ?, ?)""",
            (f"テストメモリ {i+1}", 0.1 * i, 0.5, 0.6),
        )
    conn.commit()
    conn.close()
    return db_path


# ── get_context_file_path ──


class TestGetContextFilePath:
    def test_returns_expected_path(self):
        path = get_context_file_path()
        assert "amygdala_" in path
        assert path.endswith("context.json")


# ── resolve_db_path ──


class TestResolveDbPath:
    def test_cli_arg_takes_priority(self):
        result = resolve_db_path("/explicit/path/memory.db")
        assert result == "/explicit/path/memory.db"

    def test_env_var_fallback(self):
        with mock.patch.dict(os.environ, {"EMS_DB_PATH": "/env/memory.db"}):
            result = resolve_db_path(None)
            assert result == "/env/memory.db"

    def test_file_based_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMS_DB_PATH", None)
            result = resolve_db_path(None)
            assert result.endswith("memory.db")
            # プロジェクトルートからの相対パスであること
            assert os.path.isabs(result)


# ── read_context_file ──


class TestReadContextFile:
    def test_fresh_file_returns_data(self, tmp_context_dir, fresh_context_data):
        path = str(tmp_context_dir / "context.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fresh_context_data, f)
        result = read_context_file(path, max_age_hours=24.0)
        assert result is not None
        assert len(result["recalled_memories"]) == 2

    def test_stale_file_returns_none(self, tmp_context_dir, fresh_context_data):
        # 48時間前のタイムスタンプ
        fresh_context_data["updated_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat()
        path = str(tmp_context_dir / "context.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fresh_context_data, f)
        result = read_context_file(path, max_age_hours=24.0)
        assert result is None

    def test_missing_file_returns_none(self):
        result = read_context_file("/nonexistent/path/context.json", 24.0)
        assert result is None

    def test_invalid_json_returns_none(self, tmp_context_dir):
        path = str(tmp_context_dir / "context.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        result = read_context_file(path, 24.0)
        assert result is None

    def test_missing_updated_at_returns_none(self, tmp_context_dir):
        path = str(tmp_context_dir / "context.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"recalled_memories": []}, f)
        result = read_context_file(path, 24.0)
        assert result is None


# ── fetch_from_db ──


class TestFetchFromDb:
    def test_returns_latest_memories(self, test_db):
        memories = fetch_from_db(test_db, max_memories=5)
        assert len(memories) == 5
        # id DESC なので最新が最初
        assert memories[0]["content"] == "テストメモリ 7"
        assert memories[4]["content"] == "テストメモリ 3"

    def test_respects_max_memories(self, test_db):
        memories = fetch_from_db(test_db, max_memories=2)
        assert len(memories) == 2

    def test_missing_db_returns_empty(self):
        memories = fetch_from_db("/nonexistent/memory.db", 5)
        assert memories == []

    def test_invalid_db_returns_empty(self, tmp_path):
        bad_db = str(tmp_path / "bad.db")
        with open(bad_db, "w") as f:
            f.write("not a database")
        memories = fetch_from_db(bad_db, 5)
        assert memories == []


# ── format_emotions ──


class TestFormatEmotions:
    def test_formats_high_scores(self):
        mem = {"joy": 0.8, "trust": 0.5, "anger": 0.1, "importance": 0.9}
        result = format_emotions(mem)
        assert "joy=0.8" in result
        assert "trust=0.5" in result
        assert "importance=0.9" in result
        assert "anger" not in result

    def test_neutral_when_all_low(self):
        mem = {"joy": 0.1, "trust": 0.2, "anger": 0.0}
        result = format_emotions(mem)
        assert result == "neutral"


# ── format_context_json ──


class TestFormatContextJson:
    def test_includes_header_and_footer(self, fresh_context_data):
        text = format_context_json(fresh_context_data)
        assert "[amygdala: 前回の記憶コンテキスト]" in text
        assert "データソース: context.json（感情ベース検索）" in text
        assert "recall_memoriesで追加検索" in text

    def test_includes_memory_content(self, fresh_context_data):
        text = format_context_json(fresh_context_data)
        assert "amygdalaの設計会議を行った" in text
        assert "デーモンの実機テストを実施" in text

    def test_includes_emotion_info(self, fresh_context_data):
        text = format_context_json(fresh_context_data)
        assert "trust=" in text
        assert "importance=" in text


# ── format_db_memories ──


class TestFormatDbMemories:
    def test_empty_returns_empty_string(self):
        assert format_db_memories([]) == ""

    def test_includes_db_source(self):
        memories = [{"content": "test memory", "joy": 0.5, "trust": 0.4}]
        text = format_db_memories(memories)
        assert "DB直接検索（最新N件）" in text
        assert "test memory" in text


# ── main (統合テスト) ──


class TestMain:
    def test_context_json_path(self, tmp_context_dir, fresh_context_data, capsys):
        """context.json が鮮度OK なら、それを出力する。"""
        context_path = str(tmp_context_dir / "context.json")
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(fresh_context_data, f)

        with mock.patch(
            "src.session_hook.get_context_file_path", return_value=context_path
        ):
            with mock.patch("sys.argv", ["session_hook.py"]):
                main()

        captured = capsys.readouterr()
        assert "[amygdala: 前回の記憶コンテキスト]" in captured.out
        assert "context.json（感情ベース検索）" in captured.out

    def test_db_fallback(self, test_db, capsys):
        """context.json がなければ DB フォールバック。"""
        with mock.patch(
            "src.session_hook.get_context_file_path",
            return_value="/nonexistent/context.json",
        ):
            with mock.patch(
                "sys.argv", ["session_hook.py", "--db-path", test_db]
            ):
                main()

        captured = capsys.readouterr()
        assert "[amygdala: 前回の記憶コンテキスト]" in captured.out
        assert "DB直接検索（最新N件）" in captured.out

    def test_empty_output_on_total_failure(self, capsys):
        """全て失敗したら空出力 + exit 0。"""
        with mock.patch(
            "src.session_hook.get_context_file_path",
            return_value="/nonexistent/context.json",
        ):
            with mock.patch(
                "sys.argv",
                ["session_hook.py", "--db-path", "/nonexistent/memory.db"],
            ):
                main()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_stale_context_falls_back_to_db(
        self, tmp_context_dir, fresh_context_data, test_db, capsys
    ):
        """context.json が古すぎれば DB フォールバック。"""
        fresh_context_data["updated_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat()
        context_path = str(tmp_context_dir / "context.json")
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(fresh_context_data, f)

        with mock.patch(
            "src.session_hook.get_context_file_path", return_value=context_path
        ):
            with mock.patch(
                "sys.argv", ["session_hook.py", "--db-path", test_db]
            ):
                main()

        captured = capsys.readouterr()
        assert "DB直接検索（最新N件）" in captured.out
