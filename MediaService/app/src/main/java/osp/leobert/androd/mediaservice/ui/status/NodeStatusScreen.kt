package osp.leobert.androd.mediaservice.ui.status

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import osp.leobert.androd.mediaservice.domain.state.TaskState

/**
 * 节点状态界面：显示当前任务 ID、视频名称、阶段进度；错误时显示提示横幅。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NodeStatusScreen(
    onDisconnect: () -> Unit,
    vm: NodeStatusViewModel = viewModel(),
) {
    val state by vm.taskState.collectAsState()
    var showDisconnectConfirm by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("节点状态") },
                colors = TopAppBarDefaults.topAppBarColors(),
                actions = {
                    TextButton(onClick = { showDisconnectConfirm = true }) {
                        Text("断开")
                    }
                },
            )
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(horizontal = 24.dp, vertical = 16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val (chipLabel, _) = stateChip(state)
            AssistChip(
                onClick = {},
                label = { Text(chipLabel) },
            )

            if (state is TaskState.Receiving || state is TaskState.Processing || state is TaskState.Uploading) {
                Text(
                    text = "界面已保持常亮；即使长时间停留在此页，屏幕也不会自动熄灭。",
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    when (state) {
                        is TaskState.Receiving -> {
                            val s = state as TaskState.Receiving
                            Text("任务 ID: ${s.taskId}")
                            Text("视频: ${s.videoName}")
                            Text("接收中…")
                            LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth())
                        }
                        is TaskState.Processing -> {
                            val s = state as TaskState.Processing
                            Text("任务 ID: ${s.taskId}")
                            Text("阶段: ${stageName(s)}")
                            LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth())
                        }
                        is TaskState.Uploading -> {
                            val s = state as TaskState.Uploading
                            Text("任务 ID: ${s.taskId}")
                            Text("上传结果中…")
                            LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth())
                        }
                        is TaskState.Done -> {
                            val s = state as TaskState.Done
                            Text("任务 ID: ${s.taskId}")
                            Text("✓ 处理完成，节点将回到待命状态", color = Color(0xFF2E7D32))
                        }
                        is TaskState.Error -> {
                            val s = state as TaskState.Error
                            s.taskId?.let { Text("任务 ID: $it") }
                            Text(
                                if (s.recoverable) "任务中断，恢复连接后将从原阶段继续" else "任务失败，需要人工处理",
                                color = MaterialTheme.colorScheme.error,
                            )
                            Text("原因: ${s.reason}")
                        }
                        is TaskState.Connecting -> {
                            val s = state as TaskState.Connecting
                            Text("连接到 ${s.host}:${s.controlPort}…")
                            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        }
                        is TaskState.AwaitingTask -> {
                            Text("已连接，等待任务下发")
                            Text("连接会继续保持在线，任务完成后不需要手动重连。")
                        }
                        else -> Text("空闲")
                    }
                }
            }

            if (state is TaskState.Error) {
                val err = state as TaskState.Error
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            "错误: ${err.reason}",
                            color = MaterialTheme.colorScheme.error,
                        )
                        Text(
                            if (err.recoverable) "恢复时只会继续原本的接收/处理/上传阶段，不会无条件开始回传。" else "当前错误不会自动恢复。",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            OutlinedButton(
                onClick = { showDisconnectConfirm = true },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("断开连接")
            }
        }
    }

    if (showDisconnectConfirm) {
        AlertDialog(
            onDismissRequest = { showDisconnectConfirm = false },
            title = { Text("确认断开连接？") },
            text = { Text("断开后节点会停止接收新任务，当前任务也会中断并在下次连接时尝试恢复。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        showDisconnectConfirm = false
                        onDisconnect()
                    },
                ) {
                    Text("确认断开")
                }
            },
            dismissButton = {
                TextButton(onClick = { showDisconnectConfirm = false }) {
                    Text("取消")
                }
            },
        )
    }
}

@Composable
private fun stageName(state: TaskState.Processing): String = when (state.stage.name) {
    "CUTTING"     -> "裁剪片段"
    "MERGING"     -> "合并片段"
    "COMPRESSING" -> "压缩编码"
    "TRANSCODING" -> "转码处理"
    else          -> state.stage.name
}

private fun stateChip(state: TaskState): Pair<String, Color> = when (state) {
    is TaskState.Idle -> "空闲" to Color.Gray
    is TaskState.Connecting -> "连接中" to Color(0xFFF9A825)
    is TaskState.AwaitingTask -> "等待任务" to Color(0xFF1565C0)
    is TaskState.Receiving -> "接收中" to Color(0xFF1565C0)
    is TaskState.Processing -> "处理中" to Color(0xFF6A1B9A)
    is TaskState.Uploading -> "上传中" to Color(0xFF1565C0)
    is TaskState.Done -> "完成" to Color(0xFF2E7D32)
    is TaskState.Error -> "错误" to Color.Red
}

