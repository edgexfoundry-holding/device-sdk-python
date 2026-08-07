# EdgeX MessageBus

## Introduction
Internal message bus for EdgeX service-to-service communications. Not meant as external entry point (except eKuiper Rules Engine).

### External Entry Points:
- REST API on all EdgeX services
- App Service with External MQTT/HTTP/Custom Trigger
- Core Command External MQTT Connection

### Services Using MessageBus:
- Device Services publish Events/Readings directly to MessageBus (not via REST to Core Data)
- Service Metrics published to MessageBus
- System Events published to MessageBus
- Command Request/Responses published by Core Command and Device Services
- Device validation requests from Core Metadata via MessageBus

## Message Envelope (EdgeX 4.0)
All messages wrapped in `MessageEnvelope` with metadata:
- Content Type (JSON or CBOR)
- Correlation Id
- NEW in v4: `EDGEX_MSG_BASE64_PAYLOAD` env var (default false) - payload can be JSON object, not double-encoded

## Implementations (go-mod-messaging)
Four implementations:

### Common Configuration (`MessageBus:` section)
| Property | Description |
|----------|-------------|
| Type | `mqtt` (default), `nats-core`, `nats-jetstream` |
| Host | Broker name/IP |
| Port | Broker port |
| Protocol | `tcp` for all |

### MQTT 3.1 (Default)
**Security:**
| Option | Default | Description |
|--------|---------|-------------|
| AuthMode | `none` | `none`, `usernamepassword`, `clientcert`, `cacert` |
| SecretName | blank | Credentials from SecretStore |

**Additional:**
| Option | Default | Description |
|--------|---------|-------------|
| ClientId | service key | Unique client name |
| Qos | `0` | 0: At most once, 1: At least once, 2: Exactly once |
| KeepAlive | `10` | Max seconds between control packets |
| Retained | `false` | Store message for future subscribers |
| AutoReconnect | `true` | Auto reconnect on loss |
| ConnectTimeout | `30` | Connection timeout seconds |
| CleanSession | `false` | Discard previous session |

### NATS Core
Interest-based, fire-and-forget, at-most-once QoS.

### NATS JetStream
Persistence layer, at-least-once QoS, supports exactly-once semantics with `ExactlyOnce` option.

**Security:**
| Option | Default | Description |
|--------|---------|-------------|
| AuthMode | `none` | `none`, `usernamepassword`, `clientcert`, `cacert` |
| NKeySeedFile | blank | Path to seed file |
| CredentialsFile | blank | Path to credentials file |

**Additional:**
| Option | Default | Description |
|--------|---------|-------------|
| ClientId | service key | Unique client name |
| Format | `nats` | Message format |

## Multi-level Topics and Wildcards
Supported for flexible subscriptions.

## Deployment
- MQTT: Configuration changes, CBOR encoding, event payload optimization, Docker
- NATS: Configuration changes, Docker

Source: https://docs.edgexfoundry.org/4.0.2/microservices/general/messagebus/