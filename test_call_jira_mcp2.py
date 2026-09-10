import asyncio
import httpx2
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession
import base64

raw_cred = "rohanp12326@gmail.com:ATATT3xFfGF0PvDORi8IPPjyyvFlYAjE63pRoKRsFs0SqhAahnbPXFjq-ZpGpWSd9JUT9uIqsNWRQMx3o3Gl05ecaEb9TZpdrkMTBjwIp236rfI2WUI8sdXXoea3pVOKjCc1Gr3MF6RY50KdAeGS-SavFYGDqA4Rk_MNJKH31gz9o0KLt7hNJjM=B67B45CA"
encoded = base64.b64encode(raw_cred.encode("utf-8")).decode("utf-8")

headers = {
    "Accept": "text/event-stream, application/json",
    "User-Agent": "ContextMesh-MCP-Client/1.0",
    "MCP-Protocol-Version": "2024-11-05",
    "Authorization": f"Basic {encoded}"
}

async def main():
    async with httpx2.AsyncClient(headers=headers, timeout=20.0) as http_client:
        async with streamable_http_client("https://mcp.atlassian.com/v2/mcp", http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                # Search issues with cloudId
                search_res = await session.call_tool("searchJiraIssuesUsingJql", {
                    "cloudId": "82d1af77-f0af-4dae-94e0-83a8fcc923d3",
                    "jql": "statusCategory != Done",
                    "maxResults": 5
                })
                print("Search res text:", search_res.content[0].text[:500])

asyncio.run(main())
