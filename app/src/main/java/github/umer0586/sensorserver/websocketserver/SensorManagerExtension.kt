package github.umer0586.sensorserver.websocketserver

import android.hardware.Sensor
import android.hardware.SensorManager
import github.umer0586.sensorserver.sensors.NetworkSensorManager

/**
 * Extension function for SensorManager to get a sensor from its string type,
 * including custom network sensors that don't have actual Sensor objects.
 */
fun SensorManager.getSensorFromStringType(sensorType: String): Any? {
    // First check for custom network sensors
    when (sensorType.lowercase()) {
        NetworkSensorManager.TYPE_WIFI_SCAN -> return WifiScanSensor()
        NetworkSensorManager.TYPE_BLUETOOTH_SCAN -> return BluetoothScanSensor()
        NetworkSensorManager.TYPE_NETWORK_SCAN -> return NetworkScanSensor()
    }
    
    // If not a custom type, use the standard sensor lookup
    for (sensor in getSensorList(Sensor.TYPE_ALL)) {
        if (sensor.stringType.equals(sensorType, ignoreCase = true)) {
            return sensor
        }
    }
    
    // Sensor not found
    return null
} 