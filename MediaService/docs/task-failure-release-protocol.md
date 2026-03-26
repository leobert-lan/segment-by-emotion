# Task Failure Release Protocol

## Goal

When Android determines that a task has failed permanently, it must explicitly notify the Python server and immediately return to the online standby state so the server can dispatch the next task.

This avoids the old behavior where the node stayed on a failed task locally and stopped making forward progress.

## Scope

This document defines the control-channel extension used after a **terminal task failure**.

It does **not** replace heartbeat, `TASK_STATUS_REPORT`, or reconnect recovery.

## Transport

Control channel messages continue to use newline-delimited JSON on TCP port `23010`.

## New Messages

### 1. `TASK_FAILURE_REPORT` (Node → Server)

Sent by Android when the current task cannot continue and should be released by the server scheduler.

#### JSON shape

```json
{
  "requestId": "uuid",
  "type": "TASK_FAILURE_REPORT",
  "taskId": "task-42",
  "failedStage": "PROCESSING",
  "reason": "encoder stalled",
  "terminal": true,
  "readyForNextTask": true,
  "sentAt": "2026-03-26T10:00:00Z"
}
```

#### Fields

- `requestId`: message UUID.
- `taskId`: failed task identifier.
- `failedStage`: one of:
  - `RECEIVING`
  - `PROCESSING`
  - `UPLOADING`
- `reason`: human-readable failure reason for logs / UI / scheduler decisions.
- `terminal`: always `true` for this message version; indicates the node will not retry locally.
- `readyForNextTask`: `true` means the node remains connected and can immediately accept another task.
- `sentAt`: ISO-8601 UTC timestamp.

#### Sender behavior

After sending `TASK_FAILURE_REPORT`, Android:

1. marks the task as failed locally,
2. clears active-task ownership,
3. removes local recovery state for that task,
4. returns to `AwaitingTask` if the socket is still connected.

### 2. `TASK_FAILURE_ACK` (Server → Node, optional)

Optional acknowledgement from Python.

#### JSON shape

```json
{
  "requestId": "uuid",
  "type": "TASK_FAILURE_ACK",
  "taskId": "task-42",
  "accepted": true,
  "message": "queued next task"
}
```

#### Notes

- Android treats this message as informational only.
- Android does **not** wait for this ACK before becoming available for the next task.
- The server may omit it for simplicity.

## Recommended Server Behavior

When Python receives `TASK_FAILURE_REPORT`:

1. persist the failure reason,
2. mark the task failed / released server-side,
3. stop expecting further result upload for that task,
4. if `readyForNextTask == true`, immediately evaluate the node for next dispatch.

## Failure Semantics

`TASK_FAILURE_REPORT` is intended only for **terminal** failures such as:

- assembled input invalid,
- hash mismatch after receive,
- media pipeline failure,
- unrecoverable local state corruption for a task.

It is **not** intended for transient disconnects or retryable upload failures.

## Compatibility

- Older servers that do not recognize `TASK_FAILURE_REPORT` should ignore or reject it safely.
- Older Android builds that do not recognize `TASK_FAILURE_ACK` should be upgraded alongside the scheduler change if ACKs are enabled.

## Android Implementation Notes

Current Android behavior after terminal failure:

- sends `TASK_FAILURE_REPORT`,
- deletes the failed task from local recovery DB,
- cleans failed-task files,
- returns to standby and keeps the connection alive.

This allows the Python side to issue a new `TASK_ASSIGN` without requiring manual reconnect.

