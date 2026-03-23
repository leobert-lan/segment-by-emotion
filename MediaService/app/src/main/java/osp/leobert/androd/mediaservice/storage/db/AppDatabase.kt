package osp.leobert.androd.mediaservice.storage.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import osp.leobert.androd.mediaservice.storage.entity.LocalTaskEntity
import osp.leobert.androd.mediaservice.storage.entity.TransferChunkEntity

/**
 * Single Room database for node-local state persistence.
 * Supports cold-start recovery and chunk-level resume for interrupted transfers.
 */
@Database(
    entities = [LocalTaskEntity::class, TransferChunkEntity::class],
    version = 1,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun taskDao(): TaskDao
    abstract fun chunkDao(): ChunkDao

    companion object {
        @Volatile private var INSTANCE: AppDatabase? = null

        fun getInstance(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "node_state.db",
                ).build().also { INSTANCE = it }
            }
    }
}

