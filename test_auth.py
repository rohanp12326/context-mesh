from security.vault import VAULT
import os
creds = VAULT.get_credential("jira")
url = creds.get("base_url") or os.getenv("JIRA_URL") or os.getenv("JIRA_BASE_URL", "")
email = creds.get("user_email") or os.getenv("JIRA_USER_EMAIL", "")
token = creds.get("api_token") or os.getenv("JIRA_API_TOKEN", "")
print("url:", url)
print("email:", email)
print("token:", token)
mcp = creds.get("mcp_token") or os.getenv("ATLASSIAN_MCP_TOKEN", "")
print("mcp:", mcp)
