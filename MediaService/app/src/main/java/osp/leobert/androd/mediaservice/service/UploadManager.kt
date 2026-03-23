package osp.leobert.androd.mediaservice.service

import android.util.Log
import osp.leobert.androd.mediaservice.net.protocol.DataMessage
import osp.leobert.androd.mediaservice.net.socket.DataChannelClient
import osp.leobert.androd.mediaservice.storage.file.FileStoreManager
import java.io.File
import java.util.UUID

/**
 * Uploads the processed result file back to the Python server via the data channel.
 *
 * Protocol:
 *   For each 1 MB chunk: send RESULT_CHUNK header + binary payload → await ChunkAck
 *   After all chunks confirmed: send RESULT_TRANSFER_COMPLETE
 *
 * File roles sent: "video" (result .mp4), "json" (result.json)
 */
class UploadManager(
    private val dataChannel: DataChannelClient,
    private val fileStore: FileStoreManager,
) {

    companion object {
        private const val TAG = "UploadManager"
        private const val CHUNK_SIZE = 1 * 1024 * 1024  // 1 MB
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
        }

        val totalHash = fileStore.sha256Hex(videoFile)
        dataChannel.writeDataFrame(
            DataMessage.ResultTransferComplete(
                taskId = taskId,
                transferId = transferId,
                totalHash = totalHash,
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
                val payload = buf.copyOf(read)
                val hash = sha256Hex(payload)
                dataChannel.writeDataFrame(
                    DataMessage.ResultChunk(
                        taskId = taskId,
                        transferId = transferId,
                        chunkIndex = chunkIndex,
                        chunkHash = hash,
                        payloadSize = read,
                        fileRole = role,
                    ),
                    payload,
                )
                // TODO: await ChunkAck with timeout before advancing
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

