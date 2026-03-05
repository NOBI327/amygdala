import random
import math
from typing import Dict, List

from .config import Config
from .db import DatabaseManager


class DiversityWatchdog:
    """
    多様性監視エンジン。
    recall_logのdominant_emotion分布からShannon entropyを計算し、
    探索率を制御することで感情記憶の偏りを防ぐ。
    """

    def __init__(self, config: Config, db_manager: DatabaseManager) -> None:
        self.config = config
        self.db = db_manager

    def compute_diversity_index(self, window: int = 50) -> float:
        """
        recall_logから最近window件のrecallを取得し、
        dominant_emotionの分布からShannon entropyを計算して0~1に正規化して返す。

        Returns:
            float: 0.0（均一）〜1.0（完全多様）。recall_logが0件の場合は1.0。
        """
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT dominant_emotion FROM recall_log "
            "ORDER BY recalled_at DESC LIMIT ?",
            (window,)
        ).fetchall()

        if not rows:
            return 1.0

        counts: Dict[str, int] = {}
        for row in rows:
            emotion = row["dominant_emotion"] or "unknown"
            counts[emotion] = counts.get(emotion, 0) + 1

        total = sum(counts.values())
        entropy = -sum(
            (c / total) * math.log2(c / total)
            for c in counts.values()
            if c > 0
        )

        num_categories = len(self.config.EMOTION_AXES)
        max_entropy = math.log2(num_categories)
        return entropy / max_entropy if max_entropy > 0 else 1.0

    def get_exploration_rate(self) -> float:
        """
        diversity_indexに基づいて探索率を返す。

        Returns:
            float: 探索率 (0.08 / 0.15 / 0.35)
        """
        index = self.compute_diversity_index()
        if index > 0.7:
            return 0.08
        elif index >= 0.4:
            return 0.15
        else:
            return 0.35

    def apply_exploration(
        self,
        search_results: List[Dict],
        emotion_vec: Dict[str, float],
    ) -> List[Dict]:
        """
        検索結果Top-5に多様性を注入する。
        exploration_rateに基づき、最下位の結果を別感情カテゴリの記憶に差し替える。

        Args:
            search_results: 検索結果リスト（Dict形式）
            emotion_vec: 現在の感情ベクトル（未使用だが将来拡張用）

        Returns:
            List[Dict]: 多様性注入済みの検索結果
        """
        if not search_results:
            return search_results

        exploration_rate = self.get_exploration_rate()

        if random.random() >= exploration_rate:
            return search_results

        # 現在の検索結果のdominant_emotionを集計
        emotion_counts: Dict[str, int] = {}
        for result in search_results:
            em = result.get("dominant_emotion") or self._get_dominant_emotion(result)
            emotion_counts[em] = emotion_counts.get(em, 0) + 1

        # 最近recall_logに出現していない感情カテゴリを探す
        conn = self.db.get_connection()
        recent_emotions = {
            row["dominant_emotion"]
            for row in conn.execute(
                "SELECT DISTINCT dominant_emotion FROM recall_log "
                "ORDER BY recalled_at DESC LIMIT 50"
            ).fetchall()
            if row["dominant_emotion"]
        }

        underrepresented = [
            e for e in self.config.EMOTION_AXES
            if e not in recent_emotions
        ]

        if not underrepresented:
            # 全カテゴリが出現済みの場合、検索結果に最も少ない感情を選ぶ
            underrepresented = [
                e for e in self.config.EMOTION_AXES
                if e not in emotion_counts
            ]

        if not underrepresented:
            return search_results

        # underrepresentedな感情カテゴリの記憶をDBから取得（recalled_atが古いもの優先）
        exploration_memory = None
        for target_emotion in underrepresented:
            exploration_memory = self._fetch_exploration_memory(target_emotion)
            if exploration_memory is not None:
                break

        if exploration_memory is None:
            return search_results

        # 最下位の結果を差し替え
        result = list(search_results)
        exploration_memory["exploration"] = True
        result[-1] = exploration_memory
        return result

    def _get_dominant_emotion(self, memory: Dict) -> str:
        """メモリDictから最大値の感情軸を返す"""
        best_emotion = self.config.EMOTION_AXES[0]
        best_score = -1.0
        for emotion in self.config.EMOTION_AXES:
            score = float(memory.get(emotion, 0.0))
            if score > best_score:
                best_score = score
                best_emotion = emotion
        return best_emotion

    def _fetch_exploration_memory(self, target_emotion: str) -> Dict | None:
        """
        指定した感情カテゴリがdominantな記憶をDBから1件取得する。
        recalled_at (last_recalled) が古いもの（NULLを最優先）を返す。
        """
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM memories WHERE archived = FALSE "
            "ORDER BY last_recalled ASC NULLS FIRST LIMIT 100"
        ).fetchall()

        for row in rows:
            memory = dict(row)
            dominant = self._get_dominant_emotion(memory)
            if dominant == target_emotion:
                return memory

        return None
