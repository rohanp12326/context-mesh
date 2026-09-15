"""LLM client with OpenAI-compatible interface for real-time model inference.

Supports multiple providers selected at runtime:
- "zai":      ZAI GLM (Zhipu BigModel)
- "opencode": OpenCode Go subscription gateway (https://opencode.ai/zen/go/v1)
"""

import asyncio
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import httpx

load_dotenv()

from security.vault import VAULT
from observability.logging import get_logger

logger = get_logger("agent.llm")

# Provider default configuration (OpenAI-compatible chat/completions gateways)
PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "zai": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4-plus",
        "placeholder": "your_zai_api_key_here",
        "api_key_env": ["ZAI_API_KEY", "ZHIPUAI_API_KEY"],
    },
    "opencode": {
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "glm-5.3-flash",
        "placeholder": "your_opencode_api_key_here",
        "api_key_env": ["OPENCODE_API_KEY"],
    },
}


class LLMClient:
    """Generic OpenAI-compatible LLM client backed by a configurable provider."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self._explicit_provider = provider
        self._explicit_key = api_key
        self._explicit_base_url = base_url
        self._explicit_model = model
        # Stable per-session identifier required by the OpenCode gateway for efficient routing
        self._session_id = str(uuid.uuid4())

    @property
    def provider(self) -> str:
        if self._explicit_provider in PROVIDER_DEFAULTS:
            return self._explicit_provider
        return VAULT.get_active_llm_provider()

    @property
    def api_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key
        creds = VAULT.get_credential(self.provider)
        env_keys = PROVIDER_DEFAULTS[self.provider]["api_key_env"]
        key = creds.get("api_key") or ""
        for env_name in env_keys:
            if not key:
                key = os.getenv(env_name, "")
        return key

    @property
    def base_url(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url.rstrip("/")
        creds = VAULT.get_credential(self.provider)
        url = creds.get("base_url") or os.getenv(
            f"{self.provider.upper()}_BASE_URL",
            PROVIDER_DEFAULTS[self.provider]["base_url"]
        )
        return url.rstrip("/")

    @property
    def model(self) -> str:
        if self._explicit_model:
            return self._explicit_model
        creds = VAULT.get_credential(self.provider)
        return creds.get("model") or os.getenv(
            f"{self.provider.upper()}_MODEL",
            PROVIDER_DEFAULTS[self.provider]["model"]
        )

    @property
    def is_live(self) -> bool:
        key = self.api_key
        return bool(key) and key not in (
            "your_zai_api_key_here",
            "your_opencode_api_key_here",
        )

    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        temperature: float = 0.1,
    ) -> Any:
        """Call LLM with tools/function calling support."""
        from agent.llm_types import LLMResponse, ToolCallRequest

        if not self.is_live:
            raise RuntimeError(
                f"LLM API key is not configured for provider '{self.provider}'. "
                "Please configure your API key in the Authentication Wizard or .env to run real reasoning."
            )

        target_model = self.model
        target_url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        if self.provider == "opencode":
            headers["x-opencode-session"] = self._session_id
            headers["User-Agent"] = "context-mesh/0.1.0"
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature
        }
        if self.provider == "opencode":
            # OpenCode Go / Baseten reasoning models require a max_tokens budget strictly
            # greater than 1024 (to reserve room for a final answer). We deliberately do NOT
            # send a reasoning_effort value: the gateway maps "low" to an invalid
            # thinking.budget_tokens of 1, and the models default to a safe reasoning level.
            payload["max_tokens"] = 8192
        if tools:
            payload["tools"] = self._format_tools_openai(tools)

        max_attempts = 2
        last_error = None
        # Use reasonable timeouts (5s connect, 15s read) to avoid UI lockups
        timeout = httpx.Timeout(60.0, connect=5.0)

        for attempt in range(1, max_attempts + 1):
            start_t = time.time()
            logger.info(f"LLMClient[{self.provider}]: Sending request to {target_model} (attempt={attempt}/{max_attempts}, messages={len(messages)}, tools={len(tools) if tools else 0})")
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        target_url,
                        json=payload,
                        headers=headers
                    )
                    resp.raise_for_status()
                    latency = round((time.time() - start_t) * 1000, 2)
                    data = resp.json()
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    content = msg.get("content")
                    thought = msg.get("reasoning_content") or msg.get("thought") or msg.get("reasoning")
                    if content and "<thought>" in content and "</thought>" in content:
                        extracted_thought = content.split("<thought>")[1].split("</thought>")[0].strip()
                        clean_content = (content.split("<thought>")[0] + content.split("</thought>")[1]).strip()
                        if not thought:
                            thought = extracted_thought
                        content = clean_content or None

                    tool_calls_raw = msg.get("tool_calls") or []
                    tool_calls: List[ToolCallRequest] = []
                    for tc in tool_calls_raw:
                        fn = tc.get("function", {})
                        raw_args = fn.get("arguments", "{}")
                        try:
                            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            parsed_args = {}
                        tool_calls.append(
                            ToolCallRequest(
                                id=tc.get("id", f"call_{len(tool_calls)}"),
                                name=fn.get("name", ""),
                                arguments=parsed_args
                            )
                        )
                    finish_reason = choice.get("finish_reason", "tool_calls" if tool_calls else "stop")
                    logger.info(f"LLMClient[{self.provider}]: Received response in {latency}ms (has_content={bool(content)}, has_thought={bool(thought)}, tool_calls={len(tool_calls)})")
                    return LLMResponse(
                        content=content,
                        thought=thought,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        raw_payload=data
                    )
            except Exception as e:
                last_error = e
                latency = round((time.time() - start_t) * 1000, 2)
                err_body = e.response.text if isinstance(e, httpx.HTTPStatusError) and e.response is not None else str(e)
                logger.warning(f"LLMClient[{self.provider}]: Attempt {attempt} to {target_model} failed after {latency}ms: {err_body[:300]}")
                # Some OpenCode Go models require an explicit reasoning level; retry once with a
                # valid level (high) instead of "low" (which the gateway maps to budget 1).
                _reasoning_hint = any(k in err_body.lower() for k in ("reasoning", "thinking", "effort", "budget"))
                if self.provider == "opencode" and _reasoning_hint and "reasoning_effort" not in payload:
                    payload["reasoning_effort"] = "high"
                    logger.info("LLMClient[opencode]: retrying with reasoning_effort='high'")
                if attempt < max_attempts:
                    await asyncio.sleep(0.5)

        raise RuntimeError(f"LLM API call to {target_model} failed after {max_attempts} attempts: {last_error}")

    @staticmethod
    def _format_tools_openai(tools: List[Any]) -> List[Dict[str, Any]]:
        formatted = []
        for t in tools:
            if hasattr(t, "inputSchema"):
                formatted.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema
                    }
                })
            elif isinstance(t, dict) and "name" in t:
                formatted.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema") or t.get("parameters", {"type": "object"})
                    }
                })
        return formatted

    async def generate_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """Call completion endpoint for plain chat / planning / synthesis."""
        if not self.is_live:
            raise RuntimeError(
                f"LLM API key is not configured for provider '{self.provider}'. "
                "Please configure your API key in the Authentication Wizard or .env to run real reasoning."
            )

        target_model = self.model
        target_url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        if self.provider == "opencode":
            headers["x-opencode-session"] = self._session_id
            headers["User-Agent"] = "context-mesh/0.1.0"
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature
        }
        if self.provider == "opencode":
            payload["max_tokens"] = 8192
        if response_format:
            payload["response_format"] = response_format

        max_attempts = 2
        last_error = None
        timeout = httpx.Timeout(60.0, connect=5.0)

        for attempt in range(1, max_attempts + 1):
            start_t = time.time()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        target_url,
                        json=payload,
                        headers=headers
                    )
                    resp.raise_for_status()
                    latency = round((time.time() - start_t) * 1000, 2)
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()
            except Exception as e:
                last_error = e
                latency = round((time.time() - start_t) * 1000, 2)
                err_body = e.response.text if isinstance(e, httpx.HTTPStatusError) and e.response is not None else str(e)
                logger.warning(f"LLMClient[{self.provider}]: Attempt {attempt} to {target_model} failed after {latency}ms: {err_body[:300]}")
                _reasoning_hint = any(k in err_body.lower() for k in ("reasoning", "thinking", "effort", "budget"))
                if self.provider == "opencode" and _reasoning_hint and "reasoning_effort" not in payload:
                    payload["reasoning_effort"] = "high"
                    logger.info("LLMClient[opencode]: retrying with reasoning_effort='high'")
                if attempt < max_attempts:
                    await asyncio.sleep(0.5)

        raise RuntimeError(f"LLMClient[{self.provider}]: All {max_attempts} attempts failed for model {target_model}. Last error: {last_error}")


class ZAIClient(LLMClient):
    """Backwards-compatible alias. Auto-selects the active provider from the vault."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        super().__init__(provider=None, api_key=api_key, base_url=base_url, model=model)


def get_llm_client(provider: Optional[str] = None, **kwargs: Any) -> LLMClient:
    """Factory returning an LLMClient for the given (or active) provider."""
    return LLMClient(provider=provider, **kwargs)
