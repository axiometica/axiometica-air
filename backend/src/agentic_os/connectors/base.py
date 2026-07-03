"""
Base connector interface — all connectors implement this contract.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class ConnectorStatus:
    connected: bool
    latency_ms: Optional[float] = None
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SyncResult:
    success: bool
    records_pulled: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    finished_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class BaseConnector(ABC):
    """Abstract base for all external system connectors."""

    #: Unique machine-readable identifier, e.g. "servicenow"
    id: str
    #: Human-readable name shown in the UI
    display_name: str
    #: Connector version string
    version: str = "1.0.0"
    #: Icon key matched in the frontend
    icon: str = "plug"

    @abstractmethod
    async def test_connection(self, config: dict[str, Any]) -> ConnectorStatus:
        """Verify that credentials and URL are valid. Must not mutate state."""

    @abstractmethod
    async def sync(self, config: dict[str, Any], db_session: Any) -> SyncResult:
        """Pull data from the external system into the local cache."""

    @abstractmethod
    def get_config_schema(self) -> dict[str, Any]:
        """Return a JSON-Schema-compatible dict describing required config fields."""

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "version": self.version,
            "icon": self.icon,
        }
