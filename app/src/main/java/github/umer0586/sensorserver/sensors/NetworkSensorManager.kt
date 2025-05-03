package github.umer0586.sensorserver.sensors

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import com.google.gson.Gson
import github.umer0586.sensorserver.models.BluetoothScanResult
import github.umer0586.sensorserver.models.NetworkScanData
import github.umer0586.sensorserver.models.WifiScanResult
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

// Define custom sensor types
class NetworkSensorManager(private val context: Context) {

    companion object {
        private val TAG = NetworkSensorManager::class.java.simpleName
        
        // Define custom sensor types that clients can request
        const val TYPE_WIFI_SCAN = "android.sensor.wifi_scan"
        const val TYPE_BLUETOOTH_SCAN = "android.sensor.bluetooth_scan"
        const val TYPE_NETWORK_SCAN = "android.sensor.network_scan" // Combined WiFi + BT
        
        // Sampling rate - be conservative to avoid hitting rate limits
        private const val SCAN_INTERVAL_SECONDS = 15L
    }

    private val gson = Gson()
    private var wifiManager: WifiManager? = null
    private var bluetoothManager: BluetoothManager? = null
    private var bluetoothAdapter: BluetoothAdapter? = null
    private var locationManager: LocationManager? = null

    // Listeners
    private val networkSensorEventListeners = mutableListOf<NetworkSensorEventListener>()
    
    // Scanning state
    private var isWifiScanning = false
    private var isBtDiscovering = false
    private var scanScheduler: ScheduledExecutorService? = null
    
    // Results storage
    private val wifiScanResults = mutableListOf<WifiScanResult>()
    private val bluetoothScanResults = mutableListOf<BluetoothScanResult>()
    private val discoveredBluetoothDevices = mutableMapOf<String, BluetoothScanResult>()
    
    init {
        wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        bluetoothManager = context.applicationContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        bluetoothAdapter = bluetoothManager?.adapter
        locationManager = context.applicationContext.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
    }
    
    // Interface for network sensor event listeners
    interface NetworkSensorEventListener {
        fun onWifiScanResult(results: List<WifiScanResult>)
        fun onBluetoothScanResult(results: List<BluetoothScanResult>)
        fun onNetworkScanResult(data: NetworkScanData)
    }
    
    // Register a listener
    fun registerListener(listener: NetworkSensorEventListener) {
        if (!networkSensorEventListeners.contains(listener)) {
            networkSensorEventListeners.add(listener)
            Log.i(TAG, "Network sensor listener registered, now have ${networkSensorEventListeners.size} listeners")
            
            // Start scanning if this is the first listener
            if (networkSensorEventListeners.size == 1) {
                startScanningScheduler()
            }
        }
    }
    
    // Unregister a listener
    fun unregisterListener(listener: NetworkSensorEventListener) {
        networkSensorEventListeners.remove(listener)
        Log.i(TAG, "Network sensor listener unregistered, now have ${networkSensorEventListeners.size} listeners")
        
        // Stop scanning if no listeners remain
        if (networkSensorEventListeners.isEmpty()) {
            stopScanningScheduler()
        }
    }
    
    // Are required permissions granted?
    fun hasRequiredPermissions(): Boolean {
        val permissions = getRequiredPermissions()
        return permissions.all { 
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED 
        }
    }
    
    // Is location service enabled?
    fun isLocationServiceEnabled(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            locationManager?.isLocationEnabled ?: false
        } else {
            try {
                @Suppress("DEPRECATION")
                val locationMode = android.provider.Settings.Secure.getInt(
                    context.contentResolver,
                    android.provider.Settings.Secure.LOCATION_MODE
                )
                locationMode != android.provider.Settings.Secure.LOCATION_MODE_OFF
            } catch (e: Exception) {
                Log.e(TAG, "Error checking location mode", e)
                false
            }
        }
    }
    
    // Get required permissions
    private fun getRequiredPermissions(): Array<String> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            // Android 12+
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10 & 11
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION
            )
        } else {
            // Below Android 10
            arrayOf(
                Manifest.permission.ACCESS_COARSE_LOCATION,
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.BLUETOOTH_ADMIN,
                Manifest.permission.BLUETOOTH
            )
        }
    }
    
    // Start scanning scheduler
    private fun startScanningScheduler() {
        if (scanScheduler?.isShutdown == false) {
            Log.d(TAG, "Scan scheduler already running")
            return
        }
        
        Log.i(TAG, "=== STARTING NETWORK SCAN SCHEDULER (interval: ${SCAN_INTERVAL_SECONDS}s) ===")
        scanScheduler = Executors.newSingleThreadScheduledExecutor()
        scanScheduler?.scheduleAtFixedRate({
            if (hasRequiredPermissions() && isLocationServiceEnabled()) {
                Log.d(TAG, "Scheduled scan trigger - starting scans")
                startWifiScan()
                startBluetoothDiscovery()
            } else {
                Log.w(TAG, "Scheduled scan trigger - permissions or location service not available")
            }
        }, 0, SCAN_INTERVAL_SECONDS, TimeUnit.SECONDS)
        
        registerReceivers()
    }
    
    // Stop scanning scheduler
    fun stopScanningScheduler() {
        Log.i(TAG, "=== STOPPING NETWORK SCAN SCHEDULER ===")
        scanScheduler?.shutdown()
        try {
            scanScheduler?.awaitTermination(1, TimeUnit.SECONDS)
            if (scanScheduler?.isTerminated == false) {
                scanScheduler?.shutdownNow()
                Log.d(TAG, "Force terminated scan scheduler")
            }
        } catch (e: InterruptedException) {
            scanScheduler?.shutdownNow()
            Thread.currentThread().interrupt()
            Log.w(TAG, "Interruption while terminating scan scheduler", e)
        }
        
        scanScheduler = null
        unregisterReceivers()
        stopOngoingScans()
        Log.i(TAG, "Network scan scheduler stopped")
    }
    
    private fun stopOngoingScans() {
        isWifiScanning = false
        if (isBtDiscovering) {
            stopBluetoothDiscovery()
        }
    }
    
    // Register broadcast receivers
    private fun registerReceivers() {
        try {
            val wifiIntentFilter = IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
            context.applicationContext.registerReceiver(wifiScanReceiver, wifiIntentFilter)
            
            val btIntentFilter = IntentFilter().apply {
                addAction(BluetoothDevice.ACTION_FOUND)
                addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
                addAction(BluetoothAdapter.ACTION_DISCOVERY_STARTED)
            }
            context.applicationContext.registerReceiver(bluetoothDiscoveryReceiver, btIntentFilter)
        } catch (e: Exception) {
            Log.e(TAG, "Error registering receivers", e)
        }
    }
    
    // Unregister broadcast receivers
    private fun unregisterReceivers() {
        try {
            context.applicationContext.unregisterReceiver(wifiScanReceiver)
        } catch (e: IllegalArgumentException) {
            // Receiver not registered, ignore
        }
        
        try {
            context.applicationContext.unregisterReceiver(bluetoothDiscoveryReceiver)
        } catch (e: IllegalArgumentException) {
            // Receiver not registered, ignore
        }
    }
    
    // WiFi Scan Receiver
    private val wifiScanReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission")
        override fun onReceive(context: Context, intent: Intent) {
            if (!hasRequiredPermissions()) return
            
            val success = intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false)
            if (success) {
                try {
                    val results = wifiManager?.scanResults ?: emptyList()
                    val timestamp = System.currentTimeMillis()
                    
                    wifiScanResults.clear()
                    results.forEach { scanResult ->
                        if (!scanResult.SSID.isNullOrEmpty()) {
                            wifiScanResults.add(
                                WifiScanResult(
                                    bssid = scanResult.BSSID,
                                    ssid = scanResult.SSID,
                                    rssi = scanResult.level,
                                    frequency = scanResult.frequency,
                                    timestamp = timestamp
                                )
                            )
                        }
                    }
                    
                    Log.i(TAG, "=== WIFI SCAN COMPLETED: ${wifiScanResults.size} networks found ===")
                    
                    // Notify listeners of WiFi results
                    networkSensorEventListeners.forEach { listener ->
                        listener.onWifiScanResult(wifiScanResults.toList())
                    }
                    
                    // Also notify of combined network results
                    val networkData = NetworkScanData(
                        timestamp = timestamp,
                        wifiResults = wifiScanResults.toList(),
                        bluetoothResults = bluetoothScanResults.toList()
                    )
                    networkSensorEventListeners.forEach { listener ->
                        listener.onNetworkScanResult(networkData)
                    }
                    
                } catch (e: SecurityException) {
                    Log.e(TAG, "Security exception accessing WiFi scan results", e)
                }
            } else {
                Log.w(TAG, "WiFi scan failed or was throttled by system")
            }
            
            isWifiScanning = false
        }
    }
    
    // Bluetooth Discovery Receiver
    private val bluetoothDiscoveryReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission")
        override fun onReceive(context: Context, intent: Intent) {
            val action = intent.action
            
            when (action) {
                BluetoothDevice.ACTION_FOUND -> {
                    if (!hasRequiredPermissions()) return
                    
                    val device = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                    }
                    
                    val rssi = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE).toInt()
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE).toInt()
                    }
                    
                    device?.let {
                        try {
                            val deviceName = it.name ?: "Unknown Device"
                            val deviceAddress = it.address
                            
                            Log.d(TAG, "BT device found: $deviceName ($deviceAddress) RSSI: $rssi")
                            
                            val result = BluetoothScanResult(
                                name = deviceName,
                                address = deviceAddress,
                                rssi = rssi,
                                timestamp = System.currentTimeMillis()
                            )
                            
                            discoveredBluetoothDevices[deviceAddress] = result
                        } catch (e: SecurityException) {
                            Log.e(TAG, "Security exception accessing Bluetooth device", e)
                        }
                    }
                }
                
                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> {
                    isBtDiscovering = false
                    
                    // Process final list
                    bluetoothScanResults.clear()
                    bluetoothScanResults.addAll(discoveredBluetoothDevices.values)
                    
                    Log.i(TAG, "=== BLUETOOTH DISCOVERY COMPLETED: ${bluetoothScanResults.size} devices found ===")
                    
                    // Notify listeners of Bluetooth results
                    networkSensorEventListeners.forEach { listener ->
                        listener.onBluetoothScanResult(bluetoothScanResults.toList())
                    }
                    
                    // Also notify of combined network results
                    val networkData = NetworkScanData(
                        timestamp = System.currentTimeMillis(),
                        wifiResults = wifiScanResults.toList(),
                        bluetoothResults = bluetoothScanResults.toList()
                    )
                    networkSensorEventListeners.forEach { listener ->
                        listener.onNetworkScanResult(networkData)
                    }
                }
                
                BluetoothAdapter.ACTION_DISCOVERY_STARTED -> {
                    isBtDiscovering = true
                    discoveredBluetoothDevices.clear()
                    Log.i(TAG, "Bluetooth discovery started")
                }
            }
        }
    }
    
    // Start WiFi scan
    @SuppressLint("MissingPermission")
    private fun startWifiScan() {
        if (!hasRequiredPermissions() || !isLocationServiceEnabled()) {
            Log.w(TAG, "Cannot start WiFi scan: permissions or location service not available")
            return
        }
        
        if (isWifiScanning) {
            Log.d(TAG, "WiFi scan already in progress")
            return
        }
        
        wifiManager?.let { wifi ->
            if (wifi.isWifiEnabled) {
                Log.i(TAG, "=== STARTING WIFI SCAN ===")
                val success = wifi.startScan()
                isWifiScanning = success
                if (!success) {
                    Log.w(TAG, "Failed to start WiFi scan - possibly throttled by system")
                } else {
                    Log.i(TAG, "WiFi scan started successfully - waiting for results")
                }
            } else {
                Log.w(TAG, "WiFi is disabled, cannot scan - please enable WiFi")
            }
        } ?: Log.e(TAG, "WiFi manager is null, cannot start WiFi scan")
    }
    
    // Start Bluetooth discovery
    @SuppressLint("MissingPermission")
    private fun startBluetoothDiscovery() {
        if (!hasRequiredPermissions() || !isLocationServiceEnabled()) {
            Log.w(TAG, "Cannot start Bluetooth discovery: permissions or location service not available")
            return
        }
        
        if (isBtDiscovering) {
            Log.d(TAG, "Bluetooth discovery already in progress")
            return
        }
        
        bluetoothAdapter?.let { bt ->
            if (bt.isEnabled) {
                // Cancel any ongoing discovery first
                if (bt.isDiscovering) {
                    bt.cancelDiscovery()
                    Log.d(TAG, "Cancelled existing Bluetooth discovery")
                }
                
                Log.i(TAG, "=== STARTING BLUETOOTH DISCOVERY ===")
                val success = bt.startDiscovery()
                if (!success) {
                    Log.w(TAG, "Failed to start Bluetooth discovery")
                } else {
                    Log.i(TAG, "Bluetooth discovery started successfully - waiting for results")
                }
            } else {
                Log.w(TAG, "Bluetooth is disabled, cannot discover - please enable Bluetooth")
            }
        } ?: Log.e(TAG, "Bluetooth adapter is null, cannot start discovery")
    }
    
    // Stop Bluetooth discovery
    @SuppressLint("MissingPermission")
    private fun stopBluetoothDiscovery() {
        try {
            if (bluetoothAdapter?.isDiscovering == true) {
                bluetoothAdapter?.cancelDiscovery()
            }
        } catch (e: SecurityException) {
            Log.e(TAG, "Security exception stopping BT discovery", e)
        }
        
        isBtDiscovering = false
    }
    
    // Check if sensor type is one of our custom network sensors
    fun isNetworkSensor(sensorType: String): Boolean {
        return sensorType == TYPE_WIFI_SCAN || 
               sensorType == TYPE_BLUETOOTH_SCAN || 
               sensorType == TYPE_NETWORK_SCAN
    }
    
    // Directly trigger an immediate scan without waiting for the scheduler
    fun triggerImmediateScan() {
        Log.i(TAG, "=== TRIGGERING IMMEDIATE NETWORK SCAN ===")
        if (hasRequiredPermissions() && isLocationServiceEnabled()) {
            // Run on a background thread to avoid blocking
            Thread {
                startWifiScan()
                startBluetoothDiscovery()
            }.start()
        } else {
            Log.w(TAG, "Cannot trigger immediate scan: permissions or location service not available")
        }
    }
} 