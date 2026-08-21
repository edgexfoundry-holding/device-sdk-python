# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# Development Guide

This document targets maintainers and contributors of `device-sdk-python`. It records
architecture constraints, coding conventions, the testing strategy, and the release process.

---

## 1. Architecture Constraints (must not be violated)

1. **100% alignment with device-sdk-go v4**
   - Directory structure, module names, function signatures, constant names, error codes,
     and MessageBus topic formats must match the Go reference implementation exactly.
   - Only minimal adaptations for Python language features (type annotations, dataclasses,
     async/await, exceptions instead of multiple return values) are allowed.

2. **Zero-dependency core**
   - `internal/clients/{logger,secret,metrics}.py` use only the standard library.
   - Optional heavy dependencies (e.g. `cryptography` for TLS) must be lazily imported
     so the SDK starts without them installed.

3. **Copyright headers**
   ```python
   # Copyright (C) 2026 YIQISOFT
   # SPDX-License-Identifier: Apache-2.0
   ```

4. **Configuration-driven, zero hardcoding**
   - Ports, hosts, topic prefixes, QoS, KeepAlive, etc. are read from `configuration.yaml`
     / environment variables.
   - `59990` appears only as the fallback default in the `_DEFAULT_HTTP_PORT` constant.

5. **Unit tests first, network isolation**
   - Unit tests must not depend on external MQTT/Metadata/Redis services.
   - `_start_device_validation_handler`, `_start_command_subscription`, etc. silently
     degrade to a log message when the client is unavailable.

---

## 2. Directory and Module Responsibilities

| Path | Responsibility | Go equivalent |
|------|----------------|---------------|
| `interfaces/` | ProtocolDriver, DeviceServiceSDK, AutoEventManager ABCs | `pkg/interfaces/` |
| `models/` | CommandValue, CommandRequest, AsyncValues, DiscoveredDevice | `pkg/models/` |
| `internal/cache/` | Devices/Profiles/ProvisionWatchers singleton caches | `internal/cache/` |
| `internal/transformer/` | Mask/Shift/Base/Scale/Offset, assertion, mapping, CBOR | `internal/transformer/` |
| `internal/autoevent/` | Scheduled acquisition executor + manager | `internal/autoevent/` |
| `internal/application/` | command_read/write, callbacks, device return, profile scan | `internal/application/` |
| `internal/autodiscovery/` | Discovery scheduler + locker | `internal/autoevent/` (discovery) |
| `internal/clients/` | Logger, secret store, metrics, Core Data/Command clients, TLS manager | `pkg/clients/`, `internal/` |
| `internal/controller/http/` | REST routes: command, discovery, common endpoints | `internal/controller/http/` |
| `internal/controller/messaging/` | MQTT client, event publish, command sub, system events callback | `internal/controller/messaging/` |
| `internal/common/` | Constants, error codes, utils, configuration | `internal/common/` |
| `internal/metadata/` | Core Metadata client | `internal/metadata/` |
| `internal/provision/` | YAML resource loader (devices, profiles, watchers) | `internal/provision/` |
| `service/` | DeviceService assembly, bootstrap entry | `pkg/service/`, `service/` |
| `examples/simple/` | Minimal runnable example | `example/driver/simpledriver.go` |

---

## 3. Coding Conventions

### 3.1 Import style
```python
# stdlib
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# third party
import paho.mqtt.client as mqtt
import cbor2

# local: use absolute imports (package name device_sdk_py)
from device_sdk_py.models import CommandValue
from device_sdk_py.internal.common.consts import API_VERSION
from device_sdk_py.internal.common.utils import EdgexError
```
- Relative imports must not cross more than 2 levels (e.g. `from ...models`); use absolute imports instead.
- For circular dependencies, import inside a `TYPE_CHECKING` block.

### 3.2 Naming mapping
| Go | Python |
|----|--------|
| `CamelCase` functions/methods | `snake_case` |
| `PascalCase` types/constants | `PascalCase` (kept for export compatibility) |
| `UPPER_SNAKE_CASE` constants | `UPPER_SNAKE_CASE` |
| `errors.EdgeX` | `EdgexError` (exception) |
| Multiple return values `(T, error)` | return `T`, raise `EdgexError` on failure |
| `context.Context` | implicit (correlation_id passed through) |
| `sync.Mutex` | `threading.Lock` / `threading.Semaphore` |
| `chan T` | `queue.Queue[T]` |

### 3.3 Error code mapping
| Go `errors.Kind` | Python `EdgexErrorKind` | HTTP Status |
|------------------|-------------------------|-------------|
| `KindContractInvalid` | `KIND_CONTRACT_INVALID` | 400 |
| `KindEntityDoesNotExist` | `KIND_ENTITY_DOES_NOT_EXIST` | 404 |
| `KindNotAllowed` | `KIND_NOT_ALLOWED` | 405 |
| `KindStatusConflict` | `KIND_STATUS_CONFLICT` | 409 |
| `KindServiceLocked` | `KIND_SERVICE_LOCKED` | 423 |
| `KindServerError` | `KIND_SERVER_ERROR` | 500 |
| `KindNotImplemented` | `KIND_NOT_IMPLEMENTED` | 501 |

### 3.4 Constant sources
- HTTP routes, headers, query parameters, device states, permission strings → `internal/common/consts.py`
  (merges Go `consts.go` + `go-mod-core-contracts/common`).
- SDK-reserved prefixes such as `ds-`, `urlRawQuery`, `CorrelationHeader` likewise.

### 3.5 Docstrings
Every public function/class must include:
```python
def foo(bar: str) -> int:
    """Short description.

    Mirrors `GoFunctionName` in go-file.go: notes the corresponding Go source location.

    Args:
        bar: Parameter description.

    Returns:
        Return value description.

    Raises:
        EdgexError: When it is raised.
    """
```

---

## 4. MessageBus Protocol Details

### 4.1 Topic conventions (EdgeX v4)
```
Events:      edgex/events/device/<svc>/<profile>/<device>/<source>
Commands:    edgex/device/command/request/<svc>/<device>/<command>/<get|set>
Responses:   edgex/response/<svc>/<requestId>
SysEvents:   edgex/system-events/<svc>/<type>/<action>/<svc>   (publish)
             edgex/system-events/core-metadata/+/+/<svc>/#     (subscribe)
             edgex/system-events/core-metadata/deviceprofile/delete/#
             edgex/system-events/core-metadata/provisionwatcher/+/<baseSvc>/#
Validation:  edgex/<svc>/validate/device
```
- The base prefix defaults to `edgex` and is configurable via `MessageBus.BaseTopicPrefix`.
- Name field escaping: device/profile/command names must conform to RFC3986 (Go uses
  `PathBuilder.EnableNameFieldEscape`).

### 4.2 MessageEnvelope (v4)
```json
{
  "apiVersion": "v3",
  "correlationId": "uuid",
  "requestId": "uuid",
  "contentType": "application/json",
  "payload": { ... },
  "receivedTopic": "edgex/...",
  "queryParams": { "ds-pushevent": "true" }
}
```
- The `Checksum` field was removed (v3+).
- When `contentType` is `application/cbor`, the payload is base64-encoded CBOR bytes.

### 4.3 Publishing flow
1. `command_read/write` → `transformer.command_values_to_event` → `Event`
2. `publish_event()` → `encode_event_request()` (binary reading → CBOR, otherwise JSON)
3. Wrap in `MessageEnvelope` → check `MaxEventSize` → `client.publish(envelope, topic)`

---

## 5. Core Data Flows

### 5.1 Synchronous command (REST)
```
GET /api/v3/device/name/{name}/{command}
  → command.ReadController.get_command()
  → filter_query_params(ds-pushevent/ds-returnevent/ds-regexcommand)
  → application.command_read()
     → _validate_service_and_device_state()  // 423 check
     → Profiles().device_command/resource lookup
     → driver.handle_read_commands()
     → transformer.command_values_to_event()
        → transform_read_result (Mask→Shift→Base→Scale→Offset)
        → check_assertion (failure → OperatingState=DISABLED)
        → map_command_value (ResourceOperation mappings)
     → Event
  → ds-pushevent → _send_event_handler() → MessageBus
  → ds-returnevent → EventResponse (JSON/CBOR)
```

### 5.2 Async readings
```
ProtocolDriver → sdk.async_values_channel().put(AsyncValues)
  → DeviceService._pump_async_values() (background thread)
     → command_values_to_event()
     → _send_event_handler() → MessageBus
```

### 5.3 Discovery flow
```
POST /api/v3/discovery
  → DiscoveryController.discovery()
  → driver.discover() (background thread)
     → sdk.discovered_device_channel().put([DiscoveredDevice])
  → DeviceService._pump_discovered_devices()
     → match ProvisionWatchers (allow/block lists)
     → MetadataClient.add_device(bypass_validation=True)
     → local cache Devices().add()
     → publish discovery progress system event
```

---

## 6. Testing Strategy

### 6.1 Unit test layers
| Layer | File(s) | Coverage focus |
|-------|---------|----------------|
| Models | `tests/test_models.py` | CommandValue typed getters/setters, binary/array/object encoding |
| Cache | `tests/test_bootstrap.py` | Devices/Profiles/Watchers CRUD, admin/operating state, lastConnected |
| Transformer | `tests/test_transformer.py` | Mask/Shift/Base/Scale/Offset, overflow→"overflow", assertion, mapping |
| AutoEvent | `tests/test_autoevent.py` | Scheduling, concurrency, stop/restart |
| HTTP | `tests/test_simple_example.py` | ping/version/device GET/PUT, ds-* query params, discovery/profile scan |
| Messaging | `tests/test_system_events.py` etc. | client connect/pub/sub, command subscription, system events callback |
| Security | `tests/test_secure_mode.py`, `tests/test_application_aligned.py` | Secret store, JWT, TLS manager, application layer |

### 6.2 Running
```bash
# all tests
python -m pytest tests/ -q

# single file
python -m pytest tests/test_bootstrap.py -v

# coverage
python -m pytest tests/ --cov=device_sdk_py --cov-report=html
```

### 6.3 Mock rules
- External dependencies: MQTT client, Metadata client, Secret provider → patch with `MagicMock` in tests.
- Time: `time.time_ns()` → patch to a fixed value.
- Random IDs: `uuid.uuid4()` → patch to a predictable sequence.

---

## 7. Common Extension Points

### 7.1 Adding a data transformation
1. Add `transform_xxx()` in `transformresult.py`.
2. Insert it into the `transform_read_result()` chain (order: Mask→Shift→Base→Scale→Offset).
3. Call it in reverse on the write path via `_transform_write_parameter()`.
4. Cover forward/inverse/overflow in unit tests.

### 7.2 Adding a MessageBus type (e.g. NATS)
1. Add a `NatsMessageClient` implementing the `MessageClient` ABC in `client.py`.
2. Dispatch on `config.type` in `new_message_client()`.
3. Map NATS-specific parameters in the `Optional` config section.

### 7.3 Adding a system event type
1. Add `XXX_SYSTEM_EVENT_TYPE`, `SYSTEM_EVENT_ACTION_XXX` in `consts.py`.
2. Add a `_handle_xxx_system_event()` dispatcher in `callback.py`.
3. Add the new topic to `subscribe_system_events()`.
4. Implement the `DeviceService._on_xxx_*()` callbacks.

---

## 8. Release Checklist

| Step | Command |
|------|---------|
| Bump version | `src/device_sdk_py/__init__.py` + `pyproject.toml` |
| All tests green | `python -m pytest tests/ -q` |
| Example runs | `cd examples/simple && python -m device_service` (background) + `curl /api/v3/ping` |
| Docs in sync | README.md, DEVELOPMENT.md, CHANGELOG.md |
| Build distribution | `pip wheel . -w dist/` |
| Tag | `git tag v4.x.x && git push --tags` |

---

## 9. References

- [EdgeX Foundry documentation (latest)](https://docs.edgexfoundry.org/)
- [ADR 0011 - Device Service REST API](https://docs.edgexfoundry.org/latest/design/adr/device-service/0011-DeviceService-Rest-API/)
- [ADR 013 - Device Services Send Events via Message Bus](https://docs.edgexfoundry.org/latest/design/adr/device-service/013-Device-Services-Send-Events-via-Message-Bus/)
- [device-sdk-go v4 source](https://github.com/edgexfoundry/device-sdk-go)
- [go-mod-core-contracts v4](https://github.com/edgexfoundry/go-mod-core-contracts)
- [app-functions-sdk-python](https://github.com/edgexfoundry/app-functions-sdk-python)

---

## 10. Changelog

| Date | Version | Notes |
|------|---------|-------|
| 2026-08-06 | 4.0.0 | Initial port: REST, MessageBus, command sub, system events, async/discovery pumps, CBOR, assertion, full configuration |
| 2026-08-21 | 4.0.1 | Core Keeper registry support, TLS/mTLS manager, OpenBao secret store, JWT auto-refresh, Core Data/Command clients, default port 59990 |
