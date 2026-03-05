import json
import logging
from datetime import datetime
from typing import List, Dict
from .config import Config
from .db import DatabaseManager

logger = logging.getLogger(__name__)

PIN_KEYWORDS = ["忘れないで", "覚えといて", "記憶して", "ピンして", "重要事項", "必ず覚えて", "覚えてて"]


class PinMemory:
    """
    ピンメモリ: ユーザーが明示的に指定した情報を最大Nスロットで管理。
    企画書§2.3準拠。

    DIパターン: DatabaseManagerをコンストラクタで注入。
    """

    def __init__(self, config: Config, db_manager: DatabaseManager) -> None:
        self.config = config
        self.db = db_manager

    def is_pin_request(self, user_input: str) -> bool:
        """ユーザー入力がピン登録を要求しているか（キーワードマッチ）"""
        return any(kw in user_input for kw in PIN_KEYWORDS)

    def add_pin(self, content: str, label: str = "") -> bool:
        """
        ピンを追加する。
        Returns:
            True: 追加成功
            False: スロット満杯（config.PIN_MEMORY_SLOTS上限）
        """
        if self.is_full():
            logger.warning("Pin memory is full")
            return False
        conn = self.db.get_connection()
        conn.execute(
            "INSERT INTO pin_memories (content, label, ttl_turns_remaining) VALUES (?, ?, ?)",
            (content, label, self.config.PIN_TTL_TURNS)
        )
        conn.commit()
        return True

    def get_active_pins(self) -> List[Dict]:
        """アクティブなピンを全て返す"""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM pin_memories WHERE active = TRUE ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def slot_count(self) -> int:
        """現在アクティブなピン数"""
        conn = self.db.get_connection()
        result = conn.execute(
            "SELECT COUNT(*) as cnt FROM pin_memories WHERE active = TRUE"
        ).fetchone()
        return result["cnt"] if result else 0

    def is_full(self) -> bool:
        """ピンスロットが満杯か"""
        return self.slot_count() >= self.config.PIN_MEMORY_SLOTS

    def decrement_ttl(self) -> List[Dict]:
        """
        全アクティブピンのTTLを1減算する。
        TTLが0以下になったピンを返す（ユーザーへの確認が必要）。
        """
        conn = self.db.get_connection()
        conn.execute(
            "UPDATE pin_memories SET ttl_turns_remaining = ttl_turns_remaining - 1 WHERE active = TRUE"
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM pin_memories WHERE active = TRUE AND ttl_turns_remaining <= 0"
        ).fetchall()
        return [dict(row) for row in rows]

    def release_pin(self, pin_id: int) -> int:
        """
        ピンを解除し、長期記憶（memoriesテーブル）へ移管する。
        移管時: pinned_flag=True, relevance_score=2.0

        Returns:
            長期記憶に挿入されたmemory_id
        """
        conn = self.db.get_connection()
        pin = conn.execute(
            "SELECT * FROM pin_memories WHERE id = ?", (pin_id,)
        ).fetchone()
        if not pin:
            raise ValueError(f"Pin {pin_id} not found")

        # 長期記憶に移管
        cursor = conn.execute(
            """INSERT INTO memories (content, pinned_flag, relevance_score, archived)
               VALUES (?, TRUE, 2.0, FALSE)""",
            (pin["content"],)
        )
        memory_id = cursor.lastrowid

        # ピンを非アクティブ化
        conn.execute(
            "UPDATE pin_memories SET active = FALSE WHERE id = ?", (pin_id,)
        )
        conn.commit()
        logger.info(f"Pin {pin_id} released to memory {memory_id}")
        return memory_id

    def renew_pin(self, pin_id: int) -> None:
        """ピンのTTLをconfig.PIN_TTL_TURNSにリセット"""
        conn = self.db.get_connection()
        conn.execute(
            "UPDATE pin_memories SET ttl_turns_remaining = ? WHERE id = ?",
            (self.config.PIN_TTL_TURNS, pin_id)
        )
        conn.commit()

    def generate_ttl_prompt(self, expired_pins: List[Dict]) -> str:
        """TTL切れピンに対するユーザーへの確認プロンプトを生成"""
        if not expired_pins:
            return ""
        prompts = []
        for pin in expired_pins:
            prompts.append(f"📌 ピンメモリの確認: 『{pin['content'][:30]}』はまだ必要ですか？")
        return "\n".join(prompts)
