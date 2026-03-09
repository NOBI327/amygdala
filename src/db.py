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

            -- 関係性グラフ: ノード（エンティティ）
            CREATE TABLE IF NOT EXISTS graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('person','topic','item','place','event')),
                aliases TEXT DEFAULT '[]',
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                mention_count INTEGER DEFAULT 1,
                joy REAL DEFAULT 0, sadness REAL DEFAULT 0,
                anger REAL DEFAULT 0, fear REAL DEFAULT 0,
                surprise REAL DEFAULT 0, disgust REAL DEFAULT 0,
                trust REAL DEFAULT 0, anticipation REAL DEFAULT 0,
                importance REAL DEFAULT 0, urgency REAL DEFAULT 0,
                archived BOOLEAN DEFAULT FALSE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_node_label ON graph_nodes(label);

            -- 関係性グラフ: エッジ（ノード間の関係性）
            CREATE TABLE IF NOT EXISTS graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES graph_nodes(id),
                target_id INTEGER NOT NULL REFERENCES graph_nodes(id),
                strength REAL DEFAULT 1.0,
                confidence REAL DEFAULT 0.5,
                last_activated DATETIME DEFAULT CURRENT_TIMESTAMP,
                activation_count INTEGER DEFAULT 1,
                joy REAL DEFAULT 0, sadness REAL DEFAULT 0,
                anger REAL DEFAULT 0, fear REAL DEFAULT 0,
                surprise REAL DEFAULT 0, disgust REAL DEFAULT 0,
                trust REAL DEFAULT 0, anticipation REAL DEFAULT 0,
                importance REAL DEFAULT 0, urgency REAL DEFAULT 0,
                archived BOOLEAN DEFAULT FALSE,
                UNIQUE(source_id, target_id)
            );

            -- 関係性グラフ: タグ（エッジ上の関係性ラベル）
            CREATE TABLE IF NOT EXISTS graph_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_id INTEGER NOT NULL REFERENCES graph_edges(id),
                label TEXT NOT NULL,
                strength REAL DEFAULT 0.5,
                activation_count INTEGER DEFAULT 1,
                decay_rate REAL DEFAULT 0.05,
                confirmed BOOLEAN DEFAULT FALSE,
                created DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activated DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(edge_id, label)
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
