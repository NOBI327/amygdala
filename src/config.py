from dataclasses import dataclass, field
import os
from typing import Tuple


@dataclass
class Config:
    # DB設定
    DB_PATH: str = "memory.db"

    # ワーキングメモリ（A3: ハードコード禁止、設定値として外出し）
    WORKING_MEMORY_TURNS: int = 10
    PIN_MEMORY_SLOTS: int = 3
    PIN_TTL_TURNS: int = 10

    # モデル設定（A1: Haiku前提、切替可能構造）
    BACKMAN_MODEL: str = "claude-haiku-4-5-20251001"
    FRONTMAN_MODEL: str = "claude-haiku-4-5-20251001"

    # トークンコスト見積もり（A1: config内明記）
    BACKMAN_ESTIMATED_TOKENS_PER_CALL: int = 500
    BACKMAN_TOKEN_NOTE: str = "入力~350 + 出力~150トークン想定（Haiku使用時）"

    # 感情軸定義（B1: 8基本感情とメタ2軸を分離）
    EMOTION_AXES: Tuple[str, ...] = (
        "joy", "sadness", "anger", "fear",
        "surprise", "disgust", "trust", "anticipation"
    )
    META_AXES: Tuple[str, ...] = ("importance", "urgency")

    # 場面タグ
    SCENE_TAGS: Tuple[str, ...] = (
        "work", "relationship", "hobby", "health",
        "learning", "daily", "philosophy", "meta"
    )

    # 検索設定
    TOP_K_RESULTS: int = 5

    # 重み設定（検索スコア計算）
    EMOTION_WEIGHT: float = 0.4
    SCENE_WEIGHT: float = 0.35
    META_WEIGHT: float = 0.25

    # 時間減衰（半減期: 日数）
    HALF_LIFE_NORMAL: int = 30
    HALF_LIFE_PINNED: int = 60
    HALF_LIFE_FREQUENT: int = 45  # recall_count > 5

    # コールドスタート
    COLD_START_BOOST: float = 1.5
    COLD_START_THRESHOLD: int = 50

    @classmethod
    def from_env(cls) -> "Config":
        """環境変数でモデル切替可能（A1要件）"""
        return cls(
            DB_PATH=os.environ.get("EMS_DB_PATH", "memory.db"),
            BACKMAN_MODEL=os.environ.get("EMS_BACKMAN_MODEL", "claude-haiku-4-5-20251001"),
            FRONTMAN_MODEL=os.environ.get("EMS_FRONTMAN_MODEL", "claude-haiku-4-5-20251001"),
        )
