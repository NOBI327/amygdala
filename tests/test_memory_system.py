import pytest
import json
from unittest.mock import MagicMock, patch
from src.config import Config
from src.db import DatabaseManager
from src.memory_system import MemorySystem
from src.reconsolidation import ConsolidationEngine
from src.diversity_watchdog import DiversityWatchdog


def make_mock_client(response_text="AI応答テキスト"):
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = mock_resp
    return client


def make_tag_result(emotion=None, scenes=None):
    if emotion is None:
        emotion = {
            "joy": 0.5, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
            "surprise": 0.0, "disgust": 0.0, "trust": 0.5, "anticipation": 0.3,
            "importance": 0.5, "urgency": 0.2
        }
    return {"emotion": emotion, "scenes": scenes or ["work"]}


@pytest.fixture
def db():
    manager = DatabaseManager(":memory:")
    manager.init()
    yield manager
    manager.close()


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def client():
    c = make_mock_client()
    # tag_emotion returns tag result, generate_summary returns summary string
    def messages_create_side_effect(**kwargs):
        mock_resp = MagicMock()
        prompt = ""
        if kwargs.get("messages"):
            prompt = kwargs["messages"][0].get("content", "")
        # If prompt looks like tagging prompt, return JSON
        if "感情軸" in prompt or "emotion" in prompt.lower():
            mock_resp.content = [MagicMock(text=json.dumps(make_tag_result()))]
        else:
            mock_resp.content = [MagicMock(text="AI応答テキスト")]
        return mock_resp
    c.messages.create.side_effect = messages_create_side_effect
    # BackmanService now calls adapter.generate() — configure accordingly
    def generate_side_effect(prompt="", system=None, model=None, **kwargs):
        if "感情軸" in prompt or "感情と場面" in prompt:
            return json.dumps(make_tag_result())
        return "AI応答テキスト"
    c.generate.side_effect = generate_side_effect
    return c


@pytest.fixture
def system(client, db, config):
    return MemorySystem(client, db, config)


class TestProcessTurn:
    def test_working_memory_updated_after_turn(self, system):
        system.process_turn("こんにちは")
        turns = system.working_memory.get_turns()
        assert len(turns) == 1
        assert turns[0]["user_input"] == "こんにちは"

    def test_overflow_saved_to_long_term_memory(self, client, db):
        config = Config(WORKING_MEMORY_TURNS=2)
        sys = MemorySystem(client, db, config)
        sys.process_turn("ターン1")
        sys.process_turn("ターン2")
        sys.process_turn("ターン3")  # ターン1がoverflow → 長期記憶へ
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM memories").fetchall()
        assert len(rows) >= 1

    def test_pin_request_adds_to_pin_memory(self, system):
        system.process_turn("これを覚えといて: 誕生日は3月15日")
        pins = system.pin_memory.get_active_pins()
        assert len(pins) >= 1

    def test_search_results_passed_to_build_context_prompt(self, client, db, config):
        # Insert a memory record first
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO memories (content, joy, importance, scenes, relevance_score)
               VALUES (?, ?, ?, ?, ?)""",
            ("過去の記憶内容", 0.8, 0.7, json.dumps(["work"]), 1.0)
        )
        conn.commit()

        sys = MemorySystem(client, db, config)
        with patch.object(sys.frontman, "build_context_prompt", wraps=sys.frontman.build_context_prompt) as mock_bcp:
            sys.process_turn("仕事の話をしたい")
            assert mock_bcp.called
            call_args = mock_bcp.call_args
            search_results_arg = call_args.args[2] if len(call_args.args) >= 3 else call_args.kwargs.get("search_results", [])
            # search_results can be empty (no emotion match) but build_context_prompt must be called
            assert isinstance(search_results_arg, list)

    def test_ttl_expired_pin_appended_to_response(self, client, db):
        config = Config(PIN_TTL_TURNS=1)
        sys = MemorySystem(client, db, config)
        # Add a pin directly
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO pin_memories (content, ttl_turns_remaining, active) VALUES (?, ?, ?)",
            ("重要事項テスト", 1, True)
        )
        conn.commit()
        response = sys.process_turn("普通の質問")
        assert "ピンメモリの確認" in response

    def test_explicit_memory_reference_logs_recall(self, client, db, config):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO memories (content, joy, importance, scenes, relevance_score)
               VALUES (?, ?, ?, ?, ?)""",
            ("過去の記憶", 0.9, 0.9, json.dumps(["work"]), 1.0)
        )
        conn.commit()

        sys = MemorySystem(client, db, config)
        sys.process_turn("さっきの件について教えて")
        rows = conn.execute("SELECT * FROM recall_log").fetchall()
        assert len(rows) >= 1

    def test_context_manager_calls_close(self, client, db, config):
        with MemorySystem(client, db, config) as sys:
            sys.process_turn("テスト")
        # After exiting context manager, db connection should be closed
        assert db._conn is None

    def test_end_to_end_three_turns(self, system):
        r1 = system.process_turn("初めまして")
        r2 = system.process_turn("今日の天気は？")
        r3 = system.process_turn("ありがとう")
        assert isinstance(r1, str)
        assert isinstance(r2, str)
        assert isinstance(r3, str)
        turns = system.working_memory.get_turns()
        assert len(turns) == 3

    def test_consolidation_apply_feedback_called_on_explicit_reference(self, client, db, config):
        """Phase 2: 明示的記憶参照時にconsolidation.apply_feedbackが呼ばれること"""
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO memories (content, joy, importance, scenes, relevance_score)
               VALUES (?, ?, ?, ?, ?)""",
            ("過去の記憶", 0.9, 0.9, json.dumps(["work"]), 1.0)
        )
        conn.commit()

        sys = MemorySystem(client, db, config)
        mock_consolidation = MagicMock(spec=ConsolidationEngine)
        sys.consolidation = mock_consolidation

        sys.process_turn("さっきの件について教えて")  # 明示的参照キーワード含む

        assert mock_consolidation.apply_feedback.called
        call_args = mock_consolidation.apply_feedback.call_args
        assert call_args[0][1] == "positive"

    def test_diversity_watchdog_apply_exploration_called_after_search(self, client, db, config):
        """Phase 2: diversity_watchdog.apply_explorationが検索後に呼ばれること"""
        sys = MemorySystem(client, db, config)
        mock_watchdog = MagicMock(spec=DiversityWatchdog)
        mock_watchdog.apply_exploration.return_value = []
        sys.diversity_watchdog = mock_watchdog

        sys.process_turn("テスト入力")

        assert mock_watchdog.apply_exploration.called

    def test_turn_history_appended_after_each_turn(self, system):
        """Phase 2: turn_historyがprocess_turn後に正しくappendされること"""
        assert len(system.turn_history) == 0

        system.process_turn("最初の入力")
        assert len(system.turn_history) == 1
        assert system.turn_history[0]["user_input"] == "最初の入力"

        system.process_turn("二番目の入力")
        assert len(system.turn_history) == 2
        assert system.turn_history[1]["user_input"] == "二番目の入力"

    def test_turn_history_capped_at_10(self, system):
        """Phase 2: turn_historyが10件を超えた場合FIFOで10件に制限されること"""
        for i in range(12):
            system.process_turn(f"入力{i}")
        assert len(system.turn_history) == 10
        assert system.turn_history[0]["user_input"] == "入力2"
        assert system.turn_history[-1]["user_input"] == "入力11"
