package github.umer0586.sensorserver.webserver

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorManager
import com.yanzhenjie.andserver.annotation.GetMapping
import com.yanzhenjie.andserver.annotation.RestController
import com.yanzhenjie.andserver.http.HttpResponse
import github.umer0586.sensorserver.sensors.NetworkSensorManager
import github.umer0586.sensorserver.setting.AppSettings
import github.umer0586.sensorserver.util.JsonUtil

@RestController
class RequestController {

    @GetMapping("/wsport")
    fun getWebsocketPortNo(context : Context) : String {

        val settings = AppSettings(context)
        return JsonUtil.toJSON(
                mapOf("portNo" to settings.getWebsocketPortNo())
        )

    }

    @GetMapping("/sensors")
    fun getSensors(context: Context, response: HttpResponse) : String {

        val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val availableSensors: List<Sensor> = sensorManager.getSensorList(Sensor.TYPE_ALL).filter{ it.reportingMode != Sensor.REPORTING_MODE_ONE_SHOT}

        // Convert standard sensors to map entries
        val sensorsList = availableSensors.map { sensor ->
            val map = mutableMapOf<String, Any>()
            map["name"] = sensor.name
            map["type"] = sensor.stringType
            map
        }.toMutableList()

        // Add custom network sensors
        sensorsList.add(mutableMapOf(
            "name" to "WiFi Scanner",
            "type" to NetworkSensorManager.TYPE_WIFI_SCAN
        ))
        
        sensorsList.add(mutableMapOf(
            "name" to "Bluetooth Scanner",
            "type" to NetworkSensorManager.TYPE_BLUETOOTH_SCAN
        ))
        
        sensorsList.add(mutableMapOf(
            "name" to "Network Scanner",
            "type" to NetworkSensorManager.TYPE_NETWORK_SCAN
        ))

        return JsonUtil.toJSON(sensorsList)
    }

}