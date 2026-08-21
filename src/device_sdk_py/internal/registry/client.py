# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""Core Keeper registry REST client.

Mirrors ``go-mod-bootstrap`` ``bootstrap/registration/registry.go`` and the
``go-mod-registry`` keeper implementation: services POST their registration
(serviceId/host/port/healthCheck) to ``/api/v3/registry`` on startup, Core Keeper
probes the health-check path and tracks UP/DOWN status, and the service DELETEs
its registration on shutdown.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

__all__ = ["CoreKeeperRegistryClient", "RegistryError"]

_LOGGER = logging.getLogger(__name__)

_API_PING = "/api/v3/ping"
_API_REGISTRY = "/api/v3/registry"
_API_REGISTRY_SERVICE = f"{_API_REGISTRY}/serviceId/{{service_id}}"

#: Default startup registration deadline (Go startup.Timer default is 60s).
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 60.0


class RegistryError(RuntimeError):
    """Raised when a Core Keeper registry REST call fails."""


def _parse_duration(value: str, default: float) -> float:
    """Parse a Go-style duration string (``10s``, ``1m``) into seconds."""
    if not value:
        return default
    text = value.strip()
    multipliers = {"ns": 1e-9, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}
    for suffix, factor in multipliers.items():
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                break
    try:
        return float(text)
    except ValueError:
        return default


class CoreKeeperRegistryClient:
    """Register / deregister this service with the Core Keeper registry.

    Args:
        host: Core Keeper host (``Registry.Host``).
        port: Core Keeper port (``Registry.Port``).
        service_id: This service's key (``ServiceKey``).
        service_host: The host this service is reachable on (advertised host).
        service_port: The port this service is reachable on.
        check_interval: Health check interval (Go duration string, ``Registry``-driven
            via ``Service.HealthCheckInterval``).
        check_route: Health check path; defaults to the EdgeX ping route.
        timeout: Per-request timeout in seconds.
        logger: Optional logger.
    """

    def __init__(
        self,
        host: str,
        port: int,
        service_id: str,
        service_host: str,
        service_port: int,
        check_interval: str = "10s",
        check_route: str = _API_PING,
        timeout: float = 5.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._base_url = f"http://{host}:{port}"
        self._service_id = service_id
        self._service_host = service_host
        self._service_port = service_port
        self._check_interval = check_interval
        self._check_route = check_route
        self._timeout = timeout
        self._logger = logger or _LOGGER

    # -- Mirrors go-mod-registry Client.IsAlive ------------------------------------------

    def is_alive(self) -> bool:
        """Return True when the registry answers its ping route."""
        try:
            response = requests.get(f"{self._base_url}{_API_PING}", timeout=self._timeout)
            return response.status_code == 200
        except requests.RequestException as exc:
            self._logger.debug("Registry ping failed: %s", exc)
            return False

    # -- Mirrors go-mod-registry Client.Register -----------------------------------------

    def register(self) -> bool:
        """Register this service; returns True on success, raises RegistryError on HTTP error."""
        payload = {
            "apiVersion": "v3",
            "registration": {
                "serviceId": self._service_id,
                "host": self._service_host,
                "port": self._service_port,
                "healthCheck": {
                    "interval": self._check_interval,
                    "path": self._check_route,
                    "type": "http",
                },
            },
        }
        try:
            response = requests.post(
                f"{self._base_url}{_API_REGISTRY}", json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise RegistryError(f"registry unreachable: {exc}") from exc
        if response.status_code not in (200, 201):
            raise RegistryError(
                f"register failed with status {response.status_code}: {response.text[:200]}"
            )
        self._logger.info(
            "Registered service %s with registry at %s (host=%s port=%s)",
            self._service_id, self._base_url, self._service_host, self._service_port,
        )
        return True

    # -- Mirrors go-mod-bootstrap RegisterWithRegistry retry loop ------------------------

    def register_with_retry(
        self,
        startup_timeout: float = _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        stop_event=None,
    ) -> bool:
        """Attempt registration until success or the startup deadline elapses.

        Mirrors ``RegisterWithRegistry``: waits for the registry to be alive, then
        registers, retrying every health-check interval until the startup timer
        expires. Returns True when registered, False when the deadline elapsed.
        """
        deadline = time.monotonic() + startup_timeout
        interval = max(_parse_duration(self._check_interval, 10.0), 1.0)
        last_error: Optional[str] = None
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            if not self.is_alive():
                last_error = "registry is not available"
            else:
                try:
                    self.register()
                    return True
                except RegistryError as exc:
                    last_error = str(exc)
            self._logger.warning("Could not register with registry: %s; retrying...", last_error)
            if stop_event is not None:
                stop_event.wait(min(interval, max(deadline - time.monotonic(), 0.1)))
            else:
                time.sleep(min(interval, max(deadline - time.monotonic(), 0.1)))
        self._logger.error("Unable to register with registry in allotted time: %s", last_error)
        return False

    # -- Mirrors go-mod-registry Client.Unregister ---------------------------------------

    def deregister(self) -> bool:
        """Deregister this service; best-effort, logs failures instead of raising."""
        url = _API_REGISTRY_SERVICE.format(service_id=self._service_id)
        try:
            response = requests.delete(f"{self._base_url}{url}", timeout=self._timeout)
            if response.status_code in (200, 202, 204):
                self._logger.info("Deregistered service %s from registry", self._service_id)
                return True
            self._logger.warning(
                "Deregister %s failed with status %d: %s",
                self._service_id, response.status_code, response.text[:200],
            )
        except requests.RequestException as exc:
            self._logger.warning("Deregister %s failed: %s", self._service_id, exc)
        return False
