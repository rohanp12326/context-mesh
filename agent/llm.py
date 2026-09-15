"""ZAI GLM API client with OpenAI-compatible interface for real-time model inference."""

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import httpx

load_dotenv()

from security.vault import VAULT
from observability.logging import get_logger

logger = get_logger("agent.llm")


class ZAIClient:
    """Client for ZAI GLM API (OpenAI-compatible) querying live models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self._explicit_key = api_key
        self._explicit_base_url = base_url
        self._explicit_model = model

    @property
    def api_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key
        vault_creds = VAULT.get_credential("zai")
        return vault_creds.get("api_key") or os.getenv("ZAI_API_KEY") or os.getenv("ZHIPUAI_API_KEY", "")

    @property
    def base_url(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url.rstrip("/")
        vault_creds = VAULT.get_credential("zai")
        url = vault_creds.get("base_url") or os.getenv("ZAI_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4/"
        return url.rstrip("/")

    @property
    def model(self) -> str:
        if self._explicit_model:
            return self._explicit_model
        vault_creds = VAULT.get_credential("zai")
        return vault_creds.get("model") or os.getenv("ZAI_MODEL") or "glm-4-plus"

    @property
    def is_live(self) -> bool:
        key = self.api_key
        return bool(key and key != "your_zai_api_key_here")

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
                "LLM API key is not configured. Please configure your ZAI_API_KEY in .secrets/vault or .env to run real reasoning."
            )

        target_model = self.model
        target_url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature
        }
        if tools:
            payload["tools"] = self._format_tools_openai(tools)

        max_attempts = 2
        last_error = None
        # Use reasonable timeouts (5s connect, 15s read) to avoid UI lockups
        timeout = httpx.Timeout(15.0, connect=5.0)

        for attempt in range(1, max_attempts + 1):
            start_t = time.time()
            logger.info(f"ZAIClient: Sending request to {target_model} (attempt={attempt}/{max_attempts}, messages={len(messages)}, tools={len(tools) if tools else 0})")
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
                    thought = msg.get("reasoning_content") or msg.get("thought")
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
                    logger.info(f"ZAIClient: Received response in {latency}ms (has_content={bool(content)}, has_thought={bool(thought)}, tool_calls={len(tool_calls)})")
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
                logger.warning(f"ZAIClient: Attempt {attempt} to {target_model} failed after {latency}ms: {e}")
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
        """Call ZAI GLM completion endpoint."""
        if not self.is_live:
            raise RuntimeError(
                "LLM API key is not configured. Please configure your ZAI_API_KEY in .secrets/vault or .env to run real reasoning."
            )

        target_model = self.model
        target_url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature
        }
        if response_format:
            payload["response_format"] = response_format

        max_attempts = 2
        last_error = None
        timeout = httpx.Timeout(15.0, connect=5.0)

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
                logger.warning(f"ZAIClient: Attempt {attempt} to {target_model} failed after {latency}ms: {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(0.5)

        raise RuntimeError(f"ZAIClient: All {max_attempts} attempts failed for model {target_model}. Last error: {last_error}")

