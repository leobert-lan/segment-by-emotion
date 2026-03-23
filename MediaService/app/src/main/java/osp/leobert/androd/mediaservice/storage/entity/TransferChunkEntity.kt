package osp.leobert.androd.mediaservice.storage.entity

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index

/**
 * Tracks reception status of each 1 MB chunk during video download.
 * Enables resume: on reconnect, query [received]=0 to build TRANSFER_RESUME_REQUEST.
 *
 * Composite PK (taskId, chunkIndex). CASCADE delete on task removal.
 */
@Entity(
    tableName = "transfer_chunks",
    primaryKeys = ["taskId", "chunkIndex"],
    foreignKeys = [
        ForeignKey(
            entity = LocalTaskEntity::class,
            parentColumns = ["taskId"],
            childColumns = ["taskId"],
            onDelete = ForeignKey.CASCADE,
        )
    ],
    indices = [Index("taskId")],
)
data class TransferChunkEntity(
    val taskId: String,
    val chunkIndex: Int,
    /** SHA-256 hex of this chunk's payload, for integrity verification */
    val chunkHash: String,
    /** 1 = received and persisted, 0 = pending */
    val received: Int = 0,
    /** Byte offset within the assembled file where this chunk is written */
    val fileOffset: Long,
)

