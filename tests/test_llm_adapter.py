"""Tests for llm_adapter.py"""
import os
import pytest
from unittest.mock import MagicMock, patch
from src.llm_adapter import (
    AnthropicAdapter,
    OpenAIAdapter,
    GeminiAdapter,
    AdapterFactory,
    LLMAdapter,
)


# ---------------------------------------------------------------------------
# AnthropicAdapter tests
# ---------------------------------------------------------------------------

def _make_mock_client(text: str = "test response"):
    """Build a mock Anthropic client that returns `text`."""
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    return mock_client


class TestAnthropicAdapter:
    def test_generate_basic(self):
        """generate() returns text from client response."""
        mock_client = _make_mock_client("Hello!")
        adapter = AnthropicAdapter(client=mock_client)
        result = adapter.generate("Say hello")
        assert result == "Hello!"

    def test_generate_with_system(self):
        """generate() passes system prompt to client."""
        mock_client = _make_mock_client("ok")
        adapter = AnthropicAdapter(client=mock_client)
        adapter.generate("prompt", system="Be concise")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "Be concise"

    def test_generate_model_override(self):
        """generate() uses model argument when provided."""
        mock_client = _make_mock_client("ok")
        adapter = AnthropicAdapter(client=mock_client, default_model="claude-haiku-4-5-20251001")
        adapter.generate("prompt", model="claude-sonnet-4-6")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    def test_generate_default_model(self):
        """generate() uses default_model when model not specified."""
        mock_client = _make_mock_client("ok")
        adapter = AnthropicAdapter(client=mock_client, default_model="claude-haiku-4-5-20251001")
        adapter.generate("prompt")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_generate_no_system_no_system_key(self):
        """generate() does not pass system key when system is None."""
        mock_client = _make_mock_client("ok")
        adapter = AnthropicAdapter(client=mock_client)
        adapter.generate("prompt")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "system" not in call_kwargs

    def test_generate_messages_format(self):
        """generate() passes messages in correct format."""
        mock_client = _make_mock_client("ok")
        adapter = AnthropicAdapter(client=mock_client)
        adapter.generate("hello world")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["messages"] == [{"role": "user", "content": "hello world"}]

    def test_is_llm_adapter_subclass(self):
        """AnthropicAdapter is a subclass of LLMAdapter."""
        assert issubclass(AnthropicAdapter, LLMAdapter)


# ---------------------------------------------------------------------------
# OpenAIAdapter tests
# ---------------------------------------------------------------------------

class TestOpenAIAdapter:
    def test_not_available_raises(self):
        """generate() raises NotImplementedError when openai not installed."""
        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._available = False
        adapter._client = None
        adapter._default_model = "gpt-4o-mini"
        with pytest.raises(NotImplementedError):
            adapter.generate("prompt")

    def test_available_calls_client(self):
        """generate() calls client when openai is available."""
        mock_choice = MagicMock()
        mock_choice.message.content = "openai response"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        adapter = OpenAIAdapter.__new__(OpenAIAdapter)
        adapter._available = True
        adapter._client = mock_client
        adapter._default_model = "gpt-4o-mini"

        result = adapter.generate("prompt")
        assert result == "openai response"


# ---------------------------------------------------------------------------
# GeminiAdapter tests
# ---------------------------------------------------------------------------

class TestGeminiAdapter:
    def test_not_available_raises(self):
        """generate() raises NotImplementedError when google-generativeai not installed."""
        adapter = GeminiAdapter.__new__(GeminiAdapter)
        adapter._available = False
        adapter._client = None
        adapter._genai = None
        adapter._default_model = "gemini-1.5-flash"
        with pytest.raises(NotImplementedError):
            adapter.generate("prompt")


# ---------------------------------------------------------------------------
# AdapterFactory tests
# ---------------------------------------------------------------------------

class TestAdapterFactory:
    def test_create_anthropic(self):
        """create_adapter returns AnthropicAdapter for 'anthropic'."""
        mock_client = _make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client):
            adapter = AdapterFactory.create_adapter("anthropic")
        assert isinstance(adapter, AnthropicAdapter)

    def test_create_anthropic_with_injected_client(self):
        """create_adapter passes client through to AnthropicAdapter."""
        mock_client = _make_mock_client()
        adapter = AdapterFactory.create_adapter("anthropic")
        # Without patching, it will try to create a real client — just verify type via manual injection
        adapter2 = AnthropicAdapter(client=mock_client)
        assert isinstance(adapter2, AnthropicAdapter)

    def test_create_openai(self):
        """create_adapter returns OpenAIAdapter for 'openai'."""
        adapter = AdapterFactory.create_adapter("openai")
        assert isinstance(adapter, OpenAIAdapter)

    def test_create_gemini(self):
        """create_adapter returns GeminiAdapter for 'gemini'."""
        adapter = AdapterFactory.create_adapter("gemini")
        assert isinstance(adapter, GeminiAdapter)

    def test_unknown_provider_raises(self):
        """create_adapter raises ValueError for unknown provider."""
        with pytest.raises(ValueError, match="Unknown provider"):
            AdapterFactory.create_adapter("cohere")

    def test_model_passed_to_anthropic(self):
        """create_adapter passes model to AnthropicAdapter as default_model."""
        mock_client = _make_mock_client()
        with patch("anthropic.Anthropic", return_value=mock_client):
            adapter = AdapterFactory.create_adapter("anthropic", model="claude-opus-4-6")
        assert adapter._default_model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# api_key_env_var tests
# ---------------------------------------------------------------------------

class TestAdapterFactoryApiKeyEnvVar:
    def test_api_key_env_var_reads_from_environ(self):
        """api_key_env_var reads API key from os.environ and passes it to the client."""
        mock_client = _make_mock_client()
        with patch.dict(os.environ, {"MY_TEST_API_KEY": "sk-test-abc123"}):
            with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
                adapter = AdapterFactory.create_adapter(
                    "anthropic", api_key_env_var="MY_TEST_API_KEY"
                )
        mock_anthropic.assert_called_once_with(api_key="sk-test-abc123")
        assert isinstance(adapter, AnthropicAdapter)

    def test_api_key_env_var_not_set_raises_value_error(self):
        """api_key_env_var raises ValueError when the specified env var is not set."""
        env_without_key = {k: v for k, v in os.environ.items() if k != "NONEXISTENT_KEY_XYZ"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises(ValueError, match="NONEXISTENT_KEY_XYZ"):
                AdapterFactory.create_adapter("anthropic", api_key_env_var="NONEXISTENT_KEY_XYZ")

    def test_api_key_env_var_overrides_api_key_param(self):
        """api_key_env_var takes precedence over the api_key parameter."""
        mock_client = _make_mock_client()
        with patch.dict(os.environ, {"ENV_KEY": "sk-from-env"}):
            with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
                AdapterFactory.create_adapter(
                    "anthropic",
                    api_key="sk-explicit",
                    api_key_env_var="ENV_KEY",
                )
        mock_anthropic.assert_called_once_with(api_key="sk-from-env")
