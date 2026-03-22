# 导出数据设计（JSON + CSV）

## 1. 目标
- 导出热度分析结果，供下游做视频裁剪、片段合并与审计留痕。
- 一次导出同时生成 `JSON` 和 `CSV` 两种格式。
- 覆盖任务元数据、分段热度、最终标签以及标签变更历史。

## 2. 触发入口
- Review 页面提供“导出热度(JSON+CSV)”按钮。
- 用户选择导出目录后，系统输出两个文件：
  - `<video_name>_task<id>_heat.json`
  - `<video_name>_task<id>_heat.csv`

## 3. JSON 契约

### 3.1 顶层结构
```json
{
  "task": {},
  "summary": {},
  "segments": [],
  "label_events": []
}
```

### 3.2 task 字段
- `id`: 任务 ID
- `video_path`: 原视频路径
- `video_name`: 视频文件名
- `speaker_id`: 说话人标识
- `status`: 任务状态
- `segment_duration`: 分段时长（秒）
- `created_at`, `updated_at`: ISO-8601 时间

### 3.3 summary 字段
- `segment_count`: 分段总数
- `interesting_count`: 最终标记为 interesting 的数量
- `uninteresting_count`: 最终标记为 uninteresting 的数量

### 3.4 segments 数组元素
- `id`: 分段 ID
- `start_sec`, `end_sec`: 时间范围（秒）
- `duration_sec`: 分段时长（秒）
- `heat_score`: 热度分值（0~1）
- `current_label`: 最终标签（`interesting`/`uninteresting`/`null`）

### 3.5 label_events 数组元素
- `id`: 事件 ID
- `task_id`, `segment_id`
- `previous_label`, `new_label`
- `undone`: 是否被撤销（0/1）
- `created_at`: 事件时间

## 4. CSV 契约
- 每行对应一个分段，便于下游批处理。
- 列定义：
  - `task_id`
  - `video_name`
  - `speaker_id`
  - `segment_id`
  - `start_sec`
  - `end_sec`
  - `duration_sec`
  - `heat_score`
  - `current_label`

## 5. 下游裁剪合并建议
- 裁剪：按 `start_sec` 与 `end_sec` 直接切片。
- 合并：先过滤 `current_label=interesting`，再按相邻或重叠时段进行 merge。
- 审计：通过 `label_events` 复原人工标记过程。

## 6. 异常处理
- 导出目录不可写：返回错误并提示用户重选目录。
- 任务不存在：终止导出。
- 单格式写入失败：视为整体导出失败（确保 JSON/CSV 成对一致）。

## 7. 版本策略
- 当前版本号：`v1`（隐式）。
- 后续建议在 JSON 顶层新增 `schema_version`，兼容扩展字段。

