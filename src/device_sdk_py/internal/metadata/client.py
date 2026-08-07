# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
A lightweight Core Metadata (core-keeper) REST client used to self-register the Device
Service, load its pre-defined Profiles / Devices / Provision Watchers at startup and to
propagate the managed-entity writes (device/profile/watcher CRUD) at runtime.

This mirrors the ``app-functions-sdk-python`` metadata clients (`DeviceClient`,
`DeviceProfileClient`, `DeviceServiceClient`, `ProvisionWatcherClient`) but is self-contained - it
writes the Core Metadata v3 request envelopes directly rather than depending on the
``app_functions_sdk_py`` DTOs / request helpers (which are pinned to ``python<3.11``). It matches
the Go ``device-sdk-go`` bootstrap flow: ``selfRegister`` then ``provision.LoadProfiles`` /
``LoadDevices`` / ``LoadProvisionWatchers``.

Each call raises `MetadataError` on an unreachable service / non-2xx response. The
startup calls are best-effort (the caller ``DeviceService.initialize_resources`` logs and
continues so a missing or down Core Metadata never blocks startup); the runtime
write-back calls propagate the error so the DeviceService can roll back its local caches.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from. import dto as dto_serializers

__all__ = ["MetadataError", "MetadataClient"]

_LOGGER = logging.getLogger(__name__)

API_BASE = "/api/v3"


class MetadataError(RuntimeError):
    """Raised when a Core Metadata REST call fails (network or non-2xx response)."""


#: Route fragments, mirroring app_functions_sdk_py ``contracts/common/constants.py``.
_API_DEVICE_SERVICE_ROUTE = f"{API_BASE}/deviceservice"
_API_DEVICE_PROFILE_ROUTE = f"{API_BASE}/deviceprofile"
_API_DEVICE_ROUTE = f"{API_BASE}/device"
_API_PROVISION_WATCHER_ROUTE = f"{API_BASE}/provisionwatcher"
_ALL = "all"
_NAME = "name"


class MetadataClient:
    """Talk to Core Metadata (core-metadata / core-keeper) to self-register and load resources.

    Args:
        base_url: The base URL of Core Metadata, e.g. ``http://localhost:59881``.
        timeout: Per-request timeout in seconds.
        logger: Optional logger; defaults to the module logger.
    """

    def __init__(self, base_url: str, timeout: float = 10.0,
                 logger: Optional[logging.Logger] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._logger = logger or _LOGGER

    # -- low-level helpers ---------------------------------------------------

    def _url(self, route: str) -> str:
        return f"{self.base_url}{route}"

    def _post(self, route: str, payload: Any, params: Optional[dict] = None) -> Any:
        try:
            response = requests.post(self._url(route), json=payload, params=params,
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetadataError(f"POST {route} failed: {exc}") from exc
        return self._not_ok_raise(route, response)

    def _put(self, route: str, payload: Any, params: Optional[dict] = None) -> Any:
        try:
            response = requests.put(self._url(route), json=payload, params=params,
                                    timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetadataError(f"PUT {route} failed: {exc}") from exc
        return self._not_ok_raise(route, response)

    def _patch(self, route: str, payload: Any, params: Optional[dict] = None) -> Any:
        try:
            response = requests.patch(self._url(route), json=payload, params=params,
                                      timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetadataError(f"PATCH {route} failed: {exc}") from exc
        return self._not_ok_raise(route, response)

    def _get(self, route: str, params: Optional[dict] = None) -> Any:
        try:
            response = requests.get(self._url(route), params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetadataError(f"GET {route} failed: {exc}") from exc
        if response.status_code == 404:
            return None
        return self._not_ok_raise(route, response)

    def _delete(self, route: str, params: Optional[dict] = None) -> Any:
        try:
            response = requests.delete(self._url(route), params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise MetadataError(f"DELETE {route} failed: {exc}") from exc
        return self._not_ok_raise(route, response)

    def _not_ok_raise(self, route: str, response: "requests.Response") -> Any:
        if response.status_code < 200 or response.status_code >= 300:
            raise MetadataError(
                f"{route} returned HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------------
    # Device Service self-registration
    # ------------------------------------------------------------------------

    def device_service_by_name(self, name: str) -> Optional[dict]:
        """Return the DeviceService with the given name, or None if it does not exist."""
        data = self._get(f"{_API_DEVICE_SERVICE_ROUTE}/{_NAME}/{name}")
        if data is None:
            return None
        return data.get("service")

    def add_device_service(self, service: Dict[str, Any]) -> None:
        """Register a DeviceService (Core AdminService ``POST /api/v3/deviceservice``)."""
        self._post(_API_DEVICE_SERVICE_ROUTE,
                   [dto_serializers.add_device_service_request(service)])

    def update_device_service(self, service: Dict[str, Any]) -> None:
        """Update a DeviceService (Core UpdateDeviceService ``PATCH /api/v3/deviceservice``)."""
        payload = dto_serializers.add_device_service_request(service)
        self._patch(_API_DEVICE_SERVICE_ROUTE, [payload])

    # ------------------------------------------------------------------------
    # Device Profile endpoints
    # ------------------------------------------------------------------------

    def device_profile_by_name(self, name: str) -> Optional[dict]:
        """Return the existing DeviceProfile with the given name, or None if it does not exist."""
        data = self._get(f"{_API_DEVICE_PROFILE_ROUTE}/{_NAME}/{name}")
        if data is None:
            return None
        return data.get("profile")

    def add_device_profiles(self, profiles: List) -> None:
        """Add DeviceProfiles (``POST /api/v3/deviceprofile``).

        ``profiles`` is a list of DeviceProfile models.
        """
        requests_body = [dto_serializers.add_device_profile_request(p) for p in profiles]
        self._post(_API_DEVICE_PROFILE_ROUTE, requests_body)

    def add_device_profile(self, profile) -> Optional[str]:
        """Add a single DeviceProfile (``POST /api/v3/deviceprofile``).

        Returns:
            The profile id assigned by Core Metadata, or ``None`` if not in response.
        """
        resp = self._post(_API_DEVICE_PROFILE_ROUTE,
                   [dto_serializers.add_device_profile_request(profile)])
        if resp and isinstance(resp, list) and resp:
            return resp[0].get("id")
        return None

    def update_device_profile(self, profile) -> None:
        """Update a DeviceProfile (``PUT /api/v3/deviceprofile``)."""
        self._put(_API_DEVICE_PROFILE_ROUTE,
                  [dto_serializers.add_device_profile_request(profile)])

    def delete_device_profile(self, name: str) -> None:
        """Delete a DeviceProfile by name (``DELETE /api/v3/deviceprofile/name/{name}``)."""
        self._delete(f"{_API_DEVICE_PROFILE_ROUTE}/{_NAME}/{name}")

    # ------------------------------------------------------------------------
    # Device endpoints
    # ------------------------------------------------------------------------

    def device_by_name(self, name: str) -> Optional[dict]:
        """Return the existing Device with the given name, or ``None`` if it does not exist."""
        data = self._get(f"{_API_DEVICE_ROUTE}/{_NAME}/{name}")
        if data is None:
            return None
        return data.get("device")

    def add_devices(self, devices: List) -> None:
        """Add Devices (``POST /api/v3/device``).

Devices are added with ``bypassValidation=true``. In Go the validation round-trip is
        answered by the Device Service's message-bus subscription
        (``messaging.SubscribeDeviceValidation``); until that port is present, skipping
        validation keeps startup registration working exactly like the Go
        ``AddDeviceWithoutValidation`` path.
        """
        _body = [dto_serializers.add_device_request(d) for d in devices]
        self._post(_API_DEVICE_ROUTE, _body, params={"bypassValidation": "true"})

    def add_device(self, device, bypass_validation: bool = False) -> Optional[str]:
        """Add a single Device (``POST /api/v3/device``).

        Mirrors the Go Device client: the ``bypassValidation`` query parameter is always
        sent, telling Core Metadata whether to skip the device validation round-trip to
        this Device Service.

        Returns:
            The device id assigned by Core Metadata, or ``None`` if not in response.
        """
        _body = [dto_serializers.add_device_request(device)]
        params = {"bypassValidation": "true" if bypass_validation else "false"}
        resp = self._post(_API_DEVICE_ROUTE, _body, params=params)
        if resp and isinstance(resp, list) and resp:
            return resp[0].get("id")
        return None

    # --------------------------------------------------------------------
    # ProvisionWatcher endpoints
    # --------------------------------------------------------------------

    def provision_watcher_by_name(self, name: str) -> Optional[dict]:
        """Return the existing ProvisionWatcher with the given name, or ``None``."""
        data = self._get(f"{_API_PROVISION_WATCHER_ROUTE}/{_NAME}/{name}")
        if data is None:
            return None
        return data.get("provisionWatcher")

    def add_provision_watchers(self, watchers: List) -> None:
        """Add ProvisionWatchers (``POST /api/v3/provisionwatcher``)."""
        _body = [dto_serializers.add_provision_watcher_request(w) for w in watchers]
        self._post(_API_PROVISION_WATCHER_ROUTE, _body)

    def add_provision_watcher(self, watcher) -> Optional[str]:
        """Add a single ProvisionWatcher (``POST /api/v3/provisionwatcher``).

        Returns:
            The watcher id assigned by Core Metadata, or ``None`` if not in response.
        """
        resp = self._post(_API_PROVISION_WATCHER_ROUTE,
                   [dto_serializers.add_provision_watcher_request(watcher)])
        if resp and isinstance(resp, list) and resp:
            return resp[0].get("id")
        return None

    def update_provision_watcher(self, watcher) -> None:
        """Update a ProvisionWatcher (``PATCH /api/v3/provisionwatcher``).

        Only the updateable fields of the Watcher are sent, matching Go's
        ``UpdateProvisionWatcher``.
        """
        _body = [dto_serializers.update_provision_watcher_request(watcher)]
        self._patch(_API_PROVISION_WATCHER_ROUTE, _body)

    def delete_provision_watcher(self, name: str) -> None:
        """Delete a ProvisionWatcher by name
        (``DELETE /api/v3/provisionwatcher/name/{name}``)."""
        self._delete(f"{_API_PROVISION_WATCHER_ROUTE}/{_NAME}/{name}")

    def patch_device(self, name: str, updates: Dict[str, Any],
                     bypass_validation: bool = False) -> None:
        """Patch a Device (``PATCH /api/v3/device``).

        Uses the collection route with an `UpdateDeviceRequest` body containing only the
        non-None ``updates`` (serialized snake_case -> camelCase). ``bypassValidation`` is
        sent like Go's ``UpdateWithQueryParams``.
        """
        _body = [dto_serializers.update_device_request(name, updates)]
        params = {"bypassValidation": "true" if bypass_validation else "false"}
        self._patch(_API_DEVICE_ROUTE, _body, params=params)

    def delete_device(self, name: str) -> None:
        """Delete a Device by name (``DELETE /api/v3/device/name/{name}``)."""
        self._delete(f"{_API_DEVICE_ROUTE}/{_NAME}/{name}")


def client_from_base_url(base_url: str, timeout: float = 10.0,
                         logger: Optional[logging.Logger] = None) -> MetadataClient:
    """Convenience factory mirroring the app-functions-sdk-python client constructors."""
    return MetadataClient(base_url=base_url, timeout=timeout, logger=logger)
