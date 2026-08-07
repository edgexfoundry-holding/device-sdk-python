# Device Service - Configuration

Please refer to the general [Common Configuration documentation](https://docs.edgexfoundry.org/4.0.2/microservices/configuration/CommonConfiguration/) for configuration properties common to all services.

## Key Configuration Properties

### Writable Properties (dynamically take effect without restart)

| Property | Default | Description |
|----------|---------|-------------|
| LogLevel | INFO | Log entry severity level |

### Writable.Reading*
| Property | Default | Description |
|----------|---------|-------------|
| ReadingUnits | true | Include units of measure in Reading |

### Writable.Telemetry*
See Common Configuration for Telemetry configuration common to all services.

| Metric | Default | Description |
|--------|---------|-------------|
| EventsSent | false | Built-in EventsSent metric |
| ReadingsSent | false | Built-in ReadingsSent metric |
| LastConnected | false | Built-in LastConnected metric |

### Clients.core-metadata*
| Property | Default | Description |
|----------|---------|-------------|
| Protocol | http | Protocol for service endpoint |
| Host | localhost | Host name or IP |
| Port | 59881 | Port exposed by target service |

### Device* (how device service communicates with device)
| Property | Default | Description |
|----------|---------|-------------|
| DataTransform | true | Apply transformations to numeric readings |
| MaxCmdOps | 128 | Max resources in a device command |
| MaxCmdResultLen | 256 | Max JSON string length for command results |
| ProfilesDir | ./res/profiles | Directory/URI for profile definition files |
| DevicesDir | ./res/devices | Directory/URI for device definition files |
| ProvisionWatchersDir | '' | Directory/URI for provision watcher files |
| EnableAsyncReadings | true | Handle async readings |
| AsyncBufferSize | 16 | Buffer size for async readings |
| AllowedFails | 0 | Consecutive failures before setting device DOWN |
| DeviceDownTimeout | 0 | Seconds before retrying DOWN device |
| Discovery/Enabled | false | Enable device discovery |
| Discovery/Interval | 30s | Interval between discovery runs |
| AutoEvents/SendChangedReadingsOnly | false | Only changed readings in auto events |

### MaxEventSize*
| Property | Default | Description |
|----------|---------|-------------|
| MaxEventSize | 0 | Max event size in KB (0 = system max) |

## URIs for Device Service Files (EdgeX 3.1+)
Supports loading from remote URIs. The directory field loads an index file specifying individual files.

### Device Definition URI Example
```json
["device1.yaml", "device2.yaml"]
// Results in:
http://example.com/devices/device1.yaml
http://example.com/devices/device2.yaml
```

### Device Profile/Provision Watchers URI Example
```json
{
  "Simple-Device": "Simple-Driver.yaml",
  "Simple-Device2": "Simple-Driver2.yml"
}
```

## Custom Configuration
Two ways:
1. **Driver** section - simple custom settings via `DriverConfigs()` API (returns `map[string]string`)
2. **DriverCustom** - structured configuration (Go/C specific)

## Secrets (EdgeX 3.0+)
- SecretStore config removed from service files, uses defaults + env vars
- Secure mode: POST to `/api/v3/secret` API
- Insecure mode: Use `Writable.InsecureSecrets` in configuration.yaml

Source: https://docs.edgexfoundry.org/4.0.2/microservices/device/Configuration/