package osp.leobert.androd.mediaservice.storage.db

import android.content.Context
import androidx.room.migration.Migration
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase
import osp.leobert.androd.mediaservice.storage.entity.LocalTaskEntity
import osp.leobert.androd.mediaservice.storage.entity.TransferChunkEntity

/**
 * Single Room database for node-local state persistence.
 * Supports cold-start recovery and chunk-level resume for interrupted transfers.
 */
@Database(
    entities = [LocalTaskEntity::class, TransferChunkEntity::class],
    version = 2,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun taskDao(): TaskDao
    abstract fun chunkDao(): ChunkDao

    companion object {
        @Volatile private var INSTANCE: AppDatabase? = null

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE local_tasks ADD COLUMN transferId TEXT")
            }
        }

        fun getInstance(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "node_state.db",
                )
                    .addMigrations(MIGRATION_1_2)
                    .build()
                    .also { INSTANCE = it }
            }
    }
}

