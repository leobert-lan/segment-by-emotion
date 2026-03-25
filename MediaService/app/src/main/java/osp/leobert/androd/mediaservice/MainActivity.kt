
package osp.leobert.androd.mediaservice

import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.service.MediaNodeService
import osp.leobert.androd.mediaservice.service.NodeStateHolder
import osp.leobert.androd.mediaservice.storage.db.AppDatabase
import osp.leobert.androd.mediaservice.storage.prefs.NodePreferences
import osp.leobert.androd.mediaservice.ui.navigation.AppNavHost
import osp.leobert.androd.mediaservice.ui.theme.MediaServiceTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        resumePendingTaskIfNeeded()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableEdgeToEdge()
        setContent {
            MediaServiceTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    AppNavHost()
                }
            }
        }
    }

    private fun resumePendingTaskIfNeeded() {
        lifecycleScope.launch {
            if (NodeStateHolder.state.value !is TaskState.Idle) {
                return@launch
            }

            val pendingTask = AppDatabase.getInstance(applicationContext)
                .taskDao()
                .getPendingTask()
                ?: return@launch

            val prefs = NodePreferences(applicationContext)
            val host = prefs.serverHost.first().trim()
            if (host.isBlank()) {
                return@launch
            }

            startForegroundService(
                Intent(this@MainActivity, MediaNodeService::class.java).apply {
                    action = MediaNodeService.ACTION_CONNECT
                    putExtra(MediaNodeService.EXTRA_HOST, host)
                    putExtra(MediaNodeService.EXTRA_CONTROL_PORT, prefs.controlPort.first())
                    putExtra(MediaNodeService.EXTRA_DATA_PORT, prefs.dataPort.first())
                    putExtra("recovered_task_id", pendingTask.taskId)
                }
            )
        }
    }
}

