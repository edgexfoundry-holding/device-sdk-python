# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`go-mod-bootstrap/bootstrap/controller/commonapi.go` (`CommonController.AddSecret`).

The POST `/api/v3/secret` route is registered by the bootstrap common controller in Go
(not by the device-sdk rest router); in the Python port it lives in this mixin so that
the `RestController` registers it together with the other reserved routes. The handler
validates the `commonDTO.SecretRequest` DTO, flattens its `secretData` key/value pairs
into a map and stores them through the Device Service secret provider (see
`device_service.secret_provider()`), acknowledging with a 201 Created `BaseResponse`
carrying the request id from the DTO.

Go stores the whole map with a single `SecretProvider.StoreSecret(secretName, data)`
call; the Python port stores each key/value pair with the per-key
`SecretProvider.store_secret(path, key, value)` interface.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import Response

from ...common.consts import API_SECRET_ROUTE
from ...common.utils import (
    KIND_CONTRACT_INVALID,
    KIND_SERVER_ERROR,
    EdgexError,
    create_edgx_error,
)
from ._utils import base_response, parse_request_body, send_edgx_error, send_response


class SecretController:
    """The secret management handler of the REST controller."""

    async def add_secret(self, request: Request) -> Response:
        """Handle the POST `/api/v3/secret` request.

        Validates the `commonDTO.SecretRequest` payload (secretName and a non-empty
        secretData list with non-empty key / value pairs are required), flattens the
        pairs and stores them in the Device Service secret store. Missing or invalid
        payloads are rejected with a ContractInvalid error, a missing secret provider
        with a ServerError. On success a 201 Created `BaseResponse` carrying the DTO
        request id is returned.

        The handler is asynchronous because it reads the request body, which is a
        coroutine in Starlette.
        """
        body = await request.body()
        try:
            payload = parse_request_body(body)
        except EdgexError as exc:
            return send_edgx_error(request, exc, API_SECRET_ROUTE)

        secret_name, secrets = self._validate_secret_request(payload)
        if secret_name is None:
            err = create_edgx_error(KIND_CONTRACT_INVALID,
                                    "SecretRequest validation failed.")
            return send_edgx_error(request, err, API_SECRET_ROUTE)

        provider = self._secret_provider()
        if provider is None:
            err = create_edgx_error(
                KIND_SERVER_ERROR,
                "secret provider is missing. Make sure it is specified "
                "to be used in bootstrap.Run()")
            return send_edgx_error(request, err, API_SECRET_ROUTE)

        for key, value in secrets.items():
            try:
                provider.store_secret(secret_name, key, value)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.error("adding secret failed: %s", exc)
                err = create_edgx_error(KIND_SERVER_ERROR, "adding secret failed")
                return send_edgx_error(request, err, API_SECRET_ROUTE)

        request_id = payload.get("requestId", "")
        response = base_response(request_id, "", HTTPStatus.CREATED)
        return send_response(request, response, API_SECRET_ROUTE, HTTPStatus.CREATED)

    # -- helpers -------------------------------------------------------------

    def _secret_provider(self) -> Any:
        """Return the Device Service secret provider, or None when the device service
        is not wired yet (the Go DI container equivalent of a missing provider)."""
        device_service = getattr(self, "device_service", None)
        if device_service is None:
            return None
        return device_service.secret_provider()

    def _validate_secret_request(self,
                                 payload: Dict[str, Any]) -> Tuple[Optional[str],
                                                                   Optional[Dict[str, str]]]:
        """Validate the `commonDTO.SecretRequest` DTO and flatten its secret data.

        Mirrors the Go `validate` tags: `secretName` and a non-empty `secretData`
        list are required, and every entry must carry non-empty `key` and `value`
        fields. Returns `(None, None)` when the payload does not satisfy the DTO
        contract, otherwise the trimmed secret name and the key/value map.
        """
        secret_name = payload.get("secretName", "")
        if not isinstance(secret_name, str) or secret_name == "":
            return None, None

        secret_data = payload.get("secretData", None)
        if not isinstance(secret_data, list) or len(secret_data) == 0:
            return None, None

        secrets: Dict[str, str] = {}
        for entry in secret_data:
            if not isinstance(entry, dict):
                return None, None
            key = entry.get("key")
            value = entry.get("value")
            if not isinstance(key, str) or key == "" or \
                    not isinstance(value, str) or value == "":
                return None, None
            secrets[key] = value

        return secret_name.strip(), secrets
