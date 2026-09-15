"""Base connector interface for enterprise tool integrations."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class PermissionScope(BaseModel):
    """Permission and authorization boundary for tool access."""
    user_id: str = "default_user"
    allowed_scopes: List[str] = Field(default_factory=lambda: ["read:jira", "read:slack", "read:gmail"])
    can_mutate: bool = False


class ConnectorItem(BaseModel):
    """Raw record returned by a connector."""
    source: str
    id: str
    title: str
    content: str
    url: str
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_mode: str = "mock"


class BaseConnector(ABC):
    """Abstract base class for all enterprise data connectors."""

    def __init__(self, mode: str = "mock", synthetic_data_path: Optional[str] = None):
        self._mode = mode
        self.synthetic_data_path = synthetic_data_path

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str):
        self._mode = value

    @abstractmethod
    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        """Search items in the source system."""
        pass

    @abstractmethod
    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        """Fetch a specific item by its source ID."""
        pass

    @abstractmethod
    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        """Execute a write/mutation action in the source system (requires permission)."""
        pass
