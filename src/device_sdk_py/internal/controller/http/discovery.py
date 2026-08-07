# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/controller/http/discovery.go`.

`DiscoveryController` provides the handlers registered for the POST
`/api/v3/discovery`, POST `/api/v3/profilescan`, DELETE `/api/v3/discovery`,
DELETE `/api/v3/discovery/{requestId}` and DELETE
`/api/v3/profilescan/device/{name}` routes. The `discovery` handler checks the Device
Service AdminState and the discovery configuration, then triggers the ProtocolDriver's
`discover()` in a background thread and acknowledges with an Accepted response. The
`profile_scan` handler validates the request against the DeviceService / caches and
defers the actual scanning to a handler hook.

The Go handlers run `autodiscovery.DiscoveryWrapper` / `application.ProfileScanWrapper`
and `autodiscovery.StopDeviceDiscovery` / `application.StopProfileScan` through the DI
container; the Python port invokes the hooks wired on the `RestController` (see
`router.py`) so that the handlers are functional before those modules are ported.
"""

from __future__ import annotations

import threading
import urllib.parse
from http import HTTPStatus
from typing import Any, Dict, List

from starlette.requests import Request
from starlette.responses import Response

from ...cache import ADMIN_STATE_LOCKED, Devices, Profiles
from ...common.consts import (
    API_DISCOVERY_BY_ID_ROUTE,
    API_DISCOVERY_ROUTE,
    API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE,
    API_PROFILE_SCAN_ROUTE,
    NAME,
    REQUEST_ID,
)
from ...common.utils import (
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_IMPLEMENTED,
    KIND_SERVICE_LOCKED,
    KIND_SERVICE_UNAVAILABLE,
    KIND_STATUS_CONFLICT,
    EdgexError,
    current_time_millis,
    create_edgx_error,
)
from._utils import (
    base_response,
    correlation_id_from_request,
    parse_request_body,
    send_edgx_error,
    send_edgx_error_with_request_id,
    send_response,
)


class DiscoveryController:
    """The discovery / profile scan handlers of the REST controller."""

    # -- helpers -------------------------------------------------------------

    def _service_admin_state_locked(self) -> bool:
        """Return True when the DeviceService AdminState is Locked.

        In
The `device_service` may not be wired yet, in which case the state
        is treated as unlocked.
        """
        return self.device_service is not None and \
            getattr(self.device_service, "admin_state", None) == ADMIN_STATE_LOCKED

    def _discovery_enabled(self) -> bool:
        """Return True when device discovery is enabled in the configuration.

        In
The Python configuration model is ported in a later phase, so the
        setting is read defensively and discovery is disabled when it is not set.
        """
        configuration = self.configuration
        device = getattr(configuration, "device", None) if configuration else None
        if device is None:
            return False
        discovery = getattr(device, "discovery", None)
        return bool(getattr(discovery, "enabled", False))

    # -- discovery -----------------------------------------------------------

    def discovery(self, request: Request) -> Response:
        """Handle the POST `/api/v3/discovery` request.

        : checks
        the DeviceService AdminState and the discovery configuration, triggers the
        ProtocolDriver's `discover()` in a background thread and acknowledges with an
        Accepted response carrying the correlation id as request id.

        Also publishes discovery progress system events (0% start, 100% complete, -1% error).
        """
        correlation_id = correlation_id_from_request(request)

        if self._service_admin_state_locked():
            err = create_edgx_error(KIND_SERVICE_LOCKED, "service locked")
            return send_edgx_error(request, err, API_DISCOVERY_ROUTE)

        if not self._discovery_enabled():
            err = create_edgx_error(KIND_SERVICE_UNAVAILABLE, "device discovery disabled")
            return send_edgx_error(request, err, API_DISCOVERY_ROUTE)

        driver = self.driver
        device_service = self.device_service

        def run():
            # Publish start progress (0%)
            if device_service is not None and hasattr(device_service, "_publish_discovery_progress"):
                device_service._publish_discovery_progress(0, 0, "Discovery started")

            self.logger.info("Discovery triggered. Correlation Id: %s", correlation_id)
            try:
                driver.discover()
                # Publish completion progress (100%)
                if device_service is not None and hasattr(device_service, "_publish_discovery_progress"):
                    device_service._publish_discovery_progress(100, 0, "Discovery completed")
            except Exception:
                self.logger.exception(
                    "Discovery failed. Correlation Id: %s", correlation_id)
                # Publish error progress (-1%)
                if device_service is not None and hasattr(device_service, "_publish_discovery_progress"):
                    device_service._publish_discovery_progress(-1, 0, "Discovery failed")
            finally:
                self.logger.info("Discovery end. Correlation Id: %s", correlation_id)

        threading.Thread(target=run, daemon=True).start()

        response = base_response(correlation_id, "Device Discovery is triggered.",
                                 HTTPStatus.ACCEPTED)
        return send_response(request, response, API_DISCOVERY_ROUTE,
                             HTTPStatus.ACCEPTED)

    def stop_device_discovery(self, request: Request) -> Response:
        """Handle the DELETE `/api/v3/discovery` and
        `/api/v3/discovery/{requestId}` requests.

        In
        discovery.go: collects the query parameters as the stop options and delegates to
        the `device_discovery_stop_handler` hook (the port of
`autodiscovery.StopDeviceDiscovery`). When the hook is not wired yet an
        NotImplemented error is returned.
        """
        request_id = request.path_params.get(REQUEST_ID, "")
        options: Dict[str, Any] = {}
        for key, values in urllib.parse.parse_qs(
                request.url.query, keep_blank_values=True).items():
            options[key] = values

        handler = getattr(self, "device_discovery_stop_handler", None)
        if handler is None:
            err = create_edgx_error(KIND_NOT_IMPLEMENTED,
                                 "device discovery stop is not implemented")
            return send_edgx_error_with_request_id(
                request, err, API_DISCOVERY_BY_ID_ROUTE, request_id)

        try:
            handler(request_id, options)
        except EdgexError as exc:
            return send_edgx_error_with_request_id(
                request, exc, API_DISCOVERY_BY_ID_ROUTE, request_id)

        response = base_response(request_id, "", HTTPStatus.OK)
        return send_response(request, response, API_DISCOVERY_BY_ID_ROUTE,
                             HTTPStatus.OK)

    # -- profile scan ----------------------------------------------------------

    async def profile_scan(self, request: Request) -> Response:
        """Handle the POST `/api/v3/profilescan` request.

        : checks
        the DeviceService AdminState, validates the request payload against the caches
        (device existence, profile duplication) and triggers the scanning through the
`profile_scan_handler` hook (the port of `application.ProfileScanWrapper`). When
        the hook is not wired yet a NotImplemented error is returned.

        The handler is asynchronous because it reads the request body, which is a
        coroutine in Starlette.
        """
        if self._service_admin_state_locked():
            err = create_edgx_error(KIND_SERVICE_LOCKED, "service locked")
            return send_edgx_error(request, err, API_PROFILE_SCAN_ROUTE)

        body = await request.body()
        try:
            payload = parse_request_body(body)
        except EdgexError as exc:
            return send_edgx_error(request, exc, API_PROFILE_SCAN_ROUTE)

        # check requested device exists
        device_name = payload.get("deviceName", "")
        if device_name == "":
            err = create_edgx_error(KIND_CONTRACT_INVALID, "device name is empty")
            return send_edgx_error(request, err, API_PROFILE_SCAN_ROUTE)
        if not Devices().for_name(device_name)[1]:
            err = create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                                 f"device {device_name} not found")
            return send_edgx_error(request, err, API_PROFILE_SCAN_ROUTE)

        # check profile should not exist
        profile_name = payload.get("profileName", "")
        if profile_name != "":
            if Profiles().for_name(profile_name)[1]:
                err = create_edgx_error(
                    KIND_STATUS_CONFLICT,
                    f"profile name {profile_name} is duplicated")
                return send_edgx_error(request, err, API_PROFILE_SCAN_ROUTE)
        else:
            profile_name = f"{device_name}_profile_{current_time_millis()}"

        request_id = payload.get("requestId", "")
        if request_id == "":
            # Use correlation id as request id if request id is not provided
            request_id = correlation_id_from_request(request)

        handler = getattr(self, "profile_scan_handler", None)
        if handler is None:
            err = create_edgx_error(KIND_NOT_IMPLEMENTED,
                                 "Profile scan is not implemented")
            return send_edgx_error(request, err, API_PROFILE_SCAN_ROUTE)

        options = payload.get("options", {}) or {}
        device_service = self.device_service

        def run():
            # Publish start progress (0%)
            if device_service is not None and hasattr(device_service, "_publish_profile_scan_progress"):
                device_service._publish_profile_scan_progress(request_id, 0, "Profile scan started")

            self.logger.info("Profile scanning is triggered. Correlation Id: %s",
                             request_id)
            try:
                handler(device_name, profile_name, request_id, options)
                # Publish completion progress (100%)
                if device_service is not None and hasattr(device_service, "_publish_profile_scan_progress"):
                    device_service._publish_profile_scan_progress(request_id, 100, "Profile scan completed")
            except Exception:
                self.logger.exception(
                    "Profile scanning failed. Correlation Id: %s", request_id)
                # Publish error progress (-1%)
                if device_service is not None and hasattr(device_service, "_publish_profile_scan_progress"):
                    device_service._publish_profile_scan_progress(request_id, -1, "Profile scan failed")
            finally:
                self.logger.info("Profile scanning is end. Correlation Id: %s",
                                 request_id)

        threading.Thread(target=run, daemon=True).start()

        response = base_response(request_id, "Device ProfileScan is triggered.",
                                 HTTPStatus.ACCEPTED)
        return send_response(request, response, API_PROFILE_SCAN_ROUTE,
                             HTTPStatus.ACCEPTED)

    def stop_profile_scan(self, request: Request) -> Response:
        """Handle the DELETE `/api/v3/profilescan/device/{name}` request.

        :
        collects the query parameters as the stop options and delegates to the
        `profile_scan_stop_handler` hook (the port of `application.StopProfileScan`).
        When the hook is not wired yet an NotImplemented error is returned.
        """
        device_name = request.path_params.get(NAME, "")
        options: Dict[str, Any] = {}
        for key, values in urllib.parse.parse_qs(
                request.url.query, keep_blank_values=True).items():
            options[key] = values

        handler = getattr(self, "profile_scan_stop_handler", None)
        if handler is None:
            err = create_edgx_error(KIND_NOT_IMPLEMENTED,
                                 "profile scan stop is not implemented")
            return send_edgx_error(request, err, API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE)

        try:
            handler(device_name, options)
        except EdgexError as exc:
            return send_edgx_error(request, exc,
                                   API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE)

        response = base_response("", "", HTTPStatus.OK)
        return send_response(request, response, API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE,
                             HTTPStatus.OK)
