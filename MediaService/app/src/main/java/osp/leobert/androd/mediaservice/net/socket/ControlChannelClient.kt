package osp.leobert.androd.mediaservice.net.socket

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import osp.leobert.androd.mediaservice.net.protocol.ControlMessage
import osp.leobert.androd.mediaservice.net.protocol.MessageFramer
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.Socket

/**
 * Manages the control channel TCP connection (port 23010).
 *
 * - Connects on [connect]; sends HELLO immediately.
 * - Runs a read loop that publishes parsed [ControlMessage]s to [incomingMessages].
 * - Thread-safe write via [send] (Mutex-protected).
 * - Heartbeat: caller is responsible for periodic [TaskStatusReport] sends.
 */
class ControlChannelClient(
    private val host: String,
    private val port: Int,
    private val helloBuilder: () -> ControlMessage.Hello,
) {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val _incomingMessages = MutableSharedFlow<ControlMessage>(extraBufferCapacity = 64)
    val incomingMessages: SharedFlow<ControlMessage> = _incomingMessages

    private var socket: Socket? = null
    private val writeMutex = Mutex()
    private var readJob: Job? = null

    val isConnected: Boolean get() = socket?.isConnected == true && socket?.isClosed == false

    /**
     * Opens the TCP connection and starts the read loop.
     * Sends HELLO after connecting.
     * @throws Exception if connection fails.
     */
    suspend fun connect() = withContext(Dispatchers.IO) {
        val s = Socket(host, port)
        socket = s
        readJob = scope.launch { readLoop(s) }
        send(helloBuilder())
    }

    /**
     * Sends a [ControlMessage] on the control channel. Mutex-protected for concurrent callers.
     */
    suspend fun send(message: ControlMessage) = withContext(Dispatchers.IO) {
        writeMutex.withLock {
            val sock = socket ?: error("Control channel not connected")
            val encoded = MessageFramer.encodeControl(message)
            sock.getOutputStream().write(encoded.toByteArray(Charsets.UTF_8))
            sock.getOutputStream().flush()
        }
    }

    suspend fun disconnect() {
        readJob?.cancelAndJoin()
        withContext(Dispatchers.IO) { socket?.close() }
        socket = null
    }

    private suspend fun readLoop(s: Socket) {
        val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
        try {
            var line: String?
            while (reader.readLine().also { line = it } != null) {
                val msg = runCatching { MessageFramer.decodeControl(line!!) }.getOrNull() ?: continue
                _incomingMessages.emit(msg)
            }
        } catch (_: Exception) {
            // Socket closed or IO error — connection manager handles reconnect.
        }
    }
}

