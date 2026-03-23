package osp.leobert.androd.mediaservice.net.protocol

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.InputStream
import java.io.OutputStream

/**
 * Encodes and decodes the two wire formats used by the socket channels.
 *
 * Control channel (port 23010): newline-delimited JSON (one message per line).
 * Data channel (port 23011):    [4-byte Big-Endian header-JSON length][header JSON][binary payload]
 */
object MessageFramer {

    private val gson = Gson()

    // ── Control channel ───────────────────────────────────────────────────

    fun encodeControl(message: ControlMessage): String =
        gson.toJson(message) + "\n"

    fun decodeControl(line: String): ControlMessage {
        val json = JsonParser.parseString(line.trim()).asJsonObject
        return parseControlMessage(json)
    }

    // ── Data channel ──────────────────────────────────────────────────────

    /**
     * Write a data message header (+ optional binary payload) to [out].
     * [payload] may be null for messages with payloadSize == 0.
     */
    fun writeDataFrame(out: OutputStream, message: DataMessage, payload: ByteArray? = null) {
        val headerBytes = gson.toJson(message).toByteArray(Charsets.UTF_8)
        val dos = DataOutputStream(out)
        dos.writeInt(headerBytes.size)
        dos.write(headerBytes)
        if (payload != null && payload.isNotEmpty()) {
            dos.write(payload)
        }
        dos.flush()
    }

    /**
     * Read one data frame header from [inp].
     * The caller is responsible for reading [DataMessage.Chunk.payloadSize] bytes afterwards.
     */
    fun readDataFrameHeader(inp: InputStream): DataMessage {
        val dis = DataInputStream(inp)
        val headerLen = dis.readInt()
        val headerBytes = ByteArray(headerLen)
        dis.readFully(headerBytes)
        val json = JsonParser.parseString(headerBytes.decodeToString()).asJsonObject
        return parseDataMessage(json)
    }

    /**
     * Read exactly [size] bytes from [inp] into a new ByteArray.
     */
    fun readPayload(inp: InputStream, size: Int): ByteArray {
        if (size <= 0) return ByteArray(0)
        val buf = ByteArray(size)
        DataInputStream(inp).readFully(buf)
        return buf
    }

    private fun parseControlMessage(json: JsonObject): ControlMessage {
        return when (json.getRequiredString("type")) {
            "HELLO" -> gson.fromJson(json, ControlMessage.Hello::class.java)
            "TASK_STATUS_REPORT" -> gson.fromJson(json, ControlMessage.TaskStatusReport::class.java)
            "TASK_CONFIRM" -> gson.fromJson(json, ControlMessage.TaskConfirm::class.java)
            "HELLO_ACK" -> gson.fromJson(json, ControlMessage.HelloAck::class.java)
            "TASK_ASSIGN" -> gson.fromJson(json, ControlMessage.TaskAssign::class.java)
            "TASK_STATUS_QUERY" -> gson.fromJson(json, ControlMessage.TaskStatusQuery::class.java)
            else -> throw IllegalArgumentException("Unknown control message type=${json.getRequiredString("type")}")
        }
    }

    private fun parseDataMessage(json: JsonObject): DataMessage {
        return when (json.getRequiredString("type")) {
            "CHUNK" -> gson.fromJson(json, DataMessage.Chunk::class.java)
            "TRANSFER_COMPLETE" -> gson.fromJson(json, DataMessage.TransferComplete::class.java)
            "CHUNK_ACK" -> gson.fromJson(json, DataMessage.ChunkAck::class.java)
            "TRANSFER_RESUME_REQUEST" -> gson.fromJson(json, DataMessage.TransferResumeRequest::class.java)
            "RESULT_CHUNK" -> gson.fromJson(json, DataMessage.ResultChunk::class.java)
            "RESULT_TRANSFER_COMPLETE" -> gson.fromJson(json, DataMessage.ResultTransferComplete::class.java)
            else -> throw IllegalArgumentException("Unknown data message type=${json.getRequiredString("type")}")
        }
    }

    private fun JsonObject.getRequiredString(key: String): String {
        if (!has(key) || get(key).isJsonNull) {
            throw IllegalArgumentException("Missing required field '$key'")
        }
        return get(key).asString
    }
}
