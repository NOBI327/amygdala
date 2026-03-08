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
                from .llm_adapter import AnthropicAdapter
                llm_client = AnthropicAdapter(default_model=config.BACKMAN_MODEL)
            else:
                llm_client = None
                logger.warning(
                    "ANTHROPIC_API_KEY not set. "
                    "Emotion auto-tagging disabled. "
                    "Provide emotions explicitly in store_memory calls, "
                    "or set ANTHROPIC_API_KEY to enable auto-tagging."
                )
            self.memory_system = MemorySystem(llm_client, db, config)
        else:
            self.memory_system = memory_system

        self.auto_tagging = self.memory_system.backman.adapter is not None
        self.mcp = FastMCP("EmotionMemoryServer")
        self._register_tools()

    def _tick_pin_ttl(self) -> List[Dict]:
        """ツール呼び出し毎にピンTTLを1減算し、期限切れピンを返す。

        ユーザー入力1回 ≒ MCPツール呼び出し1回とみなす。
        期限切れピンがあればレスポンスに確認プロンプトを含める。
        """
        pm = self.memory_system.pin_memory
        expired = pm.decrement_ttl()
        return expired

    def _register_tools(self) -> None:
        """FastMCPにツールを登録する"""
        server = self

        @self.mcp.tool()
        def store_memory(
            text: str,
            context: Optional[str] = None,
            emotions: Optional[Dict[str, float]] = None,
            scenes: Optional[List[str]] = None,
        ) -> Dict:
            """感情タギングしてメモリをDBに保存する。

            emotions引数は全10軸の感情スコアをdictで渡す(0.0-1.0)。
            省略した場合、内部LLMで自動タギングする（ANTHROPIC_API_KEY必要）。
            APIキー未設定時はemotionsを明示的に渡すことを強く推奨。
            省略時はゼロベクターとなり検索精度が低下する。

            感情軸: joy, sadness, anger, fear, surprise, disgust, trust, anticipation, importance, urgency

            例(Claude CodeなどLLMクライアントから呼ぶ場合):
            emotions={"joy":0.8,"sadness":0.0,"anger":0.0,"fear":0.0,"surprise":0.2,"disgust":0.0,"trust":0.5,"anticipation":0.3,"importance":0.6,"urgency":0.1}
            scenes=["work","learning"]  # 最大3個
            """
            result = server.store_memory(text, context, emotions, scenes)
            expired = server._tick_pin_ttl()
            if expired:
                result["pin_ttl_expired"] = server.memory_system.pin_memory.generate_ttl_prompt(expired)
            return result

        @self.mcp.tool()
        def recall_memories(
            query: str,
            top_n: int = 5,
            emotions: Optional[Dict[str, float]] = None,
        ) -> List:
            """感情ベース検索でメモリを取得する。

            emotions引数で検索クエリの感情ベクトルを明示的に渡せる(0.0-1.0)。
            省略時は内部LLMでクエリを感情タギングする（ANTHROPIC_API_KEY必要）。
            APIキー未設定時はemotionsを明示的に渡すことを強く推奨。

            感情軸: joy, sadness, anger, fear, surprise, disgust, trust, anticipation, importance, urgency

            例: emotions={"joy":0.3,"sadness":0.0,"anger":0.0,"fear":0.0,"surprise":0.0,"disgust":0.0,"trust":0.5,"anticipation":0.2,"importance":0.7,"urgency":0.1}
            """
            server._tick_pin_ttl()
            return server.recall_memories(query, top_n, emotions_input=emotions)

        @self.mcp.tool()
        def get_stats() -> Dict:
            """メモリシステムの統計情報を返す"""
            server._tick_pin_ttl()
            return server.get_stats()

        @self.mcp.tool()
        def pin_memory(content: str, label: str = "") -> Dict:
            """メモリをピン固定する（ワーキングメモリに常駐）。

            スロット上限あり。満杯の場合はエラーを返す。
            """
            result = server.pin_memory(content, label)
            expired = server._tick_pin_ttl()
            if expired:
                result["pin_ttl_expired"] = server.memory_system.pin_memory.generate_ttl_prompt(expired)
            return result

        @self.mcp.tool()
        def unpin_memory(pin_id: int) -> Dict:
            """ピンを解除し、長期記憶へ移管する。

            pin_idはlist_pinned_memoriesで確認できる。
            """
            result = server.unpin_memory(pin_id)
            server._tick_pin_ttl()
            return result

        @self.mcp.tool()
        def list_pinned_memories() -> List:
            """ピン固定中のメモリ一覧を返す"""
            server._tick_pin_ttl()
            return server.list_pinned_memories()

    def store_memory(
        self,
        text: str,
        context: Optional[str] = None,
        emotions_input: Optional[Any] = None,
        scenes_input: Optional[Any] = None,
    ) -> Dict:
        """
        テキストを感情タギングしてDBに保存する。

        Args:
            text: 保存するテキスト
            context: オプションのコンテキスト情報
            emotions_input: 感情スコア（dict or JSON文字列、省略時は内部LLMでタギング）
            scenes_input: シーンリスト（list or JSON文字列、省略時は空リスト、最大3件）

        Returns:
            {"memory_id": int, "emotion": str, "score": float}
        """
        ms = self.memory_system
        emotion = None

        if emotions_input:
            try:
                if isinstance(emotions_input, str):
                    emotion = json.loads(emotions_input)
                elif isinstance(emotions_input, dict):
                    emotion = emotions_input
                else:
                    raise TypeError(f"Unsupported type: {type(emotions_input)}")
                all_axes = list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)
                for ax in all_axes:
                    val = emotion.get(ax, 0.0)
                    emotion[ax] = max(0.0, min(1.0, float(val)))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Invalid emotions_input: {e}. Falling back to backman.")
                emotion = None

        used_zero_vector = False
        if emotion is None:
            try:
                tag_result = ms.backman.tag_emotion(text)
                emotion = tag_result.get("emotion", {})
                if not self.auto_tagging:
                    used_zero_vector = True
            except Exception as e:
                logger.warning(f"Emotion tagging failed: {e}. Using zero vectors.")
                emotion = {ax: 0.0 for ax in list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)}
                used_zero_vector = True

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

        result = {
            "memory_id": cursor.lastrowid,
            "emotion": dominant_emotion,
            "score": dominant_score,
        }
        if used_zero_vector:
            result["warning"] = (
                "Auto-tagging unavailable (no ANTHROPIC_API_KEY). "
                "Memory saved with zero vector — recall accuracy will be poor. "
                "Provide emotions dict explicitly to enable accurate emotion-based search."
            )
        return result

    def recall_memories(
        self,
        query: str,
        top_n: int = 5,
        emotions_input: Optional[Any] = None,
    ) -> List[Dict]:
        """
        クエリに基づいて感情ベース検索を実行する。

        Args:
            query: 検索クエリ
            top_n: 返却件数上限
            emotions_input: 感情スコア（dict or JSON文字列、省略時は内部LLMでタギング）

        Returns:
            [{"id": int, "content": str, "emotion": str, "score": float}, ...]
        """
        ms = self.memory_system
        emotion_vec = None

        if emotions_input:
            try:
                if isinstance(emotions_input, str):
                    emotion_vec = json.loads(emotions_input)
                elif isinstance(emotions_input, dict):
                    emotion_vec = emotions_input
                else:
                    raise TypeError(f"Unsupported type: {type(emotions_input)}")
                all_axes = list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)
                for ax in all_axes:
                    val = emotion_vec.get(ax, 0.0)
                    emotion_vec[ax] = max(0.0, min(1.0, float(val)))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Invalid emotions_input: {e}. Falling back to backman.")
                emotion_vec = None

        used_zero_vector = False
        if emotion_vec is None:
            try:
                tag_result = ms.backman.tag_emotion(query)
                emotion_vec = tag_result.get("emotion", {})
                if not self.auto_tagging:
                    used_zero_vector = True
            except Exception as e:
                logger.warning(f"Emotion tagging failed: {e}. Using zero vectors.")
                emotion_vec = {ax: 0.0 for ax in list(ms.config.EMOTION_AXES) + list(ms.config.META_AXES)}
                used_zero_vector = True

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
        if used_zero_vector:
            return {
                "results": output,
                "warning": (
                    "Auto-tagging unavailable (no ANTHROPIC_API_KEY). "
                    "Searched with zero vector — results may be inaccurate. "
                    "Provide emotions dict explicitly for accurate emotion-based search."
                ),
            }
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
            "SELECT COUNT(*) FROM pin_memories WHERE active = TRUE"
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
            "auto_tagging": self.auto_tagging,
        }

    def pin_memory(self, content: str, label: str = "") -> Dict:
        """ピンメモリを追加する"""
        pm = self.memory_system.pin_memory
        if pm.is_full():
            return {"error": "Pin slots full", "max_slots": self.memory_system.config.PIN_MEMORY_SLOTS}
        success = pm.add_pin(content, label)
        if success:
            pins = pm.get_active_pins()
            new_pin = pins[-1] if pins else {}
            return {
                "pin_id": new_pin.get("id"),
                "content": content,
                "label": label,
                "slots_used": pm.slot_count(),
                "max_slots": self.memory_system.config.PIN_MEMORY_SLOTS,
            }
        return {"error": "Failed to add pin"}

    def unpin_memory(self, pin_id: int) -> Dict:
        """ピンを解除し長期記憶へ移管する"""
        pm = self.memory_system.pin_memory
        try:
            memory_id = pm.release_pin(pin_id)
            return {"released_pin_id": pin_id, "migrated_to_memory_id": memory_id}
        except ValueError as e:
            return {"error": str(e)}

    def list_pinned_memories(self) -> List[Dict]:
        """アクティブなピン一覧を返す"""
        pm = self.memory_system.pin_memory
        pins = pm.get_active_pins()
        return [
            {
                "pin_id": p["id"],
                "content": p["content"],
                "label": p.get("label", ""),
                "ttl_remaining": p.get("ttl_turns_remaining"),
            }
            for p in pins
        ]

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
