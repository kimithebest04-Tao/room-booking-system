"""Async HTTP client for the booking backend's operational endpoints.

The maintenance controller uses this to block writes, verify the restored data
through the public API and perform the controlled probe write after a switch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """The backend answered with an error or could not be reached."""


class BackendClient:
    """Small async wrapper around the backend endpoints the controller needs."""

    def __init__(
        self,
        base_url: str,
        admin_token: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._admin_token = admin_token
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-Admin-Token": self._admin_token, "Accept": "application/json"},
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._client() as client:
                    response = await client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(0.5 * attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                await asyncio.sleep(0.5 * attempt)
                continue
            if response.status_code >= 400:
                raise BackendError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:200]}")
            if not response.content:
                return {}
            return response.json()
        raise BackendError(f"{method} {path} failed: {last_error}")

    # ---------------------------------------------------------------- read side
    async def health(self) -> dict[str, Any]:
        """Return the backend health document."""
        return await self._request("GET", "/health")

    async def get_maintenance(self) -> bool:
        """Read the maintenance flag."""
        payload = await self._request("GET", "/admin/maintenance")
        return bool(payload.get("maintenance_mode"))

    async def count_bookings(self, limit: int = 1) -> int:
        """Number of reservations reported by the API (used after the switch)."""
        payload = await self._request("GET", "/bookings", params={"limit": limit})
        return int(payload.get("total", 0))

    # --------------------------------------------------------------- write side
    async def set_maintenance(self, enabled: bool) -> bool:
        """Enable or disable the write block and return the acknowledged state."""
        payload = await self._request("POST", "/admin/maintenance", json={"enabled": enabled})
        return bool(payload.get("maintenance_mode"))

    async def probe_write(self) -> bool:
        """Perform the controlled write that proves the new database accepts writes."""
        payload = await self._request("POST", "/admin/probe-write")
        return bool(payload.get("ok"))

    async def wait_until_healthy(
        self, *, timeout_seconds: float = 300.0, poll_seconds: float = 10.0
    ) -> dict[str, Any]:
        """Poll ``/health`` until the backend answers ``ok`` or the time limit expires."""
        waited = 0.0
        while waited <= timeout_seconds:
            try:
                payload = await self.health()
            except BackendError as exc:
                logger.warning("Backend not healthy yet: %s", exc)
                payload = {}
            if payload.get("status") == "ok" and payload.get("database") == "ok":
                return payload
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
        raise BackendError(f"Backend did not become healthy within {timeout_seconds:.0f}s")
