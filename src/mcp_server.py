import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .config import Config
from .db import DatabaseManager
from .memory_system import MemorySystem
from .relational_graph import RelationalGraphEngine

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
            config = Config.from_env()
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
        self.graph_engine = RelationalGraphEngine(
            config=self.memory_system.config,
            db_manager=self.memory_system.db,
            llm_adapter=self.memory_system.backman.adapter,
        )
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
            entities: Optional[List[Dict]] = None,
        ) -> Dict:
            """感情タギングしてメモリをDBに保存する。

            emotions引数は全10軸の感情スコアをdictで渡す(0.0-1.0)。
            省略した場合、内部LLMで自動タギングする(ANTHROPIC_API_KEY必要)。
            APIキー未設定時はemotionsを明示的に渡すことを強く推奨。
            省略時はゼロベクターとなり検索精度が低下する。

            感情軸: joy, sadness, anger, fear, surprise, disgust, trust, anticipation, importance, urgency

            例(Claude CodeなどLLMクライアントから呼ぶ場合):
            emotions={"joy":0.8,"sadness":0.0,"anger":0.0,"fear":0.0,"surprise":0.2,"disgust":0.0,"trust":0.5,"anticipation":0.3,"importance":0.6,"urgency":0.1}
            scenes=["work","learning"]  # 最大3個

            entities引数でエンティティ情報を渡すと関係性グラフを構築する。
            省略した場合、内部LLMで自動抽出する(ANTHROPIC_API_KEY必要)。
            APIキー未設定時はentitiesを明示的に渡すことを強く推奨。

            例:
            entities=[
              {"label":"amygdala","type":"topic","aliases":["感情記憶システム"],"relations":[{"target":"SAFL","tags":["部品","コンポーネント"]}]},
              {"label":"SAFL","type":"topic","aliases":["Self-Awareness Functional Layer"]}
            ]
            type: person | topic | item | place | event
            """
            result = server.store_memory(text, context, emotions, scenes, entities)
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

        @self.mcp.tool()
        def query_entity_graph(
            entity: str,
            hops: int = 1,
        ) -> Dict:
            """エンティティの関係性グラフを検索する。

            指定エンティティに接続するノード・エッジ・タグを返す。
            hops=2で2ホップ先の関連エンティティも含める。

            Args:
                entity: エンティティ名（部分一致で検索）
                hops: 探索ホップ数（1 or 2、デフォルト1）
            """
            server._tick_pin_ttl()
            return server.query_entity_graph(entity, hops)

        @self.mcp.tool()
        def list_graph_entities(
            type_filter: str = "",
            top_n: int = 20,
        ) -> List:
            """グラフ上のアクティブなエンティティ一覧を返す。

            mention_count × importance 降順でソート。

            Args:
                type_filter: エンティティタイプでフィルタ（person/topic/item/place/event、空文字で全件）
                top_n: 返却上限数（デフォルト20）
            """
            server._tick_pin_ttl()
            return server.list_graph_entities(type_filter, top_n)

        @self.mcp.tool()
        def forget_entity(entity: str) -> Dict:
            """指定エンティティとその関連エッジをsoft-archiveする。

            エンティティ名で検索し、該当ノードとそれに接続する全エッジを
            archived=Trueにする。元に戻すことはできない。

            Args:
                entity: エンティティ名
            """
            server._tick_pin_ttl()
            return server.forget_entity(entity)

    def store_memory(
        self,
        text: str,
        context: Optional[str] = None,
        emotions_input: Optional[Any] = None,
        scenes_input: Optional[Any] = None,
        entities_input: Optional[Any] = None,
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

        # エンティティ入力のパース
        entities = None
        if entities_input:
            try:
                if isinstance(entities_input, str):
                    entities = json.loads(entities_input)
                elif isinstance(entities_input, list):
                    entities = entities_input
                else:
                    raise TypeError(f"Unsupported type: {type(entities_input)}")
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Invalid entities_input: {e}. Falling back to LLM extraction.")
                entities = None

        # グラフ更新（非致命的）
        try:
            self.graph_engine.process_turn(text, emotion, entities=entities)
        except Exception as e:
            logger.warning(f"Graph update on store_memory failed: {e}")

        result = {"memory_id": cursor.lastrowid}
        if ms.config.VERBOSE_TOOL_RESPONSE:
            result["emotion"] = dominant_emotion
            result["score"] = dominant_score
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
        グラフ1ホップ展開で関連メモリも候補に含める。

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

        # グラフ1ホップ展開（非致命的）
        try:
            graph_candidates = self._graph_augmented_candidates(
                query, results, emotion_vec
            )
            if graph_candidates:
                existing_ids = {r["id"] for r in results}
                for c in graph_candidates:
                    if c["id"] not in existing_ids:
                        results.append(c)
                        existing_ids.add(c["id"])
                results.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception as e:
            logger.warning(f"Graph-augmented recall failed: {e}")

        verbose = ms.config.VERBOSE_TOOL_RESPONSE
        output = []
        for m in results[:top_n]:
            entry = {
                "id": m["id"],
                "content": m["content"],
            }
            if verbose:
                entry["emotion"] = self._get_dominant_emotion(m, ms.config)
                entry["score"] = float(m.get("score", m.get("relevance_score", 0.0)))
            output.append(entry)
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

    def _graph_augmented_candidates(
        self,
        query: str,
        initial_results: List[Dict],
        emotion_vec: Dict[str, float],
    ) -> List[Dict]:
        """グラフ1ホップ展開で関連メモリ候補を取得する。

        1. クエリと初期結果からエンティティを特定
        2. 1ホップ先の関連エンティティを取得
        3. 関連エンティティに言及するメモリを検索・スコアリング
        """
        ms = self.memory_system
        conn = ms.db.get_connection()

        # Step 1: 既知エンティティをクエリ+初期結果テキストから特定
        nodes = conn.execute(
            "SELECT label, aliases FROM graph_nodes WHERE archived = FALSE"
        ).fetchall()

        texts = [query] + [r["content"] for r in initial_results[:5]]
        found_labels = set()

        for node in nodes:
            label = node["label"]
            aliases = json.loads(node["aliases"]) if node["aliases"] else []
            for name in [label] + aliases:
                if any(name in t for t in texts):
                    found_labels.add(label)
                    break

        if not found_labels:
            return []

        # Step 2: 1ホップ先の関連エンティティラベルを取得
        related_labels = set()
        for label in found_labels:
            ctx = self.graph_engine.get_entity_context(label, hops=1)
            if ctx:
                related_labels.update(ctx["related_entities"])

        expand_labels = related_labels - found_labels
        if not expand_labels:
            return []

        # Step 3: 関連エンティティに言及するメモリを検索
        initial_ids = {r["id"] for r in initial_results}
        conditions = " OR ".join(["content LIKE ?"] * len(expand_labels))
        params = [f"%{lbl}%" for lbl in expand_labels]

        rows = conn.execute(
            f"SELECT * FROM memories WHERE archived = FALSE AND ({conditions})",
            params
        ).fetchall()

        rows = [r for r in rows if r["id"] not in initial_ids]
        if not rows:
            return []

        # Step 4: 同じスコアリングロジックで評価
        return ms.search_engine.score_memory_rows(rows, emotion_vec, [])

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

    def query_entity_graph(self, entity: str, hops: int = 1) -> Dict:
        """エンティティの関係性コンテキストを返す"""
        hops = max(1, min(hops, 2))
        result = self.graph_engine.get_entity_context(entity, hops)
        if result is None:
            return {"error": f"Entity not found: {entity!r}"}
        return dict(result)

    def list_graph_entities(self, type_filter: str = "", top_n: int = 20) -> List[Dict]:
        """アクティブなエンティティ一覧を返す"""
        conn = self.memory_system.db.get_connection()
        if type_filter:
            rows = conn.execute(
                """SELECT * FROM graph_nodes
                   WHERE archived = FALSE AND type = ?
                   ORDER BY (mention_count * importance) DESC
                   LIMIT ?""",
                (type_filter, top_n)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM graph_nodes
                   WHERE archived = FALSE
                   ORDER BY (mention_count * importance) DESC
                   LIMIT ?""",
                (top_n,)
            ).fetchall()

        result = []
        for row in rows:
            aliases = json.loads(row["aliases"]) if row["aliases"] else []
            result.append({
                "id": row["id"],
                "label": row["label"],
                "type": row["type"],
                "aliases": aliases,
                "mention_count": row["mention_count"],
            })
        return result

    def forget_entity(self, entity: str) -> Dict:
        """エンティティとその関連エッジをsoft-archiveする"""
        node = self.graph_engine.find_node(entity)
        if not node:
            return {"error": f"Entity not found: {entity!r}"}

        conn = self.memory_system.db.get_connection()
        node_id = node["id"]

        # 関連エッジを archive
        cursor = conn.execute(
            """UPDATE graph_edges SET archived = TRUE
               WHERE (source_id = ? OR target_id = ?) AND archived = FALSE""",
            (node_id, node_id)
        )
        archived_edges = cursor.rowcount

        # ノードを archive
        conn.execute(
            "UPDATE graph_nodes SET archived = TRUE WHERE id = ?",
            (node_id,)
        )
        conn.commit()

        return {
            "forgotten_entity": node["label"],
            "archived_edges": archived_edges,
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
