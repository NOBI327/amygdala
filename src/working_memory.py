import logging
from datetime import datetime
from typing import List, Dict
from .config import Config
from .db import DatabaseManager

logger = logging.getLogger(__name__)


class WorkingMemory:
    """
    ワーキングメモリ: 最近N턴の会話を原文保持するFIFOバッファ。
    企画書§2.1準拠。永続化にSQLiteのworking_memoryテーブルを使用。

    DIパターン: DatabaseManagerをコンストラクタで注入。
    テスト時はDatabaseManager(":memory:")で完全オフライン。
    """

    def __init__(self, config: Config, db_manager: DatabaseManager) -> None:
        self.config = config
        self.db = db_manager

    def add_turn(self, user_input: str, ai_response: str) -> List[Dict]:
        """
        新しいターンを追加する。
        容量超過時はFIFOで古いターンをpopして返す（長期記憶移管候補）。

        Returns:
            溢れ出たターンのリスト（通常0か1件）。
            形式: [{"id": int, "turn_number": int, "user_input": str,
                    "ai_response": str, "timestamp": str}]
        """
        conn = self.db.get_connection()
        overflowed = []

        # FIFOチェック: 上限に達したら最古を削除
        while self.count() >= self.config.WORKING_MEMORY_TURNS:
            oldest = conn.execute(
                "SELECT * FROM working_memory ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if oldest:
                overflowed.append(dict(oldest))
                conn.execute("DELETE FROM working_memory WHERE id = ?", (oldest["id"],))

        # 新ターン追加
        turn_number = (self.count() or 0) + 1
        conn.execute(
            "INSERT INTO working_memory (turn_number, user_input, ai_response) VALUES (?, ?, ?)",
            (turn_number, user_input, ai_response)
        )
        conn.commit()
        logger.debug(f"Added turn {turn_number}. Overflowed: {len(overflowed)}")
        return overflowed

    def get_turns(self) -> List[Dict]:
        """現在のワーキングメモリ内の全ターンを時系列順（古→新）で返す"""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM working_memory ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        """現在のターン数を返す"""
        conn = self.db.get_connection()
        result = conn.execute("SELECT COUNT(*) as cnt FROM working_memory").fetchone()
        return result["cnt"] if result else 0

    def is_full(self) -> bool:
        """ターン数がConfig.WORKING_MEMORY_TURNSに達しているか"""
        return self.count() >= self.config.WORKING_MEMORY_TURNS

    def clear(self) -> None:
        """全ターンをクリアする（テスト・セッションリセット用）"""
        conn = self.db.get_connection()
        conn.execute("DELETE FROM working_memory")
        conn.commit()
