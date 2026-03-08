"""
test_mcp_server.py — EmotionMemoryMCPServer オフラインテスト

MemorySystemをMagicMockで注入してLLM/DB呼び出しなしでテストする。
"""
import pytest
from unittest.mock import MagicMock, patch

from src.mcp_server import EmotionMemoryMCPServer
from src.config import Config


EMOTION_AXES = ("joy", "sadness", "anger", "fear", "surprise", "disgust", "trust", "anticipation")
META_AXES = ("importance", "urgency")


@pytest.fixture
def mock_memory_system():
    ms = MagicMock()
    ms.config = Config()
    return ms


@pytest.fixture
def server(mock_memory_system):
    return EmotionMemoryMCPServer(memory_system=mock_memory_system)


def _make_mock_conn(*fetchone_values):
    """execute().fetchone()[0] が順番に返るmock connectionを生成する"""
    mock_conn = MagicMock()
    side_effects = []
    for val in fetchone_values:
        mock_exec = MagicMock()
        mock_exec.fetchone.return_value = [val]
        side_effects.append(mock_exec)
    mock_conn.execute.side_effect = side_effects
    return mock_conn


def _emotion_vec(**overrides):
    vec = {ax: 0.0 for ax in EMOTION_AXES + META_AXES}
    vec.update(overrides)
    return vec


# ─── store_memory ────────────────────────────────────────────────────────────

class TestStoreMemory:
    def test_backman_is_called_with_text(self, server, mock_memory_system):
        """store_memory はbackman.tag_emotionを呼び出す"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(joy=0.9)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        server.store_memory("今日は楽しい日だ")

        mock_memory_system.backman.tag_emotion.assert_called_once_with("今日は楽しい日だ")

    def test_returns_correct_dict_format(self, server, mock_memory_system):
        """store_memory は memory_id / emotion / score を含む dict を返す"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(joy=0.8)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("I am very happy")

        assert result["memory_id"] == 42
        assert result["emotion"] == "joy"
        assert isinstance(result["score"], float)

    def test_dominant_emotion_is_highest_axis(self, server, mock_memory_system):
        """dominant emotionは最大値の感情軸が選ばれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(sadness=0.95, joy=0.1)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 5
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("悲しい気持ちだ")

        assert result["emotion"] == "sadness"
        assert result["score"] == pytest.approx(0.95)

    def test_context_is_stored_as_raw_input(self, server, mock_memory_system):
        """contextが指定された場合はINSERT文に渡される"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(trust=0.7)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 10
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("信頼できる仲間だ", context="仕事の文脈")

        assert result["memory_id"] == 10
        # execute の第2引数（values tuple）にコンテキストが含まれているか確認
        call_args = mock_conn.execute.call_args[0][1]
        assert "仕事の文脈" in call_args

    def test_backman_failure_does_not_raise(self, server, mock_memory_system):
        """backman失敗時もゼロベクトルでDB保存を試みる"""
        mock_memory_system.backman.tag_emotion.side_effect = Exception("LLM error")
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 99
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("テスト")

        assert "memory_id" in result
        assert "emotion" in result
        assert "score" in result

    def test_store_memory_with_pre_tagged_emotions_str(self, server, mock_memory_system):
        """emotions JSON文字列付き -> Backman呼ばれない"""
        emotions = '{"joy":0.8,"sadness":0.0,"anger":0.0,"fear":0.0,"surprise":0.2,"disgust":0.0,"trust":0.5,"anticipation":0.3,"importance":0.6,"urgency":0.1}'
        scenes = '["work","learning"]'
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("test", emotions_input=emotions, scenes_input=scenes)

        assert result["emotion"] == "joy"
        mock_memory_system.backman.tag_emotion.assert_not_called()

    def test_store_memory_with_pre_tagged_emotions_dict(self, server, mock_memory_system):
        """emotions dict付き -> Backman呼ばれない（MCPクライアント経由）"""
        emotions = {"joy": 0.8, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "surprise": 0.2, "disgust": 0.0, "trust": 0.5, "anticipation": 0.3, "importance": 0.6, "urgency": 0.1}
        scenes = ["work", "learning"]
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("test", emotions_input=emotions, scenes_input=scenes)

        assert result["emotion"] == "joy"
        mock_memory_system.backman.tag_emotion.assert_not_called()

    def test_store_memory_without_emotions_uses_backman(self, server, mock_memory_system):
        """emotions_json未提供 -> 従来通りBackman呼ばれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(trust=0.7)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 2
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        server.store_memory("テスト")

        mock_memory_system.backman.tag_emotion.assert_called_once_with("テスト")

    def test_store_memory_invalid_emotions_falls_back(self, server, mock_memory_system):
        """不正JSON -> フォールバック（Backman呼ばれる）"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(joy=0.5)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 3
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("テスト", emotions_input="invalid-json{{{")

        assert "memory_id" in result
        mock_memory_system.backman.tag_emotion.assert_called_once()

    def test_store_memory_emotions_clamped(self, server, mock_memory_system):
        """範囲外値(1.5等) -> 0.0-1.0クランプ"""
        emotions = '{"joy":1.5,"sadness":-0.3,"anger":0.0,"fear":0.0,"surprise":0.0,"disgust":0.0,"trust":0.0,"anticipation":0.0,"importance":0.0,"urgency":0.0}'
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 4
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("テスト", emotions_input=emotions)

        # Backmanは呼ばれない（valid JSONなので）
        mock_memory_system.backman.tag_emotion.assert_not_called()
        # joyが最大なので dominant_emotion == "joy"
        assert result["emotion"] == "joy"
        # クランプ後 joy=1.0
        assert result["score"] == pytest.approx(1.0)

    def test_store_memory_missing_axes_filled(self, server, mock_memory_system):
        """軸欠落 -> 0.0で補完（全軸のバリデーションが走る）"""
        # joy のみ指定、他は欠落
        emotions = '{"joy":0.9}'
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 5
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        result = server.store_memory("テスト", emotions_input=emotions)

        mock_memory_system.backman.tag_emotion.assert_not_called()
        assert result["emotion"] == "joy"
        assert result["score"] == pytest.approx(0.9)


# ─── recall_memories ─────────────────────────────────────────────────────────

class TestRecallMemories:
    def _make_search_results(self, n):
        return [
            {
                "id": i + 1,
                "content": f"Memory {i + 1}",
                "emotion": _emotion_vec(joy=0.6),
                "score": 0.9 - i * 0.05,
                "relevance_score": 1.0,
            }
            for i in range(n)
        ]

    def test_returns_top_n_results(self, server, mock_memory_system):
        """top_n=3 を指定すると3件以下が返る"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(joy=0.5)
        }
        results = self._make_search_results(7)
        mock_memory_system.search_engine.search_memories.return_value = results
        mock_memory_system.diversity_watchdog.apply_exploration.return_value = results

        output = server.recall_memories("楽しい記憶", top_n=3)

        assert len(output) == 3

    def test_returns_correct_keys(self, server, mock_memory_system):
        """各結果に id / content / emotion / score が含まれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(fear=0.8)
        }
        results = self._make_search_results(2)
        mock_memory_system.search_engine.search_memories.return_value = results
        mock_memory_system.diversity_watchdog.apply_exploration.return_value = results

        output = server.recall_memories("怖い体験")

        assert len(output) >= 1
        for item in output:
            assert "id" in item
            assert "content" in item
            assert "emotion" in item
            assert "score" in item

    def test_search_engine_is_called(self, server, mock_memory_system):
        """search_engine.search_memories が呼ばれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(anger=0.7)
        }
        mock_memory_system.search_engine.search_memories.return_value = []
        mock_memory_system.diversity_watchdog.apply_exploration.return_value = []

        server.recall_memories("angry memory")

        mock_memory_system.search_engine.search_memories.assert_called_once()

    def test_diversity_watchdog_is_applied(self, server, mock_memory_system):
        """diversity_watchdog.apply_exploration が呼ばれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(surprise=0.6)
        }
        mock_memory_system.search_engine.search_memories.return_value = []
        mock_memory_system.diversity_watchdog.apply_exploration.return_value = []

        server.recall_memories("surprising event")

        mock_memory_system.diversity_watchdog.apply_exploration.assert_called_once()

    def test_handles_flat_emotion_format(self, server, mock_memory_system):
        """DB raw形式（フラットキー）の探索結果も正しく処理する"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(trust=0.5)
        }
        flat_result = {
            "id": 99,
            "content": "Exploration memory",
            "joy": 0.0,
            "sadness": 0.0,
            "anger": 0.0,
            "fear": 0.0,
            "surprise": 0.0,
            "disgust": 0.9,
            "trust": 0.0,
            "anticipation": 0.0,
            "score": 0.3,
            "relevance_score": 1.0,
            "exploration": True,
        }
        mock_memory_system.search_engine.search_memories.return_value = [flat_result]
        mock_memory_system.diversity_watchdog.apply_exploration.return_value = [flat_result]

        output = server.recall_memories("test", top_n=5)

        assert len(output) == 1
        assert output[0]["emotion"] == "disgust"


# ─── get_stats ────────────────────────────────────────────────────────────────

class TestGetStats:
    def test_returns_all_required_keys(self, server, mock_memory_system):
        """get_stats は必須キーを全て含む dict を返す"""
        # total=100, pinned=5, 8 emotions each returning 10
        mock_conn = _make_mock_conn(100, 5, *([10] * 8))
        mock_memory_system.db.get_connection.return_value = mock_conn
        mock_memory_system.diversity_watchdog.compute_diversity_index.return_value = 0.75

        result = server.get_stats()

        assert "total_memories" in result
        assert "emotion_distribution" in result
        assert "diversity_index" in result
        assert "pinned_count" in result

    def test_emotion_distribution_has_all_axes(self, server, mock_memory_system):
        """emotion_distribution に8感情軸が全て含まれる"""
        mock_conn = _make_mock_conn(50, 3, *([5] * 8))
        mock_memory_system.db.get_connection.return_value = mock_conn
        mock_memory_system.diversity_watchdog.compute_diversity_index.return_value = 0.5

        result = server.get_stats()

        for ax in EMOTION_AXES:
            assert ax in result["emotion_distribution"]

    def test_diversity_index_comes_from_watchdog(self, server, mock_memory_system):
        """diversity_index は diversity_watchdog から取得される"""
        mock_conn = _make_mock_conn(10, 0, *([0] * 8))
        mock_memory_system.db.get_connection.return_value = mock_conn
        mock_memory_system.diversity_watchdog.compute_diversity_index.return_value = 0.88

        result = server.get_stats()

        assert result["diversity_index"] == pytest.approx(0.88)
        mock_memory_system.diversity_watchdog.compute_diversity_index.assert_called_once()

    def test_values_are_correct_types(self, server, mock_memory_system):
        """各フィールドの型が正しい"""
        mock_conn = _make_mock_conn(25, 2, *([3] * 8))
        mock_memory_system.db.get_connection.return_value = mock_conn
        mock_memory_system.diversity_watchdog.compute_diversity_index.return_value = 0.6

        result = server.get_stats()

        assert isinstance(result["total_memories"], int)
        assert isinstance(result["emotion_distribution"], dict)
        assert isinstance(result["diversity_index"], float)
        assert isinstance(result["pinned_count"], int)
