"""
ConnectorRegistry — singleton that holds all registered connector implementations.
Connectors self-register at import time via register().
"""

from __future__ import annotations
from typing import Optional
from agentic_os.connectors.base import BaseConnector

import logging
logger = logging.getLogger(__name__)


class ConnectorRegistry:
    _instance: Optional[ConnectorRegistry] = None
    _connectors: dict[str, BaseConnector] = {}

    def __new__(cls) -> ConnectorRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connectors = {}
        return cls._instance

    def register(self, connector: BaseConnector) -> None:
        self._connectors[connector.id] = connector
        logger.info(f"✓ Connector registered: {connector.id} ({connector.display_name})")

    def get(self, connector_id: str) -> Optional[BaseConnector]:
        return self._connectors.get(connector_id)

    def list_all(self) -> list[BaseConnector]:
        return list(self._connectors.values())

    def ids(self) -> list[str]:
        return list(self._connectors.keys())


# Module-level singleton
_registry = ConnectorRegistry()


def get_registry() -> ConnectorRegistry:
    return _registry


def register_connector(connector: BaseConnector) -> None:
    _registry.register(connector)
