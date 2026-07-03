"""
ServiceNow REST Table API async client.

Uses httpx for async HTTP. All methods raise ServiceNowError on non-2xx
responses so callers can handle consistently.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Optional
import httpx

logger = logging.getLogger(__name__)


class ServiceNowError(Exception):
    """Raised when the ServiceNow API returns a non-2xx response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"ServiceNow API error {status_code}: {detail}")


class ServiceNowClient:
    """
    Thin async wrapper around the ServiceNow Table REST API.

    Usage:
        async with ServiceNowClient(base_url, username, password) as client:
            records = await client.query_table("cmdb_ci_service", fields=[...])
    """

    DEFAULT_TIMEOUT = 30.0  # seconds per request
    PAGE_SIZE       = 200   # records per paginated request

    def __init__(self, base_url: str, username: str, password: str):
        # Normalise: strip trailing slash
        self.base_url = base_url.rstrip("/")
        self._auth    = (username, password)
        self._headers = {
            "Accept":       "application/json",
            "Content-Type": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> ServiceNowClient:
        self._client = httpx.AsyncClient(
            auth=self._auth,
            headers=self._headers,
            timeout=self.DEFAULT_TIMEOUT,
            verify=False,  # allow self-signed certs on dev instances
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── Low-level helpers ────────────────────────────────────────────────

    def _table_url(self, table: str) -> str:
        return f"{self.base_url}/api/now/table/{table}"

    async def _get(self, url: str, params: dict) -> dict:
        if not self._client:
            raise RuntimeError("Client not started — use 'async with ServiceNowClient(...)'")
        resp = await self._client.get(url, params=params)
        if resp.status_code not in (200, 201):
            raise ServiceNowError(resp.status_code, resp.text[:400])
        return resp.json()

    async def _post(self, url: str, payload: dict) -> dict:
        if not self._client:
            raise RuntimeError("Client not started")
        resp = await self._client.post(
            url, json=payload,
            params={"sysparm_input_display_value": "true"},
        )
        if resp.status_code not in (200, 201):
            raise ServiceNowError(resp.status_code, resp.text[:400])
        return resp.json()

    async def _patch(self, url: str, payload: dict) -> dict:
        if not self._client:
            raise RuntimeError("Client not started")
        resp = await self._client.patch(
            url, json=payload,
            params={"sysparm_input_display_value": "true"},
        )
        if resp.status_code not in (200, 201):
            raise ServiceNowError(resp.status_code, resp.text[:400])
        return resp.json()

    # ── Public API ───────────────────────────────────────────────────────

    async def test_auth(self) -> tuple[bool, float, str]:
        """
        Test credentials by calling the sys_user table with limit=1.
        Returns (ok, latency_ms, message).
        """
        t0 = time.monotonic()
        try:
            await self._get(
                self._table_url("sys_user"),
                {"sysparm_limit": "1", "sysparm_fields": "sys_id"},
            )
            latency = (time.monotonic() - t0) * 1000
            return True, round(latency, 1), "Connection successful"
        except ServiceNowError as e:
            latency = (time.monotonic() - t0) * 1000
            return False, round(latency, 1), str(e)
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            return False, round(latency, 1), f"Network error: {e}"

    async def query_table(
        self,
        table: str,
        fields: list[str],
        encoded_query: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Fetch all matching records, automatically paginating in PAGE_SIZE chunks.
        Returns a flat list of plain dicts (display values extracted).
        """
        url     = self._table_url(table)
        results = []
        offset  = 0

        while True:
            params: dict[str, Any] = {
                "sysparm_fields":        ",".join(fields),
                "sysparm_display_value": "all",   # get both value + display_value
                "sysparm_limit":         min(self.PAGE_SIZE, limit - len(results)),
                "sysparm_offset":        offset,
                "sysparm_exclude_reference_link": "true",
            }
            if encoded_query:
                params["sysparm_query"] = encoded_query

            data = await self._get(url, params)
            batch = data.get("result", [])
            if not batch:
                break

            # Flatten: prefer display_value for reference fields, value for plain
            for raw in batch:
                flat: dict[str, Any] = {}
                for k, v in raw.items():
                    if isinstance(v, dict):
                        flat[k] = v.get("display_value") or v.get("value") or ""
                    else:
                        flat[k] = v
                results.append(flat)

            offset += len(batch)
            if len(results) >= limit or len(batch) < self.PAGE_SIZE:
                break

        logger.debug(f"query_table({table}): {len(results)} records")
        return results

    async def get_record(self, table: str, sys_id: str, fields: list[str] | None = None) -> dict:
        """Fetch a single record by sys_id."""
        params: dict[str, Any] = {"sysparm_display_value": "all"}
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        data = await self._get(f"{self._table_url(table)}/{sys_id}", params)
        raw = data.get("result", {})
        flat: dict[str, Any] = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                flat[k] = v.get("display_value") or v.get("value") or ""
            else:
                flat[k] = v
        return flat

    async def create_record(self, table: str, payload: dict) -> dict:
        """Create a new record. Returns the created record dict."""
        data = await self._post(self._table_url(table), payload)
        return data.get("result", {})

    async def update_record(self, table: str, sys_id: str, payload: dict) -> dict:
        """Update an existing record via PATCH. Returns updated record."""
        data = await self._patch(f"{self._table_url(table)}/{sys_id}", payload)
        return data.get("result", {})
