# 开发日志（自主推进期间）

## 2026-08-06 凌晨~早上（主人休息期间自主开发）

### 背景与决策
- 目标：把 `device-sdk-go` 移植为 Python 版，复用 `app-functions-sdk-python` 的部分工具代码。
- **方向校准（主人明确）：架构 100% 照抄 device-sdk-go；app-functions-sdk-python 仅按需借用工具，不得反客为主。**
- 交付纪律（主人明确）：全部代码交给 opencode 写，我（傻蛋）只负责规划、指挥、验收、改文案版权头。我不自己手写代码文件。
- 版权头约定：`# Copyright (C) 2026 YIQISOFT`（不允许 IOTech）。

### 踩坑记录
- opencode 反复超时：因为让它"读熟 app-functions-sdk-python 的大量 DTO/clients 再写"，它陷入过度 research。
- **对策：任务拆小（一次一个子模块）、在 prompt 中把关键结构/字段直接写明、明确"不得读大量文件、直接基于 known 语义写"。**

### 进度状态（截至早上）
已完成（语法全过）：models、interfaces、internal/cache、internal/transformer、internal/autoevent、
internal/application、internal/common、internal/controller/http。
未完成：service/（DeviceService 装配）、bootstrap/（启动引导）、example/device-simple、tests。

### 政策（本次自主时段）
继续推进 service → bootstrap → internal/controller/messaging（可选）→ example/device-simple（核心示例）→ tests。
每次用 opencode 单点小任务，聚焦 device-sdk-go 参考；app-functions-sdk-python 只在需要 core-data/metadata
客户端时借用。

---

## 2026-08-06 中午~下午：文档/ADR 逐项对照 → 差距清单

### 阅读来源
- 官方文档 4.1（`/4.1/...`）：Device Service Overview、Configuration、Device Discovery & Provision Watchers、Auto Events、SDK Purpose、Device Definitions、API Reference、Device System Events、design/TOC。
- ADR 0011：Device Service REST API（已批准，EdgeX 2.x+）。
- ADR 013：Device Services Send Events via Message Bus（已批准，Ireland 版）。
- Go SDK 参照：`device-sdk-go` v4.1.0-dev（module `device-sdk-go/v4`，go-mod-core-contracts/v4 v4.1.0-dev.40）。

### ADR/文档结论清单（已验收）

#### ADR 0011 - Device Service REST API
1. **通用端点**：`/api/v3/ping`、`/api/v3/version`、`/api/v3/config`、`/api/v3/metrics` ✓（Python 已注册）。
2. **Callback 端点**（v3+ 已废弃，改为 MessageBus System Events）：
   - `callback/device` PUT/POST、`callback/device/name/{name}` DELETE
   - `callback/profile` PUT
   - `callback/watcher` PUT/POST、`callback/watcher/name/{name}` DELETE
   → Python 未实现（符合 v4 设计，**需实现 Messaging 回调订阅**，见 Gap #5）。
3. **Device 命令端点**：`GET/PUT /api/v3/device/{name}/{command}` ✓（已实现 `command.py`）。
   - 返回码：200/404/405/423/500 ✓（Kind→HTTP 映射已覆盖）。
   - Query 参数：`ds-pushevent`（默认 false）、`ds-returnevent`（默认 true）、前缀 `ds-` 保留 ✓。
4. **数据格式**：值按字符串表达，二进制触发 CBOR 编码 ✓（转换器已支持，但 HTTP 响应 **仅 JSON**，见 Gap #7）。
5. **设备状态**：Admin LOCKED / Operating DOWN → HTTP 423 ✓（`_validate_service_and_device_state`）。
6. **数据变换**：Mask → Shift → Base → Scale → Offset（顺序固定，读时正向、写时逆向）✓（`transformresult.py`）。
7. **Assertion**：失败返回 500 + "Assertion failed for device resource: ..., with value: ..."，**设备 OperatingState 置 DISABLED**，且无视 `ds-returnevent` 必须返回 ✓（异常抛出在 returnevent 检查前），**但 OperatingState 置 DISABLED 尚未落地**，见 Gap #8。
8. **Mappings**：DeviceCommand 的 mappings，读时最后应用、写时逆向 ✓（`map_command_value`）。
9. **lastConnected**：每次成功 GET/PUT 更新时间戳 ✓（`Devices().set_last_connected_by_name`，`command_read/write` 已调用）。
10. **Discovery 端点**：`POST /api/v3/discovery`、`POST /api/v3/profilescan`、DELETE 变体 ✓（`discovery.py` 已实现）。

#### ADR 013 - Events via MessageBus
1. **Publish vs POST**：v4 **始终发布到 MessageBus**，不再 POST 到 Core Data。配置 `MessageBus.Enabled` 在 v4 中已去除；`PublishTopicPrefix` 默认 `edgex/events`。
2. **Publish Topic 模式**：`<baseTopicPrefix>/events/device/<serviceName>/<profileName>/<deviceName>/<sourceName>`（即 `edgex/events/device/<svc>/<profile>/<device>/<source>`）。
3. **MessageEnvelope**：包含 `ContentType` (JSON/CBOR)、`Correlation-Id`，v4 移除 `Checksum`。
4. **MaxEventSize** 限制（`PublishWithSizeLimit`）。
5. **Core Data 订阅**：`SubscribeEnabled`、`SubscribeTopic=edgex/events/#` 持久化。
6. **App Service 订阅**：`SubscribeTopics` 支持通配符过滤。

#### Device System Events (SDK API)
- `PublishDeviceDiscoveryProgressSystemEvent(progress, count, message)` → topic `edgex/system-events/<svc>/discovery/<action>`（progress 0/100/-1）。
- `PublishProfileScanProgressSystemEvent(reqId, progress, message)` → topic `edgex/system-events/<svc>/profilescan/<action>`。
- `PublishGenericSystemEvent(eventType, action, details)`。

#### Go SDK v4 Messaging 实现（`internal/controller/messaging/`）
- `callback.go`：`MetadataSystemEventsCallback` 订阅
  - `<basePrefix>/edgex/system-events/<svc>/#`（Device/Profile/DeviceService 事件）
  - `<basePrefix>/edgex/system-events/device-profile/delete/#`（Profile Delete 专用）
  - 实例名场景：`<basePrefix>/edgex/system-events/provision-watcher/<baseSvc>/#`
  - Action 处理：Add/Update/Delete Device/ProvisionWatcher；Update/Delete DeviceProfile；Update DeviceService
- `command.go`：`SubscribeCommands`
  - 订阅 `<basePrefix>/edgex/command/request/<svc>/#`
  - 响应发布到 `<basePrefix>/edgex/response/<svc>/<requestId>`
  - 并发信号量 `defaultMaxConcurrentCommands = 32`，满时拒绝并返回 "service busy"
  - 复用 `application.GetCommand/SetCommand`，支持 query params `ds-pushevent/ds-returnevent/ds-regexcommand`
- `validation.go`：`SubscribeDeviceValidation` ✓（Python 已移植 `validation.py`）

---

### Python SDK 现状差距清单（待修复）

| # | 模块 / 功能 | 现状 | Go v4 行为 | 修复计划 |
|---|---|---|---|---|
| **1** | **Event 发布 (`SendEvent`)** | **No-op：仅 log**，`send_event_handler` 未注入 | 始终发布到 `edgex/events/device/<svc>/<profile>/<device>/<source>`；MessageEnvelope(JSON/CBOR)；MaxEventSize 限制；metrics 计数 | 在 `device_service.py`/`bootstrap.py` 注入真实 `send_event_handler`：初始化 `go-mod-messaging` 对等的 Python messaging client（paho-mqtt/nats-py），实现 `publish_event(event, correlation_id)` → `internal/controller/messaging/publish.py`；配置读取 `MessageBus` section |
| **2** | **Async values 泵 (`processAsyncResults`)** | **缺失**：`_async_values_channel` 仅写入，无消费者 | `processAsyncResults` 启动 goroutine 消费 `s.asyncCh`，并发受限 `AsyncBufferSize`，转换后调用 `SendEvent` | 在 `device_service.run()` 中启动后台任务消费 `_async_values_channel`：调用 `transformer.command_values_to_event` → `SendEvent`（复用 Gap #1 handler）；并发限制参考 `configuration.Device.AsyncBufferSize` |
| **3** | **Discovered devices 泵 (`processAsyncFilterAndAdd`)** | **缺失**：`_discovered_device_channel` 仅写入，无消费者 | 消费 `s.deviceCh`，按 ProvisionWatchers 匹配 allow/block list，通过 Core Metadata client `AddDevice`（带 `BypassValidation=true`） | 同 Gap #2 启动消费者：读取 `_discovered_device_channel`，遍历 `ProvisionWatchers().all()` 匹配，调用 `MetadataClient.add_device(bypass_validation=True)`；需先有 metadata client |
| **4** | **Profile Scan 进度系统事件** | 仅 log | 发布到 `edgex/system-events/<svc>/profilescan/<action>` | 扩展 Gap #1 handler 支持 system events topic，实现 `publish_system_event(type, action, details)` |
| **5** | **Messaging 命令订阅 (`SubscribeCommands`)** | **完全缺失** | 订阅 `edgex/command/request/<svc>/#`；响应 `edgex/response/<svc>/<requestId>`；信号量 32 并发上限；复用 `application.GetCommand/SetCommand` | 新建 `internal/controller/messaging/command.py`：`subscribe_commands(service_name, driver, config, base_topic_prefix, broker)`；启动消费循环，解析 topic 提取 device/command/method；信号量限流；复用 `application.command_read/write`；响应构建 `MessageEnvelope` 发布 |
| **6** | **Metadata System Events 回调 (`MetadataSystemEventsCallback`)** | **完全缺失**（v3+ HTTP callback 已废弃） | 订阅 2~3 个 system-events topic；解码 `SystemEvent`；按 Type/Action 分发：`application.AddDevice/UpdateDevice/DeleteDevice`、`UpdateProfile/DeleteProfile`、`AddProvisionWatcher/UpdateProvisionWatcher/DeleteProvisionWatcher`、`UpdateDeviceService` | 新建 `internal/controller/messaging/callback.py`：订阅 `edgex/system-events/<svc>/#` 与 `edgex/system-events/device-profile/delete/#`；实例名场景额外订阅 provision-watcher；实现 4 个 action handler 调用 `service.DeviceService` 对应方法（已存在 `add_device/update_device/remove_device` 等） |
| **7** | **CBOR 编码（二进制 Reading）** | HTTP 响应 **始终 JSON** | `sendEventResponse` 检测 binary reading → `encoding = CBOR`，`Content-Type: application/cbor` | 在 `_utils.send_event_response` 中检测 `event.readings` 是否含 `Reading.value_type == VALUETYPE_BINARY`；是则 `cbor2.dumps`、设置 `Content-Type: application/cbor` |
| **8** | **Assertion 失败置 OperatingState DISABLED** | 抛 `TransformerError`→500，**未置状态** | 调用 `updateOperatingState(deviceName, models.Disabled...)` | 在 `transform.command_values_to_event` 捕获 `TransformerError(assertion)` 时，调用 `Devices().update_operating_state(device_name, "DISABLED")` 再抛出；需确保 `Devices` 提供该方法 |
| **9** | **MessageBus 配置结构** | 仅读 `message_bus.host/port/base_topic_prefix` 防御式 | 完整 `MessageBus`：`Type`、`PublishTopicPrefix`(="events")、`Optional{ClientId,Qos,KeepAlive,Retained,AutoReconnect,ConnectTimeout,SkipCertVerify,ClientAuth,SecretPath}` | 扩展 `device_service._message_bus_config` 返回完整结构体 / dataclass；供 messaging client 初始化使用 |
| **10** | **Discovery/ProfileScan 进度系统事件发布** | 仅 log | 发布到对应 system-events topic | 同 Gap #4，实现 `publish_system_event` 并在 `DiscoveryController`/`device_service` 中调用 |

---

### 修复优先级与施工顺序（按依赖拓扑）

1. **P0 - 消息总线基建**（解锁后续所有发布/订阅）
   - `internal/controller/messaging/mqtt_client.py` / `nats_client.py`（复用 app-functions-sdk-python 的 client 抽象）
   - `internal/controller/messaging/publish.py`：`publish_event`、`publish_system_event`、`build_publish_topic`、`MessageEnvelope` 编码
   - 配置：`MessageBus` dataclass + `bootstrap.py` 注入 messaging client → `DeviceService._send_event_handler`

2. **P0 - Async/Discovered 泵**（device service 核心数据路径）
   - `device_service.py`：`run()` 中启动 `_pump_async_values()`、`_pump_discovered_devices()` 后台任务
   - 复用 Gap #1 的 `send_event_handler`

3. **P1 - Messaging 命令订阅**（外部通过 MQTT 下发命令）
   - `internal/controller/messaging/command.py`：`subscribe_commands`
   - `device_service.run()` 启动订阅

4. **P1 - Metadata System Events 回调**（缓存与 Core Metadata 保持同步）
   - `internal/controller/messaging/callback.py`：`subscribe_system_events`
   - `device_service.run()` 启动订阅

5. **P2 - CBOR/Assertion/配置补全**
   - `_utils.py` CBOR 编码
   - `transform.py` assertion → OperatingState DISABLED
   - `_message_bus_config` 返回完整结构

6. **P3 - System Events 进度发布**（Discovery/ProfileScan）
   - 扩展 `publish_system_event` 并在 `discovery.py`、`device_service.py` 中调用

---

### 回归验收清单（修复完成后必须通过）

1. `examples/simple` 启动，`GET /api/v3/device/simple/random-number` → 200，返回 Event（含 reading）。
2. `ds-pushevent=true` → Event 同时出现在 MessageBus `edgex/events/device/device-simple/...` topic。
3. `PUT /api/v3/device/simple/random-number` body `{"random-number":"42"}` → 200，返回 BaseResponse。
4. Core Metadata 修改 Device（AdminState/OperatingState/Profile） → Python 缓存自动同步（验证 callback 订阅）。
5. Core Metadata 删除 DeviceProfile → Python 缓存删除（验证 profile delete topic 订阅）。
6. MQTT 发送命令请求到 `edgex/command/request/device-simple/simple/random-number/get` → 收到响应在 `edgex/response/device-simple/<reqId>`。
7. AutoEvent 定时采集 → Event 出现在 MessageBus（验证 async 泵）。
8. `POST /api/v3/discovery` 触发 `driver.discover()` → 发现设备经 ProvisionWatcher 匹配 → 自动在 Core Metadata 注册（验证 discovered 泵 + metadata client）。
9. 单测 `python -m unittest discover tests` 全绿。
10. CBOR：Profile 含 Binary resource → GET 返回 `Content-Type: application/cbor`。

---

### 其它已验收项（无需改动）

- REST 端点完整性：ping/version/config/metrics、discovery/profilescan、device command GET/PUT ✓
- Query 参数 `ds-pushevent/ds-returnevent/ds-regexcommand` 解析与语义 ✓
- 数据变换 Mask/Shift/Base/Scale/Offset、Mappings、Overflow→"overflow" ✓
- Validation 订阅（`edgex/<svc>/validate/device`）✓
- Device Service 注册、Profile/Device/Watcher 预加载 ✓
- `_advertised_host()` UDP 探测 LAN IP ✓
- 版权头 `YIQISOFT 2026` ✓

---

### 下一步动作（交给 opencode 单点执行）

1. **新建** `src/device_sdk_py/internal/controller/messaging/mqtt_client.py` —— 最小 MQTT 发布/订阅封装（参考 app-functions-sdk-python `messaging/mqtt/client.py`，保留 `publish(topic, payload, qos)`、`subscribe(topic, callback)`、`connect/disconnect`）。
2. **新建** `src/device_sdk_py/internal/controller/messaging/publish.py` —— `publish_event`（含 Envelope 构建、CBOR/JSON 选择、MaxEventSize 截断、topic 生成）、`publish_system_event`、`build_event_publish_topic`。
3. **修改** `service/device_service.py`：`_message_bus_config` 返回完整配置对象；`run()` 注入 `send_event_handler=publish_event`；启动 `_pump_async_values`、`_pump_discovered_devices`。
4. **新建** `src/device_sdk_py/internal/controller/messaging/command.py` —— `subscribe_commands` 含信号量限流、topic 解析、响应发布。
5. **新建** `src/device_sdk_py/internal/controller/messaging/callback.py` —— `subscribe_system_events` 含 3~4 个 topic 订阅、SystemEvent 解码、4 类 action handler 调用 `DeviceService` 方法。
6. **修改** `internal/controller/http/_utils.py`：`send_event_response` 检测 Binary → CBOR 编码。
7. **修改** `internal/transformer/transform.py`：assertion 失败时调用 `Devices().update_operating_state(device_name, "DISABLED")`。
8. **修改** `examples/simple/res/configuration.yaml`：补充 `MessageBus` 完整字段（Type、PublishTopicPrefix、Optional.*）。
9. **回归**：启动 simple service，逐项核对验收清单 1-10。