import random
import logging
from typing import List
from .config import Config
from .db import DatabaseManager

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """
    簡易扁桃体模倣型LLMメモリ拡張システム Phase 2: 再タギングエンジン。
    企画書§4準拠。recall後のフィードバックに基づいて記憶の感情強度を調節する。

    DIパターン: DatabaseManagerをコンストラクタで注入。
    """

    def __init__(self, config: Config, db_manager: DatabaseManager) -> None:
        self.config = config
        self.db = db_manager

    def apply_feedback(self, memory_ids: List[int], feedback_type: str) -> None:
        """
        recall時のフィードバックに基づいて記憶の感情強度を調節する。

        Args:
            memory_ids: 対象記憶IDリスト
            feedback_type:
                "positive"  - ユーザーが記憶を実際に使用（明示的言及）
                "negative"  - recall後にユーザーが無視（暗黙的未使用）
                "neutral"   - 判定不可

        Raises:
            ValueError: 不明なfeedback_typeの場合
        """
        if feedback_type not in ("positive", "negative", "neutral"):
            raise ValueError(f"Unknown feedback_type: {feedback_type!r}")

        conn = self.db.get_connection()

        for mid in memory_ids:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            if row is None:
                continue

            mem = dict(row)
            emotion_vals = {ax: mem.get(ax, 0.0) for ax in self.config.EMOTION_AXES}

            if feedback_type == "positive":
                emotion_vals = self._apply_decay(emotion_vals, 0.99)
                new_relevance = mem.get("relevance_score", 1.0) + 0.1
                new_recall = mem.get("recall_count", 0) + 1
                conn.execute(
                    """UPDATE memories SET
                        joy=?, sadness=?, anger=?, fear=?,
                        surprise=?, disgust=?, trust=?, anticipation=?,
                        relevance_score=?, recall_count=?
                        WHERE id=?""",
                    (
                        emotion_vals["joy"], emotion_vals["sadness"],
                        emotion_vals["anger"], emotion_vals["fear"],
                        emotion_vals["surprise"], emotion_vals["disgust"],
                        emotion_vals["trust"], emotion_vals["anticipation"],
                        new_relevance, new_recall, mid,
                    ),
                )

            elif feedback_type == "negative":
                dominant = self._get_dominant_emotion(mem)
                emotion_vals = self._apply_decay(emotion_vals, 0.88)
                emotion_vals = self._maybe_reclassify(emotion_vals, dominant, 0.35)
                conn.execute(
                    """UPDATE memories SET
                        joy=?, sadness=?, anger=?, fear=?,
                        surprise=?, disgust=?, trust=?, anticipation=?
                        WHERE id=?""",
                    (
                        emotion_vals["joy"], emotion_vals["sadness"],
                        emotion_vals["anger"], emotion_vals["fear"],
                        emotion_vals["surprise"], emotion_vals["disgust"],
                        emotion_vals["trust"], emotion_vals["anticipation"],
                        mid,
                    ),
                )

            elif feedback_type == "neutral":
                dominant = self._get_dominant_emotion(mem)
                emotion_vals = self._apply_decay(emotion_vals, 0.95)
                emotion_vals = self._maybe_reclassify(emotion_vals, dominant, 0.15)
                conn.execute(
                    """UPDATE memories SET
                        joy=?, sadness=?, anger=?, fear=?,
                        surprise=?, disgust=?, trust=?, anticipation=?
                        WHERE id=?""",
                    (
                        emotion_vals["joy"], emotion_vals["sadness"],
                        emotion_vals["anger"], emotion_vals["fear"],
                        emotion_vals["surprise"], emotion_vals["disgust"],
                        emotion_vals["trust"], emotion_vals["anticipation"],
                        mid,
                    ),
                )

        conn.commit()

    def _get_dominant_emotion(self, memory_row: dict) -> str:
        """EMOTION_AXES中で最大値の感情軸名を返す"""
        return max(
            self.config.EMOTION_AXES,
            key=lambda ax: memory_row.get(ax, 0.0),
        )

    def _apply_decay(self, emotion_vals: dict, decay_rate: float) -> dict:
        """全感情値にdecay_rateを乗算する"""
        return {ax: val * decay_rate for ax, val in emotion_vals.items()}

    def _maybe_reclassify(self, emotion_vals: dict, dominant: str,
                           probability: float) -> dict:
        """
        probabilityの確率でdominant以外の軸にランダムで+0.25ブースト（max 1.0でclip）。
        感情方向（どの感情か）は保存し強度のみ調節する。
        """
        if random.random() < probability:
            non_dominant = [ax for ax in self.config.EMOTION_AXES if ax != dominant]
            target = random.choice(non_dominant)
            emotion_vals[target] = min(1.0, emotion_vals[target] + 0.25)
        return emotion_vals
