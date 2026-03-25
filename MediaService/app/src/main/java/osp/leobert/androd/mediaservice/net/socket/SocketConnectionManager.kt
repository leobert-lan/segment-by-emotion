package osp.leobert.androd.mediaservice.net.socket

import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
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

    data class UnexpectedDisconnect(
        val channel: String,
        val reason: String,
        val cause: Throwable? = null,
    )

    companion object {
        private const val TAG = "SocketConnectionManager"
    }

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
            Log.i(
                TAG,
                "connect attempt=${attempt + 1} host=$host controlPort=$controlPort dataPort=$dataPort",
            )
            _state.value = ConnectionState.Connecting
            val result = runCatching { openBothChannels() }
            if (result.isSuccess) {
                _state.value = ConnectionState.Ready
                Log.i(TAG, "connect ready attempt=${attempt + 1}")
                return
            }
            val reason = result.exceptionOrNull()?.message ?: "unknown"
            _state.value = ConnectionState.Failed(reason)
            Log.w(
                TAG,
                "connect failed attempt=${attempt + 1} reason=$reason retryInMs=$backoffMs",
                result.exceptionOrNull(),
            )
            delay(backoffMs)
            backoffMs = minOf(backoffMs * 2, 60_000L)
            attempt++
        }
        Log.e(TAG, "connect failed: max attempts reached")
        _state.value = ConnectionState.Failed("Max reconnect attempts reached")
    }

    suspend fun disconnect() {
        Log.i(TAG, "disconnect start")
        controlChannel?.disconnect()
        dataChannel?.disconnect()
        controlChannel = null
        dataChannel = null
        _state.value = ConnectionState.Disconnected
        Log.i(TAG, "disconnect done")
    }

    suspend fun awaitUnexpectedDisconnect(): UnexpectedDisconnect {
        val ctrl = controlChannel ?: error("Control channel not connected")
        val data = dataChannel ?: error("Data channel not connected")
        return combine(ctrl.disconnectEvents, data.disconnectEvents) { ctrlEvent, dataEvent ->
            when {
                ctrlEvent != null -> UnexpectedDisconnect(
                    channel = "control",
                    reason = ctrlEvent.reason,
                    cause = ctrlEvent.cause,
                )
                dataEvent != null -> UnexpectedDisconnect(
                    channel = "data",
                    reason = dataEvent.reason,
                    cause = dataEvent.cause,
                )
                else -> null
            }
        }.first { it != null }!!
    }

    private suspend fun openBothChannels() {
        val ctrl = controlClientFactory(host, controlPort)
        val data = dataClientFactory(host, dataPort)
        Log.d(TAG, "opening control channel")
        ctrl.connect()
        try {
            Log.d(TAG, "opening data channel")
            data.connect()
        } catch (e: Exception) {
            Log.w(TAG, "data channel connect failed, closing control channel", e)
            ctrl.disconnect()
            throw e
        }
        controlChannel = ctrl
        dataChannel = data
        Log.i(TAG, "both channels connected")
    }
}
