# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The Device Service bootstrap - ported from `device-sdk-go/service/bootstrap.go` together
with `service.go` (`NewDeviceService`).

The Bootstrap container owns the lifecycle of a Device Service:
  1. it initializes the internal caches (Devices / DeviceProfiles / ProvisionWatchers),
  2. it creates the `DeviceService` implementation,
  3. it defers to `DeviceService.run()` for the actual HTTP / AutoEvent / driver startup.

The Core Metadata side of each step is a placeholder (the app-functions-sdk-python
metadata clients are ported in a later phase); the cache is seeded empty and the
`DeviceService` model methods are the same stubs used by `device_service.py`.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ..internal.cache import (
    new_device_cache,
    new_profile_cache,
    new_provision_watcher_cache,
)
from ..service.device_service import DeviceService, new_device_service

__all__ = [
    "Bootstrap",
    "NewBootstrap",
    "new_bootstrap",
    "bootstrap",
    "run",
]

_logger = logging.getLogger(__name__)


class Bootstrap:
    """The SDK bootstrap container.

    Mirrors Go `service.Bootstrap` in bootstrap.go: it holds the service identity, the
    ProtocolDriver and the optional configuration / logger, and creates the
    `DeviceService` on `bootstrap_handler`.
    """

    def __init__(self, service_key: str, service_version: str, driver: Any,
                 configuration: Any = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.service_key = service_key
        self.service_version = service_version
        self.driver = driver
        self.configuration = configuration
        self._logger = logger or _logger
        #: The lazily created DeviceService, populated by `bootstrap_handler`.
        self.device_service: Optional[DeviceService] = None

    def initialize_caches(self, devices: Optional[List[Any]] = None,
                          profiles: Optional[List[Any]] = None,
                          watchers: Optional[List[Any]] = None) -> None:
        """Initialize the internal cache singletons.

        Mirrors the Go bootstrap which calls `cache.NewDeviceCache(...)`,
        `cache.NewDeviceProfileCache(...)` and `cache.NewProvisionWatcherCache(...)`.
        The lists are seeded empty when not provided since the Core Metadata clients are
        not ported yet (they are normally fetched from Core Metadata during bootstrap).
        """
        self._logger.debug("Initializing Device cache")
        new_device_cache(devices or [])
        self._logger.debug("Initializing DeviceProfile cache")
        new_profile_cache(profiles or [])
        self._logger.debug("Initializing ProvisionWatcher cache")
        new_provision_watcher_cache(watchers or [])

    def bootstrap_handler(self, dic: Any = None) -> DeviceService:
        """The bootstrap handler entry point.

        Mirrors Go `(*Bootstrap).BootstrapHandler(dic)` in bootstrap.go: the cache
        singletons are initialized first, then the `DeviceService` is created via
        `NewDeviceService`.  The `dic` argument is accepted for interface parity with the
        go-mod-bootstrap BootstrapHandler but is not used yet (the DI container is reused
        from app-functions-sdk-python in a later phase).

        Returns:
            The created DeviceService.
        """
        self.initialize_caches()
        self.device_service = new_device_service(
            self.service_key,
            self.service_version,
            self.driver,
            configuration=self.configuration,
            logger=self._logger,
        )
        # Load the pre-defined DeviceProfiles / Devices / ProvisionWatchers shipped under
        # the res tree (mirrors Go bootstrap which populates caches before Run()).
        self.device_service.initialize_resources()
        self._logger.debug("DeviceService %s (v%s) bootstrapped",
                           self.service_key, self.service_version)
        return self.device_service

    def boot(self, dic: Any = None) -> DeviceService:
        """Alias of `bootstrap_handler` kept for parity with the go-mod-bootstrap
        `Bootstrap.Boot` naming."""
        return self.bootstrap_handler(dic)


# PascalCase alias kept for parity with the Go exported identifier.
NewBootstrap = Bootstrap


def new_bootstrap(service_key: str, service_version: str, driver: Any,
                  configuration: Any = None,
                  logger: Optional[logging.Logger] = None) -> Bootstrap:
    """Create a new Bootstrap container for the given service.

    Python counterpart of `service.NewBootstrap(...)` in bootstrap.go.
    """
    return Bootstrap(service_key, service_version, driver, configuration, logger)


def bootstrap(service_key: str, service_version: str, driver: Any,
              configuration: Any = None,
              logger: Optional[logging.Logger] = None) -> DeviceService:
    """Convenience entry point: create the Bootstrap container, run its bootstrap handler
    and return the resulting DeviceService.

    This is the Python equivalent of the device-sdk-go bootstrap flow where
    `NewBootstrap(...).BootstrapHandler(dic)` is invoked before `DeviceService.Run()`.
    """
    logger = logger or _logger
    logger.debug("Boostrapping Device Service %s (v%s)", service_key, service_version)
    bs = new_bootstrap(service_key, service_version, driver, configuration, logger)
    return bs.bootstrap_handler()


def run(service_key: str, service_version: str, driver: Any,
        configuration: Any = None,
        logger: Optional[logging.Logger] = None) -> None:
    """Create the DeviceService via the bootstrap and start it (blocking).

    Mirrors a device service `main()` which calls `NewBootstrap(...).BootstrapHandler()`
    and then `deviceService.Run()`.
    """
    device_service = bootstrap(service_key, service_version, driver, configuration, logger)
    device_service.run()
