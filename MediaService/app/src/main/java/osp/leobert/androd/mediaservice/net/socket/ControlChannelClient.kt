package osp.leobert.androd.mediaservice.net.socket

import android.util.Log
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
 * - Heartbeat: caller is responsible for periodic [ControlMessage.TaskStatusReport] sends.
 */
class ControlChannelClient(
    private val host: String,
    private val port: Int,
    private val helloBuilder: () -> ControlMessage.Hello,
) {

    data class DisconnectEvent(
        val reason: String,
        val cause: Throwable? = null,
    )

    companion object {
        private const val TAG = "ControlChannelClient"
    }

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val _incomingMessages = MutableSharedFlow<ControlMessage>(extraBufferCapacity = 64)
    val incomingMessages: SharedFlow<ControlMessage> = _incomingMessages
    private val _disconnectEvents = MutableSharedFlow<DisconnectEvent>(extraBufferCapacity = 1)
    val disconnectEvents: SharedFlow<DisconnectEvent> = _disconnectEvents

    private var socket: Socket? = null
    private val writeMutex = Mutex()
    private var readJob: Job? = null
    private val connId: String = Integer.toHexString(System.identityHashCode(this))
    @Volatile
    private var disconnectExpected: Boolean = false

    val isConnected: Boolean get() = socket?.isConnected == true && socket?.isClosed == false

    /**
     * Opens the TCP connection and starts the read loop.
     * Sends HELLO after connecting.
     * @throws Exception if connection fails.
     */
    suspend fun connect() = withContext(Dispatchers.IO) {
        Log.i(TAG, "[$connId] connect start host=$host port=$port")
        disconnectExpected = false
        val s = Socket(host, port).apply {
            keepAlive = true
            tcpNoDelay = true
        }
        socket = s
        Log.i(
            TAG,
            "[$connId] connect success local=${s.localAddress.hostAddress}:${s.localPort} " +
                "remote=${s.inetAddress.hostAddress}:${s.port}",
        )
        readJob = scope.launch { readLoop(s) }
        val hello = helloBuilder()
        Log.d(TAG, "[$connId] send HELLO requestId=${hello.requestId}")
        send(hello)
    }

    /**
     * Sends a [ControlMessage] on the control channel. Mutex-protected for concurrent callers.
     */
    suspend fun send(message: ControlMessage) = withContext(Dispatchers.IO) {
        writeMutex.withLock {
            val sock = socket ?: error("Control channel not connected")
            val encoded = MessageFramer.encodeControl(message)
            val bytes = encoded.toByteArray(Charsets.UTF_8)
            Log.d(
                TAG,
                "[$connId] send type=${message.type} requestId=${message.requestId} bytes=${bytes.size}\r\n content=$encoded",
            )
            sock.getOutputStream().write(bytes)
            sock.getOutputStream().flush()
        }
    }

    suspend fun disconnect() {
        Log.i(TAG, "[$connId] disconnect start isConnected=$isConnected")
        disconnectExpected = true
        readJob?.cancelAndJoin()
        withContext(Dispatchers.IO) { socket?.close() }
        socket = null
        Log.i(TAG, "[$connId] disconnect done")
    }

    private suspend fun readLoop(s: Socket) {
        val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
        Log.d(TAG, "[$connId] readLoop start")
        try {
            var line: String?
            while (reader.readLine().also { line = it } != null) {
                val raw = line!!
                val msg = runCatching { MessageFramer.decodeControl(raw) }
                    .onFailure {
                        Log.w(TAG, "[$connId] decode failed rawLength=${raw.length} error=${it.message}")
                    }
                    .getOrNull() ?: continue
                Log.d(
                    TAG,
                    "[$connId] recv type=${msg.type} requestId=${msg.requestId} rawLength=${raw.length}",
                )
                _incomingMessages.emit(msg)
            }
            Log.i(TAG, "[$connId] readLoop end: peer closed stream")
        } catch (e: Exception) {
            // Socket closed or IO error — connection manager handles reconnect.
            Log.w(TAG, "[$connId] readLoop exception error=${e.message}", e)
        } finally {
            if (socket === s) {
                socket = null
            }
            if (!disconnectExpected) {
                val reason = if (s.isClosed) "Control socket closed unexpectedly" else "Control read loop ended"
                _disconnectEvents.tryEmit(DisconnectEvent(reason))
            }
            Log.d(TAG, "[$connId] readLoop exit")
        }
    }
}
