"""
tests/test_protocol.py — M1 协议序列化往返测试。

覆盖所有消息类型的 to_dict/from_dict 往返，以及 MessageFramer 帧编解码。
测试无需网络、无需 GUI。
"""
import asyncio
import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from src.net.protocol.control_message import (
    CurrentTaskSnapshot,
    MsgHeartbeat,
    MsgHello,
    MsgHelloAck,
    MsgTaskAssign,
    MsgTaskConfirm,
    MsgTaskStatusReport,
    MsgTaskStatusQuery,
    NodeCapabilities,
    ProcessingParamsPayload,
    ResultRequirements,
    SegmentPayload,
    SyncAction,
    VideoMetaPayload,
    decode_control,
    encode_control,
)
from src.net.protocol.data_message import (
    MsgChunk,
    MsgChunkAck,
    MsgChunkAckOut,
    MsgResultChunk,
    MsgResultTransferComplete,
    MsgTransferComplete,
    MsgTransferResumeRequest,
    decode_data_header,
)
from src.net.protocol.message_framer import read_data_frame, write_data_frame


# ── 控制消息往返测试 ────────────────────────────────────────────────────────────

class TestControlMessageRoundtrip(unittest.TestCase):

    def test_hello_minimal(self) -> None:
        d = {
            "type": "HELLO",
            "requestId": "req-001",
            "nodeId": "android-001",
            "nodeVersion": "1.0.0",
            "capabilities": {"gpu": False, "codec": ["h264", "hevc"]},
            "currentTask": None,
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgHello)
        self.assertEqual(msg.nodeId, "android-001")
        self.assertEqual(msg.capabilities.codec, ["h264", "hevc"])
        self.assertIsNone(msg.currentTask)

    def test_hello_with_current_task(self) -> None:
        d = {
            "type": "HELLO",
            "requestId": "req-002",
            "nodeId": "android-001",
            "nodeVersion": "1.0.1",
            "capabilities": {"gpu": True, "codec": ["hevc"]},
            "currentTask": {"taskId": "42", "status": "running", "progress": 0.5},
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgHello)
        self.assertIsNotNone(msg.currentTask)
        self.assertEqual(msg.currentTask.taskId, "42")  # type: ignore[union-attr]
        self.assertAlmostEqual(msg.currentTask.progress, 0.5)  # type: ignore[union-attr]

    def test_task_confirm_accepted(self) -> None:
        d = {
            "type": "TASK_CONFIRM",
            "requestId": "req-003",
            "taskId": "99",
            "accepted": True,
            "reason": None,
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgTaskConfirm)
        self.assertTrue(msg.accepted)
        self.assertIsNone(msg.reason)

    def test_task_confirm_rejected(self) -> None:
        d = {
            "type": "TASK_CONFIRM",
            "requestId": "req-004",
            "taskId": "99",
            "accepted": False,
            "reason": "存储空间不足",
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgTaskConfirm)
        self.assertFalse(msg.accepted)
        self.assertEqual(msg.reason, "存储空间不足")

    def test_task_status_report(self) -> None:
        d = {
            "type": "TASK_STATUS_REPORT",
            "requestId": "req-005",
            "taskId": "10",
            "status": "Processing",
            "progress": 0.75,
            "stage": "TRANSCODING",
            "lastError": None,
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgTaskStatusReport)
        self.assertAlmostEqual(msg.progress, 0.75)
        self.assertEqual(msg.stage, "TRANSCODING")

    def test_hello_ack_encode(self) -> None:
        ack = MsgHelloAck(
            requestId="srv-001",
            serverTime="2026-03-23T00:00:00Z",
            syncActions=[SyncAction("RESUME_UPLOAD", "42")],
        )
        d = ack.to_dict()
        self.assertEqual(d["type"], "HELLO_ACK")
        self.assertEqual(d["syncActions"][0]["action"], "RESUME_UPLOAD")
        self.assertEqual(d["syncActions"][0]["taskId"], "42")

    def test_task_assign_encode(self) -> None:
        assign = MsgTaskAssign(
            requestId="srv-002",
            taskId="42",
            videoMeta=VideoMetaPayload(
                videoName="test.mp4",
                fileSizeBytes=10_000_000,
                totalChunks=10,
                fileHash="abc123",
            ),
            processingParams=ProcessingParamsPayload(
                segments=[
                    SegmentPayload(startMs=0, endMs=5000, label="interesting"),
                    SegmentPayload(startMs=10000, endMs=15000, label="interesting"),
                ],
                codecHint="hevc",
                targetBitrateKbps=0,
            ),
            resultRequirements=ResultRequirements(
                includeResultJson=True, includeLog=True
            ),
        )
        d = assign.to_dict()
        self.assertEqual(d["type"], "TASK_ASSIGN")
        self.assertEqual(d["taskId"], "42")
        self.assertEqual(d["videoMeta"]["videoName"], "test.mp4")
        self.assertEqual(len(d["processingParams"]["segments"]), 2)
        self.assertEqual(d["processingParams"]["segments"][0]["startMs"], 0)

    def test_encode_control_newline_terminated(self) -> None:
        ack = MsgHelloAck(requestId="x", serverTime="t", syncActions=[])
        raw = encode_control(ack.to_dict())
        self.assertTrue(raw.endswith(b"\n"))
        parsed = json.loads(raw.decode("utf-8").strip())
        self.assertEqual(parsed["type"], "HELLO_ACK")

    def test_unknown_type_returns_dict(self) -> None:
        d = {"type": "UNKNOWN_MSG", "someField": "value"}
        result = decode_control(json.dumps(d))
        self.assertIsInstance(result, dict)
        self.assertEqual(result["type"], "UNKNOWN_MSG")

    def test_heartbeat_decode(self) -> None:
        d = {
            "type": "HEARTBEAT",
            "requestId": "req-hb-001",
            "sentAt": "2026-03-25T00:00:00Z",
        }
        msg = decode_control(json.dumps(d))
        self.assertIsInstance(msg, MsgHeartbeat)
        self.assertEqual(msg.requestId, "req-hb-001")


# ── 数据消息往返测试 ────────────────────────────────────────────────────────────

class TestDataMessageRoundtrip(unittest.TestCase):

    def test_chunk_ack_roundtrip(self) -> None:
        d = {
            "type": "CHUNK_ACK",
            "taskId": "42",
            "transferId": "tid-001",
            "chunkIndex": 7,
            "payloadSize": 0,
        }
        msg = decode_data_header(json.dumps(d).encode())
        self.assertIsInstance(msg, MsgChunkAck)
        self.assertEqual(msg.chunkIndex, 7)
        self.assertEqual(msg.transferId, "tid-001")

    def test_transfer_resume_request(self) -> None:
        d = {
            "type": "TRANSFER_RESUME_REQUEST",
            "taskId": "42",
            "transferId": "tid-002",
            "missingIndices": [0, 3, 7],
            "payloadSize": 0,
        }
        msg = decode_data_header(json.dumps(d).encode())
        self.assertIsInstance(msg, MsgTransferResumeRequest)
        self.assertEqual(msg.missingIndices, [0, 3, 7])

    def test_result_chunk_roundtrip(self) -> None:
        d = {
            "type": "RESULT_CHUNK",
            "taskId": "42",
            "transferId": "tid-003",
            "chunkIndex": 2,
            "chunkHash": "deadbeef",
            "payloadSize": 1024,
            "fileRole": "video",
        }
        msg = decode_data_header(json.dumps(d).encode())
        self.assertIsInstance(msg, MsgResultChunk)
        self.assertEqual(msg.fileRole, "video")
        self.assertEqual(msg.payloadSize, 1024)

    def test_result_transfer_complete(self) -> None:
        d = {
            "type": "RESULT_TRANSFER_COMPLETE",
            "taskId": "42",
            "transferId": "tid-004",
            "totalHash": "abc123",
            "payloadSize": 0,
        }
        msg = decode_data_header(json.dumps(d).encode())
        self.assertIsInstance(msg, MsgResultTransferComplete)
        self.assertEqual(msg.totalHash, "abc123")

    def test_chunk_to_dict(self) -> None:
        chunk = MsgChunk(
            taskId="42",
            transferId="tid-005",
            chunkIndex=0,
            chunkHash="ff00",
            payloadSize=1024,
        )
        d = chunk.to_dict()
        self.assertEqual(d["type"], "CHUNK")
        self.assertEqual(d["chunkIndex"], 0)

    def test_transfer_complete_to_dict(self) -> None:
        tc = MsgTransferComplete(
            taskId="42", transferId="tid-006", totalHash="cafebabe"
        )
        d = tc.to_dict()
        self.assertEqual(d["type"], "TRANSFER_COMPLETE")
        self.assertEqual(d["totalHash"], "cafebabe")
        self.assertEqual(d["payloadSize"], 0)

    def test_chunk_ack_out_to_dict(self) -> None:
        ack = MsgChunkAckOut(taskId="42", transferId="t", chunkIndex=3)
        d = ack.to_dict()
        self.assertEqual(d["type"], "CHUNK_ACK")
        self.assertEqual(d["chunkIndex"], 3)


# ── MessageFramer 帧编解码测试 ─────────────────────────────────────────────────

class TestMessageFramer(unittest.TestCase):

    def _roundtrip(self, header_dict: dict, payload: bytes = b"") -> tuple:
        """同步测试：encode → bytes → decode。"""
        buf = io.BytesIO()

        async def _write_then_read():
            class FakeWriter:
                def __init__(self):
                    self._buf = io.BytesIO()
                def write(self, data):
                    self._buf.write(data)
                async def drain(self):
                    pass

            fw = FakeWriter()
            await write_data_frame(fw, header_dict, payload)
            raw = fw._buf.getvalue()

            reader = asyncio.StreamReader()
            reader.feed_data(raw)
            reader.feed_eof()
            return await read_data_frame(reader)

        return asyncio.run(_write_then_read())

    def test_chunk_frame_no_payload(self) -> None:
        chunk = MsgChunk(
            taskId="42", transferId="t1", chunkIndex=5,
            chunkHash="ff", payloadSize=0,
        )
        hdr, payload = self._roundtrip(chunk.to_dict(), b"")
        self.assertIsInstance(hdr, MsgChunk)
        self.assertEqual(hdr.chunkIndex, 5)
        self.assertEqual(payload, b"")

    def test_chunk_frame_with_payload(self) -> None:
        data = b"\xDE\xAD\xBE\xEF" * 256  # 1 KB
        chunk_hash = hashlib.sha256(data).hexdigest()
        chunk = MsgChunk(
            taskId="1", transferId="t2", chunkIndex=0,
            chunkHash=chunk_hash, payloadSize=len(data),
        )
        hdr, received = self._roundtrip(chunk.to_dict(), data)
        self.assertEqual(received, data)
        self.assertEqual(hdr.payloadSize, len(data))

    def test_zero_payload(self) -> None:
        tc = MsgTransferComplete(taskId="1", transferId="t3", totalHash="abc")
        hdr, payload = self._roundtrip(tc.to_dict(), b"")
        self.assertIsInstance(hdr, MsgTransferComplete)
        self.assertEqual(payload, b"")

    def test_large_payload(self) -> None:
        """8 MB payload（模拟 Android CHUNK_SIZE）"""
        data = b"X" * (8 * 1024 * 1024)
        chunk = MsgChunk(
            taskId="2", transferId="t4", chunkIndex=0,
            chunkHash=hashlib.sha256(data).hexdigest(),
            payloadSize=len(data),
        )
        hdr, received = self._roundtrip(chunk.to_dict(), data)
        self.assertEqual(len(received), 8 * 1024 * 1024)
        self.assertEqual(hashlib.sha256(received).hexdigest(), chunk.chunkHash)

    def test_result_chunk_frame(self) -> None:
        data = b"result_bytes" * 100
        d = MsgResultChunk(
            taskId="3", transferId="t5", chunkIndex=1,
            chunkHash=hashlib.sha256(data).hexdigest(),
            payloadSize=len(data),
            fileRole="video",
        )
        hdr, received = self._roundtrip(d.to_dict(), data)
        self.assertIsInstance(hdr, MsgResultChunk)
        self.assertEqual(received, data)


if __name__ == "__main__":
    unittest.main()

