import json
import math
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from .config import Config
from .db import DatabaseManager

logger = logging.getLogger(__name__)


class SearchEngine:
    """
    感情ベース長期記憶検索エンジン。
    企画書§5準拠。B1: 感情8軸とメタ2軸を分離計算。

    DIパターン: DatabaseManagerをコンストラクタで注入。
    """

    def __init__(self, config: Config, db_manager: DatabaseManager) -> None:
        self.config = config
        self.db = db_manager

    def cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float],
                           axes: tuple) -> float:
        """
        指定された軸のみでコサイン類似度を計算する。

        Args:
            vec1, vec2: 感情ベクトル（全10軸を含むdict）
            axes: 計算に使用する軸のタプル

        Returns:
            コサイン類似度（0.0 ~ 1.0）。ゼロベクトルの場合は0.0。
        """
        v1 = [vec1.get(ax, 0.0) for ax in axes]
        v2 = [vec2.get(ax, 0.0) for ax in axes]
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a ** 2 for a in v1))
        norm2 = math.sqrt(sum(b ** 2 for b in v2))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot / (norm1 * norm2)

    def scene_similarity(self, memory_scenes: List[str], current_scenes: List[str]) -> float:
        """Jaccard係数で場面類似度を計算する（0.0 ~ 1.0）"""
        mem_set = set(memory_scenes)
        cur_set = set(current_scenes)
        if not mem_set and not cur_set:
            return 0.0
        union = mem_set | cur_set
        intersection = mem_set & cur_set
        return len(intersection) / len(union)

    def compute_time_decay(self, days_ago: float, pinned_flag: bool,
                            recall_count: int) -> float:
        """
        時間減衰係数を計算する（企画書§3.3 v0.4.1修正版）。
        正しい式: 0.5 ** (days_ago / half_life)
        半減期経過時に正確に0.5になる。
        """
        if pinned_flag:
            half_life = self.config.HALF_LIFE_PINNED
        elif recall_count > 5:
            half_life = self.config.HALF_LIFE_FREQUENT
        else:
            half_life = self.config.HALF_LIFE_NORMAL
        return 0.5 ** (days_ago / half_life)

    def score_memory_rows(self, rows, emotion_vec: Dict[str, float],
                           scenes: List[str]) -> List[Dict]:
        """メモリ行リストを感情ベクトルでスコアリングする。

        search_memoriesと同じスコアリングロジックを適用する。
        外部から取得済みの行を渡すことで、グラフ展開等での再利用が可能。

        Args:
            rows: SQLite Row or dict のリスト
            emotion_vec: 検索クエリの感情ベクトル
            scenes: 検索クエリのシーンリスト

        Returns:
            スコア降順のメモリdictリスト
        """
        now = datetime.now(timezone.utc)
        scored = []
        for row in rows:
            mem = dict(row)
            mem_emotion = {ax: mem.get(ax, 0.0) for ax in
                           list(self.config.EMOTION_AXES) + list(self.config.META_AXES)}
            try:
                mem_scenes = json.loads(mem.get("scenes", "[]"))
            except (json.JSONDecodeError, TypeError):
                mem_scenes = []

            try:
                ts = datetime.fromisoformat(mem["timestamp"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                days_ago = max(0.0, (now - ts).total_seconds() / 86400)
            except Exception:
                days_ago = 0.0

            emotion_sim = self.cosine_similarity(
                emotion_vec, mem_emotion, self.config.EMOTION_AXES
            )
            meta_score = self.cosine_similarity(
                emotion_vec, mem_emotion, self.config.META_AXES
            )
            scene_sim = self.scene_similarity(mem_scenes, scenes)
            decay = self.compute_time_decay(
                days_ago, bool(mem.get("pinned_flag")), mem.get("recall_count", 0)
            )
            feedback_weight = min(mem.get("relevance_score", 1.0) / 5.0, 2.0)

            score = ((emotion_sim * self.config.EMOTION_WEIGHT +
                      scene_sim * self.config.SCENE_WEIGHT) *
                     decay * feedback_weight +
                     meta_score * self.config.META_WEIGHT)

            scored.append({
                "id": mem["id"],
                "content": mem["content"],
                "emotion": mem_emotion,
                "scenes": mem_scenes,
                "score": score,
                "timestamp": mem["timestamp"],
                "pinned_flag": bool(mem.get("pinned_flag")),
                "recall_count": mem.get("recall_count", 0),
                "relevance_score": mem.get("relevance_score", 1.0),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def search_memories(self, emotion_vec: Dict[str, float], scenes: List[str],
                         top_k: Optional[int] = None) -> List[Dict]:
        """
        長期記憶DBを検索し、複合スコア上位の記憶を返す。

        アルゴリズム（B1準拠）:
        1. archived=FalseのDB全記憶を取得
        2. 各記憶の複合スコア計算（score_memory_rowsに委譲）
        3. スコア降順でtop_k件を返す

        Returns:
            [{"id": int, "content": str, "emotion": dict, "scenes": list,
              "score": float, "timestamp": str, "pinned_flag": bool,
              "recall_count": int, "relevance_score": float}]
        """
        k = top_k if top_k is not None else self.config.TOP_K_RESULTS
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM memories WHERE archived = FALSE"
        ).fetchall()

        scored = self.score_memory_rows(rows, emotion_vec, scenes)
        return scored[:k]

    def log_recall(self, memory_ids: List[int], was_used: bool,
                    dominant_emotion: str, context_scene: str) -> None:
        """リコールをrecall_logテーブルに記録する"""
        conn = self.db.get_connection()
        for mid in memory_ids:
            conn.execute(
                """INSERT INTO recall_log (memory_id, was_used, dominant_emotion, context_scene)
                   VALUES (?, ?, ?, ?)""",
                (mid, was_used, dominant_emotion, context_scene)
            )
        conn.commit()
