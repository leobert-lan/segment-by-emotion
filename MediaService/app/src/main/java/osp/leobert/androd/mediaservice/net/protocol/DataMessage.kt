package osp.leobert.androd.mediaservice.net.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Data channel messages (port 23011).
 *
 * Frame format: [4-byte Big-Endian header-JSON length][header JSON bytes][binary payload bytes]
 * The [payloadSize] in the header indicates how many binary bytes follow the JSON header.
 * For messages with no binary payload (e.g. ChunkAck, TransferComplete), payloadSize = 0.
 *
 * Matches SDS/socket_server_node_coordination_design.md §7.
 */
@Serializable
sealed class DataMessage {

    abstract val type: String

    // ── Server → Node (receiving) ──────────────────────────────────────────

    /**
     * Header for a single chunk; binary payload follows immediately after the header JSON.
     */
    @Serializable
    @SerialName("CHUNK")
    data class Chunk(
        override val type: String = "CHUNK",
        val taskId: String,
        val transferId: String,
        val chunkIndex: Int,
        /** SHA-256 hex of this chunk's payload bytes. */
        val chunkHash: String,
        val payloadSize: Int,
    ) : DataMessage()

    /**
     * Sent by server when the node requests resume: server replays only missing chunks.
     */
    @Serializable
    @SerialName("TRANSFER_COMPLETE")
    data class TransferComplete(
        override val type: String = "TRANSFER_COMPLETE",
        val taskId: String,
        val transferId: String,
        /** SHA-256 hex of the fully assembled file. */
        val totalHash: String,
        val payloadSize: Int = 0,
    ) : DataMessage()

    // ── Node → Server (ACK + resume + upload) ─────────────────────────────

    @Serializable
    @SerialName("CHUNK_ACK")
    data class ChunkAck(
        override val type: String = "CHUNK_ACK",
        val taskId: String,
        val transferId: String,
        val chunkIndex: Int,
        val payloadSize: Int = 0,
    ) : DataMessage()

    /**
     * Sent after reconnect to request retransmission of missing chunks.
     * [missingIndices] is the sorted list of chunk indices not yet received.
     */
    @Serializable
    @SerialName("TRANSFER_RESUME_REQUEST")
    data class TransferResumeRequest(
        override val type: String = "TRANSFER_RESUME_REQUEST",
        val taskId: String,
        val transferId: String,
        val missingIndices: List<Int>,
        val payloadSize: Int = 0,
    ) : DataMessage()

    /**
     * Upload: result file chunk sent from node to server.
     * Reuses the same frame format; binary payload follows.
     */
    @Serializable
    @SerialName("RESULT_CHUNK")
    data class ResultChunk(
        override val type: String = "RESULT_CHUNK",
        val taskId: String,
        val transferId: String,
        val chunkIndex: Int,
        val chunkHash: String,
        val payloadSize: Int,
        /** "video" | "json" | "log" — identifies which result file this chunk belongs to */
        val fileRole: String,
    ) : DataMessage()

    /**
     * Sent after all result file chunks have been uploaded and ACK'd.
     */
    @Serializable
    @SerialName("RESULT_TRANSFER_COMPLETE")
    data class ResultTransferComplete(
        override val type: String = "RESULT_TRANSFER_COMPLETE",
        val taskId: String,
        val transferId: String,
        val totalHash: String,
        val payloadSize: Int = 0,
    ) : DataMessage()
}

