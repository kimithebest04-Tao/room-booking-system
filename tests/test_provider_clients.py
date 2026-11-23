"""Offline checks for the two third-party clients used by the maintenance flow.

Both clients talk to real HTTP APIs, so the tests inject an ``httpx.MockTransport``
instead of the network and assert on the exact requests that would be sent.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import httpx

from backend.db import _normalise_url
from maintenance.neon_provider import NeonProvider
from maintenance.render_client import RenderDeployer


def _run(coro):
    return asyncio.run(coro)


def _deployer(handler: Callable[[httpx.Request], httpx.Response]) -> RenderDeployer:
    return RenderDeployer("render-key", "srv-test", transport=httpx.MockTransport(handler))


def test_render_env_var_update_uses_single_variable_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v1/services/srv-test/env-vars/DATABASE_URL"
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"key": "DATABASE_URL", "value": "postgresql://new"})

    _run(_deployer(handler).update_env_var("DATABASE_URL", "postgresql://new"))

    assert captured["body"] == {"value": "postgresql://new"}


def test_render_env_var_update_merges_instead_of_wiping_other_values() -> None:
    """If the single-variable route is unavailable the merge must keep every key."""

    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/env-vars/DATABASE_URL"):
            return httpx.Response(404, json={"message": "not found"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"envVar": {"key": "DATABASE_URL", "value": "postgresql://old"}},
                    {"envVar": {"key": "ADMIN_TOKEN", "value": "keep-me"}},
                ],
            )
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _run(_deployer(handler).update_env_var("DATABASE_URL", "postgresql://new"))

    assert sent["body"] == [
        {"key": "DATABASE_URL", "value": "postgresql://new"},
        {"key": "ADMIN_TOKEN", "value": "keep-me"},
    ]


def test_neon_create_instance_returns_a_ready_connection_string() -> None:
    calls: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.url.params.get("org_id")))
        if request.url.path == "/api/v2/users/me/organizations":
            return httpx.Response(200, json={"organizations": [{"id": "org-1", "name": "demo"}]})
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["project"]["region_id"] == "aws-eu-central-1"
        assert payload["project"]["org_id"] == "org-1"
        return httpx.Response(
            200,
            json={
                "project": {"id": "proj-1", "name": "room-booking-prod"},
                "connection_uris": [
                    {"connection_uri": "postgresql://user:pass@ep-1-pooler.example/neondb"}
                ],
            },
        )

    provider = NeonProvider("neon-key", transport=httpx.MockTransport(handler))
    instance = _run(provider.create_instance("room-booking-prod"))

    assert calls[0][1] == "/api/v2/users/me/organizations"
    assert calls[1][1] == "/api/v2/projects"
    assert instance.identifier == "proj-1"
    assert instance.ready is True
    # The pooled hostname is rewritten to the direct host before it is ever used.
    assert instance.connection_string == "postgresql://user:pass@ep-1.example/neondb"


def test_provider_connection_strings_use_the_installed_postgres_driver() -> None:
    """A raw ``postgresql://`` string from Neon must not reach psycopg2."""
    assert _normalise_url("postgresql://user:pass@host/neondb?sslmode=require").startswith(
        "postgresql+psycopg://"
    )
    assert _normalise_url("postgres://user:pass@host/db") == "postgresql+psycopg://user:pass@host/db"
    assert _normalise_url("sqlite:///./data/app.db") == "sqlite:///./data/app.db"
