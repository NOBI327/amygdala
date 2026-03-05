"""
tests/test_search_engine.py
SearchEngineのユニットテスト。全テストでDatabaseManager(":memory:")を使用。
"""
import json
import pytest
from datetime import datetime, timezone, timedelta

from src.config import Config
from src.db import DatabaseManager
from src.search_engine import SearchEngine


@pytest.fixture
def engine():
    """インメモリDBを使ったSearchEngineフィクスチャ"""
    config = Config()
    db = DatabaseManager(":memory:")
    db.init()
    se = SearchEngine(config, db)
    yield se
    db.close()


def _insert_memory(db: DatabaseManager, content: str, emotion: dict,
                   scenes: list, relevance_score: float = 1.0,
                   recall_count: int = 0, pinned_flag: bool = False,
                   archived: bool = False,
                   timestamp: str = None) -> int:
    """テスト用記憶をDBに挿入し、row IDを返す"""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    conn = db.get_connection()
    cur = conn.execute(
        """INSERT INTO memories
           (content, timestamp, joy, sadness, anger, fear, surprise, disgust,
            trust, anticipation, importance, urgency, scenes,
            relevance_score, recall_count, pinned_flag, archived)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            content, timestamp,
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
            json.dumps(scenes),
            relevance_score,
            recall_count,
            1 if pinned_flag else 0,
            1 if archived else 0,
        )
    )
    conn.commit()
    return cur.lastrowid


# ─────────────────────────────────────────────
# 1. cosine_similarity: 同一ベクトル → 1.0
# ─────────────────────────────────────────────
def test_cosine_similarity_identical(engine):
    vec = {"joy": 1.0, "sadness": 0.5, "anger": 0.0, "fear": 0.2,
           "surprise": 0.3, "disgust": 0.0, "trust": 0.8, "anticipation": 0.1}
    result = engine.cosine_similarity(vec, vec, engine.config.EMOTION_AXES)
    assert abs(result - 1.0) < 1e-9


# ─────────────────────────────────────────────
# 2. cosine_similarity: 直交ベクトル → 0.0
# ─────────────────────────────────────────────
def test_cosine_similarity_orthogonal(engine):
    vec1 = {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
            "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0}
    vec2 = {"joy": 0.0, "sadness": 1.0, "anger": 0.0, "fear": 0.0,
            "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0}
    result = engine.cosine_similarity(vec1, vec2, engine.config.EMOTION_AXES)
    assert abs(result - 0.0) < 1e-9


# ─────────────────────────────────────────────
# 3. cosine_similarity: ゼロベクトル → 0.0（エラーにならないこと）
# ─────────────────────────────────────────────
def test_cosine_similarity_zero_vector(engine):
    zero = {ax: 0.0 for ax in engine.config.EMOTION_AXES}
    vec = {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
           "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0}
    assert engine.cosine_similarity(zero, vec, engine.config.EMOTION_AXES) == 0.0
    assert engine.cosine_similarity(zero, zero, engine.config.EMOTION_AXES) == 0.0


# ─────────────────────────────────────────────
# 4. cosine_similarity: EMOTION_AXES（8軸）のみで計算すること（B1確認）
# ─────────────────────────────────────────────
def test_cosine_similarity_uses_emotion_axes_only(engine):
    """META_AXESの値が異なっても、EMOTION_AXESが同一なら結果は同一"""
    vec1 = {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
            "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
            "importance": 0.9, "urgency": 0.9}
    vec2 = {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
            "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
            "importance": 0.1, "urgency": 0.1}
    result = engine.cosine_similarity(vec1, vec2, engine.config.EMOTION_AXES)
    assert abs(result - 1.0) < 1e-9


# ─────────────────────────────────────────────
# 5. scene_similarity
# ─────────────────────────────────────────────
def test_scene_similarity_full_match(engine):
    assert engine.scene_similarity(["work", "learning"], ["work", "learning"]) == 1.0


def test_scene_similarity_no_match(engine):
    assert engine.scene_similarity(["work"], ["hobby"]) == 0.0


def test_scene_similarity_partial(engine):
    result = engine.scene_similarity(["work", "learning"], ["work", "daily"])
    # intersection=1, union=3 → 1/3
    assert abs(result - 1.0 / 3.0) < 1e-9


def test_scene_similarity_both_empty(engine):
    assert engine.scene_similarity([], []) == 0.0


# ─────────────────────────────────────────────
# 6. compute_time_decay: days_ago=0 → 1.0
# ─────────────────────────────────────────────
def test_compute_time_decay_zero_days(engine):
    result = engine.compute_time_decay(0.0, False, 0)
    assert abs(result - 1.0) < 1e-9


# ─────────────────────────────────────────────
# 7. compute_time_decay: days_ago=30（通常）→ 0.5（v0.4.1修正式の確認）
# ─────────────────────────────────────────────
def test_compute_time_decay_half_life_normal(engine):
    """HALF_LIFE_NORMAL=30日なので days_ago=30 → 0.5^1 = 0.5"""
    result = engine.compute_time_decay(30.0, False, 0)
    assert abs(result - 0.5) < 1e-9


# ─────────────────────────────────────────────
# 8. compute_time_decay: pinned_flag=True → half_life=60で計算
# ─────────────────────────────────────────────
def test_compute_time_decay_pinned(engine):
    """HALF_LIFE_PINNED=60日なので days_ago=60, pinned=True → 0.5^1 = 0.5"""
    result = engine.compute_time_decay(60.0, True, 0)
    assert abs(result - 0.5) < 1e-9

    # 同じ60日でも pinned=False（recall_count=0）は HALF_LIFE_NORMAL=30 → 0.5^2=0.25
    result_normal = engine.compute_time_decay(60.0, False, 0)
    assert abs(result_normal - 0.25) < 1e-9


# ─────────────────────────────────────────────
# 9. search_memories: 感情が近い記憶が上位に来ること（2件のテストデータ）
# ─────────────────────────────────────────────
def test_search_memories_similar_first(engine):
    now_iso = datetime.now(timezone.utc).isoformat()

    # 検索クエリに近い感情: joy高め
    _insert_memory(engine.db, "joy memory",
                   {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                    "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                    "importance": 0.0, "urgency": 0.0},
                   [], timestamp=now_iso)

    # 検索クエリと遠い感情: sadness高め
    _insert_memory(engine.db, "sadness memory",
                   {"joy": 0.0, "sadness": 1.0, "anger": 0.0, "fear": 0.0,
                    "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                    "importance": 0.0, "urgency": 0.0},
                   [], timestamp=now_iso)

    query_emotion = {"joy": 1.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                     "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                     "importance": 0.0, "urgency": 0.0}
    results = engine.search_memories(query_emotion, [])
    assert len(results) >= 2
    assert results[0]["content"] == "joy memory"
    assert results[1]["content"] == "sadness memory"


# ─────────────────────────────────────────────
# 10. search_memories: top_k=1で1件のみ返ること
# ─────────────────────────────────────────────
def test_search_memories_top_k(engine):
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        _insert_memory(engine.db, f"memory {i}",
                       {"joy": float(i), "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                        "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                        "importance": 0.0, "urgency": 0.0},
                       [], timestamp=now_iso)
    results = engine.search_memories({"joy": 1.0}, [], top_k=1)
    assert len(results) == 1


# ─────────────────────────────────────────────
# 11. B1確認: META_AXES（importance/urgency）がEMOTION_AXESゼロでもスコアに寄与すること
# ─────────────────────────────────────────────
def test_b1_meta_axes_contribute(engine):
    """EMOTION_AXESがゼロでも、META_AXESが一致すればスコア>0になること"""
    now_iso = datetime.now(timezone.utc).isoformat()
    _insert_memory(engine.db, "meta only memory",
                   {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                    "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                    "importance": 1.0, "urgency": 1.0},
                   [], timestamp=now_iso)

    # EMOTION_AXESはゼロだが META_AXESは一致するクエリ
    query_emotion = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                     "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                     "importance": 1.0, "urgency": 1.0}
    results = engine.search_memories(query_emotion, [])
    assert len(results) == 1
    assert results[0]["score"] > 0.0


# ─────────────────────────────────────────────
# 12. log_recall: recall_logテーブルに正しく記録されること
# ─────────────────────────────────────────────
def test_log_recall(engine):
    now_iso = datetime.now(timezone.utc).isoformat()
    mid = _insert_memory(engine.db, "recall test",
                         {"joy": 0.5, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                          "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0,
                          "importance": 0.0, "urgency": 0.0},
                         [], timestamp=now_iso)

    engine.log_recall([mid], was_used=True,
                      dominant_emotion="joy", context_scene="work")

    conn = engine.db.get_connection()
    rows = conn.execute(
        "SELECT * FROM recall_log WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["memory_id"] == mid
    assert bool(row["was_used"]) is True
    assert row["dominant_emotion"] == "joy"
    assert row["context_scene"] == "work"
