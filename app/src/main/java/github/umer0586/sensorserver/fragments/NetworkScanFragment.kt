package github.umer0586.sensorserver.fragments

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
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import github.umer0586.sensorserver.R
import github.umer0586.sensorserver.databinding.FragmentNetworkScanBinding
import kotlinx.coroutines.launch
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import github.umer0586.sensorserver.models.WifiScanResult
import github.umer0586.sensorserver.models.BluetoothScanResult
import github.umer0586.sensorserver.models.NetworkScanData
import github.umer0586.sensorserver.service.WebsocketService
import github.umer0586.sensorserver.service.ServiceBindHelper
import github.umer0586.sensorserver.service.WebsocketService.LocalBinder
import android.os.IBinder

class NetworkScanFragment : Fragment() {

    private var _binding: FragmentNetworkScanBinding? = null
    private val binding get() = _binding!!

    private lateinit var wifiManager: WifiManager
    private lateinit var bluetoothManager: BluetoothManager
    private lateinit var locationManager: LocationManager
    private var bluetoothAdapter: BluetoothAdapter? = null

    private var requiredPermissionsGranted = false
    private var locationServicesEnabled = false

    // Results storage
    private val wifiScanResults = mutableListOf<WifiScanResult>()
    private val bluetoothScanResults = mutableListOf<BluetoothScanResult>()
    private val discoveredBluetoothDevices = mutableMapOf<String, BluetoothScanResult>() // Use map to handle duplicates

    // Scanning state & scheduler
    private var isWifiScanning = false
    private var isBtDiscovering = false
    private var scanScheduler: ScheduledExecutorService? = null
    private val scanIntervalSeconds = 15L // Scan every 15 seconds

    private var websocketService: WebsocketService? = null
    private lateinit var serviceBindHelper: ServiceBindHelper

    // --- Permissions Handling ---
    private val requestPermissionsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissions ->
            var allGranted = true
            permissions.entries.forEach {
                if (!it.value) {
                    Log.w(TAG, "Permission denied: ${it.key}")
                    allGranted = false
                }
            }
            requiredPermissionsGranted = allGranted
            updateUiState()
            if (allGranted) {
                Log.i(TAG, "All required permissions granted.")
                startScanningIfReady()
            } else {
                Log.w(TAG, "Not all permissions granted.")
                binding.permissionStatusText.text = getString(R.string.permissions_denied)
                showPermissionRationale()
            }
        }

    private fun getRequiredPermissions(): Array<String> {
        val targetSdk = requireContext().applicationInfo.targetSdkVersion
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            // Android 12+
            mutableListOf(
                Manifest.permission.ACCESS_FINE_LOCATION, // Needed for WiFi scan results and BT Scan
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT // Optional but good practice
            ).apply {
                // Optional: Add BLUETOOTH_ADVERTISE if making device discoverable
            }.toTypedArray()
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10 & 11
             arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION // Needed for WiFi and BT Scan
             )
        }
        else {
            // Below Android 10
            arrayOf(
                Manifest.permission.ACCESS_COARSE_LOCATION, // Needed for WiFi scan
                Manifest.permission.ACCESS_FINE_LOCATION,   // Needed for BT scan
                Manifest.permission.BLUETOOTH_ADMIN, // Needed for BT discovery
                Manifest.permission.BLUETOOTH // Needed for BT discovery
            )
        }
    }


    private fun checkPermissions() {
        val permissionsToRequest = getRequiredPermissions().filter {
            ContextCompat.checkSelfPermission(requireContext(), it) != PackageManager.PERMISSION_GRANTED
        }

        if (permissionsToRequest.isEmpty()) {
            requiredPermissionsGranted = true
            Log.i(TAG, "All required permissions already granted.")
            updateUiState()
        } else {
            requiredPermissionsGranted = false
            Log.i(TAG, "Requesting permissions: ${permissionsToRequest.joinToString()}")
            requestPermissionsLauncher.launch(permissionsToRequest.toTypedArray())
        }
    }

    private fun showPermissionRationale() {
       if (shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION) ||
            (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && shouldShowRequestPermissionRationale(Manifest.permission.BLUETOOTH_SCAN)) ||
            (Build.VERSION.SDK_INT < Build.VERSION_CODES.S && shouldShowRequestPermissionRationale(Manifest.permission.BLUETOOTH_ADMIN)) )
        {
             MaterialAlertDialogBuilder(requireContext())
                .setTitle("Permissions Required")
                .setMessage(R.string.permission_rationale_location_bluetooth)
                .setPositiveButton("Grant") { _, _ ->
                    requestPermissionsLauncher.launch(getRequiredPermissions())
                }
                .setNegativeButton("Deny") { dialog, _ -> dialog.dismiss() }
                .show()
        } else {
            // User has permanently denied, guide them to settings
             binding.requestPermissionButton.visibility = View.VISIBLE
             binding.requestPermissionButton.text = "Open Settings"
             binding.requestPermissionButton.setOnClickListener {
                 val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                 val uri = android.net.Uri.fromParts("package", requireContext().packageName, null)
                 intent.data = uri
                 startActivity(intent)
             }
             Toast.makeText(requireContext(), "Please grant permissions in App Settings", Toast.LENGTH_LONG).show()
        }
    }

    // --- Location Services Check ---
    private fun checkLocationServices() {
        locationServicesEnabled = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // For Android Pie and above
            locationManager.isLocationEnabled
        } else {
            // For older versions
            try {
                @Suppress("DEPRECATION")
                Settings.Secure.getInt(requireContext().contentResolver, Settings.Secure.LOCATION_MODE) != Settings.Secure.LOCATION_MODE_OFF
            } catch (e: Settings.SettingNotFoundException) {
                Log.e(TAG, "Location setting not found", e)
                false // Assume disabled if setting not found
            }
        }
        Log.i(TAG, "Location services enabled: $locationServicesEnabled")
        updateUiState()
    }

     private fun promptEnableLocationServices() {
        MaterialAlertDialogBuilder(requireContext())
            .setTitle("Location Services Disabled")
            .setMessage(R.string.location_enable_prompt)
            .setPositiveButton(R.string.enable) { _, _ ->
                val intent = Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
                startActivity(intent) // User needs to manually enable it
            }
            .setNegativeButton(android.R.string.cancel) { dialog, _ ->
                dialog.dismiss()
                Toast.makeText(requireContext(), "Location services are required for scanning.", Toast.LENGTH_SHORT).show()
            }
            .show()
    }


    // --- Lifecycle & UI ---
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentNetworkScanBinding.inflate(inflater, container, false)
        wifiManager = requireContext().applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        bluetoothManager = requireContext().applicationContext.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        locationManager = requireContext().applicationContext.getSystemService(Context.LOCATION_SERVICE) as LocationManager

        binding.requestPermissionButton.setOnClickListener {
             checkPermissions() // Default action is to re-request
        }

        // Setup Service Binding
        serviceBindHelper = ServiceBindHelper(
            context = requireContext(),
            service = WebsocketService::class.java,
            componentLifecycle = viewLifecycleOwner.lifecycle // Use viewLifecycleOwner
        )

        serviceBindHelper.onServiceConnected(this::onServiceConnected)

        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        // Initialization and logic will go here
        updateUiState() // Initial UI setup
    }

     override fun onResume() {
        super.onResume()
        Log.d(TAG, "onResume")
        checkPermissions() // Check permissions every time fragment resumes
        checkLocationServices() // Check location services status

        if (requiredPermissionsGranted && locationServicesEnabled) {
             registerWifiReceiver()
             registerBluetoothReceiver()
            startScanningScheduler()
        }
    }

    override fun onPause() {
        super.onPause()
        Log.d(TAG, "onPause")
        stopScanningScheduler()
        unregisterWifiReceiver()
        unregisterBluetoothReceiver()
    }


    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }


    @SuppressLint("SetTextI18n")
    private fun updateUiState() {
         lifecycleScope.launch { // Ensure UI updates run on the main thread
             if (_binding == null) return@launch // Check if binding is still valid

             // Permission Status
             binding.permissionStatusText.text = if (requiredPermissionsGranted) {
                  getString(R.string.permissions_granted)
             } else {
                 getString(R.string.permissions_needed)
             }
             binding.requestPermissionButton.visibility = if (!requiredPermissionsGranted) View.VISIBLE else View.GONE
             binding.requestPermissionButton.text = "Request Permissions" // Reset button text


             // Location Status
             binding.locationStatusText.text = if (locationServicesEnabled) {
                  getString(R.string.location_services_enabled)
             } else {
                 getString(R.string.location_services_disabled)
             }
             // Offer to enable location if needed and permissions are granted
             if (!locationServicesEnabled && requiredPermissionsGranted) {
                  binding.requestPermissionButton.visibility = View.VISIBLE
                  binding.requestPermissionButton.text = getString(R.string.enable) + " Location"
                  binding.requestPermissionButton.setOnClickListener { promptEnableLocationServices() }
             } else if (!requiredPermissionsGranted) {
                // If permissions are not granted, the button should request permissions
                 binding.requestPermissionButton.setOnClickListener { checkPermissions() }
             }

             // Scan Status
             val statusText = when {
                 !requiredPermissionsGranted -> "Waiting for permissions"
                 !locationServicesEnabled -> "Waiting for location services"
                 isWifiScanning -> "WiFi Scanning..."
                 isBtDiscovering -> "BT Discovering..."
                 else -> "Idle. Next scan soon..."
             }
             binding.scanStatusText.text = "Scan Status: $statusText"
         }
    }

    // --- WiFi Scanning ---
    private val wifiScanReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission", "SetTextI18n")
        override fun onReceive(context: Context, intent: Intent) {
             if (!requiredPermissionsGranted) return // Double check permissions

            val success = intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false)
            if (success) {
                handleWifiScanSuccess()
            } else {
                handleWifiScanFailure()
            }
            isWifiScanning = false // Mark scan as finished
            binding.scanStatusText.text = "Scan Status: WiFi scan finished. Next in ~$scanIntervalSeconds sec."
        }
    }

    @SuppressLint("MissingPermission")
    private fun handleWifiScanSuccess() {
        Log.d(TAG, "WiFi scan successful.")
         try {
            val results = wifiManager.scanResults
            val timestamp = System.currentTimeMillis()
            val newResults = results.mapNotNull { scanResult ->
                // Basic filtering (optional)
                if (scanResult.SSID.isNullOrEmpty()) return@mapNotNull null
                WifiScanResult(
                    bssid = scanResult.BSSID,
                    ssid = scanResult.SSID,
                    rssi = scanResult.level,
                    frequency = scanResult.frequency,
                    timestamp = timestamp
                )
            }
            wifiScanResults.clear()
            wifiScanResults.addAll(newResults)
            Log.i(TAG, "Found ${wifiScanResults.size} WiFi networks.")
             updateWifiResultsUI()
             sendResultsToServer() // Send combined results
         } catch (e: SecurityException) {
              Log.e(TAG, "SecurityException getting WiFi scan results.", e)
              binding.scanStatusText.text = "Scan Status: Error (Permission missing?)"
              requiredPermissionsGranted = false // Re-check needed
              updateUiState()
         } catch (e: Exception) {
              Log.e(TAG, "Exception processing WiFi scan results.", e)
         }
    }

    private fun handleWifiScanFailure() {
        Log.w(TAG, "WiFi scan failed.")
        // Consider checking wifi state wifiManager.isWifiEnabled
        // Potentially notify user
    }

    @SuppressLint("SetTextI18n")
    private fun startWifiScan() {
         if (!isAdded || _binding == null || !requiredPermissionsGranted || !locationServicesEnabled) return

        if (isWifiScanning) {
            Log.d(TAG, "WiFi scan already in progress.")
            return
        }
        if (!wifiManager.isWifiEnabled) {
            Log.w(TAG, "WiFi is disabled, cannot scan.")
            binding.scanStatusText.text = "Scan Status: WiFi Disabled"
            Toast.makeText(requireContext(), "Please enable WiFi for scanning", Toast.LENGTH_SHORT).show()
            return
        }

        Log.i(TAG, "Starting WiFi scan...")
        val success = wifiManager.startScan() // Deprecated but necessary for targeted scans
        if (success) {
            isWifiScanning = true
            binding.scanStatusText.text = "Scan Status: WiFi scan initiated..."
        } else {
            Log.w(TAG, "Failed to initiate WiFi scan.")
            binding.scanStatusText.text = "Scan Status: WiFi scan failed to start."
            isWifiScanning = false
        }
    }

    private fun registerWifiReceiver() {
        val intentFilter = IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
        requireContext().registerReceiver(wifiScanReceiver, intentFilter)
        Log.d(TAG, "WiFi scan receiver registered.")
    }

    private fun unregisterWifiReceiver() {
        try {
            requireContext().unregisterReceiver(wifiScanReceiver)
            Log.d(TAG, "WiFi scan receiver unregistered.")
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "WiFi scan receiver not registered or already unregistered.")
        }
    }

    private fun updateWifiResultsUI() {
        // TODO: Implement RecyclerView adapter and update it here
        Log.d(TAG, "Updating WiFi UI with ${wifiScanResults.size} results.")
        // binding.wifiResultsRecyclerView.adapter?.notifyDataSetChanged()
    }

    private fun sendResultsToServer() {
        if (websocketService == null) {
            Log.w(TAG, "WebsocketService not connected, cannot send results.")
            return
        }

         if (wifiScanResults.isNotEmpty() || bluetoothScanResults.isNotEmpty() ) {
             val networkData = NetworkScanData(
                 timestamp = System.currentTimeMillis(),
                 wifiResults = wifiScanResults.toList(), // Create immutable copy
                 bluetoothResults = bluetoothScanResults.toList() // Create immutable copy
             )
              Log.d(TAG, "Sending network data to service: ${networkData.wifiResults.size} WiFi, ${networkData.bluetoothResults.size} BT")
              websocketService?.sendNetworkScanData(networkData)
         }
    }

    // --- Bluetooth Scanning ---
    private val bluetoothDiscoveryReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission", "SetTextI18n")
        override fun onReceive(context: Context, intent: Intent) {
            val action: String? = intent.action

            when (action) {
                BluetoothDevice.ACTION_FOUND -> {
                    val device: BluetoothDevice? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                    }

                    val rssi = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        // Note: EXTRA_RSSI is deprecated but often still works. Modern apps might use LE scanning for better RSSI.
                        intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE).toInt()
                    } else {
                         @Suppress("DEPRECATION")
                         intent.getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE).toInt()
                    }

                    device?.let {
                        // Required permission checked before starting discovery
                         try {
                             val deviceName = it.name ?: "Unknown Device"
                             val deviceAddress = it.address // MAC address
                              if (deviceAddress == null) return // Invalid device

                             Log.d(TAG, "Bluetooth device found: $deviceName ($deviceAddress) RSSI: $rssi")
                             val result = BluetoothScanResult(
                                 name = deviceName,
                                 address = deviceAddress,
                                 rssi = rssi,
                                 timestamp = System.currentTimeMillis()
                             )
                             // Add or update in map (keeps latest RSSI/timestamp for a device)
                             discoveredBluetoothDevices[deviceAddress] = result
                              updateBluetoothResultsUI() // Update UI incrementally

                         } catch (se: SecurityException) {
                             Log.e(TAG, "SecurityException accessing Bluetooth device properties", se)
                             // Potentially stop discovery or re-check permissions
                             stopBluetoothDiscovery()
                             requiredPermissionsGranted = false // Force re-check
                             updateUiState()
                         }
                    }
                }
                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> {
                    Log.i(TAG, "Bluetooth discovery finished.")
                    isBtDiscovering = false
                    binding.scanStatusText.text = "Scan Status: BT discovery finished. Next in ~$scanIntervalSeconds sec."
                    // Process final list after discovery finishes
                    bluetoothScanResults.clear()
                    bluetoothScanResults.addAll(discoveredBluetoothDevices.values)
                    Log.i(TAG, "Final Bluetooth results count: ${bluetoothScanResults.size}")
                    updateBluetoothResultsUI()
                    sendResultsToServer() // Send combined results
                }
                 BluetoothAdapter.ACTION_DISCOVERY_STARTED -> {
                    Log.i(TAG, "Bluetooth discovery started.")
                    isBtDiscovering = true
                    binding.scanStatusText.text = "Scan Status: BT discovering..."
                    discoveredBluetoothDevices.clear() // Clear previous results before new discovery
                 }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun startBluetoothDiscovery() {
         if (!isAdded || _binding == null || !requiredPermissionsGranted) return

         bluetoothAdapter = bluetoothManager.adapter
         if (bluetoothAdapter == null) {
             Log.w(TAG, "Device does not support Bluetooth")
             Toast.makeText(requireContext(), "Bluetooth not supported on this device", Toast.LENGTH_SHORT).show()
             return
         }

         if (!bluetoothAdapter!!.isEnabled) {
             Log.w(TAG, "Bluetooth is disabled.")
             binding.scanStatusText.text = "Scan Status: Bluetooth Disabled"
             Toast.makeText(requireContext(), "Please enable Bluetooth for discovery", Toast.LENGTH_SHORT).show()
             // Optional: Prompt user to enable BT
             // val enableBtIntent = Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
             // startActivityForResult(enableBtIntent, REQUEST_ENABLE_BT)
             return
         }

         if (isBtDiscovering) {
            Log.d(TAG, "Bluetooth discovery already in progress.")
            return
        }

        // Cancel any prior discovery that might be stuck
         if (bluetoothAdapter?.isDiscovering == true) {
             bluetoothAdapter?.cancelDiscovery()
             Log.d(TAG, "Cancelled existing Bluetooth discovery.")
         }

         Log.i(TAG, "Starting Bluetooth discovery...")
         // Permissions (BLUETOOTH_SCAN or BLUETOOTH_ADMIN) are checked before calling this
         val success = bluetoothAdapter?.startDiscovery() ?: false
         if (success) {
             // Status updated via ACTION_DISCOVERY_STARTED receiver
         } else {
             Log.w(TAG, "Failed to initiate Bluetooth discovery.")
             binding.scanStatusText.text = "Scan Status: BT discovery failed to start."
             isBtDiscovering = false
         }
    }

    @SuppressLint("MissingPermission")
    private fun stopBluetoothDiscovery() {
        if (bluetoothAdapter?.isDiscovering == true) {
             // Permission (BLUETOOTH_SCAN or BLUETOOTH_ADMIN) needed
             if (!requiredPermissionsGranted) {
                  Log.w(TAG, "Missing permissions to stop Bluetooth discovery.")
                 return
             }
             try {
                 bluetoothAdapter?.cancelDiscovery()
                 Log.i(TAG, "Bluetooth discovery cancelled.")
             } catch (se: SecurityException) {
                  Log.e(TAG, "SecurityException stopping Bluetooth discovery.", se)
             }
        }
        isBtDiscovering = false
    }

     private fun registerBluetoothReceiver() {
        val filter = IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_FOUND)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_STARTED)
        }
        requireContext().registerReceiver(bluetoothDiscoveryReceiver, filter)
        Log.d(TAG, "Bluetooth discovery receiver registered.")
    }

    private fun unregisterBluetoothReceiver() {
        try {
            requireContext().unregisterReceiver(bluetoothDiscoveryReceiver)
            Log.d(TAG, "Bluetooth discovery receiver unregistered.")
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "Bluetooth discovery receiver not registered or already unregistered.")
        }
    }

    private fun updateBluetoothResultsUI() {
        // TODO: Implement RecyclerView adapter and update it here
        Log.d(TAG, "Updating Bluetooth UI with ${discoveredBluetoothDevices.size} discovered devices.")
        // binding.bluetoothResultsRecyclerView.adapter?.notifyDataSetChanged()
    }

    // --- Scanning Scheduler ---
    private fun startScanningScheduler() {
         if (scanScheduler?.isShutdown == false) {
             Log.d(TAG, "Scan scheduler already running.")
             return
         }
         scanScheduler = Executors.newSingleThreadScheduledExecutor()
         scanScheduler?.scheduleAtFixedRate({
             // Run scans on the main thread if they interact with UI or certain managers directly
             // Or handle results posting back to the main thread
              activity?.runOnUiThread {
                  startWifiScan()
                  startBluetoothDiscovery()
              }
         }, 0, scanIntervalSeconds, TimeUnit.SECONDS)
         Log.i(TAG, "Scan scheduler started with interval: $scanIntervalSeconds seconds.")
    }

    private fun stopScanningScheduler() {
        scanScheduler?.shutdown()
        try {
            if (scanScheduler?.awaitTermination(1, TimeUnit.SECONDS) == false) {
                scanScheduler?.shutdownNow()
            }
        } catch (e: InterruptedException) {
            scanScheduler?.shutdownNow()
            Thread.currentThread().interrupt() // Preserve interrupt status
        }
        scanScheduler = null
        Log.i(TAG, "Scan scheduler stopped.")
        // Also explicitly stop any ongoing scans immediately
        stopOngoingScans()
    }

     private fun stopOngoingScans() {
         if (isWifiScanning) {
             // WiFi scans are typically short, main action is receiver handling
             isWifiScanning = false
         }
          if (isBtDiscovering) {
              stopBluetoothDiscovery()
          }
     }

    private fun startScanningIfReady() {
        if (requiredPermissionsGranted && locationServicesEnabled) {
            Log.i(TAG, "Permissions and location OK. Starting scan scheduler.")
            startScanningScheduler()
        } else {
             Log.w(TAG, "Cannot start scanning. Permissions granted: $requiredPermissionsGranted, Location enabled: $locationServicesEnabled")
             binding.scanStatusText.text = "Scan Status: Blocked"
             updateUiState() // Re-update UI to show prompts if necessary
        }
    }

    private fun stopScanning() {
         if (!isAdded || _binding == null) return
         Log.i(TAG, "Stopping scans and scheduler.")
         stopScanningScheduler()
         // Receivers are stopped in onPause
    }

    private fun onServiceConnected(binder: IBinder) {
        val localBinder = binder as LocalBinder
        websocketService = localBinder.service
        Log.i(TAG, "WebsocketService connected.")
        // Now we can send data to the service when available
    }

    companion object {
        private val TAG = NetworkScanFragment::class.java.simpleName
    }
} 