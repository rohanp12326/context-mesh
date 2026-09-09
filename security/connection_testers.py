"""Live connection testers for verifying third-party credentials before saving."""

import json
import time
from typing import Any, Dict, Optional, Tuple
import httpx
from observability.logging import get_logger

logger = get_logger("security.connection_testers")


async def verify_zai_connection(
    api_key: str,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model: str = "glm-4.5-air"
) -> Tuple[bool, str, float]:
    """Test ZAI GLM API connectivity by calling the completions endpoint with a test prompt."""
    clean_key = (api_key or "").strip()
    clean_url = (base_url or "https://open.bigmodel.cn/api/paas/v4/").strip().rstrip("/")
    clean_model = (model or "glm-4.5-air").strip()

    if not clean_key:
        logger.warning("ZAI connection test failed: API key is empty.")
        return False, "API Key cannot be empty.", 0.0

    url = f"{clean_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": clean_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5
    }

    logger.info(f"Testing ZAI GLM connection: endpoint={url}, model={clean_model}")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            latency = round((time.time() - start) * 1000.0, 2)

            if resp.status_code == 200:
                data = resp.json()
                model_name = data.get("model", clean_model)
                logger.info(f"ZAI connection test succeeded for model '{model_name}' ({latency}ms)")
                return True, f"Successfully connected to ZAI GLM! Model: {model_name} (Latency: {latency}ms)", latency

            # Handle non-200 responses
            error_text = resp.text
            logger.warning(f"ZAI connection test returned HTTP {resp.status_code}: {error_text[:200]}")

            if resp.status_code == 401:
                return False, "Authentication failed (401 Unauthorized): Invalid API Key.", latency
            elif resp.status_code == 404:
                return False, f"Model '{clean_model}' or endpoint not found (404). Check model name or base URL.", latency

            # Inspect error payload for specific BigModel / Z.ai error codes
            try:
                err_json = resp.json()
                err_info = err_json.get("error", {})
                code = str(err_info.get("code", ""))
                msg = err_info.get("message", "")
                if code == "1211" or "模型不存在" in msg:
                    return (
                        False,
                        f"Model '{clean_model}' is not recognized or not available for your API key/tier (BigModel Error 1211: 模型不存在). "
                        f"Please select 'glm-4.5-air' or verify your API base URL (e.g. open.bigmodel.cn vs api.z.ai).",
                        latency
                    )
                if msg:
                    return False, f"API error ({code}): {msg}", latency
            except Exception:
                pass

            return False, f"API returned error {resp.status_code}: {error_text[:150]}", latency
    except httpx.ConnectError:
        logger.error(f"Failed to connect to host: {clean_url}")
        return False, f"Failed to connect to host '{clean_url}'. Check internet connection or base URL.", 0.0
    except Exception as e:
        logger.error(f"ZAI connection error: {e}", exc_info=True)
        return False, f"Connection error: {str(e)}", 0.0



async def verify_jira_connection(
    base_url: str,
    user_email: str,
    api_token: str
) -> Tuple[bool, str, float]:
    """Test Jira connectivity by querying the current user profile (/rest/api/3/myself)."""
    if not base_url or not user_email or not api_token:
        return False, "Base URL, user email, and API token are required.", 0.0

    clean_base = base_url.rstrip("/")
    if not clean_base.startswith("http"):
        clean_base = f"https://{clean_base}"

    url = f"{clean_base}/rest/api/3/myself"
    auth = (user_email.strip(), api_token.strip())
    headers = {"Accept": "application/json"}

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, auth=auth, headers=headers)
            latency = round((time.time() - start) * 1000.0, 2)

            if resp.status_code == 200:
                user_info = resp.json()
                display_name = user_info.get("displayName", user_email)
                return True, f"Authenticated successfully as {display_name} ({latency}ms)!", latency
            elif resp.status_code == 401:
                return False, "Authentication failed (401 Unauthorized): Invalid email or API token.", latency
            elif resp.status_code == 403:
                return False, "Forbidden (403): User lacks permission or account is suspended.", latency
            else:
                return False, f"Jira error {resp.status_code}: {resp.text[:120]}", latency
    except Exception as e:
        return False, f"Could not reach Jira instance: {str(e)}", 0.0


async def verify_notion_connection(api_key: str) -> Tuple[bool, str, float]:
    """Test Notion connectivity by querying the bot user profile (/v1/users/me)."""
    if not api_key or not api_key.strip():
        return False, "Notion API Key cannot be empty.", 0.0

    url = "https://api.notion.com/v1/users/me"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Notion-Version": "2022-06-28"
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, headers=headers)
            latency = round((time.time() - start) * 1000.0, 2)

            if resp.status_code == 200:
                bot_info = resp.json()
                bot_name = bot_info.get("name", "Notion Bot")
                return True, f"Connected to Notion! Bot Name: '{bot_name}' ({latency}ms)", latency
            elif resp.status_code == 401:
                return False, "Authentication failed (401): Invalid Notion integration token.", latency
            else:
                return False, f"Notion error {resp.status_code}: {resp.text[:120]}", latency
    except Exception as e:
        return False, f"Could not connect to Notion API: {str(e)}", 0.0


async def verify_gmail_connection(account_email: str) -> Tuple[bool, str, float]:
    """Validate Gmail configuration format."""
    if not account_email or "@" not in account_email:
        return False, "Valid email address required.", 0.0
    return True, f"Gmail configuration verified for {account_email} (Ready for OAuth)", 1.0

# Aliases
test_zai_connection = verify_zai_connection
test_jira_connection = verify_jira_connection
test_notion_connection = verify_notion_connection
test_gmail_connection = verify_gmail_connection
