"""Provider abstraction for the database instance replacement workflow.

The controller talks to *one* database provider through this interface, so the
replacement logic stays testable with simulated API responses: the unit tests
inject a fake implementation that answers with recorded provider payloads and
never touches the network.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderError(RuntimeError):
    """Base class for provider API failures."""


class ProviderUnavailableError(ProviderError):
    """Temporary failure: network problem, timeout, 429 or 5xx response."""


class ProviderAuthError(ProviderError):
    """The provider rejected the credentials; retrying cannot help."""


class ProviderQuotaError(ProviderError):
    """A quota or plan limit blocks the request; never auto-upgrade a plan."""


@dataclass(frozen=True)
class InstanceInfo:
    """Normalised view of one database instance managed by the provider."""

    identifier: str
    name: str
    ready: bool
    connection_string: str | None = None
    expires_at: datetime | None = None
    raw: Mapping[str, Any] | None = None


class DatabaseProvider(ABC):
    """Minimal interface the controller needs from a database provider."""

    @abstractmethod
    async def get_instance(self, identifier: str | None = None) -> InstanceInfo:
        """Return the current instance (the configured one when ``identifier`` is None)."""

    @abstractmethod
    async def list_instances(self) -> list[InstanceInfo]:
        """Return every instance visible to the account."""

    @abstractmethod
    async def create_instance(self, name: str) -> InstanceInfo:
        """Create a new instance and return it with its connection string."""

    @abstractmethod
    async def delete_instance(self, identifier: str) -> None:
        """Delete one instance (only after explicit authorisation)."""


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    description: str,
    attempts: int = 3,
    base_delay: float = 0.5,
    retry_on: tuple[type[Exception], ...] = (ProviderUnavailableError,),
) -> T:
    """Run ``operation`` with a *bounded* number of retries.

    Only the transient error types listed in ``retry_on`` are retried; quota and
    authentication errors are raised immediately so that a paid plan can never
    be bought by accident (section 6.4).
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except retry_on as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (attempt %s/%s): %s - retrying in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_error is not None
    logger.error("%s failed after %s attempts: %s", description, attempts, last_error)
    raise last_error
