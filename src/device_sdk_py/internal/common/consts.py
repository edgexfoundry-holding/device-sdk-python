# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The common constants of the EdgeX Device Service SDK - ported from
`device-sdk-go/internal/common/consts.go` and `go-mod-core-contracts/v4/common`.

`consts.go` only defines a handful of SDK specific constants (`URLRawQuery`,
`SDKReservedPrefix`, ...); the service API routes (`ApiPingRoute`,
`ApiDeviceNameCommandNameRoute`, ...), the HTTP headers and the path / query parameter
names are imported from `go-mod-core-contracts/v4/common`.  This module merges both sets
of constants so that the Python SDK has a single home for them, mirroring the (now
exported) constants of the `app_functions_sdk_py.contracts.common.constants` module.
"""

# ---------------------------------------------------------------------------
# go-mod-core-contracts/v4/common constants
# ---------------------------------------------------------------------------

#: The base path of the v3 service APIs.
API_BASE = "/api/v3"
API_VERSION = "v3"

# Constants related to defined routes in the v3 service APIs.
API_PING_ROUTE = API_BASE + "/ping"
API_VERSION_ROUTE = API_BASE + "/version"
API_CONFIG_ROUTE = API_BASE + "/config"
API_METRICS_ROUTE = API_BASE + "/metrics"
API_SECRET_ROUTE = API_BASE + "/secret"

API_DISCOVERY_ROUTE = API_BASE + "/discovery"
API_DISCOVERY_BY_ID_ROUTE = API_DISCOVERY_ROUTE + "/{requestId}"
API_PROFILE_SCAN_ROUTE = API_BASE + "/profilescan"
API_PROFILE_SCAN_BY_DEVICE_NAME_ROUTE = API_PROFILE_SCAN_ROUTE + "/device/{name}"

API_DEVICE_ROUTE = API_BASE + "/device/{name}"
API_ALL_DEVICE_ROUTE = API_BASE + "/device/all"
API_DEVICE_COMMAND_ROUTE = API_BASE + "/device/name/{name}/{command}"

# Constants related to HTTP headers and content types.
CORRELATION_HEADER = "X-Correlation-ID"
CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_CBOR = "application/cbor"

# Constants related to defined url path names and parameters in the v3 service APIs.
NAME = "name"
COMMAND = "command"
REQUEST_ID = "requestId"
VALUE_TRUE = "true"
VALUE_FALSE = "false"
PUSH_EVENT = "ds-pushevent"
RETURN_EVENT = "ds-returnevent"
REGEX_COMMAND = "ds-regexcommand"

# Constants for the DeviceResource / DeviceCommand read/write permission.
READ_WRITE_R = "R"
READ_WRITE_W = "W"
READ_WRITE_RW = "RW"
READ_WRITE_WR = "WR"

# Constants for the Device OperatingState.
OPERATING_STATE_UP = "UP"
OPERATING_STATE_DOWN = "DOWN"

# ---------------------------------------------------------------------------
# device-sdk-go/internal/common/consts.go constants
# ---------------------------------------------------------------------------

#: The key used to store the un-filtered query parameters in a CommandRequest's
#: attributes map (Go `URLRawQuery`).
URL_RAW_QUERY = "urlRawQuery"

#: The prefix used to separate the SDK reserved query parameters from the ones passed
#: through to the ProtocolDriver (Go `SDKReservedPrefix`).
SDK_RESERVED_PREFIX = "ds-"

#: Indicates the version of the SDK - overwritten by the build.
SDK_VERSION = "0.0.0"

#: Indicates the version of the device service itself, not the SDK - overwritten by the
#: build.
SERVICE_VERSION = "0.0.0"

# ---------------------------------------------------------------------------
# device-sdk-go/internal/common/utils.go constants
# ---------------------------------------------------------------------------

EVENTS_SENT_NAME = "EventsSent"
READINGS_SENT_NAME = "ReadingsSent"
DEVICE_SERVICE_EVENT_PREFIX = "device"
BYPASS_VALIDATION_QUERY_PARAM = "bypassValidation"

# ---------------------------------------------------------------------------
# go-mod-core-contracts/v4/common messaging topic constants
# ---------------------------------------------------------------------------

# Command request/response topics
COMMAND_REQUEST_SUBSCRIBE_TOPIC = "command/request"
RESPONSE_TOPIC = "response"

# Event publish topic
EVENTS_PUBLISH_TOPIC = "events"

# System events topics
SYSTEM_EVENTS_PUBLISH_TOPIC = "system-events"
DEVICE_SYSTEM_EVENT_TYPE = "device"
DEVICE_PROFILE_SYSTEM_EVENT_TYPE = "device-profile"
PROVISION_WATCHER_SYSTEM_EVENT_TYPE = "provision-watcher"
DEVICE_SERVICE_SYSTEM_EVENT_TYPE = "device-service"

# System event actions
SYSTEM_EVENT_ACTION_ADD = "add"
SYSTEM_EVENT_ACTION_UPDATE = "update"
SYSTEM_EVENT_ACTION_DELETE = "delete"
SYSTEM_EVENT_ACTION_PROGRESS = "progress"

# Core Metadata service key (owner for profile delete events)
CORE_METADATA_SERVICE_KEY = "core-metadata"
