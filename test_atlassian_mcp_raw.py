import httpx
import base64

raw_cred = "rohanp12326@gmail.com:ATATT3xFfGF0PvDORi8IPPjyyvFlYAjE63pRoKRsFs0SqhAahnbPXFjq-ZpGpWSd9JUT9uIqsNWRQMx3o3Gl05ecaEb9TZpdrkMTBjwIp236rfI2WUI8sdXXoea3pVOKjCc1Gr3MF6RY50KdAeGS-SavFYGDqA4Rk_MNJKH31gz9o0KLt7hNJjM=B67B45CA"
encoded = base64.b64encode(raw_cred.encode("utf-8")).decode("utf-8")

headers = {
    "Accept": "text/event-stream, application/json",
    "User-Agent": "ContextMesh-MCP-Client/1.0",
    "MCP-Protocol-Version": "2024-11-05",
    "Authorization": f"Basic {encoded}"
}

with httpx.Client() as client:
    r = client.post("https://mcp.atlassian.com/v2/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
    print("Status:", r.status_code)
    print("Response:", r.text)
    print("Response headers:", r.headers)

