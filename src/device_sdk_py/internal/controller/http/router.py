# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/controller/http/restrouter.go`.

`RestController` owns the FastAPI application, registers the SDK reserved routes
(discovery, profile scan, device command, the secret management endpoint and the
common ping / version / config / metrics endpoints) and exposes `add_route` for the
service-specific routes added by `DeviceServiceSDK.add_custom_route`. Custom routes
are rejected when their path matches a reserved route, map.

The Go controller reads its dependencies (device service, configuration, protocol
driver,...) from the DI container; the Python port receives them as constructor
arguments and holds them as attributes so that the command / discovery handlers (see
`command.py` / `discovery.py`) can access them. The handlers are contributed by the
`CommandController` / `DiscoveryController` mixins, keeping the files aligned with the
Go layout while staying on a single `RestController` type.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import is_dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from ...common.consts import (
    API_CONFIG_ROUTE,
    API_DEVICE_COMMAND_ROUTE,
    API_DISCOVERY_BY_ID_ROUTE,
    API_DISCOVERY_ROUTE,
    API_METRICS_ROUTE,
    API_PING_ROUTE,
    API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE,
    API_PROFILE_SCAN_ROUTE,
    API_SECRET_ROUTE,
    API_VERSION,
    API_VERSION_ROUTE,
    SDK_VERSION,
    SERVICE_VERSION,
)
from ...common.utils import EdgexError, create_edgx_error, KIND_SERVER_ERROR
from ...common.configuration import ConfigurationStruct
from .auth import (
    JWTAuthMiddleware,
    get_jwt_authenticator,
    JWTAuthenticator,
    is_public_endpoint,
)
from._utils import (
    base_response,
    send_response,
)
from .command import CommandController
from .discovery import DiscoveryController
from .secret import SecretController

#: The type of the route authentication hook (a FastAPI dependency). Mirrors the Go
#: `authenticationHook` built by `handlers.AutoConfigAuthenticationFunc(dic)`.
RouteAuthHook = Optional[Callable[..., Any]]


class RestController(DiscoveryController, CommandController, SecretController):
    """The REST controller of the Device Service SDK.

    The handlers of the discovery / profile scan and device command routes are provided
    by the `DiscoveryController` / `CommandController` mixins; the common endpoints and
    the route registration machinery live in this class.
    """

    def __init__(self, service_name: str, service_version: str,
                 router: Optional[FastAPI] = None,
                 logger: Optional[logging.Logger] = None,
                 configuration: Any = None, driver: Any = None,
                 device_service: Any = None,
                 device_discovery_stop_handler: Optional[Callable[[str, Dict], None]] = None,
                 profile_scan_handler: Optional[
                     Callable[[str, str, str, Dict], None]] = None,
                 profile_scan_stop_handler: Optional[
                     Callable[[str, Dict], None]] = None,
                 send_event_handler: Optional[Callable[[Any, str], None]] = None,
                 metrics_provider: Optional[Callable[[], Dict[str, Any]]] = None):
        self.service_name = service_name
        self.service_version = service_version
        self.router = router if router is not None else FastAPI()
        self.logger = logger or logging.getLogger(__name__)
        self.reserved_routes: set = set()
        self.custom_config: Any = None

        # dependencies (the Go DI container equivalents)
        self.configuration = configuration
        self.driver = driver
        self.device_service = device_service

        # hooks for the not yet ported modules
        self.device_discovery_stop_handler = device_discovery_stop_handler
        self.profile_scan_handler = profile_scan_handler
        self.profile_scan_stop_handler = profile_scan_stop_handler
        self.send_event_handler = send_event_handler
        self.metrics_provider = metrics_provider

        # JWT Authentication (secure mode)
        self._jwt_authenticator = None
        self._setup_jwt_auth()

    def _setup_jwt_auth(self) -> None:
        """Setup JWT authentication middleware for secure mode."""
        if self.configuration is None:
            return

        device_opt = getattr(self.configuration, "device", None)
        if device_opt is None:
            return

        secure_mode = getattr(device_opt, "secure_mode", False)
        if not secure_mode:
            return

        # Check for JWT configuration
        jwks_url = getattr(device_opt, "jwt_jwks_url", None) or os.environ.get("EDGEX_JWT_JWKS_URL")
        public_key = getattr(device_opt, "jwt_public_key", None) or os.environ.get("EDGEX_JWT_PUBLIC_KEY")
        issuer = getattr(device_opt, "jwt_issuer", None) or os.environ.get("EDGEX_JWT_ISSUER")
        audience = getattr(device_opt, "jwt_audience", None) or os.environ.get("EDGEX_JWT_AUDIENCE")

        if not (jwks_url or public_key):
            self.logger.warning("Secure mode enabled but no JWT configuration found")
            return

        self._jwt_authenticator = get_jwt_authenticator(
            public_key=public_key,
            jwks_url=jwks_url,
            issuer=issuer,
            audience=audience,
        )

        # Add JWT middleware to router
        self.router.add_middleware(
            JWTAuthMiddleware,
            authenticator=self._jwt_authenticator,
            public_paths=["/api/v3/ping", "/api/v3/version", "/api/v3/config", "/api/v3/metrics"],
            public_prefixes=["/docs", "/openapi.json", "/redoc", "/health"],
        )
        self.logger.info("JWT authentication enabled for secure mode")

    # -- route registration ---------------------------------------------------

    def init_rest_routes(self) -> None:
        """Register the SDK reserved routes.

        The Go version also registers the common ping / version / metrics / config
        endpoints through the bootstrap common controller; they are registered here since
        the Python SDK has no separate common controller.
        """
        self.logger.info("Registering routes...")

        # Readiness probe (for security-bootstrapper)
        self._init_readiness_route()

        # common endpoints
        self.add_reserved_route(API_PING_ROUTE, self.ping, ["GET"])
        self.add_reserved_route(API_VERSION_ROUTE, self.version, ["GET"])
        self.add_reserved_route(API_METRICS_ROUTE, self.metrics, ["GET"])
        self.add_reserved_route(API_CONFIG_ROUTE, self.config, ["GET"])
        self.add_reserved_route(API_SECRET_ROUTE, self.add_secret, ["POST"])

        # discovery / profile scan
        self.add_reserved_route(API_DISCOVERY_ROUTE, self.discovery, ["POST"])
        self.add_reserved_route(API_PROFILE_SCAN_ROUTE, self.profile_scan, ["POST"])
        self.add_reserved_route(API_DISCOVERY_ROUTE, self.stop_device_discovery,
                                ["DELETE"])
        self.add_reserved_route(API_DISCOVERY_BY_ID_ROUTE, self.stop_device_discovery,
                                ["DELETE"])
        self.add_reserved_route(API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE,
                                self.stop_profile_scan, ["DELETE"])

        # device command
        self.add_reserved_route(API_DEVICE_COMMAND_ROUTE, self.get_command, ["GET"])
        self.add_reserved_route(API_DEVICE_COMMAND_ROUTE, self.set_command, ["PUT"])

    def add_reserved_route(self, route: str, handler: Callable[..., Any],
                           methods: List[str]) -> None:
        """Register a reserved route, marking its path so that custom routes cannot
        override it.

        """
        self.reserved_routes.add(route)
        self.router.add_api_route(route, handler, methods=methods)

    def add_route(self, route: str, handler: Callable[..., Any],
                  methods: Optional[List[str]] = None) -> None:
        """Register a custom route for the Device Service, rejecting paths reserved by
        the SDK.

        ; the Go version
        returns an `errors.EdgeX`, the Python port raises `EdgexError` instead.
        """
        if route in self.reserved_routes:
            raise create_edgx_error(KIND_SERVER_ERROR, "route is reserved")
        self.router.add_api_route(route, handler, methods=methods or ["GET"])
        self.logger.debug("Route added", extra={"route": route,
                                                "methods": str(methods)})

    def set_custom_config_info(self, custom_config: Any) -> None:
        """Set the custom configuration, which is used to include the service's custom
        config in the /config endpoint response.

        """
        self.custom_config = custom_config

    # -- readiness / health check (for security-bootstrapper) ----------------------

    def readiness(self, request: Request) -> Response:
        """Readiness probe endpoint for security-bootstrapper.

        Returns 200 when the service is ready to accept traffic.
        The security-bootstrapper polls this endpoint to determine when the service
        is ready to accept traffic after security initialization.
        """
        return send_response(request, {"status": "ready"}, "/api/v3/readiness", HTTPStatus.OK)

    def _init_readiness_route(self) -> None:
        """Initialize the readiness endpoint for security-bootstrapper compatibility."""
        self.add_reserved_route("/api/v3/readiness", self.readiness, ["GET"])

    # -- event publishing -------------------------------------------------------

    def send_event(self, event: Any, correlation_id: str) -> None:
        """Push an Event to the MessageBus in a background thread.

        The
        actual push is delegated to the `send_event_handler` hook (the port of
        `sdkCommon.SendEvent`); when the hook is not wired yet the event is only logged,
        since the messaging / metrics modules are ported in a later phase.
        """
        def push():
            handler = self.send_event_handler
            if handler is not None:
                handler(event, correlation_id)
                return
            self.logger.debug(
                "Event %s for Device %s is not pushed to the MessageBus "
                "(SendEvent not wired yet)", getattr(event, "event_id", ""),
                getattr(event, "device_name", ""))

        threading.Thread(target=push, daemon=True).start()

    # -- common endpoints ----------------------------------------------------------

    def ping(self, request: Request) -> Response:
        """Handle the GET `/api/v3/ping` request.

        Mirrors the bootstrap common controller `Ping` handler, returning the service
        name and the current timestamp.
        """
        response = {
            "apiVersion": API_VERSION,
            "serviceName": self.service_name,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        return send_response(request, response, API_PING_ROUTE, HTTPStatus.OK)

    def version(self, request: Request) -> Response:
        """Handle the GET `/api/v3/version` request.

        Mirrors the bootstrap common controller `Version` handler, returning the service
        and SDK versions.
        """
        response = {
            "apiVersion": API_VERSION,
            "serviceName": self.service_name,
            "version": self.service_version,
            "sdkVersion": SDK_VERSION,
        }
        return send_response(request, response, API_VERSION_ROUTE, HTTPStatus.OK)

    def metrics(self, request: Request) -> Response:
        """Handle the GET `/api/v3/metrics` request.

Mirrors the bootstrap common controller `Metrics` handler. The metrics are
        provided by the `metrics_provider` hook (the port of the Metrics Manager); when
        it is not wired yet an empty map is returned.
        """
        metrics = self.metrics_provider() if self.metrics_provider is not None else {}
        return send_response(request, metrics, API_METRICS_ROUTE, HTTPStatus.OK)

    def config(self, request: Request) -> Response:
        """Handle the GET `/api/v3/config` request.

        Mirrors the bootstrap common controller `Config` handler, returning the service
        configuration (and the custom configuration when it has been set).
        """
        response = {
            "apiVersion": API_VERSION,
            "serviceName": self.service_name,
            "config": self._config_to_dict(),
        }
        return send_response(request, response, API_CONFIG_ROUTE, HTTPStatus.OK)

    def _config_to_dict(self) -> Dict[str, Any]:
        """Serialize the service configuration (and the custom configuration) into a
        JSON friendly structure. The Python `ConfigurationStruct` model is serialized
        in the Go shape (PascalCase, sorted keys, zero values, nil maps -> null).
        """
        config = self.configuration
        if config is None:
            return {}
        if isinstance(config, ConfigurationStruct):
            result = config.to_go_dict()
        elif is_dataclass(config) and not isinstance(config, type):
            result = {
                key: value for key, value in config.__dict__.items()
                if value is not None
            }
        elif isinstance(config, dict):
            result = dict(config)
        else:
            result = {"value": str(config)}

        if self.custom_config is not None:
            if is_dataclass(self.custom_config) and not isinstance(self.custom_config,
                                                                   type):
                result["Custom"] = {
                    key: value for key, value in self.custom_config.__dict__.items()
                    if value is not None
                }
            else:
                result["Custom"] = self.custom_config
        return result

    # -- application entry point -----------------------------------------------------

    def app(self) -> FastAPI:
        """Return the underlying FastAPI application."""
        return self.router
