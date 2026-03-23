package osp.leobert.androd.mediaservice.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import osp.leobert.androd.mediaservice.domain.state.TaskState

/**
 * Process-scoped singleton that bridges [TaskOrchestrator] (writer) and
 * [NodeStatusViewModel] (reader) without a bound service.
 *
 * Write path:  MediaNodeService observes orchestrator.taskState → calls [update].
 * Read path:   NodeStatusViewModel exposes [state] as a StateFlow for Compose UI.
 *
 * Reset to [TaskState.Idle] when [MediaNodeService] disconnects.
 */
object NodeStateHolder {

    private val _state = MutableStateFlow<TaskState>(TaskState.Idle)

    /** Read-only view for ViewModels / Compose. */
    val state: StateFlow<TaskState> = _state

    /** Called by MediaNodeService on every TaskOrchestrator state transition. */
    fun update(newState: TaskState) {
        _state.value = newState
    }
}

