package osp.leobert.androd.mediaservice.net.protocol

import kotlinx.serialization.json.Json
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

    private val json = Json {
        classDiscriminator = "type"
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    // ── Control channel ───────────────────────────────────────────────────

    fun encodeControl(message: ControlMessage): String =
        json.encodeToString(ControlMessage.serializer(), message) + "\n"

    fun decodeControl(line: String): ControlMessage =
        json.decodeFromString(ControlMessage.serializer(), line.trim())

    // ── Data channel ──────────────────────────────────────────────────────

    /**
     * Write a data message header (+ optional binary payload) to [out].
     * [payload] may be null for messages with payloadSize == 0.
     */
    fun writeDataFrame(out: OutputStream, message: DataMessage, payload: ByteArray? = null) {
        val headerBytes = json.encodeToString(DataMessage.serializer(), message).toByteArray(Charsets.UTF_8)
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
        return json.decodeFromString(DataMessage.serializer(), headerBytes.decodeToString())
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
}

