"""RelationalGraphEngine のテスト。

テストパターン: :memory: DB + pytest fixture + Mock LLM（既存パターン踏襲）
"""

import json
import math
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from src.config import Config
from src.db import DatabaseManager
from src.relational_graph import (
    RelationalGraphEngine, ALL_AXES, DECAY_RATES, HONORIFIC_SUFFIXES,
)


# ========== Fixtures ==========

@pytest.fixture
def config():
    return Config(DB_PATH=":memory:")


@pytest.fixture
def db():
    manager = DatabaseManager(":memory:")
    manager.init()
    yield manager
    manager.close()


@pytest.fixture
def engine(config, db):
    return RelationalGraphEngine(config, db)


@pytest.fixture
def emotion_joy():
    """joy 優位の感情ベクトル"""
    return {"joy": 0.8, "trust": 0.6, "sadness": 0.1, "anger": 0.0,
            "fear": 0.0, "surprise": 0.2, "disgust": 0.0,
            "anticipation": 0.3, "importance": 0.5, "urgency": 0.2}


@pytest.fixture
def emotion_anger():
    """anger 優位の感情ベクトル"""
    return {"joy": 0.0, "trust": 0.1, "sadness": 0.3, "anger": 0.8,
            "fear": 0.4, "surprise": 0.1, "disgust": 0.2,
            "anticipation": 0.1, "importance": 0.7, "urgency": 0.6}


# ========== DB スキーマ ==========

class TestSchema:
    def test_graph_tables_created(self, db):
        conn = db.get_connection()
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "graph_nodes" in tables
        assert "graph_edges" in tables
        assert "graph_tags" in tables

    def test_graph_indexes_created(self, db):
        conn = db.get_connection()
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_node_label" in indexes

    def test_node_type_constraint(self, db):
        conn = db.get_connection()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO graph_nodes (label, type) VALUES (?, ?)",
                ("test", "invalid_type")
            )

    def test_edge_unique_constraint(self, db):
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO graph_nodes (label, type) VALUES (?, ?)", ("A", "person")
        )
        conn.execute(
            "INSERT INTO graph_nodes (label, type) VALUES (?, ?)", ("B", "topic")
        )
        conn.commit()
        conn.execute(
            "INSERT INTO graph_edges (source_id, target_id) VALUES (?, ?)", (1, 2)
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO graph_edges (source_id, target_id) VALUES (?, ?)", (1, 2)
            )

    def test_tag_unique_constraint(self, db):
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO graph_nodes (label, type) VALUES (?, ?)", ("A", "person")
        )
        conn.execute(
            "INSERT INTO graph_nodes (label, type) VALUES (?, ?)", ("B", "topic")
        )
        conn.execute(
            "INSERT INTO graph_edges (source_id, target_id) VALUES (?, ?)", (1, 2)
        )
        conn.commit()
        conn.execute(
            "INSERT INTO graph_tags (edge_id, label) VALUES (?, ?)", (1, "友人")
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO graph_tags (edge_id, label) VALUES (?, ?)", (1, "友人")
            )


# ========== ノード CRUD ==========

class TestNodeCRUD:
    def test_create_node(self, engine, emotion_joy):
        node = engine.upsert_node("田中", "person", emotion_joy)
        assert node["label"] == "田中"
        assert node["type"] == "person"
        assert node["mention_count"] == 1
        assert abs(node["base_emotion"]["joy"] - 0.8) < 1e-6

    def test_upsert_existing_node_increments_count(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        node = engine.upsert_node("田中", "person", emotion_joy)
        assert node["mention_count"] == 2

    def test_upsert_updates_emotion_with_ema(self, engine, emotion_joy, emotion_anger):
        engine.upsert_node("田中", "person", emotion_joy)
        node = engine.upsert_node("田中", "person", emotion_anger)
        # mention_count=2 → alpha=2/3
        # joy: 0.8 * 1/3 + 0.0 * 2/3 ≈ 0.267
        assert abs(node["base_emotion"]["joy"] - 0.8 / 3) < 0.01

    def test_normalize_label_strips_honorifics(self, engine, emotion_joy):
        node = engine.upsert_node("田中さん", "person", emotion_joy)
        assert node["label"] == "田中"

    def test_normalize_label_preserves_short_names(self, engine):
        """敬称だけの名前は敬称除去しない"""
        normalized = engine._normalize_label("さん")
        assert normalized == "さん"

    def test_aliases_search(self, engine, emotion_joy):
        engine.upsert_node("佐藤", "person", emotion_joy, aliases=["部長", "佐藤部長"])
        found = engine.find_node("部長")
        assert found is not None
        assert found["label"] == "佐藤"

    def test_aliases_merge_on_upsert(self, engine, emotion_joy):
        engine.upsert_node("佐藤", "person", emotion_joy, aliases=["部長"])
        node = engine.upsert_node("佐藤", "person", emotion_joy, aliases=["佐藤部長"])
        assert "部長" in node["aliases"]
        assert "佐藤部長" in node["aliases"]

    def test_find_nonexistent_returns_none(self, engine):
        assert engine.find_node("存在しない") is None

    def test_find_node_by_label(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        found = engine.find_node("田中")
        assert found is not None
        assert found["label"] == "田中"

    def test_find_node_normalizes_input(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        found = engine.find_node("田中さん")
        assert found is not None
        assert found["label"] == "田中"

    def test_archived_node_not_found(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        conn = engine.db.get_connection()
        conn.execute("UPDATE graph_nodes SET archived = TRUE WHERE label = '田中'")
        conn.commit()
        assert engine.find_node("田中") is None


# ========== エッジ CRUD ==========

class TestEdgeCRUD:
    def test_create_edge(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("プロジェクトA", "topic", emotion_joy)
        edge = engine.upsert_edge("田中", "プロジェクトA", emotion_joy)
        assert edge["source_id"] is not None
        assert edge["target_id"] is not None
        assert edge["activation_count"] == 1
        assert abs(edge["confidence"] - 0.5) < 1e-6

    def test_upsert_existing_edge(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("プロジェクトA", "topic", emotion_joy)
        engine.upsert_edge("田中", "プロジェクトA", emotion_joy)
        edge = engine.upsert_edge("田中", "プロジェクトA", emotion_joy)
        assert edge["activation_count"] == 2
        assert edge["strength"] > 1.0  # 補強された
        assert edge["confidence"] > 0.5  # 上昇

    def test_edge_with_tags(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("プロジェクトA", "topic", emotion_joy)
        edge = engine.upsert_edge(
            "田中", "プロジェクトA", emotion_joy, tag_labels=["担当者", "責任者"]
        )
        assert len(edge["tags"]) == 2
        tag_labels = {t["label"] for t in edge["tags"]}
        assert "担当者" in tag_labels
        assert "責任者" in tag_labels

    def test_edge_raises_on_missing_node(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        with pytest.raises(ValueError):
            engine.upsert_edge("田中", "存在しない", emotion_joy)

    def test_get_edges(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_node("B", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy)
        engine.upsert_edge("田中", "B", emotion_joy)
        node = engine.find_node("田中")
        edges = engine.get_edges(node["id"])
        assert len(edges) == 2

    def test_get_edges_excludes_archived(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy)
        conn = engine.db.get_connection()
        conn.execute("UPDATE graph_edges SET archived = TRUE")
        conn.commit()
        node = engine.find_node("田中")
        assert len(engine.get_edges(node["id"])) == 0

    def test_get_edges_include_archived(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy)
        conn = engine.db.get_connection()
        conn.execute("UPDATE graph_edges SET archived = TRUE")
        conn.commit()
        node = engine.find_node("田中")
        assert len(engine.get_edges(node["id"], include_archived=True)) == 1


# ========== タグライフサイクル ==========

class TestTagLifecycle:
    def test_create_tag_as_candidate(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        edge = engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        tag = edge["tags"][0]
        assert tag["confirmed"] is False
        assert abs(tag["strength"] - 0.5) < 1e-6

    def test_tag_activation_count_increments(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        edge = engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        tag = [t for t in edge["tags"] if t["label"] == "友人"][0]
        assert tag["activation_count"] == 2

    def test_manual_confirm_tag(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        edge = engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        tag_id = edge["tags"][0]["id"]
        engine.confirm_tag(tag_id)
        conn = engine.db.get_connection()
        row = conn.execute("SELECT * FROM graph_tags WHERE id = ?", (tag_id,)).fetchone()
        assert row["confirmed"] == True
        assert abs(row["strength"] - 1.0) < 1e-6

    def test_auto_confirm_at_threshold(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        # 3回 upsert → activation_count = 3 → 閾値到達
        for _ in range(3):
            engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["同僚"])
        confirmed = engine._auto_confirm_tags()
        assert confirmed >= 1
        conn = engine.db.get_connection()
        row = conn.execute(
            "SELECT * FROM graph_tags WHERE label = '同僚'"
        ).fetchone()
        assert row["confirmed"] == True

    def test_auto_confirm_below_threshold(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        # 2回 → activation_count = 2 → 閾値未達
        for _ in range(2):
            engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["同僚"])
        confirmed = engine._auto_confirm_tags()
        assert confirmed == 0

    def test_decay_rate_stable_for_positive_person(self, engine, emotion_joy):
        """person + 肯定感情 → stable (0.01)"""
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "person", emotion_joy)
        edge = engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        tag = edge["tags"][0]
        assert abs(tag["decay_rate"] - DECAY_RATES["stable"]) < 1e-6

    def test_decay_rate_situational_for_negative_person(self, engine, emotion_anger):
        """person + 否定感情 → situational (0.05)"""
        engine.upsert_node("田中", "person", emotion_anger)
        engine.upsert_node("A", "person", emotion_anger)
        edge = engine.upsert_edge("田中", "A", emotion_anger, tag_labels=["対立"])
        tag = edge["tags"][0]
        assert abs(tag["decay_rate"] - DECAY_RATES["situational"]) < 1e-6

    def test_decay_rate_temporary_for_item(self, engine, emotion_joy):
        """item → temporary (0.10)"""
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("本", "item", emotion_joy)
        edge = engine.upsert_edge("田中", "本", emotion_joy, tag_labels=["所有"])
        tag = edge["tags"][0]
        assert abs(tag["decay_rate"] - DECAY_RATES["temporary"]) < 1e-6


# ========== 減衰処理 ==========

class TestDecay:
    def test_compute_tag_strength_no_decay(self, engine, emotion_joy):
        """作成直後は strength ≈ 初期値"""
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        edge = engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["関連"])
        conn = engine.db.get_connection()
        tag_row = conn.execute("SELECT * FROM graph_tags WHERE label = '関連'").fetchone()
        strength = engine._compute_tag_strength(tag_row)
        assert abs(strength - 0.5) < 0.01  # ほぼ減衰なし

    def test_apply_decay_removes_weak_tags(self, engine, emotion_joy):
        """strength が閾値以下のタグが削除される"""
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["一時的"])
        # 強制的に strength を閾値以下に
        conn = engine.db.get_connection()
        conn.execute("UPDATE graph_tags SET strength = 0.05 WHERE label = '一時的'")
        conn.commit()
        result = engine.apply_decay()
        assert result["removed_tags"] >= 1

    def test_apply_decay_archives_tagless_edges(self, engine, emotion_joy):
        """タグが全削除されたエッジが soft-archive される"""
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["一時的"])
        conn = engine.db.get_connection()
        conn.execute("UPDATE graph_tags SET strength = 0.01")
        conn.commit()
        result = engine.apply_decay()
        assert result["archived_edges"] >= 1
        edge = conn.execute(
            "SELECT archived FROM graph_edges WHERE id = 1"
        ).fetchone()
        assert edge["archived"] == True

    def test_exponential_decay_formula(self, engine):
        """指数減衰の計算精度"""
        mock_row = {
            "strength": 1.0,
            "decay_rate": 0.05,
            "last_activated": (datetime.now() - timedelta(days=10)).isoformat(),
        }
        strength = engine._compute_tag_strength(mock_row)
        expected = 1.0 * math.exp(-0.05 * 10)
        assert abs(strength - expected) < 0.01


# ========== 感情移動平均 ==========

class TestEmotionEMA:
    def test_ema_second_mention(self, engine):
        vec1 = {axis: 0.0 for axis in ALL_AXES}
        vec1["joy"] = 1.0
        vec2 = {axis: 0.0 for axis in ALL_AXES}
        vec2["sadness"] = 1.0

        engine.upsert_node("X", "person", vec1)
        node = engine.upsert_node("X", "person", vec2)
        # mention_count=2 → alpha=2/3
        # joy: 1.0 * 1/3 + 0.0 * 2/3 = 0.333
        # sadness: 0.0 * 1/3 + 1.0 * 2/3 = 0.667
        assert abs(node["base_emotion"]["joy"] - 1.0 / 3) < 0.01
        assert abs(node["base_emotion"]["sadness"] - 2.0 / 3) < 0.01

    def test_ema_convergence(self, engine):
        """多数回の更新で最新値に収束"""
        vec = {axis: 0.0 for axis in ALL_AXES}
        vec["trust"] = 0.5
        engine.upsert_node("Y", "person", vec)

        target = {axis: 0.0 for axis in ALL_AXES}
        target["trust"] = 1.0
        for _ in range(20):
            node = engine.upsert_node("Y", "person", target)
        assert node["base_emotion"]["trust"] > 0.9


# ========== スコープ管理 ==========

class TestScopeManagement:
    def test_enforce_node_limit(self, engine, emotion_joy):
        config = engine.config
        config.GRAPH_MAX_ACTIVE_NODES = 3

        for i in range(5):
            engine.upsert_node(f"entity_{i}", "topic", emotion_joy)

        archived = engine._enforce_node_limit()
        assert archived == 2
        conn = engine.db.get_connection()
        active = conn.execute(
            "SELECT COUNT(*) as cnt FROM graph_nodes WHERE archived = FALSE"
        ).fetchone()["cnt"]
        assert active == 3

    def test_enforce_edge_limit(self, engine, emotion_joy):
        config = engine.config
        config.GRAPH_MAX_EDGES_PER_NODE = 2

        engine.upsert_node("center", "person", emotion_joy)
        for i in range(4):
            engine.upsert_node(f"target_{i}", "topic", emotion_joy)
            engine.upsert_edge("center", f"target_{i}", emotion_joy)

        node = engine.find_node("center")
        archived = engine._enforce_edge_limit(node["id"])
        assert archived == 2

    def test_enforce_tag_limit(self, engine, emotion_joy):
        config = engine.config
        config.GRAPH_MAX_TAGS_PER_EDGE = 2

        engine.upsert_node("A", "person", emotion_joy)
        engine.upsert_node("B", "topic", emotion_joy)
        edge = engine.upsert_edge(
            "A", "B", emotion_joy, tag_labels=["t1", "t2", "t3", "t4"]
        )
        removed = engine._enforce_tag_limit(edge["id"])
        assert removed == 2


# ========== 検索 ==========

class TestSearch:
    def test_get_entity_context(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("プロジェクト", "topic", emotion_joy)
        edge = engine.upsert_edge(
            "田中", "プロジェクト", emotion_joy, tag_labels=["担当"]
        )
        # confirm tag
        engine.confirm_tag(edge["tags"][0]["id"])

        ctx = engine.get_entity_context("田中")
        assert ctx is not None
        assert ctx["entity"] == "田中"
        assert "プロジェクト" in ctx["related_entities"]
        assert "担当" in ctx["active_tags"]

    def test_get_entity_context_not_found(self, engine):
        assert engine.get_entity_context("存在しない") is None

    def test_search_by_tag(self, engine, emotion_joy):
        engine.upsert_node("田中", "person", emotion_joy)
        engine.upsert_node("A", "topic", emotion_joy)
        engine.upsert_edge("田中", "A", emotion_joy, tag_labels=["友人"])
        results = engine.search_by_tag("友人")
        assert len(results) == 1

    def test_search_by_emotion(self, engine, emotion_joy, emotion_anger):
        engine.upsert_node("happy_person", "person", emotion_joy)
        engine.upsert_node("angry_person", "person", emotion_anger)
        results = engine.search_by_emotion(emotion_joy, top_k=1)
        assert len(results) == 1
        assert results[0]["label"] == "happy_person"

    def test_2hop_search(self, engine, emotion_joy):
        engine.upsert_node("A", "person", emotion_joy)
        engine.upsert_node("B", "person", emotion_joy)
        engine.upsert_node("C", "person", emotion_joy)
        engine.upsert_edge("A", "B", emotion_joy)
        engine.upsert_edge("B", "C", emotion_joy)
        ctx = engine.get_entity_context("A", hops=2)
        assert "C" in ctx["related_entities"]


# ========== process_turn 統合 ==========

class TestProcessTurn:
    def test_process_turn_without_llm(self, engine, emotion_joy):
        """LLM非可用時はスキップ"""
        result = engine.process_turn("テスト文", emotion_joy)
        assert result["updated"] is False

    def test_process_turn_with_mock_llm(self, db, config, emotion_joy):
        """Mock LLM でエンティティ抽出→グラフ更新"""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = json.dumps({
            "entities": [
                {
                    "label": "田中",
                    "type": "person",
                    "aliases": ["田中さん"],
                    "relations": [
                        {"target": "新プロジェクト", "tags": ["担当者"]}
                    ]
                },
                {
                    "label": "新プロジェクト",
                    "type": "topic",
                    "aliases": [],
                    "relations": []
                }
            ]
        })
        engine = RelationalGraphEngine(config, db, llm_adapter=mock_llm)
        result = engine.process_turn("田中さんが新プロジェクトを担当する", emotion_joy)
        assert result["updated"] is True
        assert result["nodes_affected"] == 2
        assert result["edges_affected"] == 1

        # ノードが作成されていることを確認
        assert engine.find_node("田中") is not None
        assert engine.find_node("新プロジェクト") is not None

    def test_process_turn_llm_error_graceful(self, db, config, emotion_joy):
        """LLM エラー時もクラッシュしない"""
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = Exception("API error")
        engine = RelationalGraphEngine(config, db, llm_adapter=mock_llm)
        result = engine.process_turn("テスト", emotion_joy)
        assert result["updated"] is False

    def test_process_turn_with_code_block_response(self, db, config, emotion_joy):
        """LLM がコードブロックで囲んだ JSON を返した場合"""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = """```json
{
  "entities": [
    {"label": "鈴木", "type": "person", "aliases": [], "relations": []}
  ]
}
```"""
        engine = RelationalGraphEngine(config, db, llm_adapter=mock_llm)
        result = engine.process_turn("鈴木さんと会った", emotion_joy)
        assert result["updated"] is True
        assert engine.find_node("鈴木") is not None


# ========== 手動マージ ==========

class TestMergeNodes:
    def test_merge_nodes(self, engine, emotion_joy, emotion_anger):
        engine.upsert_node("佐藤", "person", emotion_joy)
        engine.upsert_node("部長", "person", emotion_anger)
        keep = engine.find_node("佐藤")
        merge = engine.find_node("部長")

        result = engine.merge_nodes(keep["id"], merge["id"])
        assert result["label"] == "佐藤"
        assert "部長" in result["aliases"]
        assert result["mention_count"] == 2

        # merge_id が archived になっている
        conn = engine.db.get_connection()
        merged = conn.execute(
            "SELECT archived FROM graph_nodes WHERE id = ?", (merge["id"],)
        ).fetchone()
        assert merged["archived"] == True

    def test_merge_transfers_edges(self, engine, emotion_joy):
        engine.upsert_node("佐藤", "person", emotion_joy)
        engine.upsert_node("部長", "person", emotion_joy)
        engine.upsert_node("プロジェクト", "topic", emotion_joy)
        engine.upsert_edge("部長", "プロジェクト", emotion_joy)

        keep = engine.find_node("佐藤")
        merge = engine.find_node("部長")
        engine.merge_nodes(keep["id"], merge["id"])

        # エッジが佐藤に付け替わっている
        edges = engine.get_edges(keep["id"])
        assert len(edges) == 1

    def test_merge_nonexistent_raises(self, engine, emotion_joy):
        engine.upsert_node("佐藤", "person", emotion_joy)
        keep = engine.find_node("佐藤")
        with pytest.raises(ValueError):
            engine.merge_nodes(keep["id"], 9999)
