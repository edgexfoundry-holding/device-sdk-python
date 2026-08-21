# device-sdk-python — Architecture Design

Goal: deliver an independent, parallel SDK (EdgeX Device Service SDK for Python) that is
functionally equivalent to `edgexfoundry/device-sdk-go`, interoperating with the same EdgeX
core services over the same wire contracts. `device-sdk-go` serves only as the functional
reference, not as a source-level port.

Motivation: this SDK is introduced to advance EdgeX in the edge AI domain. Python is the
dominant language of the AI/ML ecosystem (inference runtimes, vision pipelines, NPU/CUDA
accelerators), and a Python-native device service SDK allows AI-oriented device services —
camera inference, sensor fusion with ML models, intelligent protocol gateways — to be built
as first-class citizens of the EdgeX framework.

## Reference Projects
- Go reference: `edgexfoundry/device-sdk-go` v4.x (feature parity checklist)
- Python reference: `edgexfoundry/app-functions-sdk-python` (reusable client/bootstrap/configuration concepts)

## ProtocolDriver (user-implemented, mirrors interfaces/protocoldriver.go)

Abstract base class `ProtocolDriver` with methods:
- `initialize(sdk) -> None`
- `handle_read_commands(device_name, protocols, reqs: list[CommandRequest]) -> list[CommandValue]`
- `handle_write_commands(device_name, protocols, reqs, params) -> None`
- `start() / stop(force)`
- `add_device / update_device / remove_device(device_name, protocols, admin_state)`
- `discover() -> None`
- `validate_device(device) -> None`

## Data Models (mirror pkg/models)
- `CommandValue` (resource_name, value_type, value, origin, tags), with a full set of typed accessors
- `CommandRequest` (resource_name, type, attributes, ...)
- `AsyncValues` (async report: device_name, source_name, command_values)
- `DiscoveredDevice`
- `Notify`

## SDK Main Class (mirrors pkg/service + interfaces/service.go)

`DeviceService` implements:
- Device/Profile/Watcher CRUD (backed by the Core Metadata client)
- In-memory caches (devices/profiles/provisionwatchers)
- AutoEvent scheduled acquisition (asynchronously sending AsyncValues)
- HTTP REST service (mirrors internal/controller/http): `/api/v3/device/{name}/{cmd}`
  GET (read) / PUT (write), `/api/v3/discovery`, `/api/v3/ping`, `/api/v3/version`, `/api/v3/metrics`
- MessageBus commands (MQTT; Redis/NATS via MessageClient abstraction)
- transformer: read results → Event/Reading (CommandValue mapping logic)
- Configuration loading (YAML + environment overrides) + custom config
- Logging, secrets, metrics (zero-dependency stdlib implementations; OpenBao for secure mode)

## Directory Structure
```
device-sdk-python/
├── pyproject.toml
├── src/device_sdk_py/
│   ├── __init__.py            # version
│   ├── interfaces/
│   │   ├── protocoldriver.py  # ProtocolDriver ABC
│   │   ├── manager.py         # AutoEventManager ABC
│   │   └── service.py         # DeviceServiceSDK interface
│   ├── models/                # CommandValue, CommandRequest, AsyncValues, DiscoveredDevice, Notify
│   ├── internal/
│   │   ├── cache/             # devices, profiles, provisionwatchers
│   │   ├── transformer/       # commandvalues -> Event/Reading
│   │   ├── autoevent/         # executor + manager
│   │   ├── application/       # command_read/write, callbacks, device return, profile scan
│   │   ├── autodiscovery/     # discovery scheduler + locker
│   │   ├── clients/           # logger, secret store, metrics, core data/command clients, TLS
│   │   ├── common/            # constants, errors, utils, configuration
│   │   ├── controller/http/   # REST routes
│   │   ├── controller/messaging/
│   │   ├── metadata/          # Core Metadata client
│   │   └── provision/         # YAML resource loader (devices, profiles, watchers)
│   └── service/               # DeviceService assembly, bootstrap entry point
├── examples/simple/           # example: SimpleDriver (mirrors example/driver/simpledriver.go)
└── tests/                     # unit tests
```

## Implementation Steps
1. Skeleton: pyproject, package structure, dependencies
2. models: CommandValue and full typed getters
3. interfaces: ProtocolDriver ABC + DeviceServiceSDK
4. internal/cache: three caches
5. internal/transformer: read → Event/Reading
6. internal/autoevent: scheduled acquisition
7. internal/controller/http: REST endpoints
8. bootstrap: Bootstrap() startup, DI/config/logging/secret/metrics
9. service: DeviceService main class assembling the above
10. examples/simple: SimpleDriver example + configuration.yaml + device/profile YAML
11. tests: unit tests

Key implementation constraints:
- Strictly mirror `device-sdk-go/pkg` and `internal` interface signatures and semantics
- Python 3.10+, src layout, pip installable
