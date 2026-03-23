package osp.leobert.androd.mediaservice.storage.file

import android.content.Context
import java.io.File
import java.io.RandomAccessFile
import java.security.MessageDigest

/**
 * Manages the per-task directory layout under [Context.filesDir].
 *
 * Directory structure per task:
 *   <filesDir>/tasks/<taskId>/
 *     chunks/chunk_<index>.bin   ← individual received chunks
 *     assembled.mp4              ← chunks joined in order (download complete)
 *     output/result_<taskId>.mp4 ← MediaPipeline output
 *     result.json                ← summary JSON (export_data_design.md §3)
 *     processing.log             ← pipeline execution log
 */
class FileStoreManager(private val context: Context) {

    private fun taskDir(taskId: String): File =
        File(context.filesDir, "tasks/$taskId").also { it.mkdirs() }

    private fun chunksDir(taskId: String): File =
        File(taskDir(taskId), "chunks").also { it.mkdirs() }

    private fun outputDir(taskId: String): File =
        File(taskDir(taskId), "output").also { it.mkdirs() }

    fun chunkFile(taskId: String, chunkIndex: Int): File =
        File(chunksDir(taskId), "chunk_$chunkIndex.bin")

    fun assembledFile(taskId: String): File =
        File(taskDir(taskId), "assembled.mp4")

    fun resultVideoFile(taskId: String): File =
        File(outputDir(taskId), "result_$taskId.mp4")

    fun resultJsonFile(taskId: String): File =
        File(taskDir(taskId), "result.json")

    fun processingLogFile(taskId: String): File =
        File(taskDir(taskId), "processing.log")

    /** Write [bytes] to the chunk file for [chunkIndex]. */
    fun writeChunkPayload(taskId: String, chunkIndex: Int, bytes: ByteArray) {
        chunkFile(taskId, chunkIndex).writeBytes(bytes)
    }

    /**
     * Concatenate all chunk files in index order into [assembledFile].
     * @param totalChunks expected chunk count for validation
     * @throws IllegalStateException if any chunk file is missing
     */
    fun assembleFile(taskId: String, totalChunks: Int): File {
        val out = assembledFile(taskId)
        out.outputStream().buffered().use { sink ->
            for (i in 0 until totalChunks) {
                val chunk = chunkFile(taskId, i)
                check(chunk.exists()) { "Missing chunk $i for task $taskId" }
                chunk.inputStream().buffered().use { it.copyTo(sink) }
            }
        }
        return out
    }

    /** SHA-256 hex of [file]. */
    fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { inp ->
            val buf = ByteArray(8192)
            var read: Int
            while (inp.read(buf).also { read = it } != -1) {
                digest.update(buf, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    /** Delete all files under the task directory. */
    fun cleanTask(taskId: String) {
        taskDir(taskId).deleteRecursively()
    }
}

