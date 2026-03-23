package osp.leobert.androd.mediaservice.net.socket

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Coordinates both TCP channels.
 *
 * Per SDS §3.4: task dispatch is only allowed after BOTH channels confirm open.
 * Any channel failure triggers exponential-backoff reconnect (1s → 2s → 4s → 8s → … → 60s).
 */
class SocketConnectionManager(
    private val host: String,
    private val controlPort: Int,
    private val dataPort: Int,
    private val controlClientFactory: (String, Int) -> ControlChannelClient,
    private val dataClientFactory: (String, Int) -> DataChannelClient,
) {

    sealed class ConnectionState {
        data object Disconnected : ConnectionState()
        data object Connecting : ConnectionState()
        data object Ready : ConnectionState()
        data class Failed(val reason: String) : ConnectionState()
    }

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val state: StateFlow<ConnectionState> = _state

    var controlChannel: ControlChannelClient? = null
        private set
    var dataChannel: DataChannelClient? = null
        private set

    /**
     * Attempt to open both channels.
     * Retries with exponential backoff on failure.
     * Call from a coroutine (e.g. TaskOrchestrator).
     */
    suspend fun connectWithRetry(maxAttempts: Int = Int.MAX_VALUE) {
        var attempt = 0
        var backoffMs = 1_000L
        while (attempt < maxAttempts) {
            _state.value = ConnectionState.Connecting
            val result = runCatching { openBothChannels() }
            if (result.isSuccess) {
                _state.value = ConnectionState.Ready
                return
            }
            val reason = result.exceptionOrNull()?.message ?: "unknown"
            _state.value = ConnectionState.Failed(reason)
            delay(backoffMs)
            backoffMs = minOf(backoffMs * 2, 60_000L)
            attempt++
        }
        _state.value = ConnectionState.Failed("Max reconnect attempts reached")
    }

    suspend fun disconnect() {
        controlChannel?.disconnect()
        dataChannel?.disconnect()
        controlChannel = null
        dataChannel = null
        _state.value = ConnectionState.Disconnected
    }

    private suspend fun openBothChannels() {
        val ctrl = controlClientFactory(host, controlPort)
        val data = dataClientFactory(host, dataPort)
        // Both must succeed; if either throws, the other is discarded.
        ctrl.connect()
        try {
            data.connect()
        } catch (e: Exception) {
            ctrl.disconnect()
            throw e
        }
        controlChannel = ctrl
        dataChannel = data
    }
}

