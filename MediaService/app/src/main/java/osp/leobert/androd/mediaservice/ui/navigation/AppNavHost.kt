package osp.leobert.androd.mediaservice.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import osp.leobert.androd.mediaservice.domain.state.TaskState
import osp.leobert.androd.mediaservice.service.NodeStateHolder
import osp.leobert.androd.mediaservice.ui.connection.ConnectionScreen
import osp.leobert.androd.mediaservice.ui.connection.ConnectionViewModel
import osp.leobert.androd.mediaservice.ui.status.NodeStatusScreen

private const val ROUTE_CONNECTION = "connection"
private const val ROUTE_STATUS = "status"

/**
 * Top-level navigation graph.
 *
 * ConnectionScreen → (on connect) → NodeStatusScreen → (on disconnect) → ConnectionScreen
 */
@Composable
fun AppNavHost() {
    val navController = rememberNavController()
    val connectionVm: ConnectionViewModel = viewModel()
    val nodeState by NodeStateHolder.state.collectAsState()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val preferredRoute = if (nodeState is TaskState.Idle) ROUTE_CONNECTION else ROUTE_STATUS

    LaunchedEffect(preferredRoute, currentRoute) {
        if (currentRoute != null && currentRoute != preferredRoute) {
            navController.navigate(preferredRoute) {
                popUpTo(navController.graph.startDestinationId) { inclusive = true }
                launchSingleTop = true
            }
        }
    }

    NavHost(navController = navController, startDestination = preferredRoute) {

        composable(ROUTE_CONNECTION) {
            ConnectionScreen(
                onConnected = {}
            )
        }

        composable(ROUTE_STATUS) {
            NodeStatusScreen(
                onDisconnect = {
                    connectionVm.disconnect()
                }
            )
        }
    }
}

