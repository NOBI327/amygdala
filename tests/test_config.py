import os
import pytest
from src.config import Config


def test_default_values():
    """Config()でデフォルト値が正しく設定されること"""
    cfg = Config()
    assert cfg.DB_PATH == "memory.db"
    assert cfg.WORKING_MEMORY_TURNS == 10
    assert cfg.PIN_MEMORY_SLOTS == 3
    assert cfg.PIN_TTL_TURNS == 10
    assert cfg.BACKMAN_MODEL == "claude-haiku-4-5-20251001"
    assert cfg.FRONTMAN_MODEL == "claude-haiku-4-5-20251001"
    assert cfg.TOP_K_RESULTS == 5
    assert cfg.EMOTION_WEIGHT == 0.4
    assert cfg.SCENE_WEIGHT == 0.35
    assert cfg.META_WEIGHT == 0.25
    assert cfg.HALF_LIFE_NORMAL == 30
    assert cfg.HALF_LIFE_PINNED == 60
    assert cfg.HALF_LIFE_FREQUENT == 45
    assert cfg.COLD_START_BOOST == 1.5
    assert cfg.COLD_START_THRESHOLD == 50


def test_overridable():
    """Config(WORKING_MEMORY_TURNS=5)で上書き可能なこと（A3確認）"""
    cfg = Config(WORKING_MEMORY_TURNS=5)
    assert cfg.WORKING_MEMORY_TURNS == 5
    # 他のデフォルト値は変わらない
    assert cfg.PIN_MEMORY_SLOTS == 3


def test_from_env():
    """Config.from_env()でEMS_BACKMAN_MODEL環境変数が反映されること（A1確認）"""
    os.environ["EMS_BACKMAN_MODEL"] = "claude-opus-4-6"
    os.environ["EMS_DB_PATH"] = "/tmp/test.db"
    try:
        cfg = Config.from_env()
        assert cfg.BACKMAN_MODEL == "claude-opus-4-6"
        assert cfg.DB_PATH == "/tmp/test.db"
        # 未設定の環境変数はデフォルト値
        assert cfg.FRONTMAN_MODEL == "claude-haiku-4-5-20251001"
    finally:
        del os.environ["EMS_BACKMAN_MODEL"]
        del os.environ["EMS_DB_PATH"]


def test_default_verbose_true():
    """VERBOSE_TOOL_RESPONSE はデフォルトで True"""
    cfg = Config()
    assert cfg.VERBOSE_TOOL_RESPONSE is True


def test_from_env_verbose_off():
    """EMS_VERBOSE=false で VERBOSE_TOOL_RESPONSE=False になる"""
    os.environ["EMS_VERBOSE"] = "false"
    try:
        cfg = Config.from_env()
        assert cfg.VERBOSE_TOOL_RESPONSE is False
    finally:
        del os.environ["EMS_VERBOSE"]


def test_from_env_verbose_default_true():
    """EMS_VERBOSE 未設定ならデフォルト True"""
    os.environ.pop("EMS_VERBOSE", None)
    cfg = Config.from_env()
    assert cfg.VERBOSE_TOOL_RESPONSE is True


def test_emotion_axes():
    """EMOTION_AXESに8軸が含まれること"""
    cfg = Config()
    assert len(cfg.EMOTION_AXES) == 8
    for axis in ("joy", "sadness", "anger", "fear", "surprise", "disgust", "trust", "anticipation"):
        assert axis in cfg.EMOTION_AXES


def test_meta_axes():
    """META_AXESに2軸（importance, urgency）が含まれること"""
    cfg = Config()
    assert len(cfg.META_AXES) == 2
    assert "importance" in cfg.META_AXES
    assert "urgency" in cfg.META_AXES
