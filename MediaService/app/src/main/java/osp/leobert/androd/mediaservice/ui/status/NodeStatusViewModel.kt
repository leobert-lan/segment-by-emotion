package osp.leobert.androd.mediaservice.ui.status

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import osp.leobert.androd.mediaservice.domain.state.TaskState

/**
 * Exposes [taskState] for the NodeStatusScreen.
 *
 * TODO: In M2+, bind to a SharedStateHolder singleton that the TaskOrchestrator
 * also writes to, so the ViewModel can observe live state across process boundaries.
 * For now, this is a placeholder that will be wired in bootstrap/DI phase.
 */
class NodeStatusViewModel(application: Application) : AndroidViewModel(application) {

    // TODO: replace with a singleton StateFlow published by TaskOrchestrator
    // via a bound service or Application-level holder.
    val taskState: StateFlow<TaskState> = kotlinx.coroutines.flow.MutableStateFlow(TaskState.Idle)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), TaskState.Idle)
}

