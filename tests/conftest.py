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
def synthetic_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "evals", "datasets", "synthetic_atlas.json")


@pytest.fixture
def jira_connector(synthetic_path):
    return JiraConnector(mode="mock", synthetic_data_path=synthetic_path)


@pytest.fixture
def slack_connector(synthetic_path):
    return SlackConnector(mode="mock", synthetic_data_path=synthetic_path)


@pytest.fixture
def gmail_connector(synthetic_path):
    return GmailConnector(mode="mock", synthetic_data_path=synthetic_path)


@pytest.fixture
def tool_registry():
    return MCPToolRegistry()


@pytest.fixture
def memory_stores():
    return ShortTermMemoryStore(), LongTermMemoryStore()


@pytest.fixture
def agent():
    return ContextMeshAgent()
