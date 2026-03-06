"""LLM Adapter - Multi-provider abstraction for LLM calls."""
import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMAdapter(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        """Generate a response from the LLM.

        Args:
            prompt: User prompt text.
            system: Optional system prompt.
            model: Optional model override.

        Returns:
            Generated text response.
        """


class AnthropicAdapter(LLMAdapter):
    """Adapter for Anthropic Claude models."""

    def __init__(self, client=None, default_model: str = "claude-haiku-4-5-20251001"):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self._client = client
        self._default_model = default_model

    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        resolved_model = model or self._default_model
        kwargs = {
            "model": resolved_model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        return response.content[0].text


class OpenAIAdapter(LLMAdapter):
    """Adapter for OpenAI models (stub if openai not installed)."""

    def __init__(self, client=None, default_model: str = "gpt-4o-mini"):
        self._default_model = default_model
        try:
            import openai
            self._client = client or openai.OpenAI()
            self._available = True
        except ImportError:
            self._client = client
            self._available = False

    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        if not self._available:
            raise NotImplementedError("openai package is not installed.")
        resolved_model = model or self._default_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=resolved_model,
            messages=messages,
        )
        return response.choices[0].message.content


class GeminiAdapter(LLMAdapter):
    """Adapter for Google Gemini models (stub if google-generativeai not installed)."""

    def __init__(self, client=None, default_model: str = "gemini-1.5-flash"):
        self._default_model = default_model
        try:
            import google.generativeai as genai
            self._genai = genai
            self._client = client
            self._available = True
        except ImportError:
            self._genai = None
            self._client = client
            self._available = False

    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        if not self._available:
            raise NotImplementedError("google-generativeai package is not installed.")
        resolved_model = model or self._default_model
        gemini_model = self._client or self._genai.GenerativeModel(resolved_model)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        response = gemini_model.generate_content(full_prompt)
        return response.text


class AdapterFactory:
    """Factory for creating LLM adapters."""

    @staticmethod
    def create_adapter(
        provider: str,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env_var: Optional[str] = None,
    ) -> LLMAdapter:
        """Create an LLM adapter for the given provider.

        Args:
            provider: One of "anthropic", "openai", "gemini".
            model: Optional model name override.
            api_key: Optional API key (passed to client constructor if provided).
            api_key_env_var: Optional environment variable name to read the API key from.
                If set, overrides api_key. Raises ValueError if the env var is not set.

        Returns:
            An LLMAdapter instance.

        Raises:
            ValueError: If provider is not recognized, or if api_key_env_var is specified
                but the environment variable is not set.
        """
        if api_key_env_var:
            resolved_key = os.environ.get(api_key_env_var)
            if resolved_key is None:
                raise ValueError(
                    f"Environment variable {api_key_env_var!r} is not set. "
                    "Set it before using api_key_env_var."
                )
            api_key = resolved_key

        if provider == "anthropic":
            kwargs = {}
            if model:
                kwargs["default_model"] = model
            if api_key:
                import anthropic
                kwargs["client"] = anthropic.Anthropic(api_key=api_key)
            return AnthropicAdapter(**kwargs)
        elif provider == "openai":
            kwargs = {}
            if model:
                kwargs["default_model"] = model
            if api_key:
                import openai
                kwargs["client"] = openai.OpenAI(api_key=api_key)
            return OpenAIAdapter(**kwargs)
        elif provider == "gemini":
            kwargs = {}
            if model:
                kwargs["default_model"] = model
            return GeminiAdapter(**kwargs)
        else:
            raise ValueError(f"Unknown provider: {provider!r}. Must be one of 'anthropic', 'openai', 'gemini'.")
