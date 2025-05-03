package github.umer0586.sensorserver.activities

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import android.view.MenuItem
import android.view.View
import android.widget.RelativeLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.AppBarConfiguration
import androidx.navigation.ui.setupActionBarWithNavController
import androidx.navigation.ui.setupWithNavController
import com.google.android.material.navigation.NavigationBarView
import com.permissionx.guolindev.PermissionX
import github.umer0586.sensorserver.R
import github.umer0586.sensorserver.databinding.ActivityMainBinding
import github.umer0586.sensorserver.service.HttpServerStateListener
import github.umer0586.sensorserver.service.HttpService
import github.umer0586.sensorserver.service.ServiceBindHelper
import github.umer0586.sensorserver.service.WebsocketService
import github.umer0586.sensorserver.webserver.HttpServerInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.lang.Exception

class MainActivity : AppCompatActivity(), NavigationBarView.OnItemSelectedListener
{
    private lateinit var actionBarDrawerToggle: ActionBarDrawerToggle

    private lateinit var websocketServiceBindHelper: ServiceBindHelper
    private var websocketService: WebsocketService? = null

    private lateinit var httpServiceBindHelper: ServiceBindHelper
    private var httpService: HttpService? = null

    private lateinit var binding : ActivityMainBinding

    companion object
    {
        private val TAG: String = MainActivity::class.java.simpleName
    }

    override fun onCreate(savedInstanceState: Bundle?)
    {
        super.onCreate(savedInstanceState)

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Set a Toolbar to replace the ActionBar.
        setSupportActionBar(binding.toolbar.root)

        // Passing each menu ID as a set of Ids because each
        // menu should be considered as top level destinations.
        val appBarConfiguration = AppBarConfiguration(
            setOf(
                R.id.navigation_server, R.id.navigation_sensors, R.id.navigation_connections, R.id.navigation_network_scan
            )
        )

        val navHostFragment = supportFragmentManager.findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        val navController = navHostFragment.navController

        setupActionBarWithNavController(navController, appBarConfiguration)
        binding.bottomNavView.setupWithNavController(navController)

        binding.bottomNavView.selectedItemId = R.id.navigation_server
        binding.bottomNavView.setOnItemSelectedListener(this)

        websocketServiceBindHelper = ServiceBindHelper(
            context = applicationContext,
            service = WebsocketService::class.java,
            componentLifecycle = lifecycle
        )

        websocketServiceBindHelper.onServiceConnected(this::onWebsocketServiceConnected)

        httpServiceBindHelper = ServiceBindHelper(
                context = applicationContext,
                service = HttpService::class.java,
                componentLifecycle = lifecycle
        )

        httpServiceBindHelper.onServiceConnected(this::onHttpServiceConnected)

        actionBarDrawerToggle = ActionBarDrawerToggle(this, binding.drawerLayout, R.string.nav_open, R.string.nav_close)
        binding.drawerLayout.addDrawerListener(actionBarDrawerToggle)
        actionBarDrawerToggle.syncState()

        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        // Force initialize the HTTP server option in navigation drawer
        val httpServerAddressParentView = (binding.drawerNavigationView.menu
                .findItem(R.id.nav_drawer_http_server_address).actionView as RelativeLayout)
        httpServerAddressParentView.visibility = View.VISIBLE
        
        val httpServerSwitch = (binding.drawerNavigationView.menu.findItem(R.id.nav_drawer_http_server_switch).actionView as RelativeLayout).getChildAt(0) as SwitchCompat
        httpServerSwitch.visibility = View.VISIBLE

        binding.drawerNavigationView.setNavigationItemSelectedListener { menuItem ->
            if (menuItem.itemId == R.id.nav_drawer_about)
                startActivity(Intent(this, AboutActivity::class.java))

            if (menuItem.itemId == R.id.nav_drawer_settings)
                startActivity(Intent(this,SettingsActivity::class.java))

            if (menuItem.itemId == R.id.nav_drawer_device_axis)
                startActivity(Intent(this, DeviceAxisActivity::class.java))

            if (menuItem.itemId == R.id.nav_drawer_touch_sensors)
                startActivity(Intent(this, TouchScreenActivity::class.java))

            false
        }
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean
    {
        Log.d(TAG, "onOptionsItemSelected: $item")
        return if (actionBarDrawerToggle.onOptionsItemSelected(item))
        {
            true
        }
        else super.onOptionsItemSelected(item)
    }

    private fun onWebsocketServiceConnected(binder: IBinder)
    {
        val localBinder = binder as WebsocketService.LocalBinder
        websocketService = localBinder.service

        websocketService?.let{ setConnectionCountBadge(it.getConnectionCount()) }

        websocketService?.onConnectionsCountChange { count ->
            lifecycleScope.launch(Dispatchers.Main) {
                setConnectionCountBadge(count)
            }
        }
    }

    private fun onHttpServiceConnected(binder: IBinder) {
        val httpServerAddressParentView = (binding.drawerNavigationView.menu
                .findItem(R.id.nav_drawer_http_server_address).actionView as RelativeLayout)
        val httpServerAddress = httpServerAddressParentView.findViewById<TextView>(R.id.server_address)

        val httpServerSwitch = (binding.drawerNavigationView.menu.findItem(R.id.nav_drawer_http_server_switch).actionView as RelativeLayout).getChildAt(0) as SwitchCompat

        // Force visibility of HTTP server elements even if server isn't started yet
        httpServerAddressParentView.visibility = View.VISIBLE
        httpServerSwitch.visibility = View.VISIBLE

        val showServerAddress : ((HttpServerInfo) -> Unit) = {info ->
            httpServerAddressParentView.visibility = View.VISIBLE
            httpServerAddress.apply {
                visibility = View.VISIBLE
                text = info.baseUrl
            }
        }

        val hideServerAddress = {
            httpServerAddressParentView.visibility = View.GONE
            httpServerAddress.visibility = View.INVISIBLE
        }

        hideServerAddress()

        val localBinder = binder as HttpService.LocalBinder
        httpService = localBinder.service

        httpService?.setServerStateListener(object : HttpServerStateListener{
            override fun onStart(httpServerInfo: HttpServerInfo) {
                lifecycleScope.launch(Dispatchers.Main){
                    showServerAddress(httpServerInfo)
                    Toast.makeText(this@MainActivity,"web server started",Toast.LENGTH_SHORT).show()
                    httpServerSwitch.isChecked = true
                }
            }

            override fun onStop() {
                lifecycleScope.launch(Dispatchers.Main){
                    hideServerAddress()
                    Toast.makeText(this@MainActivity,"web server stopped",Toast.LENGTH_SHORT).show()
                    httpServerSwitch.isChecked = false
                }
            }

            override fun onError(exception: Exception) {
                lifecycleScope.launch(Dispatchers.Main){
                    Toast.makeText(this@MainActivity,exception.message,Toast.LENGTH_SHORT).show()
                    httpServerSwitch.isChecked = false
                    Log.e(TAG,exception.message.toString())
                }
            }

            override fun onRunning(httpServerInfo: HttpServerInfo) {
                lifecycleScope.launch(Dispatchers.Main){
                    showServerAddress(httpServerInfo)
                    httpServerSwitch.isChecked = true
                }
            }
        })

        // Force service check and auto-start if not running
        httpService?.checkState()
        if (httpService?.isServerRunning != true) {
            Log.i(TAG, "HTTP server not running, auto-starting")
            val intent = Intent(applicationContext, HttpService::class.java)
            ContextCompat.startForegroundService(applicationContext, intent)
        }

        httpServerSwitch.setOnCheckedChangeListener { _, isChecked ->
            val isServerRunning = httpService?.isServerRunning ?: false
            if(isChecked && !isServerRunning){
                // Whether user grant this permission or not we will start service anyway
                // If permission is not granted foreground notification will not be shown
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    PermissionX.init(this)
                            .permissions(android.Manifest.permission.POST_NOTIFICATIONS)
                            .request{_,_,_ -> }
                }

                val intent = Intent(applicationContext, HttpService::class.java)
                ContextCompat.startForegroundService(applicationContext, intent)
            }
            else if (!isChecked && isServerRunning) {
                val intent = Intent(HttpService.ACTION_STOP_SERVER).apply {
                    setPackage(applicationContext.packageName)
                }
                this.sendBroadcast(intent)
            }
        }
    }

    override fun onPause()
    {
        super.onPause()
        Log.d(TAG, "onPause()")

        // To prevent memory leak
        websocketService?.onConnectionsCountChange(callBack = null)
        httpService?.setServerStateListener(null)
    }

    private fun setConnectionCountBadge(totalConnections: Int)
    {
        if (totalConnections > 0)
            binding.bottomNavView.getOrCreateBadge(R.id.navigation_connections).number = totalConnections
        else
            binding.bottomNavView.removeBadge(R.id.navigation_connections)
    }

    override fun onNavigationItemSelected(item: MenuItem): Boolean {
        val navHostFragment = supportFragmentManager.findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        val navController = navHostFragment.navController
        
        // Clear the back stack first to avoid navigation issues
        navController.popBackStack(navController.graph.startDestinationId, false)
        
        when (item.itemId) {
            R.id.navigation_sensors -> {
                navController.navigate(R.id.navigation_sensors)
                supportActionBar?.title = "Available Sensors"
                return true
            }
            R.id.navigation_connections -> {
                navController.navigate(R.id.navigation_connections)
                supportActionBar?.title = "Connections" 
                return true
            }
            R.id.navigation_server -> {
                navController.navigate(R.id.navigation_server)
                supportActionBar?.title = "Sensor Server"
                return true
            }
            R.id.navigation_network_scan -> {
                // Adding log to debug
                Log.d(TAG, "Navigating to NetworkScanFragment")
                navController.navigate(R.id.navigation_network_scan)
                supportActionBar?.title = "Network Scan"
                return true
            }
        }
        return false
    }
}