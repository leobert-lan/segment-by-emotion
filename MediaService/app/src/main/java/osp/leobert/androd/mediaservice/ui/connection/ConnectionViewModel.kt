package osp.leobert.androd.mediaservice.ui.connection

import android.app.Application
import android.content.Intent
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import osp.leobert.androd.mediaservice.service.MediaNodeService
import osp.leobert.androd.mediaservice.storage.prefs.NodePreferences

class ConnectionViewModel(application: Application) : AndroidViewModel(application) {

    private val prefs = NodePreferences(application)

    val serverHost: StateFlow<String> = prefs.serverHost.stateIn(
        viewModelScope, SharingStarted.WhileSubscribed(5_000), ""
    )
    val controlPort: StateFlow<Int> = prefs.controlPort.stateIn(
        viewModelScope, SharingStarted.WhileSubscribed(5_000), NodePreferences.DEFAULT_CONTROL_PORT
    )
    val dataPort: StateFlow<Int> = prefs.dataPort.stateIn(
        viewModelScope, SharingStarted.WhileSubscribed(5_000), NodePreferences.DEFAULT_DATA_PORT
    )
    val nodeId: StateFlow<String> = prefs.nodeId.stateIn(
        viewModelScope, SharingStarted.WhileSubscribed(5_000), ""
    )

    private val _connectError = MutableStateFlow<String?>(null)
    val connectError: StateFlow<String?> = _connectError

    fun saveHost(host: String) = viewModelScope.launch { prefs.saveServerHost(host) }
    fun saveControlPort(port: Int) = viewModelScope.launch { prefs.saveControlPort(port) }
    fun saveDataPort(port: Int) = viewModelScope.launch { prefs.saveDataPort(port) }
    fun saveNodeId(id: String) = viewModelScope.launch { prefs.saveNodeId(id) }

    fun connect(host: String, controlPort: Int, dataPort: Int) {
        if (host.isBlank()) {
            _connectError.value = "请输入服务器地址"
            return
        }
        _connectError.value = null
        viewModelScope.launch {
            prefs.saveServerHost(host)
            prefs.saveControlPort(controlPort)
            prefs.saveDataPort(dataPort)
        }
        val intent = Intent(getApplication(), MediaNodeService::class.java).apply {
            action = MediaNodeService.ACTION_CONNECT
            putExtra(MediaNodeService.EXTRA_HOST, host)
            putExtra(MediaNodeService.EXTRA_CONTROL_PORT, controlPort)
            putExtra(MediaNodeService.EXTRA_DATA_PORT, dataPort)
        }
        getApplication<Application>().startForegroundService(intent)
    }

    fun disconnect() {
        val intent = Intent(getApplication(), MediaNodeService::class.java).apply {
            action = MediaNodeService.ACTION_DISCONNECT
        }
        getApplication<Application>().startService(intent)
    }
}

