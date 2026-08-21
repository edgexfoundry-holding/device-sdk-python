# Python Device Service SDK
[![License](https://img.shields.io/github/license/edgexfoundry/device-sdk-python)](https://choosealicense.com/licenses/apache-2.0/) [![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![EdgeX](https://img.shields.io/badge/EdgeX-4.x-red)](https://www.edgexfoundry.org/) [![GitHub Pull Requests](https://img.shields.io/github/issues-pr-raw/edgexfoundry/device-sdk-python)](https://github.com/edgexfoundry/device-sdk-python/pulls) [![GitHub Contributors](https://img.shields.io/github/contributors/edgexfoundry/device-sdk-python)](https://github.com/edgexfoundry/device-sdk-python/contributors) [![GitHub Commit Activity](https://img.shields.io/github/commit-activity/m/edgexfoundry/device-sdk-python)](https://github.com/edgexfoundry/device-sdk-python/commits)

## Overview

This repository is a Python package that can be used to build Python-based [device services](https://docs.edgexfoundry.org/latest/microservices/device/Ch-DeviceServices/) for use within the EdgeX framework.

The SDK is a functional parallel of the Go Device Service SDK (`device-sdk-go` v4.x): it implements the same Device Service REST API (v3), MessageBus event publishing and command subscription, metadata system events callback, auto-events, device discovery, and provision watchers, interoperating with the same EdgeX core services over the same wire contracts.

## Usage

Developers can make their own device service by implementing the [`ProtocolDriver`](src/device_sdk_py/interfaces/protocoldriver.py) abstract base class for their desired IoT protocol, and the `main` function to start the Device Service. To implement the `main` function, the [`bootstrap`](src/device_sdk_py/service/bootstrap.py) entry point can be optionally leveraged, or developers can write customized bootstrap code by themselves.

A minimal device service looks like this:

```python
from device_sdk_py.service.bootstrap import bootstrap

service = bootstrap(
    service_key="device-my-service",
    service_version="1.0.0",
    driver=MyDriver(),          # your ProtocolDriver implementation
    configuration=my_config,    # your Configuration subclass
)
service.run()
```

Please see the provided [simple device service](examples/simple) as an example, included in this repository.

### Implementing a ProtocolDriver

```python
from device_sdk_py.interfaces import ProtocolDriver
from device_sdk_py.models import CommandRequest, CommandValue

class MyDriver(ProtocolDriver):
    def initialize(self, sdk):
        self._sdk = sdk

    def handle_read_commands(self, device_name, protocols, reqs):
        results = []
        for req in reqs:
            value = read_from_device(req.resource_name)  # talk to your device
            results.append(CommandValue(
                device_resource_name=req.resource_name,
                value_type=req.value_type,
                value=value,
                origin=time.time_ns(),
            ))
        return results

    def handle_write_commands(self, device_name, protocols, reqs, params):
        for req, param in zip(reqs, params):
            write_to_device(req.resource_name, param.value)

    def start(self): ...
    def stop(self, force): ...
    def add_device(self, device_name, protocols, admin_state): ...
    def update_device(self, device_name, protocols, admin_state): ...
    def remove_device(self, device_name, protocols): ...
    def discover(self): ...
    def validate_device(self, device): ...
```

## Configuration

Configuration is loaded from `configuration.yaml` (see the [simple example](examples/simple/res/configuration.yaml)) and can be overridden by environment variables:

```bash
EDGEX_MESSAGEBUS_HOST=192.168.1.100
EDGEX_MESSAGEBUS_PORT=1883
EDGEX_REGISTRY_HOST=edgex-core-keeper
EDGEX_REGISTRY_PORT=8500
EDGEX_REGISTRY_TYPE=core-keeper
```

The `/api/v3/config` endpoint output is byte-for-byte compatible with the Go SDK (`PascalCase` keys, alphabetical ordering, zero values for empty fields).

## Device Service REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v3/ping` | Service health |
| GET | `/api/v3/version` | Service & SDK version |
| GET | `/api/v3/config` | Full configuration |
| GET | `/api/v3/metrics` | Service metrics |
| GET | `/api/v3/device/name/{name}/{command}` | Read command (query: `ds-pushevent`, `ds-returnevent`, `ds-regexcommand`) |
| PUT | `/api/v3/device/name/{name}/{command}` | Write command (body: `{resource: value}`) |
| POST | `/api/v3/discovery` | Trigger discovery |
| DELETE | `/api/v3/discovery` | Stop discovery |
| POST | `/api/v3/profilescan` | Trigger profile scan |

## Community

- Discussion: [https://github.com/orgs/edgexfoundry/discussions](https://github.com/orgs/edgexfoundry/discussions)
- Mailing lists: [https://lists.edgexfoundry.org/mailman/listinfo](https://lists.edgexfoundry.org/mailman/listinfo)

## License

[Apache-2.0](LICENSE)

## Versioning

Please refer to the EdgeX Foundry [versioning policy](https://wiki.edgexfoundry.org/pages/viewpage.action?pageId=21823969) for information on how EdgeX services are released and how EdgeX services are compatible with one another.  Specifically, device services (and the associated SDK), application services (and the associated app functions SDK), and client tools (like the EdgeX CLI and UI) can have independent minor releases, but these services must be compatible with the latest major release of EdgeX.

## Long Term Support

Please refer to the EdgeX Foundry [LTS policy](https://wiki.edgexfoundry.org/pages/viewpage.action?pageId=69173332) for information on support of EdgeX releases. The EdgeX community does not offer support on any non-LTS release outside of the latest release.
