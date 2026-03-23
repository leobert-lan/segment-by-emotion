package osp.leobert.androd.mediaservice.ui.status

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import osp.leobert.androd.mediaservice.domain.state.TaskState

/**
 * 节点状态界面：显示当前任务 ID、视频名称、阶段进度；错误时显示提示横幅。
 */
@Composable
fun NodeStatusScreen(
    onDisconnect: () -> Unit,
    vm: NodeStatusViewModel = viewModel(),
) {
    val state by vm.taskState.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text = "节点状态", style = MaterialTheme.typography.headlineSmall)

        // State badge
        val (chipLabel, chipColor) = stateChip(state)
        AssistChip(
            onClick = {},
            label = { Text(chipLabel) },
        )

        Spacer(Modifier.height(4.dp))

        // Task info card
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
                        Text("✓ 处理完成", color = Color(0xFF2E7D32))
                    }
                    is TaskState.Connecting -> {
                        val s = state as TaskState.Connecting
                        Text("连接到 ${s.host}:${s.controlPort}…")
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                    }
                    is TaskState.AwaitingTask -> {
                        Text("已连接，等待任务下发")
                    }
                    else -> Text("空闲")
                }
            }
        }

        // Error banner
        if (state is TaskState.Error) {
            val err = state as TaskState.Error
            Card(
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        "错误: ${err.reason}",
                        color = MaterialTheme.colorScheme.error,
                    )
                    if (err.recoverable) {
                        Text("正在尝试重连…", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }

        Spacer(Modifier.weight(1f))

        Button(
            onClick = onDisconnect,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("断开连接")
        }
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

