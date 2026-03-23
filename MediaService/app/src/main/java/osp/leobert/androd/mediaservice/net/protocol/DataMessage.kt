package osp.leobert.androd.mediaservice.net.protocol

import com.google.gson.annotations.SerializedName

/**
 * Data channel messages (port 23011).
 *
 * Frame format: [4-byte Big-Endian header-JSON length][header JSON bytes][binary payload bytes]
 * The `payloadSize` field in each header indicates how many binary bytes follow the JSON header.
 * For messages with no binary payload (e.g. ChunkAck, TransferComplete), payloadSize = 0.
 *
 * Matches SDS/socket_server_node_coordination_design.md §7.
 */
sealed class DataMessage {

    abstract val type: String

    // ── Server → Node (receiving) ──────────────────────────────────────────

    /**
     * Header for a single chunk; binary payload follows immediately after the header JSON.
     */
    data class Chunk(
        @SerializedName("type")
        override val type: String = "CHUNK",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        @SerializedName("chunkIndex")
        val chunkIndex: Int,
        /** SHA-256 hex of this chunk's payload bytes. */
        @SerializedName("chunkHash")
        val chunkHash: String,
        @SerializedName("payloadSize")
        val payloadSize: Int,
    ) : DataMessage()

    /**
     * Sent by server when the node requests resume: server replays only missing chunks.
     */
    data class TransferComplete(
        @SerializedName("type")
        override val type: String = "TRANSFER_COMPLETE",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        /** SHA-256 hex of the fully assembled file. */
        @SerializedName("totalHash")
        val totalHash: String,
        @SerializedName("payloadSize")
        val payloadSize: Int = 0,
    ) : DataMessage()

    // ── Node → Server (ACK + resume + upload) ─────────────────────────────

    data class ChunkAck(
        @SerializedName("type")
        override val type: String = "CHUNK_ACK",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        @SerializedName("chunkIndex")
        val chunkIndex: Int,
        @SerializedName("payloadSize")
        val payloadSize: Int = 0,
    ) : DataMessage()

    /**
     * Sent after reconnect to request retransmission of missing chunks.
     * [missingIndices] is the sorted list of chunk indices not yet received.
     */
    data class TransferResumeRequest(
        @SerializedName("type")
        override val type: String = "TRANSFER_RESUME_REQUEST",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        @SerializedName("missingIndices")
        val missingIndices: List<Int>,
        @SerializedName("payloadSize")
        val payloadSize: Int = 0,
    ) : DataMessage()

    /**
     * Upload: result file chunk sent from node to server.
     * Reuses the same frame format; binary payload follows.
     */
    data class ResultChunk(
        @SerializedName("type")
        override val type: String = "RESULT_CHUNK",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        @SerializedName("chunkIndex")
        val chunkIndex: Int,
        @SerializedName("chunkHash")
        val chunkHash: String,
        @SerializedName("payloadSize")
        val payloadSize: Int,
        /** "video" | "json" | "log" — identifies which result file this chunk belongs to */
        @SerializedName("fileRole")
        val fileRole: String,
    ) : DataMessage()

    /**
     * Sent after all result file chunks have been uploaded and ACK'd.
     */
    data class ResultTransferComplete(
        @SerializedName("type")
        override val type: String = "RESULT_TRANSFER_COMPLETE",
        @SerializedName("taskId")
        val taskId: String,
        @SerializedName("transferId")
        val transferId: String,
        @SerializedName("totalHash")
        val totalHash: String,
        @SerializedName("payloadSize")
        val payloadSize: Int = 0,
    ) : DataMessage()
}

