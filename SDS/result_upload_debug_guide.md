# 结果回传协议调试说明（Python 服务端）

> 面向联调：当 Android 上传结果后等待 `CHUNK_ACK` 超时时，快速定位问题。

## 1. Python 端处理入口

- 数据通道接收入口：`src/net/socket/socket_server.py`
  - `_handle_data(...)`
  - `_run_data_after_hello(...)`
  - `_data_loop(...)`
- 回传业务入口：`src/services/dispatch_service.py`
  - `on_data_frame(...)`
  - `_handle_result_chunk(...)`
  - `_handle_result_transfer_complete(...)`

## 2. 正常路径（RESULT_CHUNK -> CHUNK_ACK）

1. Android 发送 `RESULT_CHUNK`（header + payload）。
2. `SocketServer._data_loop` 解帧并回调 `DispatchService.on_data_frame`。
3. `DispatchService._handle_result_chunk` 校验：
   - `payloadSize == len(payload)`
   - `sha256(payload) == chunkHash`
4. 校验通过后写入：
   - `data/node_results/<task>/<node>/chunks/<transfer>/<fileRole>/<chunk>.bin`
5. 发送 `CHUNK_ACK`（`taskId + transferId + chunkIndex`）。

## 3. 何时不会 ACK（预期行为）

- `payloadSize` 不匹配。
- `chunkHash` 不匹配。
- 写盘异常（路径/磁盘/权限/IO 错误）。
- 数据通道断开导致 ACK 无法发出。

以上情况下，Android 会等待超时并按其逻辑重试/失败。

## 4. 关键日志对照

### 4.1 连接与握手（socket_server）

- `[protocol][pair_ready]`：控制+数据已配对。
- `[protocol][data_loop_start]`：数据循环已进入，可接收回传。
- `[protocol][pending_data_timeout]`：数据先到但控制迟迟未到，已清理连接。

### 4.2 回传处理（dispatch_service）

- `recv RESULT_CHUNK ...`：Python 已收到结果分片。
- `drop message reason=invalid_field ...`：字段校验失败（不 ACK）。
- `drop message reason=write_error ...`：写盘失败（不 ACK）。
- `send CHUNK_ACK ...`：ACK 已发出。
- `[protocol][result_transfer_complete_received]`：收到上传完成通知。
- `[protocol][result_accepted]`：验收通过，任务闭环完成。

## 5. 快速判断规则

- Android 超时 + Python 无 `recv RESULT_CHUNK`：
  - 优先查数据通道是否真的进入 `data_loop_start`。
- Python 有 `recv RESULT_CHUNK` + 无 `send CHUNK_ACK`：
  - 查 `invalid_field` 或 `write_error`。
- Python 有 `send CHUNK_ACK` + Android仍超时：
  - 查 Android `DataChannelClient.readLoop` 是否收到 `CHUNK_ACK` 并发到 `dataEvents`。

## 6. 已知恢复能力边界

- 下载方向断点续传：已支持 `TRANSFER_RESUME_REQUEST`。
- 上传方向自动恢复：服务端支持 `sync_actions=RESUME_UPLOAD` 下发，但 Android `resumeUpload(...)` 仍待实现。

