import pytest
from src.config import Config
from src.db import DatabaseManager
from src.working_memory import WorkingMemory


@pytest.fixture
def setup():
    db = DatabaseManager(":memory:")
    db.init()
    config = Config(WORKING_MEMORY_TURNS=3)
    wm = WorkingMemory(config, db)
    return wm, config, db


def test_add_turn(setup):
    wm, config, db = setup
    overflowed = wm.add_turn("hello", "hi")
    assert overflowed == []
    assert wm.count() == 1


def test_get_turns_order(setup):
    wm, config, db = setup
    wm.add_turn("first", "resp1")
    wm.add_turn("second", "resp2")
    turns = wm.get_turns()
    assert len(turns) == 2
    assert turns[0]["user_input"] == "first"
    assert turns[1]["user_input"] == "second"


def test_count(setup):
    wm, config, db = setup
    assert wm.count() == 0
    wm.add_turn("a", "b")
    assert wm.count() == 1
    wm.add_turn("c", "d")
    assert wm.count() == 2


def test_is_full(setup):
    wm, config, db = setup
    # WORKING_MEMORY_TURNS=3
    wm.add_turn("a", "1")
    wm.add_turn("b", "2")
    assert not wm.is_full()
    wm.add_turn("c", "3")
    assert wm.is_full()


def test_fifo_rolling(setup):
    wm, config, db = setup
    # Add 3 turns to fill
    wm.add_turn("a", "1")
    wm.add_turn("b", "2")
    wm.add_turn("c", "3")
    # 4th turn should overflow 1
    overflowed = wm.add_turn("d", "4")
    assert len(overflowed) == 1
    assert overflowed[0]["user_input"] == "a"


def test_count_not_exceed_limit_after_overflow(setup):
    wm, config, db = setup
    for i in range(5):
        wm.add_turn(f"user{i}", f"ai{i}")
    assert wm.count() <= config.WORKING_MEMORY_TURNS


def test_clear(setup):
    wm, config, db = setup
    wm.add_turn("a", "1")
    wm.add_turn("b", "2")
    wm.clear()
    assert wm.count() == 0


def test_config_turns_respected():
    db = DatabaseManager(":memory:")
    db.init()
    config = Config(WORKING_MEMORY_TURNS=5)
    wm = WorkingMemory(config, db)
    for i in range(5):
        wm.add_turn(f"u{i}", f"a{i}")
    assert wm.is_full()
    # 6th should overflow
    overflowed = wm.add_turn("extra", "extra_resp")
    assert len(overflowed) == 1
    assert wm.count() == 5
