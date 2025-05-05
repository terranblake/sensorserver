package github.umer0586.sensorserver.activities

import android.annotation.SuppressLint
import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.view.MotionEvent
import github.umer0586.sensorserver.R
import github.umer0586.sensorserver.service.WebsocketService
import github.umer0586.sensorserver.service.ServiceBindHelper
import android.util.Log
import android.widget.TextView

class TouchScreenActivity : AppCompatActivity()
{
    private val TAG = "TouchScreenActivity"
    private var websocketService: WebsocketService? = null
    private lateinit var touchFeedbackText: TextView

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreate(savedInstanceState: Bundle?)
    {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_touch_screen)
        
        touchFeedbackText = findViewById(R.id.touchFeedbackText)
        
        // Set touch listener to directly handle touch events
        touchFeedbackText.setOnTouchListener { _, event ->
            handleTouchEvent(event)
            true // Consume the event
        }

        val serviceBindHelper = ServiceBindHelper(
            context = applicationContext,
            service = WebsocketService::class.java,
            componentLifecycle = lifecycle
        )

        serviceBindHelper.onServiceConnected { binder ->
            val localBinder = binder as WebsocketService.LocalBinder
            websocketService = localBinder.service
            Log.d(TAG, "===> WebsocketService connected")
        }
    }

    private fun handleTouchEvent(event: MotionEvent): Boolean {
        // Update UI with touch coordinates
        val actionStr = when (event.action) {
            MotionEvent.ACTION_DOWN -> "DOWN"
            MotionEvent.ACTION_MOVE -> "MOVE"
            MotionEvent.ACTION_UP -> "UP"
            else -> "OTHER"
        }
        
        touchFeedbackText.text = "Touch: $actionStr\nX: ${event.x.toInt()}\nY: ${event.y.toInt()}"
        
        // Send to WebSocket service
        Log.d(TAG, "===> handleTouchEvent: action=$actionStr, x=${event.x}, y=${event.y}")
        try {
            websocketService?.sendMotionEvent(event)
            Log.d(TAG, "===> Successfully sent motion event to WebsocketService")
        } catch (e: Exception) {
            Log.e(TAG, "===> Error sending motion event: ${e.message}", e)
        }
        
        return true
    }

    // Keep this as a fallback
    override fun onTouchEvent(event: MotionEvent?): Boolean
    {
        Log.d(TAG, "===> onTouchEvent fired: action=${event?.action}, x=${event?.x}, y=${event?.y}")
        event?.let {
            handleTouchEvent(it)
        }

        return super.onTouchEvent(event)
    }
}