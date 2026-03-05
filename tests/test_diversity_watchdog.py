"""
tests/test_diversity_watchdog.py
DiversityWatchdog のユニットテスト
"""
import math
import random
from unittest.mock import patch

import pytest

from src.config import Config
from src.db import DatabaseManager
from src.diversity_watchdog import DiversityWatchdog


@pytest.fixture
def setup():
    """インメモリDB + DiversityWatchdog を返すフィクスチャ"""
    config = Config()
    db = DatabaseManager(":memory:")
    db.init()
    watchdog = DiversityWatchdog(config, db)
    return config, db, watchdog


def insert_recall(db: DatabaseManager, dominant_emotion: str, memory_id: int = 1) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO recall_log (memory_id, dominant_emotion) VALUES (?, ?)",
        (memory_id, dominant_emotion),
    )
    conn.commit()


def insert_memory(db: DatabaseManager, **emotion_scores) -> int:
    """memoriesテーブルにレコードを挿入し、idを返す"""
    config = Config()
    axes = config.EMOTION_AXES
    # デフォルトは全て0.0
    scores = {e: emotion_scores.get(e, 0.0) for e in axes}
    conn = db.get_connection()
    cur = conn.execute(
        f"INSERT INTO memories (content, {', '.join(axes)}) "
        f"VALUES (?, {', '.join(['?'] * len(axes))})",
        ("test content", *[scores[e] for e in axes]),
    )
    conn.commit()
    return cur.lastrowid


# ── テストケース ──────────────────────────────────────────────────────────────


class TestComputeDiversityIndex:
    def test_empty_recall_log_returns_one(self, setup):
        """TC1: recall_logが空 → diversity_index == 1.0"""
        _, _, watchdog = setup
        assert watchdog.compute_diversity_index() == 1.0

    def test_uniform_emotion_returns_zero(self, setup):
        """TC2: 全て同じdominant_emotion → diversity_index ≈ 0.0"""
        _, db, watchdog = setup
        for _ in range(10):
            insert_recall(db, "joy")
        index = watchdog.compute_diversity_index()
        assert index == pytest.approx(0.0, abs=1e-9)

    def test_balanced_distribution_returns_one(self, setup):
        """TC3: 8カテゴリに均等分布 → diversity_index ≈ 1.0"""
        config, db, watchdog = setup
        for emotion in config.EMOTION_AXES:
            for _ in range(5):  # 各5件 = 均等
                insert_recall(db, emotion)
        index = watchdog.compute_diversity_index()
        assert index == pytest.approx(1.0, abs=1e-9)

    def test_partial_distribution(self, setup):
        """TC補: 2カテゴリ均等 → entropy=1bit → 1/3 ≈ 0.333"""
        _, db, watchdog = setup
        for _ in range(5):
            insert_recall(db, "joy")
        for _ in range(5):
            insert_recall(db, "sadness")
        index = watchdog.compute_diversity_index()
        expected = 1.0 / math.log2(8)
        assert index == pytest.approx(expected, abs=1e-9)

    def test_window_limits_records(self, setup):
        """TC補: windowパラメータが機能し直近N件のみ使用される"""
        _, db, watchdog = setup
        # 古いレコード: joy × 20
        for _ in range(20):
            insert_recall(db, "joy")
        # 新しいレコード: fear × 5 (window=5でこれだけ見えるはず)
        for _ in range(5):
            insert_recall(db, "fear")
        index = watchdog.compute_diversity_index(window=5)
        assert index == pytest.approx(0.0, abs=1e-9)


class TestGetExplorationRate:
    def test_high_diversity_returns_low_rate(self, setup):
        """TC4a: diversity_index > 0.7 → 0.08"""
        config, db, watchdog = setup
        # 均等分布 → index ≈ 1.0 > 0.7
        for emotion in config.EMOTION_AXES:
            for _ in range(5):
                insert_recall(db, emotion)
        assert watchdog.get_exploration_rate() == 0.08

    def test_medium_diversity_returns_default_rate(self, setup):
        """TC4b: 0.4 <= diversity_index <= 0.7 → 0.15"""
        _, _, watchdog = setup
        with patch.object(watchdog, "compute_diversity_index", return_value=0.55):
            assert watchdog.get_exploration_rate() == 0.15

    def test_low_diversity_returns_high_rate(self, setup):
        """TC4c: diversity_index < 0.4 → 0.35"""
        _, db, watchdog = setup
        # 全て同じ → index = 0.0
        for _ in range(20):
            insert_recall(db, "joy")
        assert watchdog.get_exploration_rate() == 0.35

    def test_boundary_0_7_is_low_rate(self, setup):
        """TC4d: diversity_index == 0.7 は medium (0.15) に属する"""
        _, _, watchdog = setup
        with patch.object(watchdog, "compute_diversity_index", return_value=0.7):
            assert watchdog.get_exploration_rate() == 0.15

    def test_boundary_0_4_is_medium_rate(self, setup):
        """TC4e: diversity_index == 0.4 は medium (0.15)"""
        _, _, watchdog = setup
        with patch.object(watchdog, "compute_diversity_index", return_value=0.4):
            assert watchdog.get_exploration_rate() == 0.15


class TestApplyExploration:
    def _make_search_results(self, count: int = 5) -> list:
        return [
            {"id": i, "content": f"memory {i}", "dominant_emotion": "joy",
             "joy": 0.9, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
             "surprise": 0.0, "disgust": 0.0, "trust": 0.0, "anticipation": 0.0}
            for i in range(count)
        ]

    def test_exploration_replaces_last_result(self, setup):
        """TC5: random.patch で差し替え発生を確認"""
        config, db, watchdog = setup
        # fear記憶をDBに挿入
        insert_memory(db, fear=0.9)

        search_results = self._make_search_results(5)
        emotion_vec = {e: 0.0 for e in config.EMOTION_AXES}

        # exploration_rateを強制的に高く、randomを0.0にして必ず差し替え発生
        with patch.object(watchdog, "get_exploration_rate", return_value=1.0), \
             patch("random.random", return_value=0.0):
            result = watchdog.apply_exploration(search_results, emotion_vec)

        assert result[-1].get("exploration") is True
        assert len(result) == 5

    def test_no_exploration_returns_original(self, setup):
        """TC6: random値が探索率以上 → 変更なし"""
        config, db, watchdog = setup
        search_results = self._make_search_results(5)
        emotion_vec = {e: 0.0 for e in config.EMOTION_AXES}

        with patch("random.random", return_value=0.99):
            result = watchdog.apply_exploration(search_results, emotion_vec)

        assert result == search_results
        assert not any(r.get("exploration") for r in result)

    def test_empty_search_results_returned_as_is(self, setup):
        """TC7: 空のsearch_resultsはそのまま返す"""
        config, _, watchdog = setup
        result = watchdog.apply_exploration([], {})
        assert result == []

    def test_exploration_flag_on_replaced_memory(self, setup):
        """TC8: 差し替えられた記憶に exploration: True が付与される"""
        config, db, watchdog = setup
        insert_memory(db, fear=0.9)
        search_results = self._make_search_results(3)
        emotion_vec = {e: 0.0 for e in config.EMOTION_AXES}

        with patch.object(watchdog, "get_exploration_rate", return_value=1.0), \
             patch("random.random", return_value=0.0):
            result = watchdog.apply_exploration(search_results, emotion_vec)

        last = result[-1]
        assert "exploration" in last
        assert last["exploration"] is True

    def test_no_underrepresented_emotion_no_replacement(self, setup):
        """TC9: 全感情カテゴリが recent recall_log に存在 → 差し替えなし（DBに該当記憶なし）"""
        config, db, watchdog = setup
        # 全カテゴリをrecall_logに登録
        for emotion in config.EMOTION_AXES:
            insert_recall(db, emotion)

        search_results = self._make_search_results(5)
        emotion_vec = {e: 0.0 for e in config.EMOTION_AXES}

        # exploration_rateを強制的に高くして差し替え試みるが、該当記憶がない
        with patch.object(watchdog, "get_exploration_rate", return_value=1.0), \
             patch("random.random", return_value=0.0):
            result = watchdog.apply_exploration(search_results, emotion_vec)

        # 差し替えが発生しない（underrepresented emotions is emptyで早期リターン）
        assert not any(r.get("exploration") for r in result)
