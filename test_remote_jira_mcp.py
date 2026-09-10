import asyncio
from mcp_servers.remote_client import RemoteMCPClient

async def main():
    client = RemoteMCPClient(
        service="jira",
        endpoint_url="https://mcp.atlassian.com/v2/mcp",
        auth_token="ATATT3xFfGF0PvDORi8IPPjyyvFlYAjE63pRoKRsFs0SqhAahnbPXFjq-ZpGpWSd9JUT9uIqsNWRQMx3o3Gl05ecaEb9TZpdrkMTBjwIp236rfI2WUI8sdXXoea3pVOKjCc1Gr3MF6RY50KdAeGS-SavFYGDqA4Rk_MNJKH31gz9o0KLt7hNJjM=B67B45CA",
        user_email="rohanp12326@gmail.com"
    )
    print("Headers:", client.headers)
    try:
        tools = await client.list_tools()
        print("Tools found:", len(tools))
        for t in tools[:5]:
            print("Tool:", t.get("name") if isinstance(t, dict) else getattr(t, "name", t))
    except Exception as e:
        print("Error listing tools:", type(e), e)

asyncio.run(main())
