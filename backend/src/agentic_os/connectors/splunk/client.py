"""
Splunk REST API client.

Uses Bearer token auth (generated in Splunk: Settings → Tokens).
Primary use: verify connectivity before saving configuration.
"""
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class SplunkClient:
    """
    Minimal async client for the Splunk REST API.

    Args:
        base_url: Splunk management URL, e.g. https://splunk.example.com:8089
        token:    Splunk API token (Bearer)
    """

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> tuple[bool, float, str]:
        """
        Verify credentials by calling GET /services/server/info.

        Returns:
            (ok, latency_ms, message)
        """
        url = f"{self.base_url}/services/server/info"
        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._headers,
                    params={"output_mode": "json"},
                    ssl=False,          # many Splunk installs use self-signed certs
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    latency_ms = round((time.monotonic() - start) * 1000, 1)

                    if resp.status == 200:
                        try:
                            data = await resp.json(content_type=None)
                            entry = (data.get("entry") or [{}])[0]
                            version = entry.get("content", {}).get("version", "?")
                            server_name = entry.get("content", {}).get("serverName", "")
                            detail = f" ({server_name})" if server_name else ""
                            return True, latency_ms, f"Connected to Splunk {version}{detail}"
                        except Exception:
                            return True, latency_ms, "Connected (could not parse version)"

                    elif resp.status in (401, 403):
                        return False, latency_ms, "Authentication failed — check your API token"

                    else:
                        text = (await resp.text())[:200]
                        return False, latency_ms, f"HTTP {resp.status}: {text}"

        except aiohttp.ClientConnectorError as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return False, latency_ms, f"Could not connect to {self.base_url}: {exc}"
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return False, latency_ms, f"Connection error: {exc}"
