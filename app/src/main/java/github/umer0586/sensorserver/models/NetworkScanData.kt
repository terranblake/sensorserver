package github.umer0586.sensorserver.models

data class NetworkScanData(
    val timestamp: Long,
    val wifiResults: List<WifiScanResult>,
    val bluetoothResults: List<BluetoothScanResult>
) 