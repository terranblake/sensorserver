package github.umer0586.sensorserver.models

import android.os.Parcelable
import kotlinx.parcelize.Parcelize

// Commented out duplicated data classes that are now in separate files
/*
@Parcelize
data class WifiScanResult(
    val bssid: String,
    val ssid: String,
    val rssi: Int,
    val frequency: Int,
    val timestamp: Long // Optional: timestamp of the scan result
) : Parcelable

@Parcelize
data class BluetoothScanResult(
    val name: String?,
    val address: String,
    val rssi: Int,
    val timestamp: Long // Optional: timestamp of discovery
) : Parcelable

data class NetworkScanData(
    val timestamp: Long,
    val wifiResults: List<WifiScanResult>,
    val bluetoothResults: List<BluetoothScanResult>
) 
*/ 