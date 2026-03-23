package osp.leobert.androd.mediaservice.ui.status

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.service.NodeStateHolder

/**
 * Exposes [taskState] for [NodeStatusScreen].
 *
 * Reads from [NodeStateHolder], which is updated by [MediaNodeService] whenever
 * [TaskOrchestrator] emits a new state. No bound service or BroadcastReceiver needed.
 */
class NodeStatusViewModel(application: Application) : AndroidViewModel(application) {

    val taskState: StateFlow<TaskState> = NodeStateHolder.state
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), TaskState.Idle)
}
