# Device Service SDK - Purpose

The EdgeX device service software development kits (SDKs) help developers create new device connectors for EdgeX. An SDK provides the common scaffolding that each device service needs. This allows developers to create new device/sensor connectors more quickly.

EdgeX provides two software development kits (SDKs) to help developers create new device services:
- Go SDK
- C SDK

The SDKs are libraries to be incorporated into a new micro service. They make writing a new device service much easier. By importing the SDK library of choice into your new device service project, you can focus on the details associated with getting and manipulating sensor data from your device via the specific protocol of your device. Other details, such as initialization of the device service, getting the service configured, sending sensor data to the EdgeX MessageBus, managing communications with core metadata, and much more are handled by the code in the SDK library. The code in the SDK also helps to ensure your device service adheres to rules and standards of EdgeX.

The Device Service SDK supports:
- Synchronous read and write operations
- Asynchronous device data collection
- Initialization and deconstruction of Driver Interface
- Initialization and destruction of Device Connection
- Framework for automated Provisioning Mechanism
- Support for multiple classes of Devices with Profiles
- Support for sets of actions triggered by a command
- Cached responses to queries

Source: https://docs.edgexfoundry.org/4.0.2/microservices/device/sdk/Purpose/