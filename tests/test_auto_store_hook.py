"""auto_store_hook.py のテスト。

transcript パース、対話ペア抽出、重要度フィルタ、要約生成、DB 書き込みをテスト。
"""

import json
import os
import sqlite3
import tempfile

import pytest

# auto_store_hook は src パッケージに属さないスタンドアロンスクリプトだが、
# テストでは直接インポートする
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import auto_store_hook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path):
    """テスト用一時ディレクトリ。"""
    return tmp_path


@pytest.fixture
def transcript_file(tmp_dir):
    """JSONL transcript ファイルを生成するヘルパー。"""
    def _create(messages: list[dict]) -> str:
        path = str(tmp_dir / "transcript.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return path
    return _create


@pytest.fixture
def memory_db(tmp_dir):
    """テスト用 memory.db を作成する。"""
    db_path = str(tmp_dir / "memory.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            raw_input TEXT,
            raw_response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            joy REAL DEFAULT 0.0,
            sadness REAL DEFAULT 0.0,
            anger REAL DEFAULT 0.0,
            fear REAL DEFAULT 0.0,
            surprise REAL DEFAULT 0.0,
            disgust REAL DEFAULT 0.0,
            trust REAL DEFAULT 0.0,
            anticipation REAL DEFAULT 0.0,
            importance REAL DEFAULT 0.0,
            urgency REAL DEFAULT 0.0,
            scenes TEXT DEFAULT '[]',
            relevance_score REAL DEFAULT 1.0,
            recall_count INTEGER DEFAULT 0,
            last_recalled DATETIME,
            pinned_flag BOOLEAN DEFAULT FALSE,
            archived BOOLEAN DEFAULT FALSE
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Transcript パース
# ---------------------------------------------------------------------------

class TestParseTranscript:
    def test_basic_parse(self, transcript_file):
        messages = [
            {"role": "user", "content": "こんにちは"},
            {"role": "assistant", "content": "こんにちは！"},
        ]
        path = transcript_file(messages)
        result = auto_store_hook.parse_transcript(path)
        assert len(result) == 2
        assert result[0]["role"] == "user"

    def test_empty_file(self, tmp_dir):
        path = str(tmp_dir / "empty.jsonl")
        with open(path, "w") as f:
            f.write("")
        result = auto_store_hook.parse_transcript(path)
        assert result == []

    def test_nonexistent_file(self):
        result = auto_store_hook.parse_transcript("/nonexistent/file.jsonl")
        assert result == []

    def test_malformed_lines_skipped(self, tmp_dir):
        path = str(tmp_dir / "bad.jsonl")
        with open(path, "w") as f:
            f.write('{"role":"user","content":"ok"}\n')
            f.write("not json\n")
            f.write('{"role":"assistant","content":"ok"}\n')
        result = auto_store_hook.parse_transcript(path)
        assert len(result) == 2


class TestExtractTextContent:
    def test_string_content(self):
        msg = {"role": "user", "content": "hello"}
        assert auto_store_hook.extract_text_content(msg) == "hello"

    def test_content_blocks(self):
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "part1"},
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "part2"},
        ]}
        result = auto_store_hook.extract_text_content(msg)
        assert "part1" in result
        assert "part2" in result
        assert "Bash" not in result

    def test_empty_content(self):
        assert auto_store_hook.extract_text_content({}) == ""


class TestHasToolUse:
    def test_with_tool_use_block(self):
        msg = {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash"},
        ]}
        assert auto_store_hook.has_tool_use(msg) is True

    def test_without_tool_use(self):
        msg = {"role": "assistant", "content": "just text"}
        assert auto_store_hook.has_tool_use(msg) is False


# ---------------------------------------------------------------------------
# 対話ペア抽出
# ---------------------------------------------------------------------------

class TestExtractDialoguePairs:
    def test_simple_pair(self):
        messages = [
            {"role": "user", "content": "質問です"},
            {"role": "assistant", "content": "回答です"},
        ]
        pairs = auto_store_hook.extract_dialogue_pairs(messages, 0)
        assert len(pairs) == 1
        assert pairs[0]["user"] == "質問です"
        assert pairs[0]["assistant"] == "回答です"

    def test_multiple_pairs(self):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        pairs = auto_store_hook.extract_dialogue_pairs(messages, 0)
        assert len(pairs) == 2

    def test_start_index_skips_old(self):
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old response"},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "new response"},
        ]
        pairs = auto_store_hook.extract_dialogue_pairs(messages, 2)
        assert len(pairs) == 1
        assert pairs[0]["user"] == "new"

    def test_tool_only_assistant(self):
        messages = [
            {"role": "user", "content": "run tests"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}},
            ]},
        ]
        pairs = auto_store_hook.extract_dialogue_pairs(messages, 0)
        assert len(pairs) == 1
        assert pairs[0]["assistant_has_only_tools"] is True

    def test_mixed_text_and_tools(self):
        messages = [
            {"role": "user", "content": "これについて思うんだけど"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "確かにそうですね"},
                {"type": "tool_use", "name": "Read", "input": {}},
            ]},
        ]
        pairs = auto_store_hook.extract_dialogue_pairs(messages, 0)
        assert len(pairs) == 1
        assert pairs[0]["assistant_has_only_tools"] is False
        assert "確かにそうですね" in pairs[0]["assistant"]


# ---------------------------------------------------------------------------
# 重要度フィルタ
# ---------------------------------------------------------------------------

class TestIsSignificant:
    def test_long_user_text(self):
        pair = {"user": "あ" * 30, "assistant": "ok", "assistant_has_only_tools": False}
        assert auto_store_hook.is_significant(pair) is True

    def test_short_with_emotion_keyword(self):
        pair = {"user": "嬉しい！", "assistant": "よかった", "assistant_has_only_tools": False}
        assert auto_store_hook.is_significant(pair) is True

    def test_short_with_decision_keyword(self):
        pair = {"user": "やろう", "assistant": "はい", "assistant_has_only_tools": False}
        assert auto_store_hook.is_significant(pair) is True

    def test_short_with_question(self):
        pair = {"user": "どう思う？", "assistant": "いいと思います", "assistant_has_only_tools": False}
        assert auto_store_hook.is_significant(pair) is True

    def test_trivial_short_skipped(self):
        pair = {"user": "ok", "assistant": "", "assistant_has_only_tools": True}
        assert auto_store_hook.is_significant(pair) is False

    def test_tool_only_short_command_skipped(self):
        pair = {"user": "run it", "assistant": "", "assistant_has_only_tools": True}
        assert auto_store_hook.is_significant(pair) is False

    def test_pure_conversation_kept(self):
        pair = {"user": "最近どう？", "assistant": "元気ですよ", "assistant_has_only_tools": False}
        assert auto_store_hook.is_significant(pair) is True


# ---------------------------------------------------------------------------
# 要約 & 感情推定
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_basic_summary(self):
        pair = {"user": "質問", "assistant": "回答"}
        result = auto_store_hook.summarize_pair(pair)
        assert "User: 質問" in result
        assert "Assistant: 回答" in result

    def test_long_text_truncated(self):
        pair = {"user": "あ" * 300, "assistant": "い" * 300}
        result = auto_store_hook.summarize_pair(pair)
        assert "..." in result

    def test_no_assistant(self):
        pair = {"user": "独り言", "assistant": ""}
        result = auto_store_hook.summarize_pair(pair)
        assert "Assistant" not in result


class TestEstimateImportance:
    def test_baseline(self):
        pair = {"user": "hi", "assistant": "hello"}
        score = auto_store_hook.estimate_importance(pair)
        assert score == pytest.approx(0.3)

    def test_decision_boost(self):
        pair = {"user": "方針を決めた", "assistant": "了解"}
        score = auto_store_hook.estimate_importance(pair)
        assert score > 0.5

    def test_max_cap(self):
        pair = {"user": "方針を決めたんだけど、嬉しい気持ちで？" + "あ" * 50, "assistant": ""}
        score = auto_store_hook.estimate_importance(pair)
        assert score <= 1.0


class TestEstimateScenes:
    def test_work_scene(self):
        pair = {"user": "コミットしてpushだ", "assistant": "ok"}
        scenes = auto_store_hook.estimate_scenes(pair)
        assert "work" in scenes

    def test_hobby_scene(self):
        pair = {"user": "バイブコーディング楽しい", "assistant": "いいですね"}
        scenes = auto_store_hook.estimate_scenes(pair)
        assert "hobby" in scenes

    def test_default_meta(self):
        pair = {"user": "xyz", "assistant": "abc"}
        scenes = auto_store_hook.estimate_scenes(pair)
        assert scenes == ["meta"]

    def test_max_three(self):
        pair = {"user": "仕事で疲れて趣味のゲームで勉強しながら子供と遊ぶ", "assistant": ""}
        scenes = auto_store_hook.estimate_scenes(pair)
        assert len(scenes) <= 3


# ---------------------------------------------------------------------------
# SQLite 書き込み
# ---------------------------------------------------------------------------

class TestStoreToDb:
    def test_basic_insert(self, memory_db):
        memories = [{
            "content": "テスト記憶",
            "raw_input": "User: test\nAssistant: response",
            "importance": 0.5,
            "urgency": 0.2,
            "scenes": ["work"],
        }]
        stored = auto_store_hook.store_to_db(memory_db, memories)
        assert stored == 1

        # DB から確認
        conn = sqlite3.connect(memory_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM memories").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["content"] == "テスト記憶"
        assert rows[0]["importance"] == pytest.approx(0.5)
        assert json.loads(rows[0]["scenes"]) == ["work"]

    def test_multiple_inserts(self, memory_db):
        memories = [
            {"content": f"mem{i}", "raw_input": "", "importance": 0.3, "urgency": 0.1, "scenes": []}
            for i in range(5)
        ]
        stored = auto_store_hook.store_to_db(memory_db, memories)
        assert stored == 5

    def test_empty_list(self, memory_db):
        stored = auto_store_hook.store_to_db(memory_db, [])
        assert stored == 0

    def test_nonexistent_db(self):
        stored = auto_store_hook.store_to_db("/nonexistent/db.sqlite", [{"content": "x"}])
        assert stored == 0


# ---------------------------------------------------------------------------
# 重複防止
# ---------------------------------------------------------------------------

class TestTrackingState:
    def test_get_set_last_processed(self, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        assert auto_store_hook.get_last_processed("sess1") == 0
        auto_store_hook.save_last_processed("sess1", 42)
        assert auto_store_hook.get_last_processed("sess1") == 42

    def test_different_sessions(self, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        auto_store_hook.save_last_processed("sess_a", 10)
        auto_store_hook.save_last_processed("sess_b", 20)
        assert auto_store_hook.get_last_processed("sess_a") == 10
        assert auto_store_hook.get_last_processed("sess_b") == 20


# ---------------------------------------------------------------------------
# 統合テスト: process()
# ---------------------------------------------------------------------------

class TestProcess:
    def test_full_pipeline(self, transcript_file, memory_db, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        messages = [
            {"role": "user", "content": "amygdalaのアップデート方向について相談してみようか。どう思う？"},
            {"role": "assistant", "content": "いくつかの方向性が考えられます。"},
            {"role": "user", "content": "Bから進めてみよう。"},
            {"role": "assistant", "content": "了解。プランを立てましょう。"},
        ]
        path = transcript_file(messages)

        hook_input = {"session_id": "test_sess", "transcript_path": path}
        stored = auto_store_hook.process(hook_input, memory_db)
        assert stored >= 1

        # DB に保存されたことを確認
        conn = sqlite3.connect(memory_db)
        rows = conn.execute("SELECT content FROM memories").fetchall()
        conn.close()
        assert len(rows) >= 1

    def test_incremental_processing(self, transcript_file, memory_db, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        messages = [
            {"role": "user", "content": "最初の相談をしたい。方針について考えてるんだけど"},
            {"role": "assistant", "content": "はい、聞かせてください。"},
        ]
        path = transcript_file(messages)

        hook_input = {"session_id": "inc_sess", "transcript_path": path}
        stored1 = auto_store_hook.process(hook_input, memory_db)
        assert stored1 >= 1

        # 同じ transcript で再度呼ぶ → 新規なし
        stored2 = auto_store_hook.process(hook_input, memory_db)
        assert stored2 == 0

    def test_empty_transcript(self, memory_db, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        hook_input = {"session_id": "empty", "transcript_path": ""}
        stored = auto_store_hook.process(hook_input, memory_db)
        assert stored == 0

    def test_no_significant_content(self, transcript_file, memory_db, monkeypatch, tmp_dir):
        monkeypatch.setattr(auto_store_hook, "get_tracking_dir", lambda: str(tmp_dir))

        messages = [
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]},
        ]
        path = transcript_file(messages)

        hook_input = {"session_id": "noise", "transcript_path": path}
        stored = auto_store_hook.process(hook_input, memory_db)
        assert stored == 0
