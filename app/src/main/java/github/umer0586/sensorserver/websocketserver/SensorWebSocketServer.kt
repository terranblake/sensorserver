package github.umer0586.sensorserver.websocketserver

import android.Manifest
import android.annotation.SuppressLint
import android.content.*
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.Uri
import github.umer0586.sensorserver.models.BluetoothScanResult
import github.umer0586.sensorserver.models.NetworkScanData
import github.umer0586.sensorserver.models.WifiScanResult
import github.umer0586.sensorserver.sensors.NetworkSensorManager
import android.os.*
import android.util.Log
import android.view.MotionEvent
import androidx.annotation.RequiresApi
import github.umer0586.sensorserver.util.JsonUtil
import org.java_websocket.WebSocket
import org.java_websocket.exceptions.WebsocketNotConnectedException
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.server.WebSocketServer
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.util.*
import com.google.gson.Gson
import com.google.gson.JsonSyntaxException

data class ServerInfo(val ipAddress: String, val port: Int)
class GPS
class TouchSensors
// Classes representing sensor types
class WifiScanSensor
class BluetoothScanSensor
class NetworkScanSensor


class SensorWebSocketServer(private val context: Context, address: InetSocketAddress) :
    WebSocketServer(address), SensorEventListener, LocationListener, NetworkSensorManager.NetworkSensorEventListener
{

    var samplingRate = 200000 //default value normal rate

    private var handlerThread: HandlerThread = HandlerThread("Handler Thread")
    private lateinit var handler: Handler
    private lateinit var motionEventHandler : Handler
    private val gson = Gson()

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val locationManager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val networkSensorManager = NetworkSensorManager(context)

    //To keep a record of the sensors that this server is actively listening to for their events. It may contain duplicate entries
    private val sensorsInUse = mutableListOf<Sensor>()

    //Callbacks
    private var onStartCallBack: ((ServerInfo) -> Unit)? = null
    private var onStopCallBack: (() -> Unit)? = null
    private var onErrorCallBack: ((Exception?) -> Unit)? = null
    private var connectionsChangeCallBack: ((List<WebSocket>) -> Unit)? = null

    private var serverStartUpFailed = false

    var isRunning = false
        private set


    companion object
    {

        private val TAG: String = SensorWebSocketServer::class.java.getName()
        private const val CONNECTION_PATH_SINGLE_SENSOR = "/sensor/connect"
        private const val CONNECTION_PATH_MULTIPLE_SENSORS = "/sensors/connect"
        private const val CONNECTION_PATH_GPS = "/gps"
        private const val CONNECTION_PATH_TOUCH_SENSORS = "/touchscreen"
        // Network sensor paths
    private const val CONNECTION_PATH_WIFI_SCAN = "/sensor/connect?type=android.sensor.wifi_scan"
    private const val CONNECTION_PATH_BLUETOOTH_SCAN = "/sensor/connect?type=android.sensor.bluetooth_scan"
    private const val CONNECTION_PATH_NETWORK_SCAN = "/sensor/connect?type=android.sensor.network_scan"
        private val message = mutableMapOf<String,Any>()

        //websocket close codes ranging 4000 - 4999 are for application's custom messages
        const val CLOSE_CODE_SENSOR_NOT_FOUND = 4001
        const val CLOSE_CODE_UNSUPPORTED_REQUEST = 4002
        const val CLOSE_CODE_PARAMETER_MISSING = 4003
        const val CLOSE_CODE_SERVER_STOPPED = 4004
        const val CLOSE_CODE_CONNECTION_CLOSED_BY_APP_USER = 4005
        const val CLOSE_CODE_INVALID_JSON_ARRAY = 4006
        const val CLOSE_CODE_TOO_FEW_SENSORS = 4007
        const val CLOSE_CODE_NO_SENSOR_SPECIFIED = 4008
        const val CLOSE_CODE_PERMISSION_DENIED = 4009

    }

    override fun onOpen(clientWebsocket: WebSocket, handshake: ClientHandshake)
    {
        Log.i(TAG, "New connection" + clientWebsocket.remoteSocketAddress + " Resource descriptor : " + clientWebsocket.resourceDescriptor)

        //Parse uri so that we can read parameters from query
        val requestUri = Uri.parse(clientWebsocket.resourceDescriptor)

        requestUri.let { uri ->

            uri.path?.lowercase().let { path ->

                when (path)
                {
                    CONNECTION_PATH_SINGLE_SENSOR -> handleSingleSensorRequest(uri, clientWebsocket)
                    CONNECTION_PATH_MULTIPLE_SENSORS -> handleMultiSensorRequest(uri, clientWebsocket)
                    CONNECTION_PATH_TOUCH_SENSORS -> {
                        clientWebsocket.setAttachment(TouchSensors())
                        notifyConnectionsChanged()
                    }
                    CONNECTION_PATH_GPS -> handleGPSRequest(clientWebsocket)
                    CONNECTION_PATH_WIFI_SCAN -> {
                        clientWebsocket.setAttachment(WifiScanSensor())
                        Log.i(TAG, "Client connected for WiFi scan sensor: ${clientWebsocket.remoteSocketAddress}")
                        startNetworkScanning()
                        notifyConnectionsChanged()
                    }
                    CONNECTION_PATH_BLUETOOTH_SCAN -> {
                        clientWebsocket.setAttachment(BluetoothScanSensor())
                        Log.i(TAG, "Client connected for Bluetooth scan sensor: ${clientWebsocket.remoteSocketAddress}")
                        startNetworkScanning()
                        notifyConnectionsChanged()
                    }
                    CONNECTION_PATH_NETWORK_SCAN -> {
                        clientWebsocket.setAttachment(NetworkScanSensor())
                        Log.i(TAG, "Client connected for combined Network scan sensor: ${clientWebsocket.remoteSocketAddress}")
                        startNetworkScanning()
                        notifyConnectionsChanged()
                    }
                    else -> clientWebsocket.close(CLOSE_CODE_UNSUPPORTED_REQUEST, "unsupported request")

                }

            }
        }


    }


    /**
     * Helper method to handle multiple sensor request on single websocket connection
     * this method is used in onOpen() method
     */
    private fun handleMultiSensorRequest(uri: Uri, clientWebsocket: WebSocket)
    {
        if (uri.getQueryParameter("types") == null)
        {
            clientWebsocket.close(CLOSE_CODE_PARAMETER_MISSING, "<Types> parameter required")
            return
        }
        val requestedSensorTypes = JsonUtil.readJSONArray(uri.getQueryParameter("types"))

        when {
            requestedSensorTypes == null -> {
                clientWebsocket.close( CLOSE_CODE_INVALID_JSON_ARRAY,"Syntax error : " + uri.getQueryParameter("types") + " is not valid JSON array" )
                return
            }
            requestedSensorTypes.size == 1 -> {
                clientWebsocket.close( CLOSE_CODE_TOO_FEW_SENSORS,"At least two sensor types must be specified" )
                return
            }
            requestedSensorTypes.isEmpty() -> {
                clientWebsocket.close(CLOSE_CODE_NO_SENSOR_SPECIFIED, " No sensor specified")
                return
            }
        }

        Log.i(TAG, "Multi-sensor request received: $requestedSensorTypes")

        // Create a list to store a mix of standard sensors and custom network sensors
        val requestedSensorList = mutableListOf<Any>()
        var containsNetworkSensor = false

        // Safely iterate over the non-null array
        requestedSensorTypes?.let { types ->
            for (i in 0 until types.size) {
                val requestedSensorType = types[i].toString()
                // First check if this is a network sensor type
                val sensorTypeString = requestedSensorType.lowercase(Locale.getDefault())
                
                if (networkSensorManager.isNetworkSensor(sensorTypeString)) {
                    when (sensorTypeString) {
                        NetworkSensorManager.TYPE_WIFI_SCAN -> {
                            requestedSensorList.add(WifiScanSensor())
                            containsNetworkSensor = true
                            Log.i(TAG, "Added WiFi scan sensor to multi-sensor request")
                        }
                        NetworkSensorManager.TYPE_BLUETOOTH_SCAN -> {
                            requestedSensorList.add(BluetoothScanSensor())
                            containsNetworkSensor = true
                            Log.i(TAG, "Added Bluetooth scan sensor to multi-sensor request")
                        }
                        NetworkSensorManager.TYPE_NETWORK_SCAN -> {
                            requestedSensorList.add(NetworkScanSensor())
                            containsNetworkSensor = true
                            Log.i(TAG, "Added Network scan sensor to multi-sensor request")
                        }
                    }
                } else {
                    // Not a network sensor, try regular sensors
                    val sensor = sensorManager.getSensorFromStringType(sensorTypeString)
                    if (sensor == null) {
                        clientWebsocket.close(CLOSE_CODE_SENSOR_NOT_FOUND, "sensor of type $sensorTypeString not found")
                        requestedSensorList.clear()
                        return
                    }
                    requestedSensorList.add(sensor)
                    Log.d(TAG, "Added standard sensor to multi-sensor request")
                }
            }
        }

        if (requestedSensorList.isEmpty()) {
            clientWebsocket.close(CLOSE_CODE_NO_SENSOR_SPECIFIED, "No valid sensors found in request")
            return
        }

        // For new requesting client, attach a tag of requested sensor list with client
        clientWebsocket.setAttachment(requestedSensorList)
        Log.i(TAG, "Attached ${requestedSensorList.size} sensors to client websocket")

        // Register listeners for standard sensors
        val standardSensors = requestedSensorList.filterIsInstance<Sensor>()
        for (sensor in standardSensors) {
            registerListenerForSensor(sensor)
        }
        Log.i(TAG, "Registered listeners for ${standardSensors.size} standard sensors")
        
        // Start network scanning if needed
        if (containsNetworkSensor) {
            Log.i(TAG, "Multi-sensor request contains network sensors, starting network scanning")
            startNetworkScanning()
        }
        
        notifyConnectionsChanged()
    }

    /**
     * Helper method to handle single sensor request on single websocket connection
     * this method is used in onOpen() method
     */
    private fun handleSingleSensorRequest(uri: Uri, clientWebsocket: WebSocket)
    {
        var paramType = uri.getQueryParameter("type")

        when {

            //if type param doesn't exit in the query
            paramType == null -> {

                clientWebsocket.close(CLOSE_CODE_PARAMETER_MISSING, "<type> param required")
                return
            }

            paramType.isEmpty() -> {

                clientWebsocket.close(CLOSE_CODE_NO_SENSOR_SPECIFIED, "No sensor specified")
                return

            }

            else ->{
                paramType = paramType.lowercase(Locale.getDefault())
            }

        }



        // First check if this is a network sensor type
        if (networkSensorManager.isNetworkSensor(paramType)) {
            // Handle network sensors
            when (paramType.lowercase()) {
                NetworkSensorManager.TYPE_WIFI_SCAN -> {
                    clientWebsocket.setAttachment(WifiScanSensor())
                    Log.i(TAG, "Client connected for WiFi scan sensor: ${clientWebsocket.remoteSocketAddress}")
                    startNetworkScanning()
                }
                NetworkSensorManager.TYPE_BLUETOOTH_SCAN -> {
                    clientWebsocket.setAttachment(BluetoothScanSensor())
                    Log.i(TAG, "Client connected for Bluetooth scan sensor: ${clientWebsocket.remoteSocketAddress}")
                    startNetworkScanning()
                }
                NetworkSensorManager.TYPE_NETWORK_SCAN -> {
                    clientWebsocket.setAttachment(NetworkScanSensor())
                    Log.i(TAG, "Client connected for combined Network scan sensor: ${clientWebsocket.remoteSocketAddress}")
                    startNetworkScanning()
                }
            }
            return
        }
        
        // Not a network sensor, try regular sensors
        // sensorManager.getSensorFromStringType(String) returns null when invalid sensor type is passed or when sensor type is not supported by the device
        val requestedSensor = sensorManager.getSensorFromStringType(paramType) as? Sensor
        
        //If client has requested invalid or unsupported sensor
        // then close client Websocket connection and return ( i-e do not proceed further)
        if (requestedSensor == null)
        {
            clientWebsocket.close(CLOSE_CODE_SENSOR_NOT_FOUND,"Sensor of type " + uri.getQueryParameter("type") + " not found" )
            return
        }

        //At this point paramType is valid (e.g android.sensor.light etc..)

        // Attach info with newly connected client
        // so that this Servers knows which client has requested which type of sensor
        clientWebsocket.setAttachment(requestedSensor)
        registerListenerForSensor(requestedSensor)

        notifyConnectionsChanged()
    }

    private fun registerListenerForSensor(sensor: Sensor)
    {
        // if this WebSocket Server is already listening for some type of sensor (e.g android.sensor.light)
        // then we don't have to registered this Server as listener for the same sensor again
        if (sensorsInUse.contains(sensor))
        {

            // Log the sensor type and that it is already registered
            Log.i(TAG, "Sensor ${sensor.name} already registered, skipping registration")

            // Update a list
            // Duplicate entries allowed
            sensorsInUse.add(sensor)
            notifyConnectionsChanged()

            // No need to call sensorManager.registerListener()
            return
        }

        // Register listener for requested sensor
        // Sensor events will be reported to the main thread if a handler is not provided
        sensorManager.registerListener(this, sensor, samplingRate, handler)

        // TODO:
        // Android official docs say (https://developer.android.com/reference/android/hardware/SensorManager)
        // "Note: Don't use this method (registerListener) with a one shot trigger sensor such as Sensor#TYPE_SIGNIFICANT_MOTION.
        // Use requestTriggerSensor(android.hardware.TriggerEventListener, android.hardware.Sensor) instead."

        // Update a list
        sensorsInUse.add(sensor)
        notifyConnectionsChanged()
    }



    private fun handleGPSRequest(clientWebsocket: WebSocket)
    {

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
                context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
        {
            // Reason message must be 123 bytes or less
            clientWebsocket.close(
                    CLOSE_CODE_PERMISSION_DENIED,
                    "Location permission required. Please enable it in your device's App Settings."
            )
            return
        }

        // In Android 5.0 permissions are granted at installation time
        locationManager.requestLocationUpdates(
                LocationManager.GPS_PROVIDER,
                0,
                0f,
                this,
                handlerThread.looper
        )

        clientWebsocket.setAttachment( GPS() )

        notifyConnectionsChanged()
    }

    private fun getGPSConnectionCount() : Int
    {
        return connections.filter{
            it.getAttachment<Any>() is GPS
        }.size
    }

    override fun onLocationChanged(location: Location)
    {
        if (getGPSConnectionCount() == 0)
        {
            Log.w( TAG, "onLocationChanged() :  " + "Location update received when no client with connected with GPS" )
        }
        for (websocket in connections)
        {
            if (websocket.getAttachment<Any>() is GPS)
            {
                try {
                    websocket.send(location.toJson())
                }catch (e : WebsocketNotConnectedException){
                    e.printStackTrace()
                }
            }
        }
    }

    override fun onProviderDisabled(provider: String)
    {
        Log.i(TAG, "onProviderDisabled() $provider")
    }

    override fun onProviderEnabled(provider: String)
    {
        Log.i(TAG, "onProviderEnabled() $provider")
    }

    // See issue  : https://github.com/umer0586/SensorServer/issues/61
    // solution : https://stackoverflow.com/questions/64638260/android-locationlistener-abstractmethoderror-on-onstatuschanged-and-onproviderd
    @Deprecated("Deprecated in Java")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?)
    {
       // super.onStatusChanged(provider, status, extras)
    }
    override fun onClose(clientWebsocket: WebSocket, code: Int, reason: String?, remote: Boolean)
    {
        Log.i(TAG, "Closed " + clientWebsocket.remoteSocketAddress + " with exit code " + code + " additional info: " + reason)

        val tag = clientWebsocket.getAttachment<Any?>()
        when (tag)
        {
            is Sensor ->
            {
                unregisterListenerForSensor(tag)
            }

            is List<*> ->
            {
                // First handle standard sensors
                tag.filterIsInstance<Sensor>().forEach {
                    unregisterListenerForSensor(it)
                }
                
                // Check if list contains any network sensors
                val hasNetworkSensors = tag.any { 
                    it is WifiScanSensor || it is BluetoothScanSensor || it is NetworkScanSensor 
                }
                
                // If the list contained network sensors, handle cleanup
                if (hasNetworkSensors) {
                    Log.i(TAG, "Client with multi-sensor request disconnected, checking network sensors")
                    stopNetworkScanning()
                }
            }

            is GPS -> {
                unregisterLocationListener()
            }

            is TouchSensors -> {
                //no need to do anything
            }
            is WifiScanSensor, is BluetoothScanSensor, is NetworkScanSensor -> {
                Log.i(TAG, "Client disconnected from network sensor: ${clientWebsocket.remoteSocketAddress}")
                stopNetworkScanning()
            }
        }

        notifyConnectionsChanged()
    }

    // This method is used in OnClose()
    private fun unregisterListenerForSensor(sensor: Sensor)
    {

        // When client has closed connection, how many clients receiving same sensor data from this server
        val sensorConnectionCount = getSensorConnectionCount(sensor).toLong()
        Log.i(TAG, "Sensor : " + sensor.name + " Connections : " + sensorConnectionCount)

        /*
            Suppose we have 3 clients each receiving light sensor data \
            if we unregister this server for light sensor when only one client is disconnected \
            then 2 other connected client won't receive light sensor data anymore

        */

        //  Unregister listener if and only if one client is using it
        if (sensorConnectionCount == 1L)
            sensorManager.unregisterListener(this, sensor)

        sensorsInUse.remove(sensor)
        Log.i(TAG, "Total Connections : " + getConnectionCount())

        notifyConnectionsChanged()
    }

    // Method to unregister location updates
    private fun unregisterLocationListener() {
        // Count how many clients are still using GPS
        val gpsClientCount = connections.count { it.getAttachment<Any>() is GPS }
        
        // Only unregister if this is the last client using GPS
        if (gpsClientCount <= 1) {
            locationManager.removeUpdates(this)
            Log.i(TAG, "Location updates unregistered")
        } else {
            Log.i(TAG, "Location updates still needed by $gpsClientCount clients")
        }
    }

    override fun onMessage(websocket: WebSocket, message: String)
    {
        //Log.d(TAG, "onMessage: $message")
        //Log.d(TAG, "onMessage: ${Thread.currentThread().name}")
        
        if(message.equals("getLastKnownLocation",ignoreCase = true) && websocket.getAttachment<Any>() is GPS)
        {
           // For Android 6.0 or above check if user has allowed location permission
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED)
           {
               if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED)
               {
                   locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)?.apply {
                       try {
                           websocket.send(this.toJson(lastKnownLocation = true))
                       } catch(e : WebsocketNotConnectedException){
                           e.printStackTrace()
                       }
                   }
               }
               else
               {
                   websocket.close(
                           CLOSE_CODE_PERMISSION_DENIED,
                           "App has No permission to access location. Go to your device's installed apps settings and allow location permission to Sensor Server app"
                           )
               }
           }
           // For Android 5.0 permissions are granted at install time
           else {

               locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)?.apply {
                   try {
                       websocket.send(this.toJson(lastKnownLocation = true))
                   } catch(e : WebsocketNotConnectedException){
                       e.printStackTrace()
                   }
               }
           }
        }
    }

    override fun onMessage(conn: WebSocket, message: ByteBuffer)
    {
    }
    // following doc taken from original source
    /**
     * Called when errors occurs. If an error causes the websocket connection to fail [.onClose] will be called additionally.<br></br>
     * This method will be called primarily because of IO or protocol errors.<br></br>
     * If the given exception is an RuntimeException that probably means that you encountered a bug.<br></br>
     *
     * @param conn Can be null if there error does not belong to one specific websocket. For example if the servers port could not be bound.
     * @param ex The exception causing this error
     */
    override fun onError(conn: WebSocket?, ex: Exception)
    {
        // error occurred on websocket conn (we don't notify anything to the user about this for now)
        if (conn != null)
            Log.e(TAG, "an error occurred on connection " + conn.remoteSocketAddress)

        // if conn is null than we have error related to server
        if (conn == null)
        {
            /*
                seeing the implementation of onError(conn,ex), this method
                always invokes stop() whether server is running or not,
                So onError() would invoke stop() when some exception like BindException occurs (because of port already in use)
            */

            //    if (serverErrorListener != null) serverErrorListener!!.onServerError(ex) // listener must filter exception by itself

            onErrorCallBack?.invoke(ex)

            // we will use this in stop() method to check if there was an exception during server startup
            serverStartUpFailed = true

        }
        ex.printStackTrace()
    }

    override fun onStart()
    {

        onStartCallBack?.invoke(ServerInfo(address.hostName, port))

        isRunning = true
        Log.i(TAG, "server started successfully $address")
        Log.i(TAG, "sampling rate $samplingRate")
    }

    @kotlin.Throws(InterruptedException::class)
    override fun stop()
    {
        closeAllConnections()

        locationManager.removeUpdates(this)

        super.stop()
        Log.d(TAG, "stop() called")

        // if (serverStopListener != null && !serverStartUpFailed) serverStopListener!!.onServerStopped()

        onStopCallBack?.run {

            if (!serverStartUpFailed)
                invoke()
        }


        if (handlerThread.isAlive)
            handlerThread.quitSafely()

        isRunning = false
    }

    /*
        1. calling webSocketServerObj.run() starts server.
        2. WebSocketServer do not run on a separate thread by default,
           so we need to make sure that we run server on separate thread
     */
    override fun run()
    {
        Thread { super.run() }.start()

        // see https://stackoverflow.com/questions/23209804/android-sensor-registerlistener-in-a-separate-thread

        handlerThread.start()
        handler = Handler(handlerThread.looper)



        motionEventHandler = object : Handler(handlerThread.looper){

            override fun handleMessage(msg: Message)
            {
                //Log.d(TAG,"Handler" + Thread.currentThread().name)
                message.clear()
                val motionEvent = msg.obj as MotionEvent

                for (websocket in connections)
                {
                    if(websocket.getAttachment<Any>() is TouchSensors)
                    {
                        when(motionEvent.actionMasked)
                        {
                            MotionEvent.ACTION_UP -> {
                                message["action"] = "ACTION_UP"
                                message["x"] = motionEvent.x
                                message["y"] = motionEvent.y

                                try {
                                    websocket.send(JsonUtil.toJSON(message))
                                } catch (e : WebsocketNotConnectedException){
                                    e.printStackTrace()
                                }

                            }

                            MotionEvent.ACTION_DOWN -> {
                                message["action"] = "ACTION_DOWN"
                                message["x"] = motionEvent.x
                                message["y"] = motionEvent.y

                                try {
                                    websocket.send(JsonUtil.toJSON(message))
                                } catch (e : WebsocketNotConnectedException){
                                    e.printStackTrace()
                                }

                            }

                            MotionEvent.ACTION_MOVE -> {
                                message["action"] = "ACTION_MOVE"
                                message["x"] = motionEvent.x
                                message["y"] = motionEvent.y

                                try {
                                    websocket.send(JsonUtil.toJSON(message))
                                } catch (e : WebsocketNotConnectedException){
                                    e.printStackTrace()
                                }

                            }


                        }


                    }
                }

            }
        }




    }

    fun onMotionEvent(motionEvent: MotionEvent)
    {

        motionEventHandler.post{

            message.clear()
            message["x"] = motionEvent.x
            message["y"] = motionEvent.y
            message["action"] = when (motionEvent.action)
            {
                MotionEvent.ACTION_DOWN -> "ACTION_DOWN"
                MotionEvent.ACTION_UP -> "ACTION_UP"
                MotionEvent.ACTION_MOVE -> "ACTION_MOVE"
                else -> motionEvent.action.toString() // Use toString for other actions
            }

            val jsonData = gson.toJson(message)

            // Find all clients that requested touch events
            val touchClients = connections.filter { it.getAttachment<Any?>() is TouchSensors }
            touchClients.forEach {
                try {
                    it.send(jsonData as String)
                } catch (e: WebsocketNotConnectedException) {
                    Log.w(TAG, "Attempted to send to a closed websocket (touch): ${it.remoteSocketAddress}")
                } catch (e: Exception) {
                    Log.e(TAG, "Error sending touch event", e)
                }
            }
        }
    }

    override fun onSensorChanged(sensorEvent: SensorEvent)
    {
        val sensor = sensorEvent.sensor

        message.clear()
        message["values"] = sensorEvent.values
        message["accuracy"] = sensorEvent.accuracy
        message["timestamp"] = sensorEvent.timestamp

        // Find all clients that requested this specific sensor (single or multi)
        val targetClients = connections.filter { ws ->
            val attachment = ws.getAttachment<Any?>()
            (attachment is Sensor && attachment == sensor) || (attachment is List<*> && (attachment as? List<*>)?.filterIsInstance<Sensor>()?.contains(sensor) == true)
        }

        // Send to clients that requested only this sensor
        val singleSensorJson = gson.toJson(message) // Serialize once
        targetClients.filter { it.getAttachment<Any?>() is Sensor }.forEach {
            try {
                 it.send(singleSensorJson as String)
            } catch (e: WebsocketNotConnectedException) {
                Log.w(TAG, "Attempted to send to a closed websocket (single): ${it.remoteSocketAddress}")
            } catch (e: Exception) {
                 Log.e(TAG, "Error sending sensor data (single)", e)
            }
        }

         // Send to clients that requested multiple sensors including this one
         // Fix: Use stringType instead of name to ensure we have the full type path
        message["type"] = sensor.stringType // Use stringType instead of name
        val multiSensorJson = gson.toJson(message) // Serialize again with type
        val multiSensorClients = targetClients.filter { it.getAttachment<Any?>() is List<*> }
        multiSensorClients.forEach {
            try {
                it.send(multiSensorJson as String)
            } catch (e: WebsocketNotConnectedException) {
                Log.w(TAG, "Attempted to send to a closed websocket (multi): ${it.remoteSocketAddress}")
            } catch (e: Exception) {
                Log.e(TAG, "Error sending sensor data (multi)", e)
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor, i: Int)
    {
    }

    private fun getSensorConnectionCount(sensor: Sensor): Int
    {
        var count = 0
        for (sensorInUse in sensorsInUse)
            if (sensorInUse.type == sensor.type)
                count++

        return count
    }

    private fun notifyConnectionsChanged()
    {
        Log.d(TAG, "notifyConnectionsChanged() : " + connections.size)
        connectionsChangeCallBack?.invoke(ArrayList(connections))
    }

    fun getConnectionCount(): Int
    {
        return connections.size
    }

    private fun closeAllConnections()
    {

        connections.forEach { webSocket ->
            webSocket.close(CLOSE_CODE_SERVER_STOPPED, "Server stopped")
        }

    }


    fun onStart(callBack: ((ServerInfo) -> Unit)? )
    {
        onStartCallBack = callBack
    }

    fun onStop(callBack: (() -> Unit)? )
    {
        onStopCallBack = callBack
    }
    fun onError(callBack: ((Exception?) -> Unit)? )
    {
        onErrorCallBack = callBack
    }
    fun onConnectionsChange(callBack: ((List<WebSocket>) -> Unit)?)
    {
        connectionsChangeCallBack = callBack
    }

    fun broadcastNetworkScanData(jsonData: String) {
        // Find all clients that requested network scan data
        val networkScanClients = connections.filter { it.getAttachment<Any?>() is WifiScanSensor || 
                                                      it.getAttachment<Any?>() is BluetoothScanSensor || 
                                                      it.getAttachment<Any?>() is NetworkScanSensor }
        Log.d(TAG, "Broadcasting network data to ${networkScanClients.size} clients.")
        // Use broadcast method for efficiency
        broadcast(jsonData, networkScanClients)
    }

    // Helper method to start/stop network scanning when needed
    private fun startNetworkScanning() {
        Log.i(TAG, "Starting network scanning check with ${connections.size} total connections")
        
        // Check if any clients are using network sensors directly or in a list
        val hasNetworkClients = connections.any { conn -> 
            val attachment = conn.getAttachment<Any?>()
            when (attachment) {
                is WifiScanSensor, is BluetoothScanSensor, is NetworkScanSensor -> true
                is List<*> -> attachment.any { 
                    it is WifiScanSensor || it is BluetoothScanSensor || it is NetworkScanSensor 
                }
                else -> false
            }
        }
        
        if (hasNetworkClients) {
            Log.i(TAG, "Network client detected, registering for network scan events")
            networkSensorManager.registerListener(this)
            // Force an immediate scan by manually triggering the scan scheduler
            // This ensures clients don't have to wait for the next scheduler interval
            // to receive data when they first connect
            networkSensorManager.triggerImmediateScan()
        } else {
            Log.i(TAG, "No network clients detected after check")
        }
    }
    
    private fun stopNetworkScanning() {
        Log.i(TAG, "Checking if we should stop network scanning...")
        // Check if any clients are still using network sensors directly or in a list
        val hasNetworkClients = connections.any { conn ->
            val attachment = conn.getAttachment<Any?>()
            when (attachment) {
                is WifiScanSensor, is BluetoothScanSensor, is NetworkScanSensor -> true
                is List<*> -> attachment.any { 
                    it is WifiScanSensor || it is BluetoothScanSensor || it is NetworkScanSensor 
                }
                else -> false
            }
        }
        
        if (!hasNetworkClients) {
            Log.i(TAG, "No more network clients, unregistering network sensor listener")
            networkSensorManager.unregisterListener(this)
        } else {
            Log.i(TAG, "Network scanning continues for ${connections.size} total connections")
        }
    }
    
    // Implementation of NetworkSensorManager.NetworkSensorEventListener
    override fun onWifiScanResult(results: List<WifiScanResult>) {
        // Find all clients that want WiFi scan data
        val wifiClients = connections.filter { it.getAttachment<Any?>() is WifiScanSensor }
        
        // Also find multi-sensor clients that include WiFi scan sensor
        val multiSensorWifiClients = connections.filter { conn ->
            val attachment = conn.getAttachment<Any?>()
            if (attachment is List<*>) {
                attachment.any { it is WifiScanSensor }
            } else {
                false
            }
        }
        
        val totalWifiClients = wifiClients.size + multiSensorWifiClients.size
        if (totalWifiClients == 0) return
        
        Log.i(TAG, "===> Sending WiFi scan results (${results.size} networks) to $totalWifiClients clients")
        
        // Convert to JSON and send to appropriate clients
        val jsonData = gson.toJson(mapOf("type" to NetworkSensorManager.TYPE_WIFI_SCAN, "values" to results))
        
        // Send to dedicated WiFi clients
        wifiClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send WiFi scan results: Client not connected", e)
            }
        }
        
        // Send to multi-sensor clients that include WiFi
        multiSensorWifiClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send WiFi scan results to multi-sensor client", e)
            }
        }
    }
    
    override fun onBluetoothScanResult(results: List<BluetoothScanResult>) {
        // Find all clients that want Bluetooth scan data
        val btClients = connections.filter { it.getAttachment<Any?>() is BluetoothScanSensor }
        
        // Also find multi-sensor clients that include Bluetooth scan sensor
        val multiSensorBtClients = connections.filter { conn ->
            val attachment = conn.getAttachment<Any?>()
            if (attachment is List<*>) {
                attachment.any { it is BluetoothScanSensor }
            } else {
                false
            }
        }
        
        val totalBtClients = btClients.size + multiSensorBtClients.size
        if (totalBtClients == 0) return
        
        Log.i(TAG, "===> Sending Bluetooth scan results (${results.size} devices) to $totalBtClients clients")
        
        // Convert to JSON and send to appropriate clients
        val jsonData = gson.toJson(mapOf("type" to NetworkSensorManager.TYPE_BLUETOOTH_SCAN, "values" to results))
        
        // Send to dedicated Bluetooth clients
        btClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send Bluetooth scan results: Client not connected", e)
            }
        }
        
        // Send to multi-sensor clients that include Bluetooth
        multiSensorBtClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send Bluetooth scan results to multi-sensor client", e)
            }
        }
    }
    
    override fun onNetworkScanResult(data: NetworkScanData) {
        // Find all clients that want combined network scan data
        val networkClients = connections.filter { it.getAttachment<Any?>() is NetworkScanSensor }
        
        // Also find multi-sensor clients that include Network scan sensor
        val multiSensorNetworkClients = connections.filter { conn ->
            val attachment = conn.getAttachment<Any?>()
            if (attachment is List<*>) {
                attachment.any { it is NetworkScanSensor }
            } else {
                false
            }
        }
        
        val totalNetworkClients = networkClients.size + multiSensorNetworkClients.size
        if (totalNetworkClients == 0) return
        
        Log.i(TAG, "===> Sending network scan results (${data.wifiResults.size} WiFi networks, ${data.bluetoothResults.size} BT devices) to $totalNetworkClients clients")
        
        // Convert to JSON and send to appropriate clients
        val jsonData = gson.toJson(mapOf("type" to NetworkSensorManager.TYPE_NETWORK_SCAN, "values" to data))
        
        // Send to dedicated Network clients
        networkClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send network scan results: Client not connected", e)
            }
        }
        
        // Send to multi-sensor clients that include Network
        multiSensorNetworkClients.forEach { client ->
            try {
                client.send(jsonData)
            } catch (e: WebsocketNotConnectedException) {
                Log.e(TAG, "Failed to send network scan results to multi-sensor client", e)
            }
        }
    }

}

fun Location.toJson(lastKnownLocation : Boolean = false) : String
{
    val message = mutableMapOf<String,Any>()
    message["longitude"] = longitude
    message["latitude"] = latitude
    message["altitude"] = altitude
    message["bearing"] = bearing
    message["accuracy"] = accuracy
    message["speed"] = speed
    message["time"] = time
    message["lastKnowLocation"] = lastKnownLocation


    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
    {
        message["speedAccuracyMetersPerSecond"] = speedAccuracyMetersPerSecond
        message["bearingAccuracyDegrees"] = bearingAccuracyDegrees
        message["elapsedRealtimeNanos"] = elapsedRealtimeNanos
        message["verticalAccuracyMeters"] = verticalAccuracyMeters
    }
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
    {
        message["elapsedRealtimeAgeMillis"] = elapsedRealtimeAgeMillis
    }
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
    {
        message["elapsedRealtimeUncertaintyNanos"] = elapsedRealtimeUncertaintyNanos
    }

    return JsonUtil.toJSON(message)
}