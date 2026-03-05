import json
import logging
from typing import Any, List, Dict, Optional

from .config import Config

logger = logging.getLogger(__name__)

# B3: Few-shot examples（最低3件、混合感情・曖昧場面を含む）
TAGGING_FEW_SHOT_EXAMPLES = [
    # Example 1: 混合感情（喜びと不安）
    {
        "input": "新しいプロジェクトのリーダーに任命された。嬉しいけど、うまくやれるか不安。",
        "output": {
            "emotion": {
                "joy": 0.7, "sadness": 0.0, "anger": 0.0, "fear": 0.5,
                "surprise": 0.3, "disgust": 0.0, "trust": 0.3, "anticipation": 0.6,
                "importance": 0.8, "urgency": 0.3
            },
            "scenes": ["work"]
        }
    },
    # Example 2: 曖昧な場面（work/learningどちらか）
    {
        "input": "Pythonの非同期処理をやっと理解できた。明日の業務で使えそう。",
        "output": {
            "emotion": {
                "joy": 0.6, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
                "surprise": 0.2, "disgust": 0.0, "trust": 0.7, "anticipation": 0.5,
                "importance": 0.6, "urgency": 0.2
            },
            "scenes": ["learning", "work"]
        }
    },
    # Example 3: 強い感情（anger主体）
    {
        "input": "会議で自分の意見を全部否定された。何のために準備したのか。",
        "output": {
            "emotion": {
                "joy": 0.0, "sadness": 0.4, "anger": 0.8, "fear": 0.0,
                "surprise": 0.1, "disgust": 0.3, "trust": 0.0, "anticipation": 0.0,
                "importance": 0.7, "urgency": 0.0
            },
            "scenes": ["work", "relationship"]
        }
    }
]

# A2: 明示的フィードバックキーワード（MVPはキーワードマッチのみ）
MEMORY_REFERENCE_KEYWORDS = [
    "さっきの", "前に話した", "あの件", "覚えてる", "記憶", "以前", "前回", "さっき言ってた"
]


class BackmanService:
    """
    バックマン: 会話テキストから感情タグを生成する記憶管理エージェント。
    企画書§8のデュアルエージェント構造のバックマン側。

    DIパターン: llm_clientをコンストラクタで注入。
    テスト時はMagicMock()で完全オフラインテスト可能。

    A1: デフォルトはHaikuモデル（config.BACKMAN_MODEL）。切替可能な構造。
    B3: 感情タグ付けプロンプトに3件のFew-shot exampleを組み込む。
    """

    def __init__(self, llm_adapter: Any, config: Config) -> None:
        """
        Args:
            llm_adapter: LLMAdapterまたはMagicMock（DI）
                         duck typing: generate(prompt, system=None, model=None) -> str を持つオブジェクト。
            config: Config（BACKMAN_MODELを使用）
        """
        self.adapter = llm_adapter
        self.config = config

    def _build_tagging_prompt(self, text: str) -> str:
        """B3: Few-shot付きの感情タグ付けプロンプトを生成する"""
        few_shot_str = ""
        for i, ex in enumerate(TAGGING_FEW_SHOT_EXAMPLES, 1):
            few_shot_str += f"\n例{i}:\n入力: {ex['input']}\n出力: {json.dumps(ex['output'], ensure_ascii=False)}\n"

        return f"""以下の対話内容の感情と場面を分析し、JSONのみを出力せよ。他のテキストは含めるな。

感情軸（各0.0~1.0）: joy, sadness, anger, fear, surprise, disgust, trust, anticipation, importance, urgency
場面タグ（最大3個）: work, relationship, hobby, health, learning, daily, philosophy, meta

{few_shot_str}
対話内容:
{text}

JSON出力（以下の形式のみ）:
{{"emotion": {{"joy": 0.0, ...}}, "scenes": ["work"]}}"""

    def tag_emotion(self, text: str) -> Dict:
        """
        会話テキストから感情タグを生成する（LLM呼び出し）。

        Returns:
            {"emotion": {"joy": float, ..., "importance": float, "urgency": float},
              "scenes": [str, ...]}

        Raises:
            ValueError: LLMの出力がJSON解析不可能な場合
        """
        prompt = self._build_tagging_prompt(text)
        try:
            raw = self.adapter.generate(
                prompt=prompt,
                model=self.config.BACKMAN_MODEL
            ).strip()
            result = json.loads(raw)
            # バリデーション: 全10軸の存在確認
            emotion = result.get("emotion", {})
            all_axes = list(self.config.EMOTION_AXES) + list(self.config.META_AXES)
            for ax in all_axes:
                if ax not in emotion:
                    emotion[ax] = 0.0
            result["emotion"] = emotion
            # 場面タグ上限3件
            result["scenes"] = result.get("scenes", [])[:3]
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse backman response: {e}")
            raise ValueError(f"Backman returned invalid JSON: {e}")

    def generate_summary(self, turns: List[Dict]) -> str:
        """
        ワーキングメモリのターン群から長期記憶用サマリを生成する。

        Args:
            turns: [{"user_input": str, "ai_response": str, "timestamp": str}]

        Returns:
            サマリテキスト（200-300文字程度）
        """
        if not turns:
            return ""
        conv_text = ""
        for t in turns:
            conv_text += f"User: {t.get('user_input', '')}\nAI: {t.get('ai_response', '')}\n"

        prompt = f"""以下の会話を200〜300文字程度で要約せよ。感情的な要点と重要な事実を含めること。

{conv_text}

要約:"""
        try:
            return self.adapter.generate(
                prompt=prompt,
                model=self.config.BACKMAN_MODEL
            ).strip()
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            raise

    def detect_explicit_memory_reference(self, user_input: str) -> bool:
        """
        ユーザー入力に明示的な記憶への言及があるか検出する。
        A2: MVP段階での明示的フィードバック判定（キーワードマッチのみ）。

        # PHASE2_EXTENSION: Phase 2実装: detect_implicit_feedback() を参照。
        """
        return any(kw in user_input for kw in MEMORY_REFERENCE_KEYWORDS)

    def detect_implicit_feedback(
        self,
        turn_history: List[Dict],
        recall_content: str,
        window: int = 3,
    ) -> str:
        """
        recall後の会話が同一主題で継続しているか判定する（企画書§5.3 段階2）。

        Args:
            turn_history: 最近の会話ターン [{"user_input": str, "ai_response": str}]
            recall_content: recall注入された記憶のcontentテキスト
            window: 判定に使用するターン数（デフォルト3）

        Returns:
            "positive" | "neutral" | "negative"
        """
        if not turn_history:
            return "neutral"

        recent_turns = turn_history[-window:]
        recall_words = set(recall_content.split())

        positive_count = 0
        negative_count = 0

        for turn in recent_turns:
            turn_words = set(turn.get("user_input", "").split())
            denom = max(len(recall_words), len(turn_words))
            if denom == 0:
                overlap = 0.0
            else:
                overlap = len(recall_words & turn_words) / denom

            if overlap >= 0.2:
                positive_count += 1
            elif overlap < 0.05:
                negative_count += 1

        n = len(recent_turns)
        if positive_count > n / 2:
            return "positive"
        if negative_count > n / 2:
            return "negative"
        return "neutral"
