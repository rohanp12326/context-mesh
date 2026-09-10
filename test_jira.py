import asyncio
from connectors.jira.connector import JiraConnector

async def main():
    connector = JiraConnector(mode="live")
    results = await connector.search("statusCategory != Done", limit=5)
    for r in results:
        print(r.title)

asyncio.run(main())
