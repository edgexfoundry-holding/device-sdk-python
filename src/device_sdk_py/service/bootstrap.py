# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The Device Service bootstrap.

A ``Bootstrap`` owns the lifecycle of a Device Service:
  1. it initializes the internal caches (Devices / DeviceProfiles / ProvisionWatchers),
  2. it creates the ``DeviceService`` implementation,
  3. it defers to ``DeviceService.run()`` for the actual HTTP / AutoEvent / driver startup.

The Core Metadata side of each step is a placeholder (the app-functions-sdk-python
metadata clients are ported in a later phase); the cache is seeded empty and the
``DeviceService`` model methods are the same stubs used by ``device_service.py``.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ..internal.cache import (
    create_device_cache,
    create_profile_cache,
    create_provision_watcher_cache,
)
from ..service.device_service import DeviceService, create_device_service

__all__ = [
    "Bootstrap",
    "bootstrap",
    "run",
]

_logger = logging.getLogger(__name__)


class Bootstrap:
    """The SDK bootstrap container.

    Holds the service identity, the ProtocolDriver and the optional configuration /
    logger, and creates the ``DeviceService`` lazily via :meth:`initialize`.
    """

    def __init__(self, service_key: str, service_version: str, driver: Any,
                 configuration: Any = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.service_key = service_key
        self.service_version = service_version
        self.driver = driver
        self.configuration = configuration
        self._logger = logger or _logger
        #: The lazily created DeviceService, populated by :meth:`initialize`.
        self.device_service: Optional[DeviceService] = None

    def initialize_caches(self, devices: Optional[List[Any]] = None,
                          profiles: Optional[List[Any]] = None,
                          watchers: Optional[List[Any]] = None) -> None:
        """Initialize the internal cache singletons.

        The lists are seeded empty when not provided since the Core Metadata clients are
        not ported yet (they are normally fetched from Core Metadata during bootstrap).
        """
        self._logger.debug("Initializing Device cache")
        create_device_cache(devices or [])
        self._logger.debug("Initializing DeviceProfile cache")
        create_profile_cache(profiles or [])
        self._logger.debug("Initializing ProvisionWatcher cache")
        create_provision_watcher_cache(watchers or [])

    def initialize(self) -> DeviceService:
        """Initialize the caches, create the ``DeviceService`` and load its resources.

        Returns:
            The created DeviceService.
        """
        self.initialize_caches()
        self.device_service = create_device_service(
            self.service_key,
            self.service_version,
            self.driver,
            configuration=self.configuration,
            logger=self._logger,
        )
        # Load the pre-defined DeviceProfiles / Devices / ProvisionWatchers shipped under
        # the res tree before the service starts serving requests.
        self.device_service.initialize_resources()
        self._logger.debug("DeviceService %s (v%s) bootstrapped",
                           self.service_key, self.service_version)
        return self.device_service


def bootstrap(service_key: str, service_version: str, driver: Any,
              configuration: Any = None,
              logger: Optional[logging.Logger] = None) -> DeviceService:
    """Create the Bootstrap container, initialize it and return the DeviceService.

    Args:
        service_key: Service identifier (used as a topic / API prefix).
        service_version: Service version string.
        driver: The ProtocolDriver implementation.
        configuration: Optional service configuration object.
        logger: Optional logger; defaults to the module logger.

    Returns:
        The initialized DeviceService.
    """
    logger = logger or _logger
    logger.debug("Bootstrapping Device Service %s (v%s)", service_key, service_version)
    bs = Bootstrap(service_key, service_version, driver, configuration, logger)
    return bs.initialize()


def run(service_key: str, service_version: str, driver: Any,
        configuration: Any = None,
        logger: Optional[logging.Logger] = None) -> None:
    """Create the DeviceService via the bootstrap and start it (blocking).

    This is the typical ``main()`` entry point for a device service.
    """
    device_service = bootstrap(service_key, service_version, driver, configuration, logger)
    device_service.run()
