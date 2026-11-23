"""Neon database provider.

Neon exposes a documented REST API under ``https://console.neon.tech/api/v2``
that can create, read and delete PostgreSQL projects programmatically.  That is
what makes the automated instance replacement of the assignment possible: the
controller never clicks in a console, it calls this client.

Only the endpoints used by the workflow are implemented; every call has a
timeout and a bounded number of retries.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .provider import (
    DatabaseProvider,
    InstanceInfo,
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

NEON_API_BASE = "https://console.neon.tech/api/v2"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _parse_expiry(raw: Any) -> datetime | None:
    """Parse an ISO timestamp returned by the provider, tolerating empty values."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("Unparsable expiry value: %r", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _direct_host(uri: str | None) -> str | None:
    """Neon hands out a pgbouncer host; this backend pools connections itself."""
    return uri.replace("-pooler", "") if uri else uri


class NeonProvider(DatabaseProvider):
    """Thin async client for the Neon API used by the maintenance controller."""

    def __init__(
        self,
        api_key: str,
        *,
        project_id: str = "",
        org_id: str = "",
        region_id: str = "aws-eu-central-1",
        pg_version: int = 17,
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("NEON_API_KEY is empty; the controller cannot call the provider")
        self._api_key = api_key
        self._project_id = project_id
        self._org_id = org_id
        self._region_id = region_id
        self._pg_version = pg_version
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._transport = transport

    # ------------------------------------------------------------------ plumbing
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=NEON_API_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Perform one API call, retrying only transient failures."""
        last_error: str = "unknown"
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._client() as client:
                    response = await client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
                if attempt == self._max_retries:
                    raise ProviderUnavailableError(f"{method} {path} failed: {last_error}") from exc
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code in _RETRY_STATUS:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if attempt == self._max_retries:
                    raise ProviderUnavailableError(f"{method} {path} failed: {last_error}")
                logger.warning(
                    "%s %s answered %s (attempt %s/%s), retrying",
                    method,
                    path,
                    response.status_code,
                    attempt,
                    self._max_retries,
                )
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code in (401, 403):
                text = response.text.lower()
                if "quota" in text or "limit" in text or "plan" in text:
                    raise ProviderQuotaError(f"{method} {path} blocked by plan limits: {response.text[:200]}")
                raise ProviderAuthError(f"{method} {path} rejected the API key: HTTP {response.status_code}")

            if response.status_code == 402:
                raise ProviderQuotaError(f"{method} {path} requires a paid plan: {response.text[:200]}")

            if response.status_code >= 400:
                raise ProviderError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:200]}")

            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError(f"{method} {path} returned invalid JSON") from exc

        raise ProviderUnavailableError(f"{method} {path} failed after retries: {last_error}")

    # ------------------------------------------------------------------ mapping
    @staticmethod
    def _instance_from(project: dict[str, Any], connection_uri: str | None = None) -> InstanceInfo:
        settings = project.get("settings") or {}
        expires = _parse_expiry(settings.get("quota_reset_at") or project.get("quota_reset_at"))
        state = str(project.get("state") or project.get("status") or "").lower()
        ready = bool(connection_uri) or state in {"ready", "active", "running"}
        return InstanceInfo(
            identifier=str(project.get("id", "")),
            name=str(project.get("name", "")),
            ready=ready,
            connection_string=connection_uri,
            expires_at=expires,
            raw=project,
        )

    # -------------------------------------------------------------------- API
    async def get_instance(self, identifier: str | None = None) -> InstanceInfo:
        """Return the configured project (or ``identifier``) with its connection string."""
        project_id = identifier or self._project_id
        if not project_id:
            raise ProviderError("NEON_PROJECT_ID is not configured")
        payload = await self._request("GET", f"/projects/{project_id}")
        project = payload.get("project", payload)
        connection_uri: str | None = None
        try:
            connection_uri = await self._connection_uri(project_id)
        except ProviderError as exc:
            logger.warning("Could not read the connection string of %s: %s", project_id, exc)
        return self._instance_from(project, _direct_host(connection_uri))

    async def _organisation(self) -> str:
        """Organisation id required by the project endpoints (resolved once)."""
        if self._org_id:
            return self._org_id
        payload = await self._request("GET", "/users/me/organizations")
        organizations = payload.get("organizations") or []
        if not organizations:
            raise ProviderError("No Neon organisation is visible to this API key")
        self._org_id = str(organizations[0].get("id") or "")
        if not self._org_id:
            raise ProviderError("The Neon organisation has no id")
        logger.info("Resolved Neon organisation %s", self._org_id)
        return self._org_id

    async def list_instances(self) -> list[InstanceInfo]:
        """Return every project visible to the API key."""
        payload = await self._request(
            "GET", "/projects", params={"org_id": await self._organisation()}
        )
        items = payload.get("projects", [])
        instances = []
        for entry in items:
            project = entry.get("project", entry)
            instances.append(self._instance_from(project))
        return instances

    async def create_instance(self, name: str) -> InstanceInfo:
        """Create a PostgreSQL project and return it including its connection URI."""
        body = {
            "project": {
                "name": name,
                "region_id": self._region_id,
                "pg_version": self._pg_version,
                "org_id": await self._organisation(),
            }
        }
        payload = await self._request("POST", "/projects", json=body)
        project = payload.get("project", {})
        uris = payload.get("connection_uris") or []
        connection_uri = None
        if uris:
            connection_uri = uris[0].get("connection_uri")
        logger.info("Created provider instance %s (%s)", project.get("id"), name)
        return self._instance_from(project, _direct_host(connection_uri))

    async def delete_instance(self, identifier: str) -> None:
        """Delete one project. Callers must have explicit authorisation."""
        await self._request("DELETE", f"/projects/{identifier}")
        logger.warning("Deleted provider instance %s", identifier)

    async def _connection_uri(self, project_id: str) -> str | None:
        """Fetch the pooled connection string of the default database."""
        payload = await self._request(
            "GET",
            f"/projects/{project_id}/connection_uri",
            params={"database_name": "neondb", "role_name": "neondb_owner"},
        )
        return _direct_host(payload.get("uri") or payload.get("connection_uri"))
