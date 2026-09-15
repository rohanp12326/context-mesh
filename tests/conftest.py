"""Pytest shared fixtures."""

import os
import pytest
from connectors.jira.connector import JiraConnector
from connectors.slack.connector import SlackConnector
from connectors.gmail.connector import GmailConnector
from mcp_servers.registry import MCPToolRegistry
from memory.short_term import ShortTermMemoryStore
from memory.long_term import LongTermMemoryStore
from agent.graph import ContextMeshAgent


@pytest.fixture
def jira_connector():
    return JiraConnector(mode="live")


@pytest.fixture
def slack_connector():
    return SlackConnector(mode="live")


@pytest.fixture
def gmail_connector():
    return GmailConnector(mode="live")


@pytest.fixture
def tool_registry():
    return MCPToolRegistry()


@pytest.fixture
def memory_stores():
    return ShortTermMemoryStore(), LongTermMemoryStore()


@pytest.fixture
def agent():
    return ContextMeshAgent()
