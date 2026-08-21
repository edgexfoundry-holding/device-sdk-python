# Device Simple Example

A minimal device service built with the Python Device Service SDK. It simulates a device
(`fake`) with two commands:

- `Get` (read): returns a random integer (`random-number` resource, 0-100, changes every call)
- `Set` (write): toggles a simulated switch (`switch` resource)

This example demonstrates the core SDK capabilities: REST API, device/profile provisioning,
read/write commands, event publishing to the EdgeX MessageBus, and Core Metadata registration.

## Prerequisites

- Docker and Docker Compose
- An EdgeX stack already running on the `edgex_edgex-network` network with:
  - `edgex-core-metadata` (port 59881)
  - `edgex-core-data` (port 59880)
  - `edgex-core-command` (port 59882)
  - `edgex-mqtt-broker` (port 1883)
  - `edgex-core-keeper` (port 8500)

  If you don't have one, get it from [edgex-compose](https://github.com/edgexfoundry/edgex-compose)
  and start it first.

## Run with Docker (recommended)

From the repository root:

```bash
docker build -f examples/simple/Dockerfile --build-context root=. -t device-simple:latest ./examples/simple
cd examples/simple
docker compose up -d
```

> **Note (China mainland users):** the Dockerfile does not pin a pip mirror by default.
> If pulls are slow, uncomment/set the `pip config set global.index-url` line in the
> `Dockerfile` to your preferred mirror before building.

Check that the service is healthy (takes ~30s):

```bash
docker inspect --format '{{.State.Health.Status}}' edgex-device-simple
# healthy
```

## Run Locally (without Docker)

```bash
pip install -e .                # from the repository root
cd examples/simple
python -m device_service        # requires a local MQTT broker, see mosquitto.conf
```

## Verifying the Service

### 1. Health check

```bash
curl http://localhost:59990/api/v3/ping
```

```json
{"apiVersion": "v3", "serviceName": "device-simple", "timestamp": "..."}
```

### 2. Read the random number

```bash
curl http://localhost:59990/api/v3/device/name/fake/Get
```

```json
{
  "apiVersion": "v3",
  "statusCode": 200,
  "event": {
    "deviceName": "fake",
    "profileName": "fake-profile",
    "sourceName": "random-number",
    "readings": [
      {
        "resourceName": "random-number",
        "valueType": "Int32",
        "value": "92"
      }
    ]
  }
}
```

Call it a few times — `value` changes on every read (it is a random 0-100 integer).

### 3. Write the switch

```bash
curl -X PUT http://localhost:59990/api/v3/device/name/fake/Set \
  -H "Content-Type: application/json" \
  -d '{"switch": "true"}'
```

```json
{"apiVersion": "v3", "statusCode": 200}
```

### 4. Inspect configuration

```bash
curl http://localhost:59990/api/v3/config | jq .config.Service
curl http://localhost:59990/api/v3/config | jq .config.Registry   # Type: core-keeper
```

### 5. Check service & SDK versions

```bash
curl http://localhost:59990/api/v3/version
```

### 6. Verify registration in EdgeX

The service registers itself with Core Metadata at startup. Query via core-command:

```bash
curl http://localhost:59882/api/v3/device/name/fake | jq .
```

And confirm events arrive in core-data (after a read with `ds-pushevent`):

```bash
curl -s "http://localhost:59990/api/v3/device/name/fake/Get?ds-pushevent=true" >/dev/null
sleep 1
curl "http://localhost:59880/api/v3/event/device/name/fake?limit=1" | jq .events[0].readings
```

### 7. Watch events on the MessageBus (optional)

```bash
docker exec -it edgex-mqtt-broker mosquitto_sub -t 'edgex/events/device/#' -v
```

Then trigger a read from another terminal — you should see JSON envelopes being published.

## Useful Query Parameters

Read commands support the standard device-service query params:

| Param | Effect |
|-------|--------|
| `ds-pushevent=true` | Push the resulting Event to the MessageBus / core-data |
| `ds-returnevent=false` | Do not return the Event in the HTTP response |
| `ds-regexcommand=true` | Treat the command name as a regular expression |

Example:

```bash
curl "http://localhost:59990/api/v3/device/name/fake/Get?ds-pushevent=true&ds-returnevent=false"
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Container `unhealthy` | `docker logs edgex-device-simple`; usually the EdgeX stack or MQTT broker is unreachable |
| `device "fake" not found` | The `fake` device comes from `res/devices/*.yaml`; check startup logs for provisioning errors |
| Build fails at `pip install /repo` | Build from the **repository root** with `--build-context root=.` as shown above |
| Port conflict on 59990 | Stop the conflicting service or change `SERVICE_PORT` + the `ports:` mapping in `docker-compose.yaml` |

## Files

```
examples/simple/
├── device_service.py       # SimpleDriver + Configuration + bootstrap entry
├── Dockerfile
├── docker-compose.yaml
├── mosquitto.conf          # local MQTT broker config (non-Docker runs)
└── res/
    ├── configuration.yaml  # SDK configuration (Service/MessageBus/Device/...)
    ├── profiles/           # device profile: fake-profile (random-number, switch)
    ├── devices/            # static device: fake
    └── provisionwatchers/  # (optional) auto-registration rules
```
