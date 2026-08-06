# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

# EdgeX Device SDK for Python

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![EdgeX](https://img.shields.io/badge/EdgeX-4.x-red)](https://www.edgexfoundry.org/)

Python port of the **EdgeX Foundry Device Service SDK** (originally `device-sdk-go` v4.x).
Implements the full Device Service REST API, MessageBus event publishing, command subscription,
metadata system events callback, auto-events, discovery, and provision watchers.

## Features

- **REST API** (EdgeX v3): `/api/v3/ping`, `version`, `config`, `metrics`, `device/name/{name}/{command}` (GET/PUT), `discovery`, `profilescan`
- **MessageBus Publishing**: Events → `edgex/events/device/<svc>/<profile>/<device>/<source>` (JSON/CBOR, MaxEventSize)
- **Command Subscription**: MQTT `edgex/command/request/<svc>/#` → semaphore-limited (default 32) → response on `edgex/response/<svc>/<reqId>`
- **Metadata System Events**: Subscribes to `edgex/system-events/<svc>/#`, `device-profile/delete/#`; dispatches Device/Profile/Watcher/Service CRUD
- **Async Readings Pump**: Consumes `AsyncValues` channel → transforms → publishes
- **Discovery Pump**: Consumes discovered devices → matches ProvisionWatchers → registers
- **Data Transformations**: Mask → Shift → Base → Scale → Offset (read); inverse (write)
- **Assertions & Mappings**: Per ADR 0011; assertion failure sets OperatingState=DISABLED
- **CBOR Encoding**: Automatic for binary readings
- **Configuration**: YAML-based, environment variable overrides, full MessageBus section

## Quick Start

### Prerequisites
- Python 3.10+
- MQTT broker (e.g., mosquitto on `127.0.0.1:1883`)
- EdgeX Core Metadata (optional, for registration)

### Install
```bash
git clone https://github.com/your-org/device-sdk-python
cd device-sdk-python
pip install -e .
```

### Run Simple Example
```bash
# Terminal 1: Start MQTT broker
mosquitto -p 1883

# Terminal 2: Run device-simple
cd examples/simple
python -m device_service

# Terminal 3: Test
curl http://localhost:59986/api/v3/ping
curl http://localhost:59986/api/v3/device/name/fake/Get
curl -X PUT http://localhost:59986/api/v3/device/name/fake/Set \
  -H "Content-Type: application/json" \
  -d '{"random-number":"42"}'
```

### With Docker
```bash
docker build -t device-sdk-python .
docker run -p 59986:59986 \
  -e EDGEX_MESSAGEBUS_HOST=host.docker.internal \
  device-sdk-python
```

## Architecture

```
device-sdk-python/
├── src/device_sdk_py/
│   ├── interfaces/          # ProtocolDriver, DeviceServiceSDK (ABCs)
│   ├── models/              # CommandValue, CommandRequest, AsyncValues, DiscoveredDevice
│   ├── internal/
│   │   ├── cache/           # Devices, Profiles, ProvisionWatchers (singletons)
│   │   ├── transformer/     # Mask/Shift/Base/Scale/Offset, assertions, mappings
│   │   ├── autoevent/       # Scheduled event executor
│   │   ├── application/     # command_read, command_write
│   │   ├── controller/
│   │   │   ├── http/        # REST routes (command, discovery, common)
│   │   │   └── messaging/   # MQTT client, event publish, command sub, system events callback
│   │   ├── common/          # Constants, errors, utils
│   │   ├── metadata/        # Core Metadata client (placeholder)
│   │   └── provision.py     # YAML resource loader
│   ├── service/             # DeviceService, Bootstrap
│   └── __init__.py
├── examples/simple/         # Minimal runnable Device Service
│   ├── device_service.py    # Configuration + SimpleDriver
│   └── res/                 # profiles/, devices/, provisionwatchers/, configuration.yaml
└── tests/                   # Unit tests
```

## Configuration

`examples/simple/res/configuration.yaml` (key sections):

```yaml
Service:
  Host: "0.0.0.0"
  Port: 59986

MessageBus:              # EdgeX v4 style
  Host: "127.0.0.1"
  Port: 1883
  Type: "mqtt"
  BaseTopicPrefix: "edgex"
  AuthMode: "none"
  Optional:
    ClientId: ""
    Qos: "0"
    KeepAlive: "60"
    Retained: "false"
    AutoReconnect: "true"
    CleanSession: "true"
    ConnectTimeout: "5"
    SkipCertVerify: "false"

Device:
  AsyncBufferSize: 1
  ProfilesDir: "./res/profiles"
  DevicesDir: "./res/devices"
  ProvisionWatchersDir: "./res/provisionwatchers"
  Labels: ["simple", "simulated"]
  Discovery:
    Enabled: false
    Interval: "30s"

Clients:
  core-metadata:
    Host: "127.0.0.1"
    Port: 59881
```

Environment variable overrides (all sections):
```bash
EDGEX_MESSAGEBUS_HOST=192.168.1.100
EDGEX_MESSAGEBUS_PORT=1883
EDGEX_MESSAGEBUS_TOPIC=edgex
```

## Implementing a ProtocolDriver

```python
from device_sdk_py.interfaces import ProtocolDriver
from device_sdk_py.models import CommandRequest, CommandValue

class MyDriver(ProtocolDriver):
    def initialize(self, sdk):
        self._sdk = sdk

    def handle_read_commands(self, device_name, protocols, reqs):
        results = []
        for req in reqs:
            # Talk to your device via protocols[protocol_name]
            value = read_from_device(req.resource_name)
            results.append(CommandValue(
                device_resource_name=req.resource_name,
                value_type=req.value_type,
                value=value,
                origin=time.time_ns()
            ))
        return results

    def handle_write_commands(self, device_name, protocols, reqs, params):
        for req, param in zip(reqs, params):
            write_to_device(req.resource_name, param.value)

    def start(self): pass
    def stop(self, force): pass
    def add_device(self, device_name, protocols, admin_state): pass
    def update_device(self, device_name, protocols, admin_state): pass
    def remove_device(self, device_name, protocols): pass
    def discover(self): pass
    def validate_device(self, device): pass
```

Register in `device_service.py`:
```python
from device_sdk_py.service.bootstrap import bootstrap

bootstrap(
    service_key="device-my-driver",
    service_version="1.0.0",
    driver=MyDriver(),
    configuration=my_config,
).run()
```

## REST API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v3/ping` | Service health |
| GET | `/api/v3/version` | Service & SDK version |
| GET | `/api/v3/config` | Full configuration |
| GET | `/api/v3/metrics` | Prometheus metrics |
| GET | `/api/v3/device/name/{name}/{command}` | Read command (query: `ds-pushevent`, `ds-returnevent`, `ds-regexcommand`) |
| PUT | `/api/v3/device/name/{name}/{command}` | Write command (body: `{resource: value}`) |
| POST | `/api/v3/discovery` | Trigger discovery |
| DELETE | `/api/v3/discovery` | Stop discovery |
| POST | `/api/v3/profilescan` | Trigger profile scan |
| DELETE | `/api/v3/profilescan/device/{name}` | Stop profile scan |

## MessageBus Topics

| Purpose | Topic Pattern |
|---------|---------------|
| Event Publish | `edgex/events/device/<svc>/<profile>/<device>/<source>` |
| Command Request | `edgex/command/request/<svc>/<device>/<command>/<get\|set>` |
| Command Response | `edgex/response/<svc>/<requestId>` |
| System Events | `edgex/system-events/<svc>/<type>/<action>` |
| Profile Delete | `edgex/system-events/device-profile/delete/#` |
| Validation | `edgex/<svc>/validate/device` |

## Testing

```bash
# Unit tests
python -m unittest discover tests -v

# With coverage
pip install coverage
coverage run -m unittest discover tests
coverage report -m
```

## Project Status

| Component | Status |
|-----------|--------|
| REST API (v3) | ✅ Complete |
| Event Publishing | ✅ Complete |
| Command Subscription | ✅ Complete |
| System Events Callback | ✅ Complete |
| Async/Discovered Pumps | ✅ Complete |
| Data Transformations | ✅ Complete |
| Assertions/Mappings | ✅ Complete |
| CBOR Encoding | ✅ Complete |
| Core Metadata Client | ⚠️ Placeholder |
| NATS Support | ⚠️ Planned |
| Secure Mode (TLS) | ⚠️ Planned |

## License

Apache 2.0 © 2026 YIQISOFT

Based on EdgeX Foundry Device SDK Go (Apache 2.0 © IOTech).