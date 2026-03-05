import logging
from typing import Any, List, Dict
from .config import Config

logger = logging.getLogger(__name__)


class FrontmanService:
    """
    フロントマン: ユーザーと直接対話する応答生成エージェント。
    企画書§8のデュアルエージェント構造のフロントマン側。

    DIパターン: llm_clientをコンストラクタで注入。
    テスト時はMagicMock()で完全オフラインテスト可能。
    """

    def __init__(self, llm_client: Any, config: Config) -> None:
        """
        Args:
            llm_client: Anthropicクライアントまたはモック
            config: Config（FRONTMAN_MODELを使用）
        """
        self.client = llm_client
        self.config = config

    def build_context_prompt(
        self,
        working_memory: List[Dict],
        pin_memories: List[Dict],
        search_results: List[Dict],
    ) -> str:
        """
        バックマンが組み立てたコンテキストからシステムプロンプトを生成する。

        プロンプト構造（コンテキスト汚染防止原則）:
        1. [📌 ピンメモリ] 最大3件（常時含める）
        2. [🔍 関連記憶] 検索結果上位N件（感情ベース）
        3. [💬 最近の会話] ワーキングメモリ（直近Nターン）
        """
        sections = []

        if pin_memories:
            pin_section = "📌 **ピンメモリ（必ず参照すること）**:\n"
            for pin in pin_memories:
                pin_section += f"- {pin['content']}\n"
            sections.append(pin_section)

        if search_results:
            search_section = "🔍 **関連する過去の記憶（感情ベース検索）**:\n"
            for mem in search_results[:3]:  # 最大3件
                score = mem.get("score", 0.0)
                search_section += f"- [score:{score:.2f}] {mem['content']}\n"
            sections.append(search_section)

        if working_memory:
            wm_section = "💬 **最近の会話**:\n"
            for turn in working_memory[-5:]:  # 最新5ターン
                wm_section += f"User: {turn['user_input']}\nAI: {turn.get('ai_response', '')}\n"
            sections.append(wm_section)

        if not sections:
            return "あなたはユーザーと対話するAIアシスタントです。"

        return "あなたはユーザーと対話するAIアシスタントです。以下のコンテキストを参照してください:\n\n" + \
               "\n---\n".join(sections)

    def generate_response(self, user_input: str, context_prompt: str) -> str:
        """
        コンテキスト付きでフロントマンの応答を生成する。

        Args:
            user_input: ユーザーの入力テキスト
            context_prompt: build_context_prompt()で生成したシステムプロンプト

        Returns:
            AI応答テキスト
        """
        response = self.client.messages.create(
            model=self.config.FRONTMAN_MODEL,
            max_tokens=1000,
            system=context_prompt,
            messages=[{"role": "user", "content": user_input}]
        )
        return response.content[0].text.strip()
