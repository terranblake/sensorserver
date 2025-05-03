# TASK: Network Location Inference

**Goal:** Implement WiFi and Bluetooth scanning functionality to enable network-based location inference.

**Status:** Completed

## Overview
Implement functionality within the Android sensor server application to scan for visible WiFi networks and discoverable Bluetooth devices. This data, including BSSID/SSID/RSSI for WiFi and MAC Address/Name/RSSI for Bluetooth, is collected periodically and exposed to the server pipeline alongside existing sensor data to enable network-based location inference.

## Requirements
- [x] The application can initiate scans for visible WiFi networks.
- [x] The application can initiate discovery for nearby Bluetooth devices.
- [x] For each scanned WiFi network, capture the BSSID, SSID, and RSSI.
- [x] For each discovered Bluetooth device, capture the MAC address, name (if available), and RSSI.
- [x] Handle necessary Android runtime permissions (ACCESS_FINE_LOCATION, BLUETOOTH_SCAN etc.) required for scanning.
- [x] Ensure Location Services is enabled on the device, as required for WiFi scan results on modern Android versions.
- [x] Expose the collected WiFi and Bluetooth data to the application's server logging/API mechanism.
- [x] Manage the scanning process respecting the Android lifecycle (start scans when active, stop when paused/backgrounded to save battery).

## Implementation Details
1. **NetworkSensorManager**
   - Manages WiFi scanning using WifiManager and Bluetooth scanning using BluetoothAdapter
   - Implements broadcast receivers to handle scan results
   - Properly formats scan data into structured JSON

2. **SensorWebSocketServer Integration**
   - Added handling of network sensor types (wifi_scan, bluetooth_scan, network_scan)
   - Properly starts and stops network scanning when clients connect/disconnect
   - Formats network scan results with correct type information for multiple clients

3. **Permission Handling**
   - Added necessary permissions to AndroidManifest.xml
   - Implements runtime permission requests for Location and Bluetooth

## Testing
- The application successfully requests necessary WiFi and Bluetooth permissions.
- The application checks for and indicates the status of Location Services.
- The application can initiate WiFi scans and receive scan results containing BSSID, SSID, and RSSI for multiple networks.
- The application can initiate Bluetooth discovery and receive results containing MAC address, name (if available), and RSSI for discoverable devices.
- The collected WiFi and Bluetooth data is correctly formatted and sent to the server pipeline.
- The server successfully receives and logs/processes the WiFi and Bluetooth network data.
- Starting and stopping the relevant Fragment/Activity correctly manages the scanning process (starting on active, stopping on pause).

## Design
The implementation will utilize the Android WifiManager and BluetoothAdapter system services.

WifiManager will be used to start WiFi scans, and a BroadcastReceiver will listen for SCAN_RESULTS_AVAILABLE_ACTION to process the results.

BluetoothAdapter will be used to start device discovery, and a BroadcastReceiver will listen for ACTION_FOUND and ACTION_DISCOVERY_FINISHED.

Runtime permissions (ACCESS_FINE_LOCATION, BLUETOOTH_SCAN, etc.) will be requested using the Activity Result API.

A check for Location Services enablement will be performed before initiating scans.

Data classes (WifiScanResult, BluetoothScanResult) will be created to structure the collected network information.

The collected network data will be formatted (e.g., JSON) and sent to the existing server communication module for logging/exposure.

Scanning will be managed within a Fragment or Activity's lifecycle methods (onResume, onPause) or potentially a dedicated service for background operation (though initial implementation can focus on foreground).

⚠️ DESIGN REVIEW CHECKPOINT: Before proceeding to implementation, please confirm this design approach.

## Implementation Plan
Add Permissions:

Add required permissions (ACCESS_WIFI_STATE, CHANGE_WIFI_STATE, BLUETOOTH, BLUETOOTH_ADMIN, ACCESS_FINE_LOCATION, BLUETOOTH_SCAN for S+) to AndroidManifest.xml.

Implement Permission Handling:

Use ActivityResultContracts.RequestMultiplePermissions to request necessary runtime permissions in the relevant Fragment or Activity.

Add logic to check if permissions are already granted.

Implement Location Services Check:

Add a function to check if Location Services is enabled using LocationManager.

Prompt the user or display a message if Location Services is disabled.

Define Data Structures:

Create Kotlin data classes WifiScanResult (BSSID, SSID, RSSI, frequency) and BluetoothScanResult (name, address, RSSI).

Implement WiFi Scanning:

Get an instance of WifiManager.

Create a BroadcastReceiver to handle WifiManager.SCAN_RESULTS_AVAILABLE_ACTION.

Inside the receiver, get scan results using wifiManager.getScanResults() and map them to WifiScanResult objects.

Implement a function to start a WiFi scan using wifiManager.startScan().

Implement Bluetooth Scanning:

Get instances of BluetoothManager and BluetoothAdapter.

Check if Bluetooth is supported and enabled.

Create a BroadcastReceiver to handle BluetoothDevice.ACTION_FOUND and BluetoothAdapter.ACTION_DISCOVERY_FINISHED.

Inside the ACTION_FOUND receiver, extract BluetoothDevice and EXTRA_RSSI, map to BluetoothScanResult objects, and add to a list (handle duplicates).

Implement a function to start Bluetooth discovery using bluetoothAdapter.startDiscovery().

Implement a function to cancel discovery using bluetoothAdapter.cancelDiscovery().

Integrate with Lifecycle:

Register the WiFi and Bluetooth BroadcastReceivers in onResume or after permissions/location checks pass.

Start scans in onResume or after permissions/location checks pass.

Unregister receivers and cancel Bluetooth discovery in onPause or onDestroyView.

Expose Data:

Modify the logic within the BroadcastReceivers (or a separate processing step) to take the lists of WifiScanResult and BluetoothScanResult.

Format this data (e.g., into a JSON object containing lists of WiFi and Bluetooth results).

Call the existing server communication method to send this formatted data, potentially adding a timestamp.

Update the server-side data ingestion to handle the new android.networks.wifi and android.networks.bluetooth data types.

⚠️ IMPLEMENTATION REVIEW CHECKPOINT: After outlining implementation details but before writing code.

## Technical Constraints
Requires Android device with WiFi and Bluetooth hardware.

Requires user to grant Location and Bluetooth runtime permissions.

Requires user to have Location Services enabled on the device for WiFi scan results.

Bluetooth discovery (startDiscovery) is power-intensive and has a limited duration (typically 12 seconds).

WiFi scanning can be subject to platform-level throttling, limiting scan frequency.

Background scanning requires careful implementation (e.g., Foreground Service, WorkManager) to avoid system limitations and battery drain.

## Testing Strategy
Unit tests:

Test mapping of Android system scan results (ScanResult, BluetoothDevice) to custom data classes (WifiScanResult, BluetoothScanResult).

Test data formatting logic before sending to the server.

Integration tests:

Verify that requesting permissions triggers the system dialog.

Verify that enabling/disabling Location Services affects WiFi scan results as expected.

Verify that starting/stopping scans correctly registers/unregisters receivers and manages Bluetooth discovery state.

Manual verification:

Run the app on a device in an environment with multiple WiFi networks and Bluetooth devices.

Observe logcat for successful scan initiation and result reception.

Check the server logs/API endpoint to confirm that WiFi and Bluetooth data is being received with correct BSSID/MAC, SSID/Name, and RSSI values.

Test starting/stopping the app and moving it to the background to ensure scanning behavior is as expected and battery drain is reasonable.

## Acceptance Criteria
[x] The application successfully requests necessary WiFi and Bluetooth permissions.

[x] The application checks for and indicates the status of Location Services.

[x] The application can initiate WiFi scans and receive scan results containing BSSID, SSID, and RSSI for multiple networks.

[x] The application can initiate Bluetooth discovery and receive results containing MAC address, name (if available), and RSSI for discoverable devices.

[x] The collected WiFi and Bluetooth data is correctly formatted and sent to the server pipeline.

[x] The server successfully receives and logs/processes the WiFi and Bluetooth network data.

[x] Starting and stopping the relevant Fragment/Activity correctly manages the scanning process (starting on active, stopping on pause).

## References
Android Developer Documentation: WifiManager

Android Developer Documentation: BluetoothAdapter

Android Developer Documentation: Request App Permissions

Android Developer Documentation: Get Location Updates (relevant for Location Services check)

Android Developer Documentation: BroadcastReceiver

Kotlin Data Classes