package osp.leobert.androd.mediaservice.storage.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Persists the current task assigned by the Python server.
 * Used for cold-start recovery: if status is not "done" or "idle" on startup,
 * TaskOrchestrator will attempt to resume.
 */
@Entity(tableName = "local_tasks")
data class LocalTaskEntity(
    @PrimaryKey val taskId: String,
    val videoName: String,
    val fileSizeBytes: Long,
    val totalChunks: Int,
    /** SHA-256 hex of the fully assembled file */
    val fileHash: String,
    /** ProcessingParams serialized as JSON string */
    val processingParamsJson: String,
    /** Mirrors TaskState class name: "Idle","Connecting","Receiving","Processing","Uploading","Done","Error" */
    val status: String,
    val errorMessage: String? = null,
    val createdAt: String,
    val updatedAt: String,
)

