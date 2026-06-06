from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: LLMClient | None = None


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.model = model
        self._openai = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> dict:
        """
        Возвращает dict:
        {
            "content": str | None,
            "tool_calls": list[dict] | None,
            "finish_reason": str,
        }
        """
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = tools

        response = await self._openai.chat.completions.create(**kwargs)
        choice = response.choices[0]

        raw_tool_calls = choice.message.tool_calls
        tool_calls = None
        if raw_tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in raw_tool_calls
            ]

        return {
            "content": choice.message.content,
            "tool_calls": tool_calls,
            "finish_reason": choice.finish_reason,
        }


def get_llm_client() -> LLMClient:
    """Синглтон — создаёт LLMClient из settings при первом вызове."""
    global _client
    if _client is None:
        from app.config import settings

        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        _client = LLMClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
    return _client
