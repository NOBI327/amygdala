import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .config import Config
from .db import DatabaseManager
from .memory_system import MemorySystem

logger = logging.getLogger(__name__)


class EmotionMemoryMCPServer:
    """
    感情記憶システムのMCPサーバー。
    FastMCPを使用してstdio transportでClaude Codeと通信する。

    DIパターン: memory_systemをコンストラクタで注入。
    Noneの場合はデフォルト設定で初期化（Config() + 実際のDB使用）。
    """

    def __init__(self, memory_system: Optional[Any] = None) -> None:
        if memory_system is None:
            import os
            config = Config()
            db = DatabaseManager.from_config(config)
            db.init()
            if os.environ.get("ANTHROPIC_API_KEY"):
                import anthropic
                llm_client = anthropic.Anthropic()
            else:
                llm_client = None  # タギングはゼロベクトルフォールバック
            self.memory_system = MemorySystem(llm_client, db, config)
        else:
            self.memory_system = memory_system

        self.mcp = FastMCP("EmotionMemoryServer")
        self._register_tools()

    def _register_tools(self) -> None:
        """FastMCPにツールを登録する"""
        server = self

        @self.mcp.tool()
        def store_memory(
            text: str,
            context: Optional[str] = None,
            emotions: Optional[str] = None,
            scenes: Optional[str] = None,
        ) -> Dict:
            """感情タギングしてメモリをDBに保存する。

            emotions引数は全10軸の感情スコアをJSON文字列で渡す（0.0-1.0）。
            省略した場合、内部LLMで自動タギングする。

            感情軸: joy, sadness, anger, fear, surprise, disgust, trust, anticipation, importance, urgency

            例（Claude CodeなどLLMクライアントから呼ぶ場合）:
            emotions='{"joy":0.8,"sadness":0.0,"anger":0.0,"fear":0.0,"surprise":0.2,"disgust":0.0,"trust":0.5,"anticipation":0.3,"importance":0.6,"urgency":0.1}'
            scenes='["work","learning"]'  # 最大3個
            """
            return server.store_memory(text, context, emotions, scenes)

        @self.mcp.tool()
        def recall_memories(query: str, top_n: int = 5) -> List:
            """感情ベース検索でメモリを取得する"""
            return server.recall_memories(query, top_n)

        @self.mcp.tool()
        def get_stats() -> Dict:
            """メモリシステムの統計情報を返す"""
            return server.get_stats()

    def store_memory(
        self,
        text: str,
        context: Optional[str] = None,
        emotions_json: Optional[str] = None,
        scenes_json: Optional[str] = None,
    ) -> Dict:
        """
        テキストを感情タギングしてDBに保存する。

        Args:
            text: 保存するテキスト
            context: オプションのコンテキスト情報
            emotions_json: 感情スコアのJSON文字列（省略時は内部LLMでタギング）
            scenes_json: シーンリストのJSON文字列（省略時は空リスト、最大3件）

        Returns:
            {"memory_id": int, "emotion": str, "score": float}
        """
        ms = self.memory_system
        emotion = None

        if emotions_json:
            try:
                emotion = json.loads(emotions_json)
                all_axes = list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)
                for ax in all_axes:
                    val = emotion.get(ax, 0.0)
                    emotion[ax] = max(0.0, min(1.0, float(val)))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Invalid emotions_json: {e}. Falling back to backman.")
                emotion = None

        if emotion is None:
            try:
                tag_result = ms.backman.tag_emotion(text)
                emotion = tag_result.get("emotion", {})
            except Exception as e:
                logger.warning(f"Emotion tagging failed: {e}. Using zero vectors.")
                emotion = {ax: 0.0 for ax in list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)}

        dominant_emotion, dominant_score = max(
            ((ax, float(emotion.get(ax, 0.0))) for ax in ms.config.EMOTION_AXES),
            key=lambda x: x[1],
            default=("neutral", 0.0),
        )

        conn = ms.db.get_connection()
        cursor = conn.execute(
            """INSERT INTO memories
               (content, raw_input,
                joy, sadness, anger, fear, surprise, disgust, trust, anticipation,
                importance, urgency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                text,
                context or "",
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
            ),
        )
        conn.commit()

        return {
            "memory_id": cursor.lastrowid,
            "emotion": dominant_emotion,
            "score": dominant_score,
        }

    def recall_memories(self, query: str, top_n: int = 5) -> List[Dict]:
        """
        クエリに基づいて感情ベース検索を実行する。

        Args:
            query: 検索クエリ
            top_n: 返却件数上限

        Returns:
            [{"id": int, "content": str, "emotion": str, "score": float}, ...]
        """
        ms = self.memory_system
        try:
            tag_result = ms.backman.tag_emotion(query)
            emotion_vec = tag_result.get("emotion", {})
        except Exception as e:
            logger.warning(f"Emotion tagging failed: {e}. Using zero vectors.")
            emotion_vec = {ax: 0.0 for ax in list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)}

        results = ms.search_engine.search_memories(emotion_vec, [])
        results = ms.diversity_watchdog.apply_exploration(results, emotion_vec)

        output = []
        for m in results[:top_n]:
            output.append({
                "id": m["id"],
                "content": m["content"],
                "emotion": self._get_dominant_emotion(m, ms.config),
                "score": float(m.get("score", m.get("relevance_score", 0.0))),
            })
        return output

    def get_stats(self) -> Dict:
        """
        メモリシステムの統計情報を返す。

        Returns:
            {
                "total_memories": int,
                "emotion_distribution": {"joy": N, ...},
                "diversity_index": float,
                "pinned_count": int,
            }
        """
        ms = self.memory_system
        conn = ms.db.get_connection()

        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE archived = FALSE"
        ).fetchone()[0]

        pinned = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE pinned_flag = TRUE AND archived = FALSE"
        ).fetchone()[0]

        emotion_distribution = {}
        for ax in ms.config.EMOTION_AXES:
            count = conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE archived = FALSE AND {ax} > 0.3"
            ).fetchone()[0]
            emotion_distribution[ax] = count

        diversity_index = ms.diversity_watchdog.compute_diversity_index()

        return {
            "total_memories": total,
            "emotion_distribution": emotion_distribution,
            "diversity_index": diversity_index,
            "pinned_count": pinned,
        }

    def _get_dominant_emotion(self, memory: Dict, config: Config) -> str:
        """メモリDictから支配的な感情軸名を返す。

        search_engine形式（emotion: dict）と
        DB raw形式（joy, sadness, ...フラットキー）の両方に対応。
        """
        emotion_data = memory.get("emotion")
        if isinstance(emotion_data, dict):
            candidates = {ax: float(emotion_data.get(ax, 0.0)) for ax in config.EMOTION_AXES}
        else:
            candidates = {ax: float(memory.get(ax, 0.0)) for ax in config.EMOTION_AXES}

        return max(candidates.items(), key=lambda x: x[1], default=(config.EMOTION_AXES[0], 0.0))[0]

    def run(self) -> None:
        """stdio transportでMCPサーバーを起動する"""
        self.mcp.run(transport="stdio")


if __name__ == "__main__":
    server = EmotionMemoryMCPServer()
    server.run()
