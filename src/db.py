import sqlite3
import logging
from pathlib import Path
from typing import Optional
from .config import Config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    SQLiteデータベース管理クラス。
    DIパターン: db_pathをコンストラクタで注入。
    テスト時は DatabaseManager(":memory:") で完全オフラインテスト可能。
    """

    def __init__(self, db_path: str) -> None:
        """
        Args:
            db_path: SQLiteファイルパス。":memory:"でインメモリDB。
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def init(self) -> None:
        """
        DB接続を開き、全テーブルを作成する（べき等性保証）。
        必ず最初に呼ぶこと。
        """
        conn = self.get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS working_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_number INTEGER NOT NULL,
                user_input TEXT NOT NULL,
                ai_response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS pin_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                label TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ttl_turns_remaining INTEGER DEFAULT 10,
                active BOOLEAN DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                raw_input TEXT,
                raw_response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                joy REAL DEFAULT 0.0,
                sadness REAL DEFAULT 0.0,
                anger REAL DEFAULT 0.0,
                fear REAL DEFAULT 0.0,
                surprise REAL DEFAULT 0.0,
                disgust REAL DEFAULT 0.0,
                trust REAL DEFAULT 0.0,
                anticipation REAL DEFAULT 0.0,
                importance REAL DEFAULT 0.0,
                urgency REAL DEFAULT 0.0,
                scenes TEXT DEFAULT '[]',
                relevance_score REAL DEFAULT 1.0,
                recall_count INTEGER DEFAULT 0,
                last_recalled DATETIME,
                pinned_flag BOOLEAN DEFAULT FALSE,
                archived BOOLEAN DEFAULT FALSE
            );

            CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance DESC);
            CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_relevance ON memories(relevance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_pinned ON memories(pinned_flag);

            CREATE TABLE IF NOT EXISTS recall_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER REFERENCES memories(id),
                recalled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                was_used BOOLEAN DEFAULT FALSE,
                dominant_emotion TEXT,
                context_scene TEXT
            );
        """)
        conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def get_connection(self) -> sqlite3.Connection:
        """
        SQLite接続を返す（row_factory=sqlite3.Row設定済み）。
        接続は使い回す（シングルトン接続）。
        """
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        """接続を閉じる"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "DatabaseManager":
        self.init()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @classmethod
    def from_config(cls, config: Config) -> "DatabaseManager":
        """Config.DB_PATHからDatabaseManagerを生成するファクトリメソッド"""
        return cls(config.DB_PATH)
