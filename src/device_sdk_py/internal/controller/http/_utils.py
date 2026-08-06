# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The shared HTTP helpers of the REST controllers - ported from
`device-sdk-go/internal/controller/http/restrouter.go`.

The Go `RestController` assembles the response packet (setting the correlation id and
content type headers, marshaling the payload and writing the status code) in
`sendResponse` / `sendEventResponse` and reports errors through
`sendEdgexError` / `sendEdgexErrorWithRequestId`.  The Python port provides the same
helpers as standalone functions operating on a `fastapi.Request` and returning a
`starlette.Response`, so that they can be shared by the command / discovery controllers
without depending on a framework base class.

The Go `parseRequestBody` / `filterQueryParams` helpers (see
`device-sdk-go/internal/controller/http/command.go`) are also reproduced here, together
with the v3 API JSON serialization of the `BaseResponse` DTO and the `Event` / `Reading`
DTOs produced by the transformer.
"""

from __future__ import annotations

import base64
import cbor2
import json
import logging
import urllib.parse
from dataclasses import is_dataclass
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import Response

from ....models import VALUETYPE_BINARY
from ...transformer.transform import Event, Reading
from ...common.consts import (
    API_VERSION,
    CONTENT_TYPE,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_CBOR,
    CORRELATION_HEADER,
    SDK_RESERVED_PREFIX,
)
from ...common.utils import EdgexError, new_edgx_error, KIND_CONTRACT_INVALID

__all__ = [
    "correlation_id_from_request",
    "filter_query_params",
    "parse_request_body",
    "base_response",
    "event_response",
    "event_to_dict",
    "reading_to_dict",
    "send_response",
    "send_event_response",
    "send_edgx_error",
    "send_edgx_error_with_request_id",
]


def correlation_id_from_request(request: Request) -> str:
    """Return the correlation id carried by the request, or an empty string when the
    header is not present.

    Mirrors `utils.FromContext(ctx, common.CorrelationHeader)` used by the Go
    controllers.
    """
    return request.headers.get(CORRELATION_HEADER, "")


def filter_query_params(raw_query: str) -> Tuple[str, Dict[str, str]]:
    """Separate the SDK reserved query parameters (prefixed with `ds-`) from the ones
    passed through to the ProtocolDriver.

    Mirrors `filterQueryParams(rawQuery string)` in command.go.  Returns the
    re-encoded query string without the reserved parameters and the map of reserved
    parameters.  Go's `url.ParseQuery` never reports a parse error in practice, so the
    Python port has no error path.
    """
    query_params = urllib.parse.parse_qs(raw_query, keep_blank_values=True)
    reserved: Dict[str, str] = {}
    for key in list(query_params):
        if key.startswith(SDK_RESERVED_PREFIX):
            reserved[key] = query_params[key][0]
            del query_params[key]
    return urllib.parse.urlencode(query_params, doseq=True), reserved


def parse_request_body(body: bytes) -> Dict[str, Any]:
    """Parse the request body into a map of resource name to value.

    Mirrors `parseRequestBody(r *http.Request)` in command.go.  An empty body produces
    an empty map; a body that is not a valid JSON object raises an `EdgexError` of kind
    ContractInvalid ("failed to parse request body").
    """
    if not body:
        return {}
    try:
        param_map = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise new_edgx_error(KIND_CONTRACT_INVALID,
                             "failed to parse request body") from exc
    if not isinstance(param_map, dict):
        raise new_edgx_error(KIND_CONTRACT_INVALID, "failed to parse request body")
    return param_map


def _omit_empty(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Remove the keys whose value is None, an empty string, an empty list or an empty
    map, mirroring the `omitempty` JSON tags of the v3 DTOs."""
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        result[key] = value
    return result


def base_response(request_id: str, message: str, status_code: HTTPStatus) -> Dict[str, Any]:
    """Assemble the JSON packet of a `commonDTO.BaseResponse`.

    Mirrors `commonDTO.NewBaseResponse(requestId, message, statusCode)`.  The
    `requestId` and `message` fields are omitted when empty, like the Go `omitempty`
    JSON tags.
    """
    return _omit_empty({
        "apiVersion": API_VERSION,
        "requestId": request_id,
        "message": message,
        "statusCode": status_code,
    })


def _json_default(obj: Any) -> Any:
    """The `json.dumps` default converter used by the response helpers.

    Dataclasses are serialized from their `__dict__` (dropping `None` values), byte
    strings as base64 and everything else through `repr`.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {key: value for key, value in obj.__dict__.items()
                if value is not None}
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return repr(obj)


def reading_to_dict(reading: Reading) -> Dict[str, Any]:
    """Serialize a `Reading` into the v3 DTO JSON (aligned to `dtos.Reading`), using the
    Go `omitempty` semantics for the optional fields.
    """
    return _omit_empty({
        "id": reading.reading_id,
        "origin": reading.origin,
        "deviceName": reading.device_name,
        "resourceName": reading.resource_name,
        "profileName": reading.profile_name,
        "valueType": reading.value_type,
        "units": reading.units,
        "value": reading.value,
        "binaryValue": reading.binary_value,
        "objectValue": reading.object_value,
        "mediaType": reading.media_type,
        "tags": reading.tags,
    })


def event_to_dict(event: Event) -> Dict[str, Any]:
    """Serialize an `Event` into the v3 DTO JSON (aligned to `dtos.Event`), using the Go
    `omitempty` semantics for the optional fields.
    """
    readings = [reading_to_dict(reading) for reading in event.readings]
    return _omit_empty({
        "id": event.event_id,
        "deviceName": event.device_name,
        "profileName": event.profile_name,
        "sourceName": event.source_name,
        "origin": event.origin,
        "readings": readings,
        "tags": event.tags,
    })


def event_response(event: Optional[Event], status_code: HTTPStatus) -> Dict[str, Any]:
    """Assemble the JSON packet of a `responses.EventResponse`.

    Mirrors `responses.NewEventResponse("", "", statusCode, *event)`.  The event is
    omitted from the packet when None (no readings were produced by the driver).
    """
    payload = _omit_empty({
        "apiVersion": API_VERSION,
        "statusCode": status_code,
    })
    if event is not None:
        payload["event"] = event_to_dict(event)
    return payload


def send_response(request: Request, response: Dict[str, Any], api: str,
                  status_code: HTTPStatus) -> Response:
    """Put together the response packet for the v3 API.

    Mirrors `(c *RestController) sendResponse(...)` in restrouter.go: echoes back the
    correlation id, sets the content type and marshals the payload to JSON.
    """
    correlation_id = correlation_id_from_request(request)
    headers = {
        CORRELATION_HEADER: correlation_id,
        CONTENT_TYPE: CONTENT_TYPE_JSON,
    }
    data = json.dumps(response, default=_json_default)
    return Response(content=data, status_code=status_code, headers=headers,
                    media_type=CONTENT_TYPE_JSON)


def send_event_response(request: Request, event: Optional[Event],
                        status_code: HTTPStatus) -> Response:
    """Put together the EventResponse packet for the v3 API.

    Mirrors `(c *RestController) sendEventResponse(...)` in restrouter.go.
    Uses CBOR encoding when the event contains binary readings.
    """
    # Check if event has binary readings (triggers CBOR encoding per ADR 0011)
    use_cbor = False
    if event is not None:
        for reading in event.readings:
            if reading.value_type == VALUETYPE_BINARY:
                use_cbor = True
                break

    content_type = CONTENT_TYPE_CBOR if use_cbor else CONTENT_TYPE_JSON
    correlation_id = correlation_id_from_request(request)
    headers = {
        CORRELATION_HEADER: correlation_id,
        CONTENT_TYPE: content_type,
    }

    response = event_response(event, status_code)
    if use_cbor:
        data = cbor2.dumps(response)
    else:
        data = json.dumps(response, default=_json_default)

    return Response(content=data, status_code=status_code, headers=headers,
                    media_type=content_type)


def send_edgx_error(request: Request, err: EdgexError, api: str) -> Response:
    """Report an `EdgexError` as the v3 error response packet.

    Mirrors `(c *RestController) sendEdgexError(...)` in restrouter.go.
    """
    return send_edgx_error_with_request_id(request, err, api, "")


def send_edgx_error_with_request_id(request: Request, err: EdgexError, api: str,
                                    request_id: str) -> Response:
    """Report an `EdgexError`, including the request id, as the v3 error response packet.

    Mirrors `(c *RestController) sendEdgexErrorWithRequestId(...)` in restrouter.go.
    """
    correlation_id = correlation_id_from_request(request)
    _log_error(request, err, correlation_id)
    response = base_response(request_id, err.message, err.code)
    return send_response(request, response, api, err.code)


def _log_error(request: Request, err: EdgexError, correlation_id: str) -> None:
    """Log the error and its debug messages with the correlation id, mirroring the
    `lc.Error` / `lc.Debug` calls in sendEdgexErrorWithRequestId.  A logger attached to
    the request state is used when present, otherwise the module logger."""
    logger = request.state.logger if hasattr(request.state, "logger") \
        else logging.getLogger(__name__)
    logger.error("%s", err.message, extra={CORRELATION_HEADER: correlation_id})
    logger.debug("%s", err.debug_messages(), extra={CORRELATION_HEADER: correlation_id})
