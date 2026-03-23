package osp.leobert.androd.mediaservice.ui.connection

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

/**
 * 连接配置界面：输入服务器 IP、端口和节点 ID，点击"连接"启动 MediaNodeService。
 */
@Composable
fun ConnectionScreen(
    onConnected: () -> Unit,
    vm: ConnectionViewModel = viewModel(),
) {
    val savedHost by vm.serverHost.collectAsState()
    val savedControlPort by vm.controlPort.collectAsState()
    val savedDataPort by vm.dataPort.collectAsState()
    val savedNodeId by vm.nodeId.collectAsState()
    val error by vm.connectError.collectAsState()

    // Local editable state mirrors saved prefs
    var host by remember(savedHost) { mutableStateOf(savedHost) }
    var controlPort by remember(savedControlPort) { mutableStateOf(savedControlPort.toString()) }
    var dataPort by remember(savedDataPort) { mutableStateOf(savedDataPort.toString()) }
    var nodeId by remember(savedNodeId) { mutableStateOf(savedNodeId) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text = "媒体节点 — 连接配置", style = MaterialTheme.typography.headlineSmall)

        Spacer(Modifier.height(8.dp))

        OutlinedTextField(
            value = host,
            onValueChange = { host = it },
            label = { Text("服务器地址 (IP / 主机名)") },
            placeholder = { Text("例：192.168.1.100") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
        )

        Row(modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = controlPort,
                onValueChange = { controlPort = it },
                label = { Text("控制端口") },
                modifier = Modifier.weight(1f),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )
            Spacer(Modifier.width(12.dp))
            OutlinedTextField(
                value = dataPort,
                onValueChange = { dataPort = it },
                label = { Text("数据端口") },
                modifier = Modifier.weight(1f),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )
        }

        OutlinedTextField(
            value = nodeId,
            onValueChange = { nodeId = it },
            label = { Text("节点 ID") },
            placeholder = { Text("留空自动生成") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        if (error != null) {
            Text(text = error!!, color = MaterialTheme.colorScheme.error)
        }

        Spacer(Modifier.height(8.dp))

        Button(
            onClick = {
                if (nodeId.isNotBlank()) vm.saveNodeId(nodeId)
                vm.connect(
                    host = host,
                    controlPort = controlPort.toIntOrNull() ?: 23010,
                    dataPort = dataPort.toIntOrNull() ?: 23011,
                )
                onConnected()
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("连接")
        }
    }
}

