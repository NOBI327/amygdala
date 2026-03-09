import json
import logging
from typing import Any, Dict, List, Optional
from .config import Config
from .db import DatabaseManager
from .backman import BackmanService
from .frontman import FrontmanService
from .working_memory import WorkingMemory
from .pin_memory import PinMemory
from .search_engine import SearchEngine
from .reconsolidation import ConsolidationEngine
from .diversity_watchdog import DiversityWatchdog
from .relational_graph import RelationalGraphEngine

logger = logging.getLogger(__name__)


class MemorySystem:
    """
    簡易扁桃体模倣型LLMメモリ拡張システムのメインオーケストレーター。
    企画書§8のパイプライン全体を管理する。

    DIパターン: 全依存（llm_client, db_manager）をコンストラクタで注入。
    テスト時はモックで完全オフラインテスト可能。
    """

    def __init__(
        self,
        llm_client: Any,
        db_manager: DatabaseManager,
        config: Optional[Config] = None,
    ) -> None:
        self.config = config or Config()
        self.db = db_manager
        self.backman = BackmanService(llm_client, self.config)
        self.frontman = FrontmanService(llm_client, self.config)
        self.working_memory = WorkingMemory(self.config, db_manager)
        self.pin_memory = PinMemory(self.config, db_manager)
        self.search_engine = SearchEngine(self.config, db_manager)
        self.consolidation = ConsolidationEngine(self.config, db_manager)
        self.diversity_watchdog = DiversityWatchdog(self.config, db_manager)
        self.graph_engine = RelationalGraphEngine(
            config=self.config,
            db_manager=db_manager,
            llm_adapter=llm_client,
        )
        self.turn_history: List[Dict] = []

    def process_turn(self, user_input: str) -> str:
        """
        1ターンの会話を処理する。企画書§8のフローに準拠。

        処理順序:
        1. ピン登録要求の検出 → PinMemory.add_pin
        2. バックマンで現在入力の感情+場面を分析（tag_emotion）
        3. 長期記憶DBを検索（SearchEngine.search_memories）
        4. コンテキストプロンプトを組み立て（FrontmanService.build_context_prompt）
        5. フロントマンで応答生成
        6. ワーキングメモリに追加（WorkingMemory.add_turn）
        7. ワーキングメモリ溢れ処理:
           - overflow turnをbackman.generate_summary
           - 感情タグ付け（tag_emotion）
           - 長期記憶DBに保存（memoriesテーブル）
        8. ピンTTLデクリメント → TTL切れの場合は応答に確認プロンプトを付加
        9. 明示的記憶参照の検出 → search_engine.log_recall

        Returns:
            AI応答テキスト（ピンTTL確認が必要な場合は確認プロンプトを付加）
        """
        conn = self.db.get_connection()

        # 1. ピン登録要求の検出
        if self.pin_memory.is_pin_request(user_input):
            self.pin_memory.add_pin(user_input)

        # 2. 現在入力の感情+場面を分析
        try:
            tag_result = self.backman.tag_emotion(user_input)
            emotion_vec = tag_result.get("emotion", {})
            current_scenes = tag_result.get("scenes", [])
        except Exception as e:
            logger.warning(f"Emotion tagging failed: {e}. Using empty vectors.")
            emotion_vec = {ax: 0.0 for ax in list(self.config.EMOTION_AXES) + list(self.config.META_AXES)}
            current_scenes = []

        # 2.5. グラフ更新（非致命的）
        graph_result = {"updated": False}
        try:
            graph_result = self.graph_engine.process_turn(user_input, emotion_vec)
        except Exception as e:
            logger.warning(f"Graph processing failed: {e}")

        # 3. 長期記憶検索
        search_results = self.search_engine.search_memories(emotion_vec, current_scenes)

        # 3.5. DiversityWatchdogによる多様性注入
        search_results = self.diversity_watchdog.apply_exploration(search_results, emotion_vec)

        # 3.7. 関連エンティティのコンテキスト取得
        graph_contexts = []
        if graph_result.get("updated"):
            graph_contexts = self._get_relevant_graph_contexts(user_input, emotion_vec)

        # 4. コンテキストプロンプト組み立て
        working_mem = self.working_memory.get_turns()
        active_pins = self.pin_memory.get_active_pins()
        context_prompt = self.frontman.build_context_prompt(
            working_mem, active_pins, search_results, graph_contexts=graph_contexts
        )

        # 5. 応答生成
        response_text = self.frontman.generate_response(user_input, context_prompt)

        # 6. ワーキングメモリに追加
        overflowed = self.working_memory.add_turn(user_input, response_text)

        # 7. ワーキングメモリ溢れ処理 → 長期記憶へ移管
        for turn in overflowed:
            try:
                summary = self.backman.generate_summary([turn])
                tag = self.backman.tag_emotion(
                    f"{turn.get('user_input', '')} {turn.get('ai_response', '')}"
                )
                emotion = tag.get("emotion", {})
                scenes = tag.get("scenes", [])
                conn.execute(
                    """INSERT INTO memories
                       (content, raw_input, raw_response,
                        joy, sadness, anger, fear, surprise, disgust, trust, anticipation,
                        importance, urgency, scenes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        summary,
                        turn.get("user_input", ""),
                        turn.get("ai_response", ""),
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
                        json.dumps(scenes, ensure_ascii=False),
                    )
                )
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to transfer overflow turn to LTM: {e}")

        # 8. ピンTTLデクリメント
        expired_pins = self.pin_memory.decrement_ttl()
        if expired_pins:
            ttl_prompt = self.pin_memory.generate_ttl_prompt(expired_pins)
            response_text = response_text + "\n\n" + ttl_prompt

        # 9. 記憶参照検出とフィードバック適用
        if search_results:
            memory_ids = [m["id"] for m in search_results]
            dominant = max(
                {ax: emotion_vec.get(ax, 0.0) for ax in self.config.EMOTION_AXES}.items(),
                key=lambda x: x[1],
                default=("neutral", 0.0)
            )[0]
            scene_str = current_scenes[0] if current_scenes else ""

            # 9a. 明示的シグナル
            if self.backman.detect_explicit_memory_reference(user_input):
                self.search_engine.log_recall(memory_ids, True, dominant, scene_str)
                self.consolidation.apply_feedback(memory_ids, "positive")
            else:
                # 9b. 暗黙的シグナル（turn_historyがwindow(3)ターン以上の場合）
                if len(self.turn_history) >= 3:
                    recall_content = search_results[0].get("content", "")
                    feedback_type = self.backman.detect_implicit_feedback(
                        self.turn_history[-4:], recall_content
                    )
                    was_used = feedback_type == "positive"
                    self.search_engine.log_recall(memory_ids, was_used, dominant, scene_str)
                    self.consolidation.apply_feedback(memory_ids, feedback_type)

        # 9c. turn_historyへの追加（最新10件のみ保持）
        self.turn_history.append({"user_input": user_input, "ai_response": response_text})
        if len(self.turn_history) > 10:
            self.turn_history = self.turn_history[-10:]

        return response_text

    def _get_relevant_graph_contexts(self, text: str, emotion_vec: dict) -> list:
        """テキストに関連するグラフコンテキストを最大3件取得する。

        感情ベクトルで類似ノードを検索し、各ノードの EntityContext を返す。
        """
        try:
            nodes = self.graph_engine.search_by_emotion(emotion_vec, top_k=3)
            contexts = []
            for node in nodes:
                ctx = self.graph_engine.get_entity_context(node["label"])
                if ctx:
                    contexts.append(ctx)
            return contexts
        except Exception as e:
            logger.warning(f"Failed to get graph contexts: {e}")
            return []

    def close(self) -> None:
        """DB接続を閉じる"""
        self.db.close()

    def __enter__(self) -> "MemorySystem":
        return self

    def __exit__(self, *args) -> None:
        self.close()
