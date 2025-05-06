import asyncio
import websockets
import aiohttp
from aiohttp import web
import json
import logging
from typing import Dict, Any, List, Optional, Set
import threading # Needed for running asyncio loop in a thread
from urllib.parse import urlencode # Needed for WebSocket client URI
import os

# Configure basic logging for the module
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from collector import Collector


class DeviceManager:
    """
    Manages communication with connected device clients. Hosts the HTTP and
    WebSocket servers that device clients connect to. Handles receiving raw
    sensor data and includes functionality to query the device for available sensors
    and establish WebSocket client connections to receive data.
    Also hosts a WebSocket server for pushing real-time data to the frontend.
    """
    def __init__(self, collector: Collector, device_host: str, device_http_port: int, device_ws_port: int, listen_host: str = '0.0.0.0', listen_http_port: int = 9090, listen_ws_port: int = 8080, frontend_ws_port: int = 5001):
        """
        Initializes the DeviceManager.

        Args:
            collector: An instance of the Collector.
            device_host: The IP address or hostname of the device client.
            device_http_port: The HTTP port on the device client.
            device_ws_port: The WebSocket port on the device client.
            listen_host: The host address to bind the DeviceManager's servers to. Defaults to '0.0.0.0'.
            listen_http_port: The port for the DeviceManager's device-facing HTTP server. Defaults to 9090.
            listen_ws_port: The port for the DeviceManager's device-facing WebSocket server. Defaults to 8080.
            frontend_ws_port: The port for the DeviceManager's frontend-facing WebSocket server. Defaults to 5001.
        """
        self.collector = collector
        self.device_host = device_host
        self.device_http_port = device_http_port
        self.device_ws_port = device_ws_port
        self.device_http_url = f"http://{device_host}:{device_http_port}/sensors" # URL to query device sensors
        self.device_info_url = f"http://{device_host}:{device_http_port}/device_info" # URL for device info
        self.device_ws_uri = f"ws://{device_host}:{device_ws_port}" # URI for device WebSocket

        self.listen_host = listen_host
        self.listen_http_port = listen_http_port
        self.listen_ws_port = listen_ws_port
        self.frontend_ws_port = frontend_ws_port

        self._stop_event = None # Created within the asyncio loop
        self._ws_server = None # Device-facing WebSocket server
        self._http_runner = None # Device-facing HTTP server
        self._frontend_ws_server = None # Frontend-facing WebSocket server
        self._frontend_websockets: Set[websockets.WebSocketServerProtocol] = set() # Connected frontend websockets

        self._device_websocket_client = None # WebSocket client connection to the device
        self._available_sensor_types: List[str] = [] # Store discovered sensor types
        self._device_client_task = None # Task for the WebSocket client connection

        # Get the asyncio loop for run_coroutine_threadsafe
        self._loop: Optional[asyncio.AbstractEventLoop] = None


        logger.info(f"DeviceManager initialized. Device: HTTP={self.device_http_url}, WS={self.device_ws_uri}. Listening on: HTTP={self.listen_host}:{self.listen_http_port}, WS={self.listen_host}:{self.listen_ws_port}, Frontend WS={self.listen_host}:{self.frontend_ws_port}")


    async def query_device_info(self) -> Dict[str, Optional[str]]:
        """Queries the device's HTTP endpoint to get device name and model."""
        logger.info(f"Attempting to fetch device info from {self.device_info_url}")
        info = {'name': None, 'model': None}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.device_info_url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        info['name'] = data.get('name')
                        info['model'] = data.get('model')
                        logger.info(f"Received device info from {self.device_info_url}: {info}")
                    else:
                        logger.warning(f"Failed to fetch device info from {self.device_info_url}. Status: {response.status}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching device info from {self.device_info_url}.")
        except aiohttp.ClientError as e:
            logger.warning(f"Connection error fetching device info from {self.device_info_url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching device info from {self.device_info_url}: {e}", exc_info=True)
        
        # Assign to instance attribute EVEN IF FAILED (will contain None values)
        self.device_info = info 
        logger.info(f"Device info set to: {self.device_info}") # Added verification log
        return info

    async def query_available_sensors(self) -> List[str]:
        """
        Queries the device's HTTP endpoint to get a list of available sensor types.

        Returns:
            A list of standardized sensor type strings (e.g., 'android.sensor.pressure'),
            or an empty list if discovery fails.
        """
        logger.info(f"Attempting to fetch sensor list from device at {self.device_http_url}")
        sensor_types = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.device_http_url, timeout=20) as response:
                    if response.status == 200:
                        sensors_data = await response.json(content_type=None)
                        sensor_types = [sensor.get('type') for sensor in sensors_data if isinstance(sensor, dict) and sensor.get('type')]
                        if not sensor_types:
                             logger.warning(f"Sensor list received from {self.device_http_url} but it was empty or filtered to empty.")
                        else:
                             logger.info(f"Discovered {len(sensor_types)} sensor types from {self.device_http_url}")
                    else:
                        logger.error(f"Failed to fetch sensor list from {self.device_http_url}. HTTP status: {response.status}")
        except aiohttp.ClientConnectorError as e:
             logger.error(f"Connection error fetching sensor list from {self.device_http_url}: {e}")
        except aiohttp.ClientResponseError as e:
             logger.error(f"HTTP response error fetching sensor list from {self.device_http_url}: {e}")
        except asyncio.TimeoutError:
             logger.error(f"Timeout fetching sensor list from {self.device_http_url}.")
        except json.JSONDecodeError as e:
            logger.error(f"Could not decode JSON response from {self.device_http_url}: {e}")
        except Exception as e:
            # Log the specific exception
            logger.error(f"Unexpected error fetching sensor list from {self.device_http_url}: {e}", exc_info=True)

        self._available_sensor_types = sensor_types # Store the discovered types
        return sensor_types

    async def _connect_and_receive_websocket_data(self):
        """
        Connects to the device's WebSocket server and receives sensor data.
        Attempts to reconnect if the connection is lost.
        """
        # Use IP address as the consistent identifier for logging and WS messages
        device_ip = self.device_host 
        # Fetch friendly name info once using self.device_info (populated by query_device_info)
        # Add error handling in case self.device_info is somehow still missing, default to IP
        device_info_dict = getattr(self, 'device_info', {}) 
        friendly_name = device_info_dict.get('name') or device_info_dict.get('model') or device_ip
        
        while not self._stop_event.is_set():
            try:
                # Construct the WebSocket URI with sensor types as a query parameter
                # The device client's WSS server needs to support this
                if not self._available_sensor_types:
                     logger.warning("No available sensor types discovered. Cannot connect WebSocket client.")
                     await asyncio.sleep(5) # Wait before retrying discovery
                     # Re-query available sensors before the next connection attempt
                     await self.query_available_sensors()
                     continue

                types_json_string = json.dumps(self._available_sensor_types)
                query_params = urlencode({"types": types_json_string})
                websocket_uri = f"{self.device_ws_uri}/sensors/connect?{query_params}"

                logger.info(f"Attempting WebSocket client connection to device at {websocket_uri}")
                async with websockets.connect(websocket_uri, ping_interval=20, ping_timeout=20, open_timeout=20) as websocket:
                    logger.info(f"WebSocket client connection established to device at {websocket_uri}")
                    self._device_websocket_client = websocket # Store the active connection

                    try:
                        async for message in websocket:
                            try:
                                raw_data = json.loads(message)
                                # Pass the raw data to the Collector using variables defined at function start
                                self.collector.receive_raw_data(raw_data, device_identifier=friendly_name, device_ip=device_ip)
                                logger.debug(f"Received and passed raw data from device WebSocket client ({device_ip}, ID: {friendly_name}): {raw_data.get('type', 'Unknown Type')}")

                                # Push sensor data with IP - Directly await the push coroutine

                            except json.JSONDecodeError:
                                logger.warning(f"Received invalid JSON over device WebSocket client: {message}")
                            except Exception as e:
                                logger.error(f"Error processing device WebSocket client message: {e}", exc_info=True)

                    except websockets.exceptions.ConnectionClosedOK:
                        logger.info(f"Device WebSocket client connection closed cleanly.")
                    except websockets.exceptions.ConnectionClosedError as e:
                        logger.warning(f"Device WebSocket client connection closed with error: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error in device WebSocket client: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error in device WebSocket client: {e}", exc_info=True)

            logger.info("Device WebSocket client connection lost. Attempting to reconnect in 5 seconds...")
            self._device_websocket_client = None # Clear the old connection
            await asyncio.sleep(5) # Wait before attempting to reconnect

        logger.info("Device WebSocket client connection task stopped.")


    async def _http_handler(self, request):
        """Handles incoming HTTP requests from device clients."""
        logger.info(f"Received HTTP request from device: {request.method} {request.url}")
        # This endpoint's original purpose was to list sensors on the device.
        # In this new architecture, it might be used for initial handshake or
        # to provide server information to the client.
        # Example: Respond to a /sensors request with a dummy list of available sensor types.
        # This is the server-side endpoint that the device client would query.
        if request.url.path == '/sensors':
            # In a real scenario, you might get this list from the device itself
            # or a configuration. For now, return a placeholder.
            available_sensors = [
                {"type": "android.sensor.pressure", "name": "Pressure Sensor"},
                {"type": "android.sensor.accelerometer", "name": "Accelerometer"},
                {"type": "android.sensor.gyroscope", "name": "Gyroscope"},
                {"type": "gps", "name": "GPS Location"},
                {"type": "android.sensor.wifi_scan", "name": "WiFi Scan"},
                {"type": "android.sensor.bluetooth_scan", "name": "Bluetooth Scan"},
                {"type": "android.sensor.network_scan", "name": "Network Scan (WiFi+BT)"},
                # Add other sensors your device might provide
            ]
            return web.Response(
                text=json.dumps(available_sensors),
                content_type='application/json'
            )
        else:
            return web.Response(status=404, text="Not Found")


    async def _websocket_server_handler(self, websocket):
        """Handles incoming WebSocket connections from device clients (server role)."""
        logger.info(f"Device WebSocket server connection established from {websocket.remote_address}")
        try:
            async for message in websocket:
                # Assume message is a JSON string containing raw sensor data
                try:
                    raw_data = json.loads(message)
                    # Pass the raw data to the Collector
                    self.collector.receive_raw_data(raw_data)
                    logger.debug(f"Received and passed raw data from device WebSocket server: {raw_data.get('type', 'Unknown Type')}")

                    # --- Real-time Push to Frontend (Conceptual) ---
                    # If this raw data should be immediately pushed to the frontend,
                    # you would format it and send it to connected frontend websockets.
                    # This requires a separate WebSocket server for the frontend.
                    # For now, this is a placeholder for that logic.
                    # await self._push_data_to_frontend(raw_data) # Example call

                except json.JSONDecodeError:
                    logger.warning(f"Received invalid JSON over device WebSocket server: {message}")
                except Exception as e:
                    logger.error(f"Error processing device WebSocket server message: {e}", exc_info=True)

        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"Device WebSocket server connection closed cleanly from {websocket.remote_address}")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"Device WebSocket server connection closed with error from {websocket.remote_address}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in device WebSocket server handler for {websocket.remote_address}: {e}", exc_info=True)


    async def _frontend_websocket_handler(self, websocket):
        """Handles incoming WebSocket connections from frontend clients."""
        logger.info(f"WebSocket connection established from frontend: {websocket.remote_address}")
        self._frontend_websockets.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            self._frontend_websockets.remove(websocket)
            logger.info(f"WebSocket connection closed from frontend: {websocket.remote_address}")

    async def _push_data_to_frontend(self, data):
        """Pushes data (raw or processed) to all connected frontend websockets."""
        # Format the data as needed for the frontend
        message = json.dumps(data) # Example: send raw data directly
        # Send the message to all connected frontend websockets
        await asyncio.gather(*[ws.send(message) for ws in self._frontend_websockets if not ws.closed])

    def push_realtime_update(self, data):
        """
        Thread-safe method to push data to connected frontend clients.
        Called by other modules (e.g., Collector) running in different threads.
        """
        if self._loop and self._frontend_websockets:
            # Schedule the async _push_data_to_frontend coroutine on the asyncio loop
            asyncio.run_coroutine_threadsafe(self._push_data_to_frontend(data), self._loop)
        elif not self._loop:
             logger.warning("Asyncio loop not set in DeviceManager. Cannot push real-time updates.")
        # No warning if _frontend_websockets is empty, as there's no one to push to


    async def start(self):
        """
        Starts the DeviceManager's servers and client connections.
        Queries available sensors and establishes WebSocket client connection to the device.
        """
        # Get the current asyncio loop
        self._loop = asyncio.get_event_loop()

        # Create the asyncio Event within the asyncio loop context
        self._stop_event = asyncio.Event()

        # Start the DeviceManager's own HTTP and WebSocket servers (for device clients to connect to)
        self._ws_server = await websockets.serve(
            self._websocket_server_handler, # Use the server handler
            self.listen_host,
            self.listen_ws_port
        )
        logger.info(f"DeviceManager WebSocket server started on ws://{self.listen_host}:{self.listen_ws_port}")

        app_http = web.Application()
        app_http.router.add_get('/sensors', self._http_handler) # Endpoint for sensor discovery (server role)
        # Add other HTTP endpoints for device clients here if needed
        self._http_runner = web.AppRunner(app_http)
        await self._http_runner.setup()
        http_site = web.TCPSite(self._http_runner, self.listen_host, self.listen_http_port)
        await http_site.start()
        logger.info(f"DeviceManager HTTP server started on http://{self.listen_host}:{self.listen_http_port}")

        # --- Start Frontend WebSocket Server ---
        # This server listens for connections from the frontend for real-time data push
        self._frontend_ws_server = await websockets.serve(
            self._frontend_websocket_handler,
            self.listen_host, # Or a different host/port for frontend
            self.frontend_ws_port
        )
        logger.info(f"Frontend WebSocket server started on ws://{self.listen_host}:{self.frontend_ws_port}")


        # --- Query Device Info and Available Sensors ---
        await self.query_device_info()
        await self.query_available_sensors()
        # You might want to do something with this list here, e.g., log it or make it available via API

        # --- Establish and Maintain WebSocket Client Connection to Device ---
        # This will run concurrently with the servers
        self._device_client_task = asyncio.create_task(self._connect_and_receive_websocket_data())


        # Keep the DeviceManager running until the stop event is set
        await self._stop_event.wait()

    async def stop(self):
        """Stops the HTTP and WebSocket servers and client connections."""
        logger.info("Stopping DeviceManager servers and clients...")
        if self._stop_event:
            self._stop_event.set() # Signal the wait() to finish

        # Stop the DeviceManager's own servers
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            logger.info("DeviceManager WebSocket server stopped.")

        if self._http_runner:
            await self._http_runner.cleanup()
            logger.info("DeviceManager HTTP server stopped.")

        # Stop Frontend WebSocket Server
        if hasattr(self, '_frontend_ws_server') and self._frontend_ws_server:
            self._frontend_ws_server.close()
            await self._frontend_ws_server.wait_closed()
            logger.info("Frontend WebSocket server stopped.")

        # Cancel the WebSocket client connection task
        if self._device_client_task:
             self._device_client_task.cancel()
             try:
                  await self._device_client_task # Wait for it to finish cancelling
             except asyncio.CancelledError:
                  pass # Expected

        # Close the WebSocket client connection if it exists (should be closed by task cancellation)
        if self._device_websocket_client and not self._device_websocket_client.closed:
             await self._device_websocket_client.close()
             logger.info("Device WebSocket client connection closed.")


        logger.info("DeviceManager servers and clients stopped.")

    def get_device_details(self) -> Optional[Dict[str, Any]]:
        """Returns stored device info (name/model) and current status."""
        # Use getattr to safely access device_info, defaulting to empty dict
        device_info_dict = getattr(self, 'device_info', {})
        
        details = {
            "ip_address": self.device_host,
            "name": device_info_dict.get('name'),
            "model": device_info_dict.get('model'),
            "status": "unknown" # Default
        }
        
        # Determine status based on the websocket client state
        client = self._device_websocket_client
        if client and isinstance(client, websockets.WebSocketClientProtocol):
            # Check standard attributes for websockets client protocol
            if client.open:
                details['status'] = 'connected'
            elif client.closed:
                 details['status'] = 'disconnected'
            else:
                 # It might be connecting or closing, treat as unknown/disconnected for simplicity
                 details['status'] = 'disconnected' 
        else:
             # No valid client object exists
             details['status'] = 'disconnected'
             
        return details

    def push_realtime_update(self, data: Dict[str, Any]) -> None:
        """
        Thread-safe method to push data to connected frontend clients.
        Called by other modules (e.g., Collector) running in different threads.

        Args:
            data: The data to push (e.g., a data_point dictionary).
        """
        # Ensure the asyncio loop is available and the stop event is not set
        if self._loop and not self._stop_event.is_set():
            # Schedule the async _push_data_to_frontend coroutine on the asyncio loop
            # Use a try-except block to catch potential errors when scheduling
            try:
                asyncio.run_coroutine_threadsafe(self._push_data_to_frontend(data), self._loop)
            except RuntimeError as e:
                 # This can happen if the loop is already closing or closed
                 logger.warning(f"Failed to schedule frontend push coroutine: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error scheduling frontend push: {e}", exc_info=True)

        elif not self._loop:
             logger.warning("Asyncio loop not set in DeviceManager. Cannot push real-time updates.")
        # No warning if _frontend_websockets is empty, as there's no one to push to

