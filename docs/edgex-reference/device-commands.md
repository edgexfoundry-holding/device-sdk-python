# Device Service - Device Commands

## Concept
Device commands instruct your device to take an action. Core command service accesses the database to query information about the device, device service and device profile to collect the information, and passes the request to the device service, which then passes it on to the device/sensor.

## Example and Types
Device commands specify access to reads and writes for multiple simultaneous device resources.

### Example: Aggregated Command
```yaml
deviceCommands:
  - name: "Counts"
    readWrite: "R"
    isHidden: false
    resourceOperations:
      - { deviceResource: "HumanCount" }
      - { deviceResource: "DogCount" }
```

### Two Types of Commands
1. **GET command** - requests data from the device (often parameter-less)
2. **SET command** - requests to take action/actuate or set configuration (requires request body with key/value pairs)

Source: https://docs.edgexfoundry.org/4.0.2/microservices/device/details/DeviceCommands/