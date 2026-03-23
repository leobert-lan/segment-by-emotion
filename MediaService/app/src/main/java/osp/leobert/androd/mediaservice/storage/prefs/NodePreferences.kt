package osp.leobert.androd.mediaservice.storage.prefs

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.util.UUID

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "node_prefs")

/**
 * Persists node connection preferences via DataStore.
 * These survive app restarts so the user doesn't need to re-enter settings.
 */
class NodePreferences(private val context: Context) {

    companion object {
        private val KEY_SERVER_HOST = stringPreferencesKey("server_host")
        private val KEY_CONTROL_PORT = intPreferencesKey("control_port")
        private val KEY_DATA_PORT = intPreferencesKey("data_port")
        private val KEY_NODE_ID = stringPreferencesKey("node_id")
        private val KEY_NODE_VERSION = stringPreferencesKey("node_version")

        const val DEFAULT_CONTROL_PORT = 23010
        const val DEFAULT_DATA_PORT = 23011
        const val DEFAULT_NODE_VERSION = "1.0.0"
    }

    val serverHost: Flow<String> = context.dataStore.data.map { it[KEY_SERVER_HOST] ?: "" }
    val controlPort: Flow<Int> = context.dataStore.data.map { it[KEY_CONTROL_PORT] ?: DEFAULT_CONTROL_PORT }
    val dataPort: Flow<Int> = context.dataStore.data.map { it[KEY_DATA_PORT] ?: DEFAULT_DATA_PORT }
    val nodeId: Flow<String> = context.dataStore.data.map {
        it[KEY_NODE_ID] ?: UUID.randomUUID().toString()
    }
    val nodeVersion: Flow<String> = context.dataStore.data.map {
        it[KEY_NODE_VERSION] ?: DEFAULT_NODE_VERSION
    }

    suspend fun saveServerHost(host: String) {
        context.dataStore.edit { it[KEY_SERVER_HOST] = host }
    }

    suspend fun saveControlPort(port: Int) {
        context.dataStore.edit { it[KEY_CONTROL_PORT] = port }
    }

    suspend fun saveDataPort(port: Int) {
        context.dataStore.edit { it[KEY_DATA_PORT] = port }
    }

    suspend fun saveNodeId(nodeId: String) {
        context.dataStore.edit { it[KEY_NODE_ID] = nodeId }
    }

    /** Ensures a nodeId exists; generates and persists one if not. */
    suspend fun ensureNodeId(): String {
        val prefs = context.dataStore.data.map { it[KEY_NODE_ID] }
        // Read once synchronously via first emission is done in the caller via collect.
        // Callers should use nodeId Flow; this helper is for one-shot initialization.
        val id = UUID.randomUUID().toString()
        context.dataStore.edit { prefs2 ->
            if (prefs2[KEY_NODE_ID] == null) prefs2[KEY_NODE_ID] = id
        }
        return id
    }
}

