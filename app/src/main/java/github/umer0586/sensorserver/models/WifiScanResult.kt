package github.umer0586.sensorserver.models

data class WifiScanResult(
    val bssid: String,
    val ssid: String,
    val rssi: Int,
    val frequency: Int,
    val timestamp: Long
) 