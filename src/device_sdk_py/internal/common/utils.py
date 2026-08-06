# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The common utility functions of the EdgeX Device Service SDK - ported from
`device-sdk-go/internal/common/utils.go`.

The Go file provides timestamp / ID helpers used all over the SDK, the Event / Reading
tag helpers (`AddEventTags`, `AddReadingTags`) that the messaging layer applies before
publishing, the Core Metadata OperatingState update helper (`UpdateOperatingState`) and
the sent-event metric counters.  This module also defines the `EdgexError` exception
hierarchy used by the application and controller layers as the Python counterpart of the
Go `errors.EdgeX` return values (`errors.NewCommonEdgeX(kind, message, cause)`), together
with the mapping from the Go error kind to the HTTP status code.

The `SendEvent` / `InitializeSentMetrics` functions depend on the messaging and metrics
infrastructure that is ported in a later phase; they are therefore not reproduced here.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from ..cache import Devices, Profiles

_logger = logging.getLogger(__name__)


class EdgexErrorKind(Enum):
    """Categorical identifier for EdgeX errors.

    Python counterpart of the `errors.Kind` values from go-mod-core-contracts that are
    used by the device service.
    """
    UNKNOWN = "Unknown"
    SERVER_ERROR = "ServerError"
    CONTRACT_INVALID = "ContractInvalid"
    LIMIT_EXCEEDED = "LimitExceeded"
    NOT_FOUND = "NotFound"
    ENTITY_DOES_NOT_EXIST = "NotFound"
    DUPLICATE_NAME = "DuplicateName"
    SERVICE_UNAVAILABLE = "ServiceUnavailable"
    SERVICE_LOCKED = "ServiceLocked"
    STATUS_CONFLICT = "StatusConflict"
    NOT_ALLOWED = "NotAllowed"
    NOT_IMPLEMENTED = "NotImplemented"
    NAN_ERROR = "NaNError"
    OVERFLOW_ERROR = "OverflowError"
    IO_ERROR = "IOError"


#: Mapping from the error kind to the HTTP status code returned by the REST API
#: (mirrors the default mapping used by `errors.DefaultHTTPErrorCode` in
#: go-mod-core-contracts).
_ERROR_KIND_STATUS: Dict[EdgexErrorKind, int] = {
    EdgexErrorKind.UNKNOWN: 500,
    EdgexErrorKind.SERVER_ERROR: 500,
    EdgexErrorKind.CONTRACT_INVALID: 400,
    EdgexErrorKind.LIMIT_EXCEEDED: 413,
    EdgexErrorKind.NOT_FOUND: 404,
    EdgexErrorKind.ENTITY_DOES_NOT_EXIST: 404,
    EdgexErrorKind.DUPLICATE_NAME: 409,
    EdgexErrorKind.SERVICE_UNAVAILABLE: 503,
    EdgexErrorKind.SERVICE_LOCKED: 423,
    EdgexErrorKind.STATUS_CONFLICT: 409,
    EdgexErrorKind.NOT_ALLOWED: 403,
    EdgexErrorKind.NOT_IMPLEMENTED: 501,
    EdgexErrorKind.NAN_ERROR: 400,
    EdgexErrorKind.OVERFLOW_ERROR: 400,
    EdgexErrorKind.IO_ERROR: 500,
}


class EdgexError(Exception):
    """Raised when an EdgeX operation fails.

    Python counterpart of the `errors.EdgeX` error returned by the Go functions.  Carries
    an `EdgexErrorKind` and the HTTP status code derived from it (`.code`).
    """

    def __init__(self, kind: EdgexErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.code = _ERROR_KIND_STATUS.get(kind, 500)

    def debug_messages(self) -> str:
        """Return the debug messages of the error (mirrors `errors.EdgeX.DebugMessages`).

        The Python exceptions preserve the original exception chain, so the message is
        simply this error's message.
        """
        return self.message


def new_edgx_error(kind: EdgexErrorKind, message: str) -> EdgexError:
    """Create an `EdgexError` with the given kind and message.

    Python counterpart of `errors.NewCommonEdgeX(kind, message, nil)`.
    """
    return EdgexError(kind=kind, message=message)


#: Convenience alias for the most commonly used kinds (kept for parity with the Go
#: `errors.Kind*` constants).
KIND_SERVER_ERROR = EdgexErrorKind.SERVER_ERROR
KIND_CONTRACT_INVALID = EdgexErrorKind.CONTRACT_INVALID
KIND_ENTITY_DOES_NOT_EXIST = EdgexErrorKind.ENTITY_DOES_NOT_EXIST
KIND_NOT_ALLOWED = EdgexErrorKind.NOT_ALLOWED
KIND_NOT_IMPLEMENTED = EdgexErrorKind.NOT_IMPLEMENTED
KIND_SERVICE_UNAVAILABLE = EdgexErrorKind.SERVICE_UNAVAILABLE
KIND_SERVICE_LOCKED = EdgexErrorKind.SERVICE_LOCKED
KIND_STATUS_CONFLICT = EdgexErrorKind.STATUS_CONFLICT


def make_uid() -> str:
    """Return a new unique ID (a random UUID string).

    Python counterpart of the `uuid.NewString()` calls in the Go code.
    """
    return str(uuid.uuid4())


def make_timestamp() -> int:
    """Return the current time in nanoseconds since the Unix epoch.

    Python counterpart of `time.Now().UnixNano()`.
    """
    return time.time_ns()


def current_time_millis() -> int:
    """Return the current time in milliseconds since the Unix epoch.

    Python counterpart of `time.Now().UnixMilli()`.
    """
    return time.time_ns() // 1_000_000


def update_operating_state(name: str, state: str, logger: logging.Logger,
                           device_client: Optional[Any] = None) -> None:
    """Update the OperatingState of the Device with the given name in Core Metadata.

    Mirrors `UpdateOperatingState(name, state, lc, dc)` in utils.go which issues an
    `UpdateDeviceRequest` (with `bypassValidation=true`) through the Device client.  The
    Python `device_client` is expected to provide an `update_operating_state(name, state)`
    method; when it is not provided (the client is ported in a later phase) the update is
    skipped with a warning instead of raising.
    """
    if device_client is None:
        logger.warning(
            "no Device client available; skipping OperatingState update for "
            "Device %s in Core Metadata", name)
        return
    try:
        device_client.update_operating_state(name, state)
    except Exception:
        logger.exception("failed to update OperatingState for Device %s in Core Metadata",
                         name)


def add_event_tags(event: Any) -> None:
    """Merge the tags of the DeviceCommand (the Event source) and the Device into the
    Event.

    Mirrors `AddEventTags(event *dtos.Event)` in utils.go.  The Python `DeviceCommand`
    model carries no tags, so only the Device tags are applied; the tags are merged into
    the existing `event.tags` map in place.
    """
    if event.tags is None:
        event.tags = {}

    cmd, cmd_exist = Profiles().device_command(event.profile_name, event.source_name)
    if cmd_exist and len(getattr(cmd, "tags", {})) > 0:
        for key, value in cmd.tags.items():
            event.tags[key] = value

    device, device_exist = Devices().for_name(event.device_name)
    if device_exist and len(device.tags) > 0:
        for key, value in device.tags.items():
            event.tags[key] = value


def add_reading_tags(reading: Any) -> None:
    """Merge the tags of the DeviceResource into the reading.

    Mirrors `AddReadingTags(reading *dtos.BaseReading)` in utils.go.  The Python
    `DeviceResource` model carries a single `tag` string (instead of the Go
    `Tags map[string]interface{}`); when it is set it is stored under the "tag" key.
    """
    dr, dr_exist = Profiles().device_resource(reading.profile_name, reading.resource_name)
    if not dr_exist:
        return
    if dr.tag != "":
        if reading.tags is None:
            reading.tags = {}
        reading.tags["tag"] = dr.tag
