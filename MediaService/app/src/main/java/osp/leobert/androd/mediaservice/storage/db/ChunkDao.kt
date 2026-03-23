package osp.leobert.androd.mediaservice.storage.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import osp.leobert.androd.mediaservice.storage.entity.TransferChunkEntity

@Dao
interface ChunkDao {

    /** Insert a chunk record (ignore if already exists — idempotent). */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertOrIgnore(chunk: TransferChunkEntity)

    /** Bulk-insert chunk placeholders at task start (received=0). */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertAllOrIgnore(chunks: List<TransferChunkEntity>)

    @Query("UPDATE transfer_chunks SET received = 1 WHERE taskId = :taskId AND chunkIndex = :chunkIndex")
    suspend fun markReceived(taskId: String, chunkIndex: Int)

    /** Returns sorted list of chunk indices not yet received — used for TRANSFER_RESUME_REQUEST. */
    @Query("SELECT chunkIndex FROM transfer_chunks WHERE taskId = :taskId AND received = 0 ORDER BY chunkIndex")
    suspend fun getMissingIndices(taskId: String): List<Int>

    @Query("SELECT COUNT(*) FROM transfer_chunks WHERE taskId = :taskId AND received = 1")
    suspend fun countReceived(taskId: String): Int

    @Query("SELECT COUNT(*) FROM transfer_chunks WHERE taskId = :taskId")
    suspend fun countTotal(taskId: String): Int

    @Query("DELETE FROM transfer_chunks WHERE taskId = :taskId")
    suspend fun deleteByTask(taskId: String)
}

