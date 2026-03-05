import pytest
from unittest import mock
from src.config import Config
from src.db import DatabaseManager
from src.reconsolidation import ConsolidationEngine


@pytest.fixture
def setup():
    config = Config()
    db = DatabaseManager(":memory:")
    db.init()
    engine = ConsolidationEngine(config, db)
    return config, db, engine


def insert_memory(db, joy=0.5, sadness=0.2, anger=0.1, fear=0.1,
                  surprise=0.1, disgust=0.1, trust=0.1, anticipation=0.1,
                  relevance_score=1.0, recall_count=0):
    conn = db.get_connection()
    cursor = conn.execute(
        """INSERT INTO memories
           (content, joy, sadness, anger, fear, surprise, disgust, trust,
            anticipation, relevance_score, recall_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("test memory", joy, sadness, anger, fear, surprise, disgust, trust,
         anticipation, relevance_score, recall_count),
    )
    conn.commit()
    return cursor.lastrowid


def get_memory(db, memory_id):
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row)


def test_positive_feedback_relevance_recall_decay(setup):
    """positive: relevance_score+0.1, recall_count+1, decay 0.99 確認"""
    config, db, engine = setup
    mid = insert_memory(db, joy=0.5, relevance_score=1.0, recall_count=2)
    engine.apply_feedback([mid], "positive")
    mem = get_memory(db, mid)
    assert abs(mem["joy"] - 0.5 * 0.99) < 1e-9
    assert abs(mem["relevance_score"] - 1.1) < 1e-9
    assert mem["recall_count"] == 3


def test_negative_feedback_decay(setup):
    """negative: decay 0.88 適用確認（感情値が0.88倍になっていること）"""
    config, db, engine = setup
    # Use probability=0 to avoid reclassify noise: patch random.random to always return > 0.35
    mid = insert_memory(db, joy=0.8, sadness=0.4)
    with mock.patch("random.random", return_value=0.99):
        engine.apply_feedback([mid], "negative")
    mem = get_memory(db, mid)
    assert abs(mem["joy"] - 0.8 * 0.88) < 1e-9
    assert abs(mem["sadness"] - 0.4 * 0.88) < 1e-9


def test_neutral_feedback_decay(setup):
    """neutral: decay 0.95 適用確認"""
    config, db, engine = setup
    mid = insert_memory(db, joy=0.6, anger=0.3)
    with mock.patch("random.random", return_value=0.99):
        engine.apply_feedback([mid], "neutral")
    mem = get_memory(db, mid)
    assert abs(mem["joy"] - 0.6 * 0.95) < 1e-9
    assert abs(mem["anger"] - 0.3 * 0.95) < 1e-9


def test_reclassify_logic_boost(setup):
    """再分類ロジック: random制御でブースト確認"""
    config, db, engine = setup
    # joy is dominant (0.8), negative with 35% reclassify -> sadness chosen
    mid = insert_memory(db, joy=0.8, sadness=0.1, anger=0.0, fear=0.0,
                        surprise=0.0, disgust=0.0, trust=0.0, anticipation=0.0)
    with mock.patch("random.random", return_value=0.1):   # 0.1 < 0.35 → reclassify
        with mock.patch("random.choice", return_value="sadness"):
            engine.apply_feedback([mid], "negative")
    mem = get_memory(db, mid)
    expected_sadness = min(1.0, 0.1 * 0.88 + 0.25)
    assert abs(mem["sadness"] - expected_sadness) < 1e-9
    # joy should just be decayed, not boosted
    assert abs(mem["joy"] - 0.8 * 0.88) < 1e-9


def test_empty_memory_ids_no_exception(setup):
    """空memory_idsでも例外が出ないこと"""
    config, db, engine = setup
    engine.apply_feedback([], "positive")
    engine.apply_feedback([], "negative")
    engine.apply_feedback([], "neutral")


def test_unknown_feedback_type_raises_value_error(setup):
    """不明なfeedback_typeはValueErrorとして処理"""
    config, db, engine = setup
    mid = insert_memory(db)
    with pytest.raises(ValueError):
        engine.apply_feedback([mid], "unknown_type")


def test_reclassify_not_triggered_below_probability(setup):
    """確率以上のrandom値では再分類が発生しないこと"""
    config, db, engine = setup
    mid = insert_memory(db, joy=0.8, sadness=0.2)
    original_sadness = 0.2
    with mock.patch("random.random", return_value=0.99):  # 0.99 > 0.35 → no reclassify
        engine.apply_feedback([mid], "negative")
    mem = get_memory(db, mid)
    # sadness should only be decayed, no boost
    assert abs(mem["sadness"] - original_sadness * 0.88) < 1e-9


def test_positive_does_not_reclassify(setup):
    """positive feedbackでは再分類が発生しないこと"""
    config, db, engine = setup
    mid = insert_memory(db, joy=0.8, sadness=0.1, relevance_score=1.0, recall_count=0)
    engine.apply_feedback([mid], "positive")
    mem = get_memory(db, mid)
    # relevance and recall updated
    assert abs(mem["relevance_score"] - 1.1) < 1e-9
    assert mem["recall_count"] == 1
    # sadness only decayed by 0.99, no boost
    assert abs(mem["sadness"] - 0.1 * 0.99) < 1e-9
