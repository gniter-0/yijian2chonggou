"""Non-authoritative registration support for delivery adapters.

The P6 delivery contract remains the authority in
``examcard.gates.release.delivery_v2``.  This module only keeps an in-memory
mapping from a backend name to a factory and its assembly-time capabilities.
It deliberately has no persistence, identity generation, retry policy,
receipt creation, or checkpoint access.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..gates.release.delivery_v2 import DeliveryAdapter

DeliveryBackendFactory: TypeAlias = Callable[..., DeliveryAdapter]


@dataclass(frozen=True, slots=True)
class DeliveryBackendCapabilities:
    """Assembly-time capabilities; never persisted as delivery authority."""

    supports_lookup: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryBackendRegistration:
    """One non-authoritative backend factory registration."""

    backend_type: str
    factory: DeliveryBackendFactory
    capabilities: DeliveryBackendCapabilities


class DeliveryBackendRegistryV1:
    """An explicit, process-local registry for P6-compatible adapters."""

    def __init__(self) -> None:
        self._registrations: dict[str, DeliveryBackendRegistration] = {}

    def register(
        self,
        backend_type: str,
        factory: DeliveryBackendFactory,
        *,
        capabilities: DeliveryBackendCapabilities | None = None,
    ) -> DeliveryBackendRegistration:
        """Register one factory without replacing an existing registration."""

        if not backend_type or backend_type.strip() != backend_type:
            raise ValueError("backend_type must be a non-empty, trimmed string")
        if not callable(factory):
            raise TypeError("delivery backend factory must be callable")
        if backend_type in self._registrations:
            raise ValueError(f"delivery backend is already registered: {backend_type}")
        registration = DeliveryBackendRegistration(
            backend_type=backend_type,
            factory=factory,
            capabilities=capabilities or DeliveryBackendCapabilities(),
        )
        self._registrations[backend_type] = registration
        return registration

    def registration(self, backend_type: str) -> DeliveryBackendRegistration:
        """Return an explicit registration or raise for an unknown backend."""

        try:
            return self._registrations[backend_type]
        except KeyError as error:
            raise KeyError(f"unknown delivery backend: {backend_type}") from error

    def create(self, backend_type: str, **kwargs: Any) -> DeliveryAdapter:
        """Create an adapter through its registered factory.

        The returned object is checked only for the P6 ``deliver`` surface.
        P6 remains responsible for invoking it, normalizing its result, and
        creating any authoritative receipt.
        """

        registration = self.registration(backend_type)
        adapter = registration.factory(**kwargs)
        if not callable(getattr(adapter, "deliver", None)):
            raise TypeError("delivery backend factory did not return a P6 adapter")
        if registration.capabilities.supports_lookup and not callable(
            getattr(adapter, "lookup", None)
        ):
            raise TypeError("delivery backend declares lookup support but has no lookup()")
        return adapter

    def backend_types(self) -> tuple[str, ...]:
        """Return registered backend names in registration order."""

        return tuple(self._registrations)


__all__ = (
    "DeliveryBackendCapabilities",
    "DeliveryBackendFactory",
    "DeliveryBackendRegistration",
    "DeliveryBackendRegistryV1",
)
