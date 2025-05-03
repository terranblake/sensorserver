package github.umer0586.sensorserver.models

data class BluetoothScanResult(
    val name: String,
    val address: String,
    val rssi: Int,
    val timestamp: Long
) 