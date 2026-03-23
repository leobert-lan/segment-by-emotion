package osp.leobert.androd.mediaservice.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import osp.leobert.androd.mediaservice.ui.connection.ConnectionScreen
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

    NavHost(navController = navController, startDestination = ROUTE_CONNECTION) {

        composable(ROUTE_CONNECTION) {
            ConnectionScreen(
                onConnected = {
                    navController.navigate(ROUTE_STATUS) {
                        popUpTo(ROUTE_CONNECTION) { inclusive = true }
                    }
                }
            )
        }

        composable(ROUTE_STATUS) {
            NodeStatusScreen(
                onDisconnect = {
                    navController.navigate(ROUTE_CONNECTION) {
                        popUpTo(ROUTE_STATUS) { inclusive = true }
                    }
                }
            )
        }
    }
}

