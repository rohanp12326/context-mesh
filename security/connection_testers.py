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


async def verify_slack_connection(token: str) -> Tuple[bool, str, float]:
    """Test Slack connectivity by querying the auth.test endpoint."""
    clean_token = (token or "").strip()
    if not clean_token:
        return False, "Slack token cannot be empty.", 0.0

    url = "https://slack.com/api/auth.test"
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, headers=headers)
            latency = round((time.time() - start) * 1000.0, 2)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    team = data.get("team", "Slack Workspace")
                    user = data.get("user", "Slack App")
                    return True, f"Connected to Slack! Workspace: '{team}', User/Bot: '{user}' ({latency}ms)", latency
                else:
                    err = data.get("error", "authentication_failed")
                    return False, f"Slack auth test failed: {err}", latency
            elif resp.status_code == 401:
                return False, "Authentication failed (401): Invalid Slack token.", latency
            else:
                return False, f"Slack API error {resp.status_code}: {resp.text[:120]}", latency
    except Exception as e:
        return False, f"Could not connect to Slack API: {str(e)}", 0.0


async def verify_gmail_connection(
    account_email: str,
    app_password: Optional[str] = None,
    access_token: Optional[str] = None
) -> Tuple[bool, str, float]:
    """Test live Gmail connectivity using either Google App Password (IMAP SSL) or OAuth Bearer Token."""
    clean_email = (account_email or "").strip()
    if not clean_email or "@" not in clean_email:
        return False, "A valid email address (e.g. user@gmail.com) is required.", 0.0

    start = time.time()

    # 1. Test via Google App Password (IMAP SSL)
    if app_password and app_password.strip():
        clean_pw = app_password.strip().replace(" ", "")
        try:
            import imaplib
            import asyncio

            def _test_imap():
                mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
                mail.login(clean_email, clean_pw)
                mail.select("INBOX", readonly=True)
                mail.logout()

            await asyncio.to_thread(_test_imap)
            latency = round((time.time() - start) * 1000.0, 2)
            return True, f"Successfully authenticated to Gmail as {clean_email} via IMAP SSL ({latency}ms)!", latency
        except Exception as e:
            err_msg = str(e)
            if "AUTHENTICATIONFAILED" in err_msg or "Invalid credentials" in err_msg:
                return (
                    False,
                    "Authentication failed: Invalid Gmail address or App Password. "
                    "Make sure you generate a 16-character App Password at myaccount.google.com/apppasswords.",
                    0.0
                )
            return False, f"Could not connect to Gmail IMAP: {err_msg}", 0.0

    # 2. Test via OAuth Access Token (Gmail REST API)
    if access_token and access_token.strip():
        clean_token = access_token.strip()
        url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
        headers = {"Authorization": f"Bearer {clean_token}"}
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(url, headers=headers)
                latency = round((time.time() - start) * 1000.0, 2)
                if resp.status_code == 200:
                    profile = resp.json()
                    email_addr = profile.get("emailAddress", clean_email)
                    return True, f"Connected to Gmail API as {email_addr} ({latency}ms)!", latency
                elif resp.status_code == 401:
                    return False, "OAuth token expired or unauthorized (401).", latency
                else:
                    return False, f"Gmail API error {resp.status_code}: {resp.text[:120]}", latency
        except Exception as e:
            return False, f"Could not reach Gmail REST API: {str(e)}", 0.0

    return False, "Please provide a Google App Password (16 chars) or OAuth Access Token.", 0.0


async def verify_mcp_connection(
    service: str,
    endpoint_url: str,
    auth_token: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Tuple[bool, str, float]:
    """Test connectivity to an official remote MCP server."""
    clean_url = (endpoint_url or "").strip()
    if not clean_url:
        return False, "Endpoint URL is required.", 0.0
    if not clean_url.startswith("http"):
        clean_url = f"https://{clean_url}"

    clean_token = (auth_token or "").strip()
    if not clean_token:
        return False, f"Authentication token is required for {service.capitalize()} official MCP server.", 0.0

    start = time.time()
    try:
        from mcp_servers.remote_client import RemoteMCPClient
        client = RemoteMCPClient(
            service=service,
            endpoint_url=clean_url,
            auth_token=clean_token,
            user_email=user_email,
            timeout=8.0,
        )
        tools = await client.list_tools()
        latency = round((time.time() - start) * 1000.0, 2)
        return True, f"Connected to {service.capitalize()} Official MCP Server ({len(tools)} tools discovered, {latency}ms)!", latency
    except Exception as e:
        latency = round((time.time() - start) * 1000.0, 2)
        return False, f"MCP connection to {clean_url} failed: {str(e)}", latency


async def verify_composio_connection(
    api_key: str,
    base_url: str = "https://backend.composio.dev/api/v3.1"
) -> Tuple[bool, str, float]:
    """Test Composio API Key connectivity."""
    clean_key = (api_key or "").strip()
    if not clean_key:
        return False, "Composio API Key cannot be empty.", 0.0

    clean_url = (base_url or "https://backend.composio.dev/api/v3.1").strip().rstrip("/")
    url = f"{clean_url}/connected_accounts"
    headers = {
        "x-api-key": clean_key,
        "Accept": "application/json",
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            latency = round((time.time() - start) * 1000.0, 2)
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("items") or data.get("connected_accounts") or (data if isinstance(data, list) else []))
                return True, f"Successfully connected to Composio! ({count} connected accounts found, {latency}ms)", latency
            elif resp.status_code == 401:
                return False, "Authentication failed (401): Invalid Composio API Key.", latency
            elif resp.status_code == 403:
                return False, "Forbidden (403): Account suspended or lacks permission.", latency
            else:
                return False, f"Composio API error ({resp.status_code}): {resp.text[:120]}", latency
    except Exception as e:
        latency = round((time.time() - start) * 1000.0, 2)
        return False, f"Could not connect to Composio endpoint: {str(e)}", latency


async def verify_composio_app_connection(
    toolkit: str,
    user_id: Optional[str] = None,
    api_key: Optional[str] = None
) -> Tuple[bool, str, float]:
    """Test if a specific application (Jira, Slack, Gmail) is authenticated in Composio."""
    start = time.time()
    try:
        from mcp_servers.composio_client import ComposioMCPClient
        client = ComposioMCPClient(api_key=api_key, user_id=user_id)
        effective_user_id = client.user_id
        is_conn, acc_id = await client.check_connection_status(toolkit, user_id=effective_user_id)
        latency = round((time.time() - start) * 1000.0, 2)
        if is_conn:
            return True, f"Verified: {toolkit.capitalize()} is actively connected via Composio (Account ID: {acc_id})!", latency
        return False, f"{toolkit.capitalize()} is not currently connected in Composio (User: {effective_user_id}). Click 'Connect via Composio' to authenticate.", latency
    except Exception as e:
        latency = round((time.time() - start) * 1000.0, 2)
        return False, f"Failed to check {toolkit.capitalize()} connection: {str(e)}", latency


# Aliases
test_zai_connection = verify_zai_connection
test_jira_connection = verify_jira_connection
test_slack_connection = verify_slack_connection
test_gmail_connection = verify_gmail_connection
test_mcp_connection = verify_mcp_connection
test_composio_connection = verify_composio_connection
test_composio_app_connection = verify_composio_app_connection

