# Reference Implementation Analysis

## device-sdk-go (Primary Reference - Architecture 100% Copy)

### Structure: `internal/controller/messaging/`
Key files to port to Python:

| File | Purpose | Gap # |
|------|---------|-------|
| `callback.go` | MetadataSystemEventsCallback - subscribes to system events from Core Metadata | #6 |
| `command.go` | SubscribeCommands - handles command requests from MessageBus | #5 |
| `validation.go` | SubscribeDeviceValidation - device validation | Already done |

### Key Patterns from device-sdk-go:

#### 1. MetadataSystemEventsCallback (callback.go)
- Subscribes to 2-3 topics:
  - `edgex/system-events/core-metadata/+/+/<svc>/#` (Device/Profile/DeviceService events)
  - `edgex/system-events/core-metadata/deviceprofile/delete/#` (Profile Delete)
  - Instance name: `edgex/system-events/core-metadata/provisionwatcher/+/<baseSvc>/#`
- Actions: Add/Update/Delete Device/ProvisionWatcher; Update/Delete DeviceProfile; Update DeviceService
- Calls `application.AddDevice/UpdateDevice/DeleteDevice` etc.

#### 2. SubscribeCommands (command.go)
- Subscribes to `edgex/device/command/request/<svc>/#`
- Response publishes to `edgex/response/<svc>/<requestId>`
- Concurrency: `defaultMaxConcurrentCommands = 32` semaphore
- When full: reject with "service busy"
- Reuses `application.GetCommand/SetCommand`
- Handles query params: `ds-pushevent`, `ds-returnevent`, `ds-regexcommand`

---

## app-functions-sdk-python (Python Reference - Borrow Tools Only)

### Structure: `src/app_functions_sdk_py/messaging/`
```
messaging/
├── __init__.py
├── mqtt/
│   ├── __init__.py
│   └── client.py        # MQTT client using paho-mqtt
└── nats/
    ├── __init__.py
    └── client.py        # NATS client
```

### Key Components to Borrow:

#### MQTT Client (mqtt/client.py)
- `MQTTClientOptions` - dataclass for config from MessageBusConfig
- `_new_mqtt_client()` - creates paho.mqtt.Client with TLS/auth
- `MqttMessageClient` class implementing `MessageClient` interface:
  - `connect()` - connects to broker, starts loop
  - `publish(message, topic)` - publishes MessageEnvelope
  - `subscribe(topic_queues, error_queue)` - subscribes with callbacks
  - `unsubscribe(topics)` - unsubscribes
  - `disconnect()` - stops loop, disconnects

#### MessageClient Interface (interfaces/messaging.py)
- Abstract base class defining the contract

#### MessageEnvelope
- Used for wrapping messages with metadata (ContentType, CorrelationId)

---

## Our Implementation Plan (Per DEVLOG.md)

### P0 - Messaging Infrastructure (Unlock all pub/sub)
1. `src/device_sdk_py/internal/controller/messaging/mqtt_client.py` - Minimal MQTT client (borrow from app-functions-sdk-python)
2. `src/device_sdk_py/internal/controller/messaging/publish.py` - publish_event, publish_system_event, build_publish_topic, MessageEnvelope encoding
3. `service/device_service.py` - `_message_bus_config` returns full config; `run()` injects `send_event_handler=publish_event`; starts pumps

### P1 - Messaging Command Subscription (External MQTT commands)
4. `src/device_sdk_py/internal/controller/messaging/command.py` - `subscribe_commands` with semaphore, topic parsing, response publishing

### P1 - Metadata System Events Callback (Cache sync)
5. `src/device_sdk_py/internal/controller/messaging/callback.py` - `subscribe_system_events` with 3-4 topic subscriptions, SystemEvent decoding, 4 action handlers calling DeviceService methods

### P2 - Fixes
6. CBOR encoding in `_utils.py`
7. Assertion → OperatingState DISABLED in `transform.py`
8. Full MessageBus config structure

### Test Acceptance Criteria (from DEVLOG.md)
1. examples/simple starts, GET /api/v3/device/simple/random-number → 200 with Event
2. ds-pushevent=true → Event on MessageBus topic
3. PUT command → 200 with BaseResponse
4. Core Metadata changes → Python cache sync (callback test)
5. Profile delete → Python cache delete (profile delete topic test)
6. MQTT command request → response on edgex/response/<svc>/<reqId>
7. AutoEvent → Event on MessageBus (async pump test)
8. POST /discovery → driver.discover() → device registered (discovered pump + metadata client)
9. python -m unittest discover tests - all green
10. CBOR: Binary resource → Content-Type: application/cbor