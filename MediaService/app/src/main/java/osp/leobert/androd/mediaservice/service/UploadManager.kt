package osp.leobert.androd.mediaservice.service

import android.util.Log
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import osp.leobert.androd.mediaservice.net.protocol.DataMessage
import osp.leobert.androd.mediaservice.net.socket.DataChannelClient
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import java.io.File
import java.io.IOException
import java.util.UUID

/**
 * Uploads the processed result files back to the Python server via the data channel.
 *
 * Protocol per chunk:
 *   1. Subscribe to [DataChannelClient.dataEvents] for a matching [DataMessage.ChunkAck]
 *      **before** sending (avoids the race where ACK arrives before we start listening).
 *   2. Write [DataMessage.ResultChunk] header + binary payload.
 *   3. Await [DataMessage.ChunkAck] with a [ACK_TIMEOUT_MS] timeout; throw on timeout.
 *
 * After all chunks: send [DataMessage.ResultTransferComplete].
 *
 * Files sent: "video" (result .mp4) then "json" (result.json, if present).
 */
class UploadManager(
    private val dataChannel: DataChannelClient,
    private val fileStore: FileStoreManager,
) {

    companion object {
        private const val TAG = "UploadManager"

        /**
         * Upload chunk size: 8 MB.
         *
         * Tuning rationale (stop-and-wait per-chunk protocol):
         *   throughput = chunkSize / (transferTime + RTT)
         *
         * On Gigabit LAN (125 MB/s, RTT ≈ 1 ms):
         *   1 MB → 8ms + 1ms  =  9ms/chunk → ~111 MB/s  (89% efficiency)
         *   8 MB → 64ms + 1ms = 65ms/chunk → ~123 MB/s  (98% efficiency)
         *
         * 8 MB balances:
         *   • Near-wire throughput on 100 Mbps – Gigabit LAN
         *   • Peak heap: ~16 MB (buffer + last-chunk copy) — safe on Android
         *   • Resume loss per reconnect: ≤ 8 MB (acceptable for GB files)
         *   • ACK round-trips for a 1 GB file: ~128 (vs 1 024 at 1 MB)
         *
         * For wired Gigabit + very large files (>10 GB), consider 16 MB.
         * For weak WiFi / low-end devices, fall back to 2 MB.
         */
        const val CHUNK_SIZE = 8 * 1024 * 1024   // 8 MB

        private const val ACK_TIMEOUT_MS = 30_000L   // 30 s per chunk
    }

    /**
     * Upload [videoFile] and the companion result.json for [taskId].
     * @param onProgress [0.0, 1.0] overall upload progress
     */
    suspend fun upload(taskId: String, videoFile: File, onProgress: (Float) -> Unit) {
        val jsonFile = fileStore.resultJsonFile(taskId)
        val filesToUpload = buildList {
            add(videoFile to "video")
            if (jsonFile.exists()) add(jsonFile to "json")
        }

        val totalBytes = filesToUpload.sumOf { (f, _) -> f.length() }
        var uploadedBytes = 0L
        val transferId = UUID.randomUUID().toString()

        for ((file, role) in filesToUpload) {
            uploadFile(taskId, transferId, file, role) { bytesWritten ->
                uploadedBytes += bytesWritten
                onProgress((uploadedBytes.toFloat() / totalBytes).coerceIn(0f, 1f))
            }
            Log.d(TAG, "[$taskId] File '$role' upload complete")
        }

        val totalHash = fileStore.sha256Hex(videoFile)
        dataChannel.writeDataFrame(
            DataMessage.ResultTransferComplete(
                taskId     = taskId,
                transferId = transferId,
                totalHash  = totalHash,
            )
        )
        Log.i(TAG, "[$taskId] Upload complete, totalHash=$totalHash")
    }

    private suspend fun uploadFile(
        taskId: String,
        transferId: String,
        file: File,
        role: String,
        onChunkUploaded: (Int) -> Unit,
    ) {
        file.inputStream().buffered().use { inp ->
            var chunkIndex = 0
            val buf = ByteArray(CHUNK_SIZE)
            var read: Int
            while (inp.read(buf).also { read = it } != -1) {
                // For full chunks (read == CHUNK_SIZE), copyOf allocates exactly CHUNK_SIZE.
                // For the tail chunk (read < CHUNK_SIZE), copyOf allocates only the actual
                // bytes, avoiding a 8 MB allocation for a potentially tiny remainder.
                // copyOf is needed because MessageFramer.writeDataFrame writes payload.size
                // bytes, so we must not pass the full buf when read < CHUNK_SIZE.
                val payload = if (read == CHUNK_SIZE) buf.copyOf() else buf.copyOf(read)
                val hash    = sha256Hex(payload)
                val idx     = chunkIndex

                coroutineScope {
                    // ── Subscribe BEFORE sending to prevent missing ACK ──
                    // async(UNDISPATCHED) runs synchronously until the first
                    // suspension point inside `first { }`, at which point the
                    // SharedFlow subscriber is registered. Only then do we send.
                    val ackDeferred = async(start = CoroutineStart.UNDISPATCHED) {
                        dataChannel.dataEvents.first { msg ->
                            msg is DataMessage.ChunkAck &&
                                msg.taskId      == taskId     &&
                                msg.transferId  == transferId &&
                                msg.chunkIndex  == idx
                        }
                    }

                    Log.d(
                        TAG,
                        "[$taskId] upload send role=$role chunk=$idx size=$read transferId=$transferId",
                    )

                    dataChannel.writeDataFrame(
                        DataMessage.ResultChunk(
                            taskId     = taskId,
                            transferId = transferId,
                            chunkIndex = idx,
                            chunkHash  = hash,
                            payloadSize = read,
                            fileRole   = role,
                        ),
                        payload,
                    )

                    val waitStartNs = System.nanoTime()
                    val ack = withTimeoutOrNull(ACK_TIMEOUT_MS) {
                        ackDeferred.await()
                    } as? DataMessage.ChunkAck

                    if (ack == null) {
                        ackDeferred.cancel()
                        Log.w(
                            TAG,
                            "[$taskId] upload ack timeout role=$role chunk=$idx transferId=$transferId",
                        )
                        throw IOException(
                            "[$taskId] Timeout waiting for ChunkAck: role=$role chunk=$idx"
                        )
                    }

                    val waitedMs = (System.nanoTime() - waitStartNs) / 1_000_000L
                    Log.d(
                        TAG,
                        "[$taskId] upload ack role=$role chunk=$idx waitedMs=$waitedMs transferId=${ack.transferId}",
                    )
                }

                chunkIndex++
                onChunkUploaded(read)
            }
        }
    }

    private fun sha256Hex(bytes: ByteArray): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        return digest.digest(bytes).joinToString("") { "%02x".format(it) }
    }
}
