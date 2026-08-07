# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/transformer/transform.go`.

`command_values_to_event` converts a list of CommandValues produced by a ProtocolDriver into
an `Event`. For each CommandValue it applies the outgoing data transformation (Mask /
Shift / Base / Scale / Offset), the assertion check and the ResourceOperation mapping, and
then converts the value into a `Reading` (binary / object / simple) tagged with a unique
origin timestamp.

The Go function returns an `errors.EdgeX`; this port raises `TransformerError` instead.
Since the Python SDK keeps its own data model, the `Event` and `Reading` dataclasses defined
here mirror the `dtos.Event` / `dtos.Reading` structures from go-mod-core-contracts rather
than depending on the (not installed) app-functions-sdk-python package.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..cache import (
    CacheError,
    Devices,
    Profiles,
)
from ...models import (
    VALUETYPE_BINARY,
    VALUETYPE_FLOAT32,
    VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
    VALUETYPE_STRING,
    CommandValue,
    create_command_value,
)
from .transformresult import (
    NAN,
    OVERFLOW,
    NaNTransformerError,
    OverflowTransformerError,
    TransformerError,
    _to_float32,
    check_assertion,
    map_command_value,
    transform_read_result,
)

#: The module-level previous origin used to guarantee strictly increasing unique origins
#:.
_previous_origin: int = 0
_origin_mutex = threading.Lock()


def get_unique_origin() -> int:
    """Return a strictly increasing timestamp in nanoseconds.

    Guarantees that two events created in the
    same nanosecond get distinct origins.
    """
    global _previous_origin
    with _origin_mutex:
        now = time.time_ns()
        if now <= _previous_origin:
            now = _previous_origin + 1
        _previous_origin = now
        return now


def _format_float32(value: float) -> str:
    """Format a float32 value the way Go's `%v` does (shortest representation that
    round-trips to the same float32)."""
    rounded = _to_float32(value)
    for precision in range(1, 10):
        candidate = f"{rounded:.{precision}g}"
        if _to_float32(float(candidate)) == rounded:
            return candidate
    return repr(rounded)


def _reading_value_string(cv: CommandValue) -> str:
    """Return the string form of the value stored in a simple reading.

Mirrors the Go `fmt.Sprintf("%v", cv.Value)` used by `dtos.NewSimpleReading`. Float32
    values are formatted with the shortest float32 representation to match Go's default
    formatting.
    """
    if cv.value_type == VALUETYPE_FLOAT32:
        return _format_float32(cv.value)
    return cv.value_to_string()


@dataclass
class Reading:
    """A single data point from a Device, aligned to `dtos.Reading` in go-mod-core-contracts.

    Attributes:
        reading_id: The ID of the reading.
        origin: The time the reading was generated (nanoseconds).
        device_name: The name of the Device that generated the reading.
        resource_name: The name of the Device Resource the reading is for.
        profile_name: The name of the Profile the Device is associated with.
        value_type: The data type of the value (see the `VALUETYPE_*` constants).
        units: The units of the value.
        value: The value of the reading for simple readings (its string form).
        binary_value: The raw value for Binary readings.
        object_value: The value for Object / ObjectArray readings.
        tags: Custom tags applied to the reading.
        media_type: The media type for Binary readings.
    """
    resource_name: str = ""
    value_type: str = ""
    origin: int = 0
    device_name: str = ""
    profile_name: str = ""
    reading_id: str = ""
    value: Optional[str] = None
    units: str = ""
    binary_value: Any = None
    object_value: Any = None
    tags: Dict[str, Any] = field(default_factory=dict)
    media_type: str = ""


@dataclass
class Event:
    """An Event carrying the readings of one Device operation, aligned to `dtos.Event`.

    Attributes:
        event_id: The ID of the Event (a UUID generated for each new Event).
        device_name: The name of the Device the Event is for.
        profile_name: The name of the Profile the Device is associated with.
        source_name: The name of the DeviceCommand / source the Event came from.
        origin: The time the Event was generated (nanoseconds).
        readings: The readings of the Event.
        tags: Custom tags applied to the Event.
    """
    event_id: str
    device_name: str
    profile_name: str
    source_name: str
    origin: int
    readings: List[Reading]
    tags: Dict[str, Any] = field(default_factory=dict)


def command_value_to_reading(cv: CommandValue, device_name: str, profile_name: str,
                             media_type: str, event_origin: int) -> Reading:
    """Convert a CommandValue to a Reading.

    A `None` value produces a null
    reading, a Binary value a binary reading, an Object / ObjectArray value an object
reading and every other value a simple reading. The reading origin is the CommandValue
    origin when it was set by the ProtocolDriver, otherwise the Event origin.
    """
    if cv.value is None:
        reading = Reading(
            reading_id=str(uuid.uuid4()),
            profile_name=profile_name,
            device_name=device_name,
            resource_name=cv.device_resource_name,
            value_type=cv.value_type,
            value=None)
    elif cv.value_type == VALUETYPE_BINARY:
        reading = Reading(
            reading_id=str(uuid.uuid4()),
            profile_name=profile_name,
            device_name=device_name,
            resource_name=cv.device_resource_name,
            value_type=VALUETYPE_BINARY,
            binary_value=cv.binary_value(),
            media_type=media_type)
    elif cv.value_type == VALUETYPE_OBJECT:
        reading = Reading(
            reading_id=str(uuid.uuid4()),
            profile_name=profile_name,
            device_name=device_name,
            resource_name=cv.device_resource_name,
            value_type=VALUETYPE_OBJECT,
            object_value=cv.value)
    elif cv.value_type == VALUETYPE_OBJECT_ARRAY:
        reading = Reading(
            reading_id=str(uuid.uuid4()),
            profile_name=profile_name,
            device_name=device_name,
            resource_name=cv.device_resource_name,
            value_type=VALUETYPE_OBJECT_ARRAY,
            object_value=cv.value)
    else:
        reading = Reading(
            reading_id=str(uuid.uuid4()),
            profile_name=profile_name,
            device_name=device_name,
            resource_name=cv.device_resource_name,
            value_type=cv.value_type,
            value=_reading_value_string(cv))

    # use the Origin if it was already set by the ProtocolDriver implementation,
    # otherwise use the same Origin of the upstream Event
    if cv.origin != 0:
        reading.origin = cv.origin
    else:
        reading.origin = event_origin

    return reading


def command_values_to_event(cvs: Optional[List[CommandValue]], device_name: str,
                            source_name: str, data_transform: bool = True,
                            reading_units: bool = True) -> Optional[Event]:
    """Convert a list of CommandValues into an Event for the given Device.

    Returns None when no readings
    were produced (an uninitialized or empty reading set). For each CommandValue the
    outgoing data transformation, the assertion check and the ResourceOperation mapping are
    applied; an overflowing or NaN value is replaced by a String reading with the value
    "overflow" / "NaN".

    Args:
        cvs: The CommandValues to convert.
        device_name: The name of the device.
        source_name: The source name for the event.
        data_transform: Whether to apply data transformation.
        reading_units: Whether to include units in the readings. When False, units are omitted.

    Raises:
        TransformerError: When the Device or a DeviceResource is not found, an assertion
            fails, or a value cannot be transformed.
    """
    if cvs is None:
        # in some case the device service driver implementation would generate no readings;
        # in this case no Event is created.
        return None

    device, exist = Devices().for_name(device_name)
    if not exist:
        raise TransformerError(f"failed to find device {device_name}")

    transforms_ok = True
    origin = get_unique_origin()
    tags: Dict[str, Any] = {}
    readings: List[Reading] = []

    for cv in cvs:
        if cv is None:
            continue
        # double-check the CommandValue returned from the ProtocolDriver against the
        # DeviceProfile.
        dr, ok = Profiles().device_resource(device.profile_name, cv.device_resource_name)
        if not ok:
            raise TransformerError(
                f"failed to find DeviceResource {cv.device_resource_name} in Device "
                f"{device_name} for CommandValue ({cv})")

        # perform the outgoing data transformation
        if data_transform and cv.value is not None:
            try:
                transform_read_result(cv, dr.properties)
            except OverflowTransformerError:
                cv = create_command_value(cv.device_resource_name, VALUETYPE_STRING, OVERFLOW)
            except NaNTransformerError:
                cv = create_command_value(cv.device_resource_name, VALUETYPE_STRING, NAN)
            except TransformerError:
                transforms_ok = False

        # assertion
        try:
            check_assertion(cv, dr.properties.assertion)
        except TransformerError:
            # Per ADR 0011: assertion failure sets device OperatingState to DISABLED
            Devices().update_operating_state(device.name, "DISABLED")
            raise

        for key, value in cv.tags.items():
            tags[key] = value

        # ResourceOperation mapping
        try:
            ro = Profiles().resource_operation(device.profile_name, cv.device_resource_name)
        except CacheError:
            # this allows the SDK to directly read a DeviceResource without DeviceCommands
            # being defined.
            pass
        else:
            if len(ro.mappings) > 0:
                mapped_cv = map_command_value(cv, ro.mappings)
                if mapped_cv is not None:
                    cv = mapped_cv

        reading = command_value_to_reading(
            cv, device.name, device.profile_name, dr.properties.media_type, origin)
        if reading_units:
            reading.units = dr.properties.units
        else:
            reading.units = ""
        readings.append(reading)

    if not transforms_ok:
        raise TransformerError(f"failed to transform value for {device_name}")

    if len(readings) > 0:
        # merge the Device tags into the Event tags (mirrors the device part of the Go
        # `sdkCommon.AddEventTags`; the DeviceCommand tags are not applicable since the
        # Python DeviceCommand model carries no tags).
        for key, value in device.tags.items():
            tags[key] = value
        return Event(
            event_id=str(uuid.uuid4()),
            profile_name=device.profile_name,
            device_name=device.name,
            source_name=source_name,
            origin=origin,
            readings=readings,
            tags=tags)

    return None


