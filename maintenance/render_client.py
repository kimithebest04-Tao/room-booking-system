"""Render deployment control used by the "switch the backend" step.

The connection setting (``DATABASE_URL``) is changed through the Render API and
a deployment is triggered and polled until it finishes, so the switch is
verifiable rather than a manual console click.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .provider import ProviderAuthError, ProviderError, ProviderUnavailableError

logger = logging.getLogger(__name__)

RENDER_API_BASE = "https://api.render.com/v1"
_RETRY_STATUS = {429, 500, 502, 503, 504}

SUCCESS_STATUSES = {"live"}
FAILURE_STATUSES = {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}


class RenderDeployer:
    """Async client for the small part of the Render API the workflow needs."""

    def __init__(
        self,
        api_key: str,
        service_id: str,
        *,
        base_url: str = RENDER_API_BASE,
        timeout: float = 15.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("RENDER_API_KEY is empty; the controller cannot redeploy")
        if not service_id:
            raise ProviderError("RENDER_SERVICE_ID is empty")
        self._api_key = api_key
        self._service_id = service_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._client() as client:
                    response = await client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise ProviderUnavailableError(f"{method} {path} failed: {exc}") from exc
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code in _RETRY_STATUS:
                if attempt == self._max_retries:
                    raise ProviderUnavailableError(f"{method} {path} failed: HTTP {response.status_code}")
                await asyncio.sleep(0.5 * attempt)
                continue
            if response.status_code in (401, 403):
                raise ProviderAuthError(f"Render rejected the API key on {method} {path}")
            if response.status_code >= 400:
                raise ProviderError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:200]}")
            if not response.content:
                return {}
            return response.json()
        raise ProviderUnavailableError(f"{method} {path} failed after retries")

    # ------------------------------------------------------------------- env
    @staticmethod
    def _flatten_env_vars(items: Any) -> list[tuple[str, str | None]]:
        """Normalise the two list shapes Render returns into ``(key, value)`` pairs."""
        pairs: list[tuple[str, str | None]] = []
        for entry in items if isinstance(items, list) else []:
            env = entry.get("envVar", entry) if isinstance(entry, dict) else {}
            if "key" in env:
                pairs.append((str(env["key"]), env.get("value")))
        return pairs

    async def update_env_var(self, key: str, value: str) -> None:
        """Set one environment variable of the backend service."""
        try:
            await self._request("PUT", f"/services/{self._service_id}/env-vars/{key}", json={"value": value})
            logger.info("Updated environment variable %s on Render service %s", key, self._service_id)
            return
        except ProviderError as exc:
            if "HTTP 404" not in str(exc):
                raise
            logger.warning("Single-variable endpoint unavailable; merging into the full list instead")

        # Fallback: this endpoint replaces the whole list, so read it first and
        # merge, otherwise the other variables (including secrets) would vanish.
        items = await self._request("GET", f"/services/{self._service_id}/env-vars")
        variables = self._flatten_env_vars(items)
        if any(existing_key != key and existing_value is None for existing_key, existing_value in variables):
            raise ProviderError(
                "Cannot read the existing environment values; refusing to overwrite the whole list"
            )
        merged = [
            {"key": existing_key, "value": value if existing_key == key else existing_value}
            for existing_key, existing_value in variables
        ]
        if not any(item["key"] == key for item in merged):
            merged.append({"key": key, "value": value})
        await self._request("PUT", f"/services/{self._service_id}/env-vars", json=merged)
        logger.info("Updated environment variable %s by replacing the merged list", key)

    # ----------------------------------------------------------------- deploy
    async def trigger_deploy(self) -> str:
        """Start a new deployment and return its identifier."""
        payload = await self._request("POST", f"/services/{self._service_id}/deploys", json={"clearCache": "do_not_clear"})
        deploy = payload.get("deploy", payload)
        deploy_id = str(deploy.get("id", ""))
        if not deploy_id:
            raise ProviderError("Render did not return a deploy id")
        logger.info("Triggered Render deploy %s", deploy_id)
        return deploy_id

    async def get_deploy(self, deploy_id: str) -> dict[str, Any]:
        """Return the current status document of one deployment."""
        payload = await self._request("GET", f"/services/{self._service_id}/deploys/{deploy_id}")
        return payload.get("deploy", payload)

    async def wait_for_deploy(
        self, deploy_id: str, *, timeout_seconds: float = 900.0, poll_seconds: float = 15.0
    ) -> str:
        """Poll until the deployment is live or failed; bounded by ``timeout_seconds``."""
        waited = 0.0
        last_status = "unknown"
        while waited < timeout_seconds:
            deploy = await self.get_deploy(deploy_id)
            last_status = str(deploy.get("status", last_status))
            logger.info("Deploy %s status=%s (%.0fs elapsed)", deploy_id, last_status, waited)
            if last_status in SUCCESS_STATUSES:
                return last_status
            if last_status in FAILURE_STATUSES:
                raise ProviderError(f"Deployment {deploy_id} finished with status {last_status}")
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
        raise ProviderUnavailableError(
            f"Deployment {deploy_id} did not finish within {timeout_seconds:.0f}s (last status {last_status})"
        )
