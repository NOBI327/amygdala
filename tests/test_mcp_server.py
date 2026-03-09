"""
test_mcp_server.py — EmotionMemoryMCPServer オフラインテスト

MemorySystemをMagicMockで注入してLLM/DB呼び出しなしでテストする。
グラフ系ツールは実DB(:memory:)を使うfixture(server_with_db)でテスト。
"""
import pytest
from unittest.mock import MagicMock, patch

from src.mcp_server import EmotionMemoryMCPServer
from src.config import Config
from src.db import DatabaseManager


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


# ─── Graph tools fixtures (real DB) ─────────────────────────────────────────

def _graph_emotion(**overrides):
    vec = {ax: 0.0 for ax in EMOTION_AXES + META_AXES}
    vec.update(overrides)
    return vec


@pytest.fixture
def server_with_db():
    """実DB(:memory:)を使うサーバー。グラフツールテスト用。"""
    ms = MagicMock()
    config = Config(DB_PATH=":memory:")
    db = DatabaseManager(":memory:")
    db.init()
    ms.config = config
    ms.db = db
    ms.backman.adapter = None  # LLM なし
    ms.pin_memory.decrement_ttl.return_value = []
    srv = EmotionMemoryMCPServer(memory_system=ms)
    yield srv
    db.close()


# ─── query_entity_graph ─────────────────────────────────────────────────────

class TestQueryEntityGraph:
    def test_returns_error_for_unknown_entity(self, server_with_db):
        result = server_with_db.query_entity_graph("不存在", 1)
        assert "error" in result

    def test_returns_context_for_known_entity(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(joy=0.8, trust=0.6, importance=0.5)
        srv.graph_engine.upsert_node("田中", "person", emo)
        srv.graph_engine.upsert_node("プロジェクトA", "topic", emo)
        srv.graph_engine.upsert_edge("田中", "プロジェクトA", emo, ["担当者"])

        result = srv.query_entity_graph("田中", 1)

        assert result["entity"] == "田中"
        assert "プロジェクトA" in result["related_entities"]

    def test_hops_clamped_to_max_2(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(joy=0.5, importance=0.3)
        srv.graph_engine.upsert_node("A", "person", emo)

        # hops=99 でもエラーにならない（内部で clamp される）
        result = srv.query_entity_graph("A", 99)
        assert result["entity"] == "A"

    def test_2hop_returns_distant_entities(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(trust=0.7, importance=0.5)
        srv.graph_engine.upsert_node("X", "person", emo)
        srv.graph_engine.upsert_node("Y", "topic", emo)
        srv.graph_engine.upsert_node("Z", "place", emo)
        srv.graph_engine.upsert_edge("X", "Y", emo, ["関連"])
        srv.graph_engine.upsert_edge("Y", "Z", emo, ["場所"])

        result = srv.query_entity_graph("X", 2)

        assert "Z" in result["related_entities"]


# ─── list_graph_entities ─────────────────────────────────────────────────────

class TestListGraphEntities:
    def test_empty_graph_returns_empty_list(self, server_with_db):
        result = server_with_db.list_graph_entities()
        assert result == []

    def test_returns_all_entities(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(joy=0.5, importance=0.5)
        srv.graph_engine.upsert_node("Alice", "person", emo)
        srv.graph_engine.upsert_node("Python", "topic", emo)

        result = srv.list_graph_entities()

        labels = [r["label"] for r in result]
        assert "Alice" in labels
        assert "Python" in labels

    def test_type_filter_person(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(importance=0.5)
        srv.graph_engine.upsert_node("Bob", "person", emo)
        srv.graph_engine.upsert_node("東京", "place", emo)

        result = srv.list_graph_entities(type_filter="person")

        labels = [r["label"] for r in result]
        assert "Bob" in labels
        assert "東京" not in labels

    def test_top_n_limits_results(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(importance=0.5)
        for i in range(10):
            srv.graph_engine.upsert_node(f"Entity{i}", "topic", emo)

        result = srv.list_graph_entities(top_n=3)

        assert len(result) == 3

    def test_result_contains_expected_keys(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(joy=0.3, importance=0.7)
        srv.graph_engine.upsert_node("テスト", "item", emo, aliases=["test"])

        result = srv.list_graph_entities()

        assert len(result) == 1
        item = result[0]
        assert "id" in item
        assert item["label"] == "テスト"
        assert item["type"] == "item"
        assert "test" in item["aliases"]
        assert item["mention_count"] == 1

    def test_ordered_by_importance_x_mention(self, server_with_db):
        srv = server_with_db
        # 高importance
        srv.graph_engine.upsert_node("High", "topic", _graph_emotion(importance=0.9))
        # 低importance
        srv.graph_engine.upsert_node("Low", "topic", _graph_emotion(importance=0.1))

        result = srv.list_graph_entities()

        assert result[0]["label"] == "High"
        assert result[1]["label"] == "Low"


# ─── forget_entity ───────────────────────────────────────────────────────────

class TestForgetEntity:
    def test_returns_error_for_unknown_entity(self, server_with_db):
        result = server_with_db.forget_entity("ghost")
        assert "error" in result

    def test_archives_node_and_edges(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(trust=0.5, importance=0.5)
        srv.graph_engine.upsert_node("佐藤", "person", emo)
        srv.graph_engine.upsert_node("仕事", "topic", emo)
        srv.graph_engine.upsert_edge("佐藤", "仕事", emo, ["担当"])

        result = srv.forget_entity("佐藤")

        assert result["forgotten_entity"] == "佐藤"
        assert result["archived_edges"] == 1

        # ノードが検索できなくなっていること
        assert srv.graph_engine.find_node("佐藤") is None

    def test_forget_does_not_affect_other_entities(self, server_with_db):
        srv = server_with_db
        emo = _graph_emotion(importance=0.5)
        srv.graph_engine.upsert_node("削除対象", "topic", emo)
        srv.graph_engine.upsert_node("残す対象", "topic", emo)

        srv.forget_entity("削除対象")

        assert srv.graph_engine.find_node("残す対象") is not None
        assert srv.graph_engine.find_node("削除対象") is None

    def test_forget_with_alias(self, server_with_db):
        """aliasで登録されたエンティティもforgetできる"""
        srv = server_with_db
        emo = _graph_emotion(importance=0.5)
        srv.graph_engine.upsert_node("山田", "person", emo, aliases=["山田部長"])

        result = srv.forget_entity("山田部長")

        assert result["forgotten_entity"] == "山田"
        assert srv.graph_engine.find_node("山田") is None


# ─── store_memory → graph update ─────────────────────────────────────────────

class TestStoreMemoryGraphUpdate:
    """Step 6c: store_memory がグラフ更新を呼び出すテスト"""

    def test_store_memory_triggers_graph_update(self, server, mock_memory_system):
        """store_memory 呼び出し後に graph_engine.process_turn が呼ばれる"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(joy=0.7)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        with patch.object(server.graph_engine, "process_turn") as mock_gpt:
            server.store_memory("田中さんとプロジェクトAについて話した")
            assert mock_gpt.called
            call_args = mock_gpt.call_args[0]
            assert call_args[0] == "田中さんとプロジェクトAについて話した"

    def test_store_memory_graph_failure_still_saves(self, server, mock_memory_system):
        """graph_engine.process_turn が失敗しても store_memory は正常完了する"""
        mock_memory_system.backman.tag_emotion.return_value = {
            "emotion": _emotion_vec(trust=0.5)
        }
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_memory_system.db.get_connection.return_value = mock_conn

        with patch.object(server.graph_engine, "process_turn", side_effect=Exception("graph error")):
            result = server.store_memory("テスト")
            assert result["memory_id"] == 42
            assert "emotion" in result
