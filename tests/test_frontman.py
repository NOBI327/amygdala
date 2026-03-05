import pytest
from unittest.mock import MagicMock
from src.config import Config
from src.frontman import FrontmanService


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def mock_client():
    client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="テスト応答です。")]
    client.messages.create.return_value = mock_response
    return client


@pytest.fixture
def frontman(mock_client, config):
    return FrontmanService(mock_client, config)


class TestBuildContextPrompt:
    def test_pin_memories_included(self, frontman):
        pin_memories = [{"content": "誕生日は3月15日"}]
        result = frontman.build_context_prompt([], pin_memories, [])
        assert "ピンメモリ" in result
        assert "誕生日は3月15日" in result

    def test_search_results_included(self, frontman):
        search_results = [{"content": "以前の会話内容", "score": 0.85}]
        result = frontman.build_context_prompt([], [], search_results)
        assert "関連する過去の記憶" in result
        assert "以前の会話内容" in result
        assert "0.85" in result

    def test_working_memory_included(self, frontman):
        working_memory = [{"user_input": "こんにちは", "ai_response": "はい、こんにちは"}]
        result = frontman.build_context_prompt(working_memory, [], [])
        assert "最近の会話" in result
        assert "こんにちは" in result

    def test_all_empty_returns_default(self, frontman):
        result = frontman.build_context_prompt([], [], [])
        assert isinstance(result, str)
        assert len(result) > 0
        assert "AIアシスタント" in result

    def test_generate_response_returns_string(self, frontman):
        result = frontman.generate_response("テスト入力", "システムプロンプト")
        assert isinstance(result, str)
        assert result == "テスト応答です。"

    def test_generate_response_context_prompt_as_system(self, mock_client, frontman):
        context_prompt = "カスタムシステムプロンプト"
        frontman.generate_response("ユーザー入力", context_prompt)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == context_prompt
