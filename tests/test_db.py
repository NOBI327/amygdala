import sqlite3
import pytest
from src.db import DatabaseManager
from src.config import Config


@pytest.fixture
def db():
    manager = DatabaseManager(":memory:")
    manager.init()
    yield manager
    manager.close()


def test_init_creates_all_tables(db):
    conn = db.get_connection()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "working_memory" in tables
    assert "pin_memories" in tables
    assert "memories" in tables
    assert "recall_log" in tables


def test_init_creates_indexes(db):
    conn = db.get_connection()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_importance" in indexes
    assert "idx_timestamp" in indexes
    assert "idx_relevance" in indexes
    assert "idx_pinned" in indexes


def test_get_connection_row_factory(db):
    conn = db.get_connection()
    assert conn.row_factory == sqlite3.Row


def test_get_connection_singleton(db):
    conn1 = db.get_connection()
    conn2 = db.get_connection()
    assert conn1 is conn2


def test_memories_insert_select(db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO memories (content, joy, importance) VALUES (?, ?, ?)",
        ("test memory", 0.8, 0.9)
    )
    conn.commit()
    cursor = conn.execute("SELECT content, joy, importance FROM memories WHERE content=?", ("test memory",))
    row = cursor.fetchone()
    assert row is not None
    assert row["content"] == "test memory"
    assert abs(row["joy"] - 0.8) < 1e-6
    assert abs(row["importance"] - 0.9) < 1e-6


def test_working_memory_insert_select(db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO working_memory (turn_number, user_input, ai_response) VALUES (?, ?, ?)",
        (1, "hello", "hi there")
    )
    conn.commit()
    cursor = conn.execute("SELECT turn_number, user_input, ai_response FROM working_memory WHERE turn_number=1")
    row = cursor.fetchone()
    assert row is not None
    assert row["turn_number"] == 1
    assert row["user_input"] == "hello"
    assert row["ai_response"] == "hi there"


def test_pin_memories_insert_select(db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO pin_memories (content, label) VALUES (?, ?)",
        ("pinned content", "important")
    )
    conn.commit()
    cursor = conn.execute("SELECT content, label, active FROM pin_memories WHERE content=?", ("pinned content",))
    row = cursor.fetchone()
    assert row is not None
    assert row["content"] == "pinned content"
    assert row["label"] == "important"
    assert row["active"] == True


def test_recall_log_insert_select(db):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO memories (content) VALUES (?)", ("memory for recall",)
    )
    conn.commit()
    mem_id = conn.execute("SELECT id FROM memories WHERE content=?", ("memory for recall",)).fetchone()["id"]
    conn.execute(
        "INSERT INTO recall_log (memory_id, was_used, dominant_emotion) VALUES (?, ?, ?)",
        (mem_id, True, "joy")
    )
    conn.commit()
    cursor = conn.execute("SELECT memory_id, was_used, dominant_emotion FROM recall_log WHERE memory_id=?", (mem_id,))
    row = cursor.fetchone()
    assert row is not None
    assert row["memory_id"] == mem_id
    assert row["was_used"] == True
    assert row["dominant_emotion"] == "joy"


def test_init_idempotent():
    manager = DatabaseManager(":memory:")
    manager.init()
    manager.init()  # 2回目もエラーにならない
    conn = manager.get_connection()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "memories" in tables
    manager.close()


def test_context_manager():
    with DatabaseManager(":memory:") as db:
        conn = db.get_connection()
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "memories" in tables
    # close後は _conn が None になっている
    assert db._conn is None


def test_from_config():
    config = Config(DB_PATH=":memory:")
    manager = DatabaseManager.from_config(config)
    assert manager.db_path == ":memory:"
    manager.init()
    conn = manager.get_connection()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "memories" in tables
    manager.close()


def test_close_and_reconnect():
    manager = DatabaseManager(":memory:")
    manager.init()
    conn1 = manager.get_connection()
    manager.close()
    assert manager._conn is None
    # close後に再度get_connection()すると新しい接続が返る
    conn2 = manager.get_connection()
    assert conn2 is not None
    assert conn2 is not conn1
    manager.close()
