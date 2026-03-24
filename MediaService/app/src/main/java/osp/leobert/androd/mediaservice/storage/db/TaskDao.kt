package osp.leobert.androd.mediaservice.storage.db

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import osp.leobert.androd.mediaservice.storage.entity.LocalTaskEntity

@Dao
interface TaskDao {

    @Upsert
    suspend fun upsert(task: LocalTaskEntity)

    @Query("SELECT * FROM local_tasks WHERE taskId = :taskId")
    suspend fun getById(taskId: String): LocalTaskEntity?

    /**
     * Returns tasks that need recovery on startup (not done / not idle).
     */
    @Query("SELECT * FROM local_tasks WHERE status NOT IN ('Done', 'Idle') ORDER BY updatedAt DESC LIMIT 1")
    suspend fun getPendingTask(): LocalTaskEntity?

    @Query("UPDATE local_tasks SET status = :status, errorMessage = NULL, updatedAt = :updatedAt WHERE taskId = :taskId")
    suspend fun updateStatus(taskId: String, status: String, updatedAt: String)

    @Query("UPDATE local_tasks SET status = :status, errorMessage = :error, updatedAt = :updatedAt WHERE taskId = :taskId")
    suspend fun updateStatusWithError(taskId: String, status: String, error: String?, updatedAt: String)

    @Query("DELETE FROM local_tasks WHERE taskId = :taskId")
    suspend fun delete(taskId: String)
}

