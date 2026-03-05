import pytest
from src.config import Config
from src.db import DatabaseManager
from src.pin_memory import PinMemory


@pytest.fixture
def setup():
    db = DatabaseManager(":memory:")
    db.init()
    config = Config(PIN_MEMORY_SLOTS=3, PIN_TTL_TURNS=10)
    pm = PinMemory(config, db)
    return pm, config, db


def test_is_pin_request_true(setup):
    pm, config, db = setup
    assert pm.is_pin_request("これを覚えといて") is True


def test_is_pin_request_false(setup):
    pm, config, db = setup
    assert pm.is_pin_request("今日の天気は？") is False


def test_add_pin_success(setup):
    pm, config, db = setup
    result = pm.add_pin("重要な情報")
    assert result is True
    assert pm.slot_count() == 1


def test_add_pin_full(setup):
    pm, config, db = setup
    pm.add_pin("pin1")
    pm.add_pin("pin2")
    pm.add_pin("pin3")
    # slots full (PIN_MEMORY_SLOTS=3)
    result = pm.add_pin("pin4")
    assert result is False
    assert pm.slot_count() == 3


def test_get_active_pins(setup):
    pm, config, db = setup
    pm.add_pin("content_a", label="labelA")
    pins = pm.get_active_pins()
    assert len(pins) == 1
    assert pins[0]["content"] == "content_a"
    assert pins[0]["label"] == "labelA"


def test_slot_count_and_is_full(setup):
    pm, config, db = setup
    assert pm.slot_count() == 0
    assert not pm.is_full()
    pm.add_pin("x")
    pm.add_pin("y")
    pm.add_pin("z")
    assert pm.slot_count() == 3
    assert pm.is_full()


def test_decrement_ttl(setup):
    pm, config, db = setup
    # Add pin with TTL=1
    config2 = Config(PIN_MEMORY_SLOTS=3, PIN_TTL_TURNS=1)
    pm2 = PinMemory(config2, db)
    pm2.add_pin("expiring pin")
    expired = pm2.decrement_ttl()
    assert len(expired) == 1
    assert expired[0]["content"] == "expiring pin"


def test_release_pin(setup):
    pm, config, db = setup
    pm.add_pin("pin content")
    pins = pm.get_active_pins()
    pin_id = pins[0]["id"]

    memory_id = pm.release_pin(pin_id)
    assert isinstance(memory_id, int)

    # Verify in memories table
    conn = db.get_connection()
    mem = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    assert mem is not None
    assert mem["pinned_flag"] == 1
    assert mem["relevance_score"] == 2.0

    # Pin should be deactivated
    assert pm.slot_count() == 0


def test_renew_pin(setup):
    pm, config, db = setup
    pm.add_pin("renewable pin")
    pins = pm.get_active_pins()
    pin_id = pins[0]["id"]

    # Decrement TTL manually
    conn = db.get_connection()
    conn.execute("UPDATE pin_memories SET ttl_turns_remaining = 1 WHERE id = ?", (pin_id,))
    conn.commit()

    pm.renew_pin(pin_id)
    updated = conn.execute("SELECT ttl_turns_remaining FROM pin_memories WHERE id = ?", (pin_id,)).fetchone()
    assert updated["ttl_turns_remaining"] == config.PIN_TTL_TURNS


def test_generate_ttl_prompt(setup):
    pm, config, db = setup
    expired_pins = [{"content": "重要な件", "id": 1}]
    prompt = pm.generate_ttl_prompt(expired_pins)
    assert "📌" in prompt
    assert "重要な件" in prompt


def test_generate_ttl_prompt_empty(setup):
    pm, config, db = setup
    prompt = pm.generate_ttl_prompt([])
    assert prompt == ""
