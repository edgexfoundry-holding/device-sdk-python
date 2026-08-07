# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/application/command.go`.

`command_read` and `command_write` implement the application logic behind the REST
`/api/v3/device/{name}/{command}` endpoints (and are also used by the messaging command
subscription): they validate the service / device state, resolve the requested
DeviceCommand or DeviceResource from the profile cache, build the `CommandRequest` list,
invoke the ProtocolDriver read / write handlers and convert the resulting CommandValues
into an `Event` via the transformer.

The Go functions receive their dependencies (driver, configuration, device service model)
from the DI container; the Python port passes them as explicit arguments instead. Errors
are reported by raising `EdgexError` (the counterpart of the Go `errors.EdgeX` return
values), and the write-parameter value coercion is implemented by
`create_command_value_from_device_resource` which mirrors `createCommandValueFromDeviceResource`.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
import struct
from typing import Any, Dict, List, Optional

from ..cache import (
    ADMIN_STATE_LOCKED,
    Devices,
    Profiles,
    Device,
)
from ..common.consts import (
    OPERATING_STATE_DOWN,
    OPERATING_STATE_UP,
    READ_WRITE_R,
    READ_WRITE_W,
    URL_RAW_QUERY,
)
from ..common.utils import (
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_ALLOWED,
    KIND_SERVER_ERROR,
    KIND_SERVICE_LOCKED,
    EdgexError,
    create_edgx_error,
    update_operating_state,
)
from ...models import (
    VALUETYPE_BOOL,
    VALUETYPE_BOOL_ARRAY,
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT32_ARRAY,
    VALUETYPE_FLOAT64,
    VALUETYPE_FLOAT64_ARRAY,
    VALUETYPE_INT16,
    VALUETYPE_INT16_ARRAY,
    VALUETYPE_INT32,
    VALUETYPE_INT32_ARRAY,
    VALUETYPE_INT64,
    VALUETYPE_INT64_ARRAY,
    VALUETYPE_INT8,
    VALUETYPE_INT8_ARRAY,
    VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
    VALUETYPE_STRING,
    VALUETYPE_STRING_ARRAY,
    VALUETYPE_UINT16,
    VALUETYPE_UINT16_ARRAY,
    VALUETYPE_UINT32,
    VALUETYPE_UINT32_ARRAY,
    VALUETYPE_UINT64,
    VALUETYPE_UINT64_ARRAY,
    VALUETYPE_UINT8,
    VALUETYPE_UINT8_ARRAY,
    CommandRequest,
    CommandValue,
    create_command_value,
)
from ..transformer.transform import Event, command_values_to_event
from ..transformer.transformparam import (
    WriteParameterError,
    transform_write_parameter,
)
from ..transformer.transformresult import (
    TransformerError,
    _to_float32,
)

_logger = logging.getLogger(__name__)

#: The module level map of allowed request failures per Device, mirroring the
#: `AllowedRequestFailuresTracker` from the Go DI container. It is only used when the
#: configuration enables the Device Down auto-recovery (`AllowedFails` / `DeviceDownTimeout`).
_allowed_request_failures: Dict[str, int] = {}


def _device_option(configuration: Any, name: str, default: Any) -> Any:
    """Return the `configuration.device.<name>` option, or `default` when the
configuration (or the option) is not set. The Python configuration model is ported
    in a later phase, so the Device options are read defensively."""
    device = getattr(configuration, "device", None)
    if device is None:
        return default
    return getattr(device, name, default)


def set_failure_count(device_name: str, count: int) -> None:
    """Set the number of allowed request failures for the Device with the given name.

    """
    _allowed_request_failures[device_name] = count


def failure_count(device_name: str) -> int:
    """Return the remaining number of allowed request failures for the Device.

    """
    return _allowed_request_failures.get(device_name, 0)


def decrease_failure_count(device_name: str) -> int:
    """Decrease the allowed request failures count by one and return the new value.

    """
    _allowed_request_failures[device_name] = \
        _allowed_request_failures.get(device_name, 0) - 1
    return _allowed_request_failures[device_name]


def device_request_failed(device_name: str, configuration: Any,
                          logger: Optional[logging.Logger] = None,
                          device_service: Any = None) -> None:
    """Record a failed Device request and, once the allowed failures are exhausted, mark
    the Device as non-operational.

    The Core Metadata update and the background retry loop (`deviceReturn`) are
    triggered when failures are exhausted.
    """
    log = logger or _logger
    if _device_option(configuration, "allowed_fails", 0) > 0:
        if decrease_failure_count(device_name) == 0:
            device, ok = Devices().for_name(device_name)
            if not ok:
                return
            if device.operating_state != OPERATING_STATE_DOWN:
                log.info("Marking device %s non-operational", device_name)
                if device_service is not None and hasattr(device_service, "update_device_operating_state"):
                    device_service.update_device_operating_state(device_name, OPERATING_STATE_DOWN)
                else:
                    update_operating_state(device_name, OPERATING_STATE_DOWN, log)
            if _device_option(configuration, "device_down_timeout", 0) > 0:
                log.warning("Will retry device %s in %s seconds", device_name,
                            _device_option(configuration, "device_down_timeout", 0))


def device_request_succeeded(device: Device, configuration: Any,
                             logger: Optional[logging.Logger] = None,
                             device_service: Any = None) -> None:
    """Record a successful Device request, resetting the allowed failures count and
    restoring the Device operating state when it was down.

    """
    log = logger or _logger
    allowed_fails = _device_option(configuration, "allowed_fails", 0)
    if allowed_fails > 0 and failure_count(device.name) < allowed_fails:
        set_failure_count(device.name, allowed_fails)
        if device.operating_state == OPERATING_STATE_DOWN:
            log.info("Device %s is operational again", device.name)
            if device_service is not None and hasattr(device_service, "update_device_operating_state"):
                device_service.update_device_operating_state(device.name, OPERATING_STATE_UP)
            else:
                update_operating_state(device.name, OPERATING_STATE_UP, log)


def command_read(device_name: str, request_id: str, command_name: str, *,
                 driver: Any, configuration: Any, attributes: str = "",
                 regex_cmd: bool = True, device_service: Any = None,
                 logger: Optional[logging.Logger] = None) -> Optional[Event]:
    """Execute a Get (read) command on the Device with the given name.

    In
When the name refers to a DeviceCommand the whole command is read; a
    regular expression (default, unless `ds-regexcommand=false`) reads all matching
    DeviceResources, otherwise the name is treated as a single DeviceResource.

    Args:
        device_name: The name of the Device being read.
        request_id: The correlation id of the request (used for logging).
        command_name: The name of the DeviceCommand or DeviceResource, or a regexp.
        driver: The ProtocolDriver implementation.
        configuration: The service configuration (must expose the `device` options).
        attributes: The raw query parameters passed through to the driver.
        regex_cmd: Whether `command_name` is treated as a regexp over DeviceResources.
        device_service: The DeviceService model (used for the AdminState check); optional.
        logger: An optional logger used for request logging.

    Returns:
        The Event containing the readings, or None when the driver produced no readings.

    Raises:
        EdgexError: When the service / device state is invalid or the read fails.
    """
    log = logger or _logger
    if device_name == "":
        raise create_edgx_error(KIND_CONTRACT_INVALID, "device name is empty")
    if command_name == "":
        raise create_edgx_error(KIND_CONTRACT_INVALID, "command is empty")

    try:
        device = _validate_service_and_device_state(device_name, configuration,
                                                    device_service)
        _, cmd_exist = Profiles().device_command(device.profile_name, command_name)
        if cmd_exist:
            event = _read_device_command(device, command_name, attributes, driver,
                                         configuration)
        elif regex_cmd:
            event = _read_device_resources_regex(device, command_name, attributes,
                                                 driver, configuration)
        else:
            event = _read_device_resource(device, command_name, attributes, driver,
                                          configuration)
    except EdgexError:
        device_request_failed(device_name, configuration, log, device_service)
        raise

    device_request_succeeded(device, configuration, log, device_service)
    Devices().set_last_connected_by_name(device_name)
    log.debug("GET Device Command successfully. Device: %s, Source: %s, "
              "X-Correlation-ID: %s", device_name, command_name, request_id)
    return event


def command_write(device_name: str, request_id: str, command_name: str, *,
                  driver: Any, configuration: Any, requests: Dict[str, Any],
                  attributes: str = "", device_service: Any = None,
                  logger: Optional[logging.Logger] = None) -> Optional[Event]:
    """Execute a Set (write) command on the Device with the given name.

    In
When the name refers to a DeviceCommand all its ResourceOperations are
    written, otherwise the name is treated as a single DeviceResource.

    Args:
        device_name: The name of the Device being written.
        request_id: The correlation id of the request (used for logging).
        command_name: The name of the DeviceCommand or DeviceResource.
        driver: The ProtocolDriver implementation.
        configuration: The service configuration (must expose the `device` options).
        requests: The request body as a map of resource name to value.
        attributes: The raw query parameters passed through to the driver.
        device_service: The DeviceService model (used for the AdminState check); optional.
        logger: An optional logger used for request logging.

    Returns:
        The Event containing the written values (unless the command / resource is
        write-only), or None.

    Raises:
        EdgexError: When the service / device state is invalid or the write fails.
    """
    log = logger or _logger
    if device_name == "":
        raise create_edgx_error(KIND_CONTRACT_INVALID, "device name is empty")
    if command_name == "":
        raise create_edgx_error(KIND_CONTRACT_INVALID, "command is empty")

    try:
        device = _validate_service_and_device_state(device_name, configuration,
                                                    device_service)
        _, cmd_exist = Profiles().device_command(device.profile_name, command_name)
        if cmd_exist:
            event = _write_device_command(device, command_name, attributes, requests,
                                          driver, configuration)
        else:
            event = _write_device_resource(device, command_name, attributes, requests,
                                           driver, configuration)
    except EdgexError:
        device_request_failed(device_name, configuration, log, device_service)
        raise

    device_request_succeeded(device, configuration, log, device_service)
    Devices().set_last_connected_by_name(device_name)
    log.debug("SET Device Command successfully. Device: %s, Source: %s, "
              "X-Correlation-ID: %s", device_name, command_name, request_id)
    return event


# -- read helpers ----------------------------------------------------------


def _read_device_resource(device: Device, resource_name: str, attributes: str,
                          driver: Any, configuration: Any) -> Optional[Event]:
    """Read a single DeviceResource and convert the result to an Event.

    """
    dr, ok = Profiles().device_resource(device.profile_name, resource_name)
    if not ok:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                             f"DeviceResource {resource_name} not found")
    # check the device resource is not write-only
    if dr.properties.read_write == READ_WRITE_W:
        raise create_edgx_error(KIND_NOT_ALLOWED,
                             f"DeviceResource {dr.name} is marked as write-only")

    # prepare the CommandRequest
    req = CommandRequest(
        resource_name=dr.name,
        attributes=dict(dr.attributes),
        value_type=dr.properties.value_type)
    _set_attributes_query(req, attributes)

    results = _handle_read_commands(
        driver, device, [req],
        f"error reading DeviceResource {dr.name} for {device.name}")
    return _command_values_to_event(results, device, dr.name, configuration)


def _read_device_resources_regex(device: Device, regex_resource_name: str,
                                 attributes: str, driver: Any,
                                 configuration: Any) -> Optional[Event]:
    """Read all DeviceResources whose name matches the regexp and convert the results to
    an Event.

    In
The reference implementation compiles the regexp in POSIX (leftmost-longest) mode; Python's `re`
    has no POSIX mode so the plain module regexp is used.
    """
    try:
        regex = re.compile(regex_resource_name)
    except re.error as exc:
        raise create_edgx_error(KIND_CONTRACT_INVALID,
                             "failed to CompilePOSIX resource name") from exc

    device_resources, ok = Profiles().device_resources_by_regex(device.profile_name, regex)
    if not ok or len(device_resources) == 0:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                             f"Regex DeviceResource {regex_resource_name} not found")

    reqs: List[CommandRequest] = []
    for dr in device_resources:
        # check the device resource is not write-only
        if dr.properties.read_write == READ_WRITE_W:
            _logger.debug("DeviceResource %s is marked as write-only, skipping adding "
                          "to RegEx Read list", dr.name)
            continue
        req = CommandRequest(
            resource_name=dr.name,
            attributes=dict(dr.attributes),
            value_type=dr.properties.value_type)
        _set_attributes_query(req, attributes)
        reqs.append(req)

    if len(reqs) == 0:
        raise create_edgx_error(KIND_NOT_ALLOWED,
                             f"no readable resources matched with {regex_resource_name}")

    results = _handle_read_commands(
        driver, device, reqs,
        f"error reading Regex DeviceResource(s) {regex_resource_name} for {device.name}")
    return _command_values_to_event(results, device, regex_resource_name, configuration)


def _read_device_command(device: Device, command_name: str, attributes: str,
                         driver: Any, configuration: Any) -> Optional[Event]:
    """Read the DeviceCommand and convert the result to an Event.

    """
    dc, ok = Profiles().device_command(device.profile_name, command_name)
    if not ok:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                             f"DeviceCommand {command_name} not found")
    # check the device command is not write-only
    if dc.read_write == READ_WRITE_W:
        raise create_edgx_error(KIND_NOT_ALLOWED,
                             f"DeviceCommand {dc.name} is marked as write-only")
    # check the ResourceOperation count does not exceed MaxCmdOps defined in configuration
    max_cmd_ops = _device_option(configuration, "max_cmd_ops", 0)
    if max_cmd_ops > 0 and len(dc.resource_operations) > max_cmd_ops:
        raise create_edgx_error(
            KIND_SERVER_ERROR,
            f"GET command {dc.name} exceed device {device.name} MaxCmdOps ({max_cmd_ops})")

    # prepare the CommandRequests
    reqs: List[CommandRequest] = []
    for op in dc.resource_operations:
        dr_name = op.device_resource
        # check the DeviceResource in the ResourceOperation actually exists
        dr, ok = Profiles().device_resource(device.profile_name, dr_name)
        if not ok:
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"DeviceResource {dr_name} in GET commnd {dc.name} for {device.name} "
                f"not defined")
        req = CommandRequest(
            resource_name=dr.name,
            attributes=dict(dr.attributes),
            value_type=dr.properties.value_type)
        _set_attributes_query(req, attributes)
        reqs.append(req)

    results = _handle_read_commands(
        driver, device, reqs,
        f"error reading DeviceCommand {dc.name} for {device.name}")
    return _command_values_to_event(results, device, dc.name, configuration)


# -- write helpers ---------------------------------------------------------


def _write_device_resource(device: Device, resource_name: str, attributes: str,
                           requests: Dict[str, Any], driver: Any,
                           configuration: Any) -> Optional[Event]:
    """Write a single DeviceResource and convert the result to an Event.

    In
    """
    dr, ok = Profiles().device_resource(device.profile_name, resource_name)
    if not ok:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                             f"DeviceResource {resource_name} not found")
    # check the device resource is not read-only
    if dr.properties.read_write == READ_WRITE_R:
        raise create_edgx_error(KIND_NOT_ALLOWED,
                             f"DeviceResource {dr.name} is marked as read-only")

    # check the set parameters contain the provided DeviceResource
    value = requests.get(dr.name)
    if value is None:
        if dr.properties.default_value != "":
            value = dr.properties.default_value
        else:
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"DeviceResource {dr.name} not found in request body and no default "
                f"value defined")

    # create the CommandValue
    try:
        cv = create_command_value_from_device_resource(dr, value)
    except EdgexError as exc:
        raise create_edgx_error(KIND_CONTRACT_INVALID, "failed to create CommandValue") \
            from exc

    # prepare the CommandRequest
    req = CommandRequest(
        resource_name=cv.device_resource_name,
        attributes=dict(dr.attributes),
        value_type=cv.value_type)
    _set_attributes_query(req, attributes)

    # transform the write value
    if _device_option(configuration, "data_transform", True):
        _transform_write_parameter(cv, dr.properties)

    _handle_write_commands(driver, device, [req], [cv],
                           f"error writing DeviceResource {dr.name} for {device.name}")

    # the updated resource value will be published to the MessageBus as long as it's not
    # write-only
    if dr.properties.read_write != READ_WRITE_W:
        return _command_values_to_event([cv], device, resource_name, configuration)
    return None


def _write_device_command(device: Device, command_name: str, attributes: str,
                          requests: Dict[str, Any], driver: Any,
                          configuration: Any) -> Optional[Event]:
    """Write the DeviceCommand and convert the result to an Event.

    In
    """
    dc, ok = Profiles().device_command(device.profile_name, command_name)
    if not ok:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                             f"DeviceCommand {command_name} not found")
    # check the device command is not read-only
    if dc.read_write == READ_WRITE_R:
        raise create_edgx_error(KIND_NOT_ALLOWED,
                             f"DeviceCommand {dc.name} is marked as read-only")
    # check the ResourceOperation count does not exceed MaxCmdOps defined in configuration
    max_cmd_ops = _device_option(configuration, "max_cmd_ops", 0)
    if max_cmd_ops > 0 and len(dc.resource_operations) > max_cmd_ops:
        raise create_edgx_error(
            KIND_SERVER_ERROR,
            f"SET command {dc.name} exceed device {device.name} MaxCmdOps ({max_cmd_ops})")

    # create the CommandValues
    cvs: List[CommandValue] = []
    for ro in dc.resource_operations:
        dr_name = ro.device_resource
        # check the DeviceResource in the ResourceOperation actually exists
        dr, ok = Profiles().device_resource(device.profile_name, dr_name)
        if not ok:
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"DeviceResource {dr_name} in SET commnd {dc.name} for {device.name} "
                f"not defined")

        # check the request body contains the DeviceResource
        value = requests.get(ro.device_resource)
        if value is None:
            if ro.default_value != "":
                value = ro.default_value
            elif dr.properties.default_value != "":
                value = dr.properties.default_value
            else:
                raise create_edgx_error(
                    KIND_SERVER_ERROR,
                    f"DeviceResource {dr.name} not found in request body and no default "
                    f"value defined")

        # ResourceOperation mapping, notice that the order is opposite to the get command
        # mapping i.e. the mapping value is actually the key for the set command
        if len(ro.mappings) > 0:
            for key, mapped_value in ro.mappings.items():
                if mapped_value == value:
                    value = key
                    break

        # create the CommandValue
        try:
            cv = create_command_value_from_device_resource(dr, value)
        except EdgexError as exc:
            raise create_edgx_error(KIND_CONTRACT_INVALID, "failed to create CommandValue") \
                from exc
        cvs.append(cv)

    # prepare the CommandRequests and transform the write values
    reqs: List[CommandRequest] = []
    for cv in cvs:
        dr, _ = Profiles().device_resource(device.profile_name, cv.device_resource_name)
        req = CommandRequest(
            resource_name=cv.device_resource_name,
            attributes=dict(dr.attributes),
            value_type=cv.value_type)
        _set_attributes_query(req, attributes)
        reqs.append(req)

        if _device_option(configuration, "data_transform", True):
            _transform_write_parameter(cv, dr.properties)

    _handle_write_commands(driver, device, reqs, cvs,
                           f"error writing DeviceCommand {dc.name} for {device.name}")

    # the updated resource(s) value will be published to the MessageBus as long as they
    # are not write-only
    if dc.read_write != READ_WRITE_W:
        return _command_values_to_event(cvs, device, command_name, configuration)
    return None


# -- shared helpers ---------------------------------------------------------


def _set_attributes_query(req: CommandRequest, attributes: str) -> None:
    """Store the raw query parameters in the CommandRequest attributes under the
`urlRawQuery` key ().
    """
    if attributes != "":
        if len(req.attributes) <= 0:
            req.attributes = {}
        req.attributes[URL_RAW_QUERY] = attributes


def _handle_read_commands(driver: Any, device: Device, reqs: List[CommandRequest],
                          error_message: str) -> List[CommandValue]:
    """Execute the protocol-specific read operation, wrapping driver exceptions in an
`EdgexError` with the given message ( call
    and its error wrapping).
    """
    try:
        results = driver.handle_read_commands(device.name, device.protocols, reqs)
    except Exception as exc:
        raise create_edgx_error(KIND_SERVER_ERROR, error_message) from exc
    return list(results) if results is not None else []


def _handle_write_commands(driver: Any, device: Device, reqs: List[CommandRequest],
                           params: List[CommandValue], error_message: str) -> None:
    """Execute the protocol-specific write operation, wrapping driver exceptions in an
`EdgexError` with the given message ( call
    and its error wrapping).
    """
    try:
        driver.handle_write_commands(device.name, device.protocols, reqs, params)
    except Exception as exc:
        raise create_edgx_error(KIND_SERVER_ERROR, error_message) from exc


def _transform_write_parameter(cv: CommandValue, properties: Any) -> None:
    """Apply the incoming write data transformation, wrapping transformation failures in
an `EdgexError` of kind ContractInvalid ( and its error wrapping).
    """
    try:
        transform_write_parameter(cv, properties)
    except (WriteParameterError, TransformerError) as exc:
        raise create_edgx_error(KIND_CONTRACT_INVALID,
                             "failed to transform set parameter") from exc


def _command_values_to_event(results: List[CommandValue], device: Device,
                             source_name: str,
                             configuration: Any) -> Optional[Event]:
    """Convert the CommandValues produced by the driver into an Event via the transformer
    ( call and its error wrapping).
    """
    data_transform = _device_option(configuration, "data_transform", True)
    reading_units = _device_option(configuration, "reading_units", True)
    try:
        return command_values_to_event(results, device.name, source_name, data_transform, reading_units)
    except TransformerError as exc:
        raise create_edgx_error(KIND_SERVER_ERROR,
                             "failed to convert CommandValue to Event") from exc


def _validate_service_and_device_state(device_name: str, configuration: Any,
                                       device_service: Any = None) -> Device:
    """Validate that the Device Service and the Device are in a state that allows
    commands.

    The
    `device_service` is the DeviceService model whose `admin_state` is checked; the
    `configuration` provides the `allowed_fails` / `device_down_timeout` options used when
    the Device OperatingState is DOWN.
    """
    # check the Device Service AdminState
    if device_service is not None and \
            getattr(device_service, "admin_state", None) == ADMIN_STATE_LOCKED:
        raise create_edgx_error(KIND_SERVICE_LOCKED, "service locked")

    # check the requested Device exists
    device, ok = Devices().for_name(device_name)
    if not ok:
        raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, f"device {device_name} not found")

    # check the Device's AdminState
    if device.admin_state == ADMIN_STATE_LOCKED:
        raise create_edgx_error(KIND_SERVICE_LOCKED, f"device {device.name} locked")
    # check the Device's OperatingState; if it is a device return attempt, the operating
    # state is allowed to be DOWN
    if device.operating_state == OPERATING_STATE_DOWN:
        err = create_edgx_error(KIND_SERVICE_LOCKED,
                             f"device {device.name} OperatingState is DOWN")
        if _device_option(configuration, "allowed_fails", 0) == 0 or \
                _device_option(configuration, "device_down_timeout", 0) == 0:
            raise err
        if failure_count(device_name) > 0:
            raise err

    # check the Device's ProfileName
    if device.profile_name == "":
        raise create_edgx_error(KIND_SERVICE_LOCKED, "no associated device profile")

    return device


# -- write value coercion ---------------------------------------------------


def create_command_value_from_device_resource(dr: Any,
                                              value: Any) -> CommandValue:
    """Create a CommandValue from the request value coerced to the DeviceResource value
    type.

A `None` value produces a CommandValue with a `None` value; otherwise
    the value is stringified and converted according to `dr.properties.value_type`
    (JSON for the array / Object types, `strconv` semantics for the scalar types, base64
    big-endian decoding for the float fallback).

    Raises:
        EdgexError: When the value cannot be converted (KindServerError) or is invalid
            for the value type (KindContractInvalid).
    """
    if value is None:
        return CommandValue(
            device_resource_name=dr.name,
            value_type=dr.properties.value_type,
            value=None,
            tags={})

    v = str(value)
    value_type = dr.properties.value_type

    if value_type != VALUETYPE_STRING and v.strip() == "":
        raise create_edgx_error(
            KIND_CONTRACT_INVALID,
            f"empty string is invalid for {value_type} value type")

    if value_type == VALUETYPE_STRING:
        return create_command_value(dr.name, VALUETYPE_STRING, v)
    if value_type == VALUETYPE_STRING_ARRAY:
        return create_command_value(dr.name, VALUETYPE_STRING_ARRAY,
                                 _json_array(v, VALUETYPE_STRING_ARRAY))
    if value_type == VALUETYPE_BOOL:
        return create_command_value(dr.name, VALUETYPE_BOOL, _parse_bool(v, value_type))
    if value_type == VALUETYPE_BOOL_ARRAY:
        return create_command_value(dr.name, VALUETYPE_BOOL_ARRAY,
                                 _json_array(v, VALUETYPE_BOOL_ARRAY))
    if value_type == VALUETYPE_UINT8:
        return create_command_value(dr.name, VALUETYPE_UINT8, _parse_uint(v, 8, value_type))
    if value_type == VALUETYPE_UINT8_ARRAY:
        return create_command_value(dr.name, VALUETYPE_UINT8_ARRAY,
                                 _parse_uint_array(v, 8, value_type))
    if value_type == VALUETYPE_UINT16:
        return create_command_value(dr.name, VALUETYPE_UINT16, _parse_uint(v, 16, value_type))
    if value_type == VALUETYPE_UINT16_ARRAY:
        return create_command_value(dr.name, VALUETYPE_UINT16_ARRAY,
                                 _parse_uint_array(v, 16, value_type))
    if value_type == VALUETYPE_UINT32:
        return create_command_value(dr.name, VALUETYPE_UINT32, _parse_uint(v, 32, value_type))
    if value_type == VALUETYPE_UINT32_ARRAY:
        return create_command_value(dr.name, VALUETYPE_UINT32_ARRAY,
                                 _parse_uint_array(v, 32, value_type))
    if value_type == VALUETYPE_UINT64:
        return create_command_value(dr.name, VALUETYPE_UINT64, _parse_uint(v, 64, value_type))
    if value_type == VALUETYPE_UINT64_ARRAY:
        return create_command_value(dr.name, VALUETYPE_UINT64_ARRAY,
                                 _parse_uint_array(v, 64, value_type))
    if value_type == VALUETYPE_INT8:
        return create_command_value(dr.name, VALUETYPE_INT8, _parse_int(v, 8, value_type))
    if value_type == VALUETYPE_INT8_ARRAY:
        return create_command_value(dr.name, VALUETYPE_INT8_ARRAY,
                                 _parse_int_array(v, 8, value_type))
    if value_type == VALUETYPE_INT16:
        return create_command_value(dr.name, VALUETYPE_INT16, _parse_int(v, 16, value_type))
    if value_type == VALUETYPE_INT16_ARRAY:
        return create_command_value(dr.name, VALUETYPE_INT16_ARRAY,
                                 _parse_int_array(v, 16, value_type))
    if value_type == VALUETYPE_INT32:
        return create_command_value(dr.name, VALUETYPE_INT32, _parse_int(v, 32, value_type))
    if value_type == VALUETYPE_INT32_ARRAY:
        return create_command_value(dr.name, VALUETYPE_INT32_ARRAY,
                                 _parse_int_array(v, 32, value_type))
    if value_type == VALUETYPE_INT64:
        return create_command_value(dr.name, VALUETYPE_INT64, _parse_int(v, 64, value_type))
    if value_type == VALUETYPE_INT64_ARRAY:
        return create_command_value(dr.name, VALUETYPE_INT64_ARRAY,
                                 _parse_int_array(v, 64, value_type))
    if value_type == VALUETYPE_FLOAT32:
        return create_command_value(dr.name, VALUETYPE_FLOAT32,
                                 _parse_float32(v, value_type))
    if value_type == VALUETYPE_FLOAT32_ARRAY:
        return create_command_value(
            dr.name, VALUETYPE_FLOAT32_ARRAY,
            [_to_float32(float(item)) for item in _json_array(v, value_type)])
    if value_type == VALUETYPE_FLOAT64:
        return create_command_value(dr.name, VALUETYPE_FLOAT64,
                                 _parse_float64(v, value_type))
    if value_type == VALUETYPE_FLOAT64_ARRAY:
        return create_command_value(dr.name, VALUETYPE_FLOAT64_ARRAY,
                                 _json_array(v, value_type))
    if value_type == VALUETYPE_OBJECT:
        return create_command_value(dr.name, VALUETYPE_OBJECT,
                                 _normalize_to_object(value, v))
    if value_type == VALUETYPE_OBJECT_ARRAY:
        return create_command_value(dr.name, VALUETYPE_OBJECT_ARRAY,
                                 _normalize_to_object_array(value, v))

    raise create_edgx_error(KIND_SERVER_ERROR, "unrecognized value type")


def _conversion_error(v: str, value_type: str) -> EdgexError:
    """Build the conversion error message (KindServerError)."""
    return create_edgx_error(
        KIND_SERVER_ERROR,
        f"failed to convert set parameter {v} to ValueType {value_type}")


def _json_array(v: str, value_type: str) -> Any:
    """Decode a JSON array from the stringified value (used for the array value types)."""
    try:
        return json.loads(v)
    except (TypeError, ValueError) as exc:
        raise _conversion_error(v, value_type) from exc


def _parse_bool(v: str, value_type: str) -> bool:
    """Parse a bool value the way Go's `strconv.ParseBool` does."""
    if v in ("1", "t", "T", "TRUE", "true", "True"):
        return True
    if v in ("0", "f", "F", "FALSE", "false", "False"):
        return False
    raise _conversion_error(v, value_type)


def _parse_uint(v: str, bits: int, value_type: str) -> int:
    """Parse an unsigned integer with the given bit width, enforcing the range the way
    Go's `strconv.ParseUint(v, 10, bitSize)` does."""
    try:
        number = int(v, 10)
    except ValueError as exc:
        raise _conversion_error(v, value_type) from exc
    if number < 0 or number > (1 << bits) - 1:
        raise _conversion_error(v, value_type)
    return number


def _parse_uint_array(v: str, bits: int, value_type: str) -> List[int]:
    """Parse a comma separated unsigned integer array (used for the Uint*Array value types)."""
    result: List[int] = []
    for item in v.strip("[]").split(","):
        result.append(_parse_uint(item.strip(" "), bits, value_type))
    return result


def _parse_int(v: str, bits: int, value_type: str) -> int:
    """Parse a signed integer with the given bit width, enforcing the range the way Go's
    `strconv.ParseInt(v, 10, bitSize)` does."""
    try:
        number = int(v, 10)
    except ValueError as exc:
        raise _conversion_error(v, value_type) from exc
    if number < -(1 << (bits - 1)) or number > (1 << (bits - 1)) - 1:
        raise _conversion_error(v, value_type)
    return number


def _parse_int_array(v: str, bits: int, value_type: str) -> List[int]:
    """Decode a signed integer array from JSON and enforce the element range (the Go
`json.Unmarshal` into `[]int8` / `[]int16` /... rejects out-of-range numbers)."""
    arr = _json_array(v, value_type)
    result: List[int] = []
    for item in arr:
        if isinstance(item, bool) or not isinstance(item, int):
            raise _conversion_error(v, value_type)
        if not -(1 << (bits - 1)) <= item <= (1 << (bits - 1)) - 1:
            raise _conversion_error(v, value_type)
        result.append(item)
    return result


def _parse_float32(v: str, value_type: str) -> float:
    """Parse a float32 value.

    Mirrors the Go `strconv.ParseFloat(v, 32)` first and, on a syntax error, the base64 /
big-endian byte decoding fallback (`float32FromBytes`). An out of range (infinite)
    result raises the Go `KindServerError("NumError")`; a decoded NaN is rejected.
    """
    try:
        value = float(v)
    except ValueError:
        return _float_from_bytes(base64.b64decode(v), ">f", v, value_type, 32)
    if math.isinf(value):
        raise create_edgx_error(KIND_SERVER_ERROR, "NumError")
    return _to_float32(value)


def _parse_float64(v: str, value_type: str) -> float:
    """Parse a float64 value.

    Mirrors the Go `strconv.ParseFloat(v, 64)` first and, on a syntax error, the base64 /
big-endian byte decoding fallback (`float64FromBytes`). An out of range (infinite)
    result raises the Go `KindServerError("NumError")`; a decoded NaN is rejected.
    """
    try:
        value = float(v)
    except ValueError:
        return _float_from_bytes(base64.b64decode(v), ">d", v, value_type, 64)
    if math.isinf(value):
        raise create_edgx_error(KIND_SERVER_ERROR, "NumError")
    return value


def _float_from_bytes(data: bytes, fmt: str, v: str, value_type: str,
                      width: int) -> float:
    """Decode a big-endian float from the base64 decoded bytes, rejecting a decoded NaN
    (mirrors `float32FromBytes` / `float64FromBytes` and the NaN check in command.go)."""
    try:
        value = struct.unpack(fmt, data[:width])[0]
    except struct.error as exc:
        raise _conversion_error(v, value_type) from exc
    if math.isnan(value):
        raise create_edgx_error(
            KIND_SERVER_ERROR,
            f"fail to parse {v} to {value_type.lower()}, unexpected result {value}")
    return value


def _normalize_to_object(value: Any, v: str) -> Optional[Dict[str, Any]]:
    """Normalize the request value to a JSON object.

    : a string is decoded as JSON and
a dict is used as-is; any other type is rejected. Returns None for a `None` value.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            obj = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"failed to convert set parameter {v} to ValueType {VALUETYPE_OBJECT}") \
                from exc
        if not isinstance(obj, dict):
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"failed to convert set parameter {v} to ValueType {VALUETYPE_OBJECT}")
        return obj
    if isinstance(value, dict):
        return value
    raise create_edgx_error(
        KIND_SERVER_ERROR,
        f"unsupported type for Object: {type(value)}")


def _normalize_to_object_array(value: Any,
                               v: str) -> Optional[List[Dict[str, Any]]]:
    """Normalize the request value to a JSON object array.

    : a string is decoded as
    JSON, a list is used as-is (each element must be a dict); any other type is rejected.
    Returns None for a `None` value.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            obj = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"failed to convert set parameter {v} to ValueType "
                f"{VALUETYPE_OBJECT_ARRAY}") from exc
        arr = obj if isinstance(obj, list) else None
    elif isinstance(value, list):
        arr = value
    else:
        raise create_edgx_error(
            KIND_SERVER_ERROR,
            f"unsupported type for ObjectArray: {type(value)}")

    if arr is None:
        raise create_edgx_error(
            KIND_SERVER_ERROR,
            f"failed to convert set parameter {v} to ValueType {VALUETYPE_OBJECT_ARRAY}")
    for elem in arr:
        if not isinstance(elem, dict):
            raise create_edgx_error(
                KIND_SERVER_ERROR,
                f"failed to convert set parameter {v} to ValueType "
                f"{VALUETYPE_OBJECT_ARRAY}")
    return arr
