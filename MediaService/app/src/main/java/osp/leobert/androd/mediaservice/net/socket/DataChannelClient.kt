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
import osp.leobert.androd.mediaservice.net.protocol.DataMessage
import osp.leobert.androd.mediaservice.net.protocol.MessageFramer
import java.net.Socket

/**
 * Manages the data channel TCP connection (port 23011).
 *
 * Receive path: frame read loop → [onChunkReceived] callback → emits [DataMessage.ChunkAck].
 * Upload path: [writeDataFrame] sends result file chunks back to the server.
 *
 * The same socket is reused for both download and upload (sequential, not concurrent).
 */
class DataChannelClient(
    private val host: String,
    private val port: Int,
    /**
     * Called for each received [DataMessage.Chunk]. The implementer should:
     *   1. Persist the payload bytes via FileStoreManager.
     *   2. Update ChunkDao.markReceived.
     *   3. Return true if stored successfully (triggers ChunkAck).
     */
    private val onChunkReceived: suspend (DataMessage.Chunk, ByteArray) -> Boolean,
    /** Called when a [DataMessage.TransferComplete] is received. */
    private val onTransferComplete: suspend (DataMessage.TransferComplete) -> Unit,
) {

    companion object {
        private const val TAG = "DataChannelClient"
    }

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val writeMutex = Mutex()

    private val _dataEvents = MutableSharedFlow<DataMessage>(extraBufferCapacity = 64)
    /** Non-chunk data events (TransferComplete, etc.) for the orchestrator. */
    val dataEvents: SharedFlow<DataMessage> = _dataEvents

    private var socket: Socket? = null
    private var readJob: Job? = null
    private val connId: String = Integer.toHexString(System.identityHashCode(this))

    val isConnected: Boolean get() = socket?.isConnected == true && socket?.isClosed == false

    suspend fun connect() = withContext(Dispatchers.IO) {
        Log.i(TAG, "[$connId] connect start host=$host port=$port")
        val s = Socket(host, port)
        socket = s
        Log.i(
            TAG,
            "[$connId] connect success local=${s.localAddress.hostAddress}:${s.localPort} " +
                "remote=${s.inetAddress.hostAddress}:${s.port}",
        )
        readJob = scope.launch { readLoop(s) }
    }

    /**
     * Write any DataMessage header + optional binary payload (for result upload).
     */
    suspend fun writeDataFrame(message: DataMessage, payload: ByteArray? = null) =
        withContext(Dispatchers.IO) {
            writeMutex.withLock {
                val out = socket?.getOutputStream() ?: error("Data channel not connected")
                Log.d(
                    TAG,
                    "[$connId] send type=${message.type} payloadBytes=${payload?.size ?: 0}",
                )
                MessageFramer.writeDataFrame(out, message, payload)
            }
        }

    suspend fun disconnect() {
        Log.i(TAG, "[$connId] disconnect start isConnected=$isConnected")
        readJob?.cancelAndJoin()
        withContext(Dispatchers.IO) { socket?.close() }
        socket = null
        Log.i(TAG, "[$connId] disconnect done")
    }

    private suspend fun readLoop(s: Socket) {
        val inp = s.getInputStream()
        Log.d(TAG, "[$connId] readLoop start")
        try {
            while (true) {
                val header = MessageFramer.readDataFrameHeader(inp)
                when (header) {
                    is DataMessage.Chunk -> {
                        Log.d(
                            TAG,
                            "[$connId] recv CHUNK taskId=${header.taskId} transferId=${header.transferId} " +
                                "chunk=${header.chunkIndex} payload=${header.payloadSize}",
                        )
                        val payload = MessageFramer.readPayload(inp, header.payloadSize)
                        val ok = onChunkReceived(header, payload)
                        Log.d(TAG, "[$connId] chunk store result chunk=${header.chunkIndex} ok=$ok")
                        if (ok) {
                            writeDataFrame(
                                DataMessage.ChunkAck(
                                    taskId = header.taskId,
                                    transferId = header.transferId,
                                    chunkIndex = header.chunkIndex,
                                )
                            )
                        }
                    }
                    is DataMessage.TransferComplete -> {
                        MessageFramer.readPayload(inp, header.payloadSize) // consume (0 bytes)
                        Log.d(
                            TAG,
                            "[$connId] recv TRANSFER_COMPLETE taskId=${header.taskId} " +
                                "transferId=${header.transferId}",
                        )
                        onTransferComplete(header)
                        _dataEvents.emit(header)
                    }
                    else -> {
                        val payloadSize = when (header) {
                            is DataMessage.ChunkAck -> header.payloadSize
                            is DataMessage.ResultTransferComplete -> header.payloadSize
                            is DataMessage.TransferResumeRequest -> header.payloadSize
                            is DataMessage.ResultChunk -> header.payloadSize
                        }
                        Log.d(TAG, "[$connId] recv ${header.type} payload=$payloadSize")
                        MessageFramer.readPayload(inp, payloadSize)
                        _dataEvents.emit(header)
                    }
                }
            }
        } catch (e: Exception) {
            // Socket closed or IO error — connection manager handles reconnect.
            Log.w(TAG, "[$connId] readLoop exception error=${e.message}", e)
        } finally {
            Log.d(TAG, "[$connId] readLoop exit")
        }
    }
}
