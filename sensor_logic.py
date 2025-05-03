import asyncio
import websockets
import json
import logging
import aiohttp # For async HTTP requests
from urllib.parse import urlencode # For encoding URL parameters
import time # For ISO timestamp
from datetime import datetime, timedelta # For ISO timestamp and timedelta
import math # For vector magnitude
import os # For file handler path

# --- Configuration (Moved from all_sensors.py) ---
# Server details (Will be passed in or configured differently later)
# SERVER_ADDRESS = "10.0.0.2"
# HTTP_PORT = 9091
# WS_PORT = 8081
# HTTP_ENDPOINT = f"http://{SERVER_ADDRESS}:{HTTP_PORT}/sensors"
# WS_BASE_URI = f"ws://{SERVER_ADDRESS}:{WS_PORT}"
GPS_SEND_INTERVAL = 1 # Seconds between sending getLastKnownLocation
# LOG_FILE = "sensor_data.log" # Specific log files handled differently now

# Inference Thresholds
MOTION_MAGNITUDE_THRESHOLD = 0.3 # For accelerometer magnitude
ROTATION_MAGNITUDE_THRESHOLD = 0.2 # For gyroscope magnitude
STATIONARY_GPS_SPEED_THRESHOLD = 0.1 # m/s
STATIONARY_GPS_DIST_THRESHOLD = 1.0 # meters between subsequent points
EVENT_RESET_TIME = timedelta(seconds=1.5) # How long event states like "Step Detected" persist
LIGHT_DARK_THRESHOLD = 10
LIGHT_DIM_THRESHOLD = 100
PROXIMITY_NEAR_THRESHOLD = 4.0 # Assume values < threshold are NEAR (specific to device)

# --- Logging Setup (Module Specific) ---
# Basic logger for this module
logger = logging.getLogger(__name__)
# Specific logger for raw data - will be configured by the main application
raw_data_logger = logging.getLogger("raw_data")
raw_data_logger.propagate = False # Prevent duplication if root logger is configured

# --- Shared State ---
# Use a standard dict for nested structure
nested_sensor_data = {}
# TODO: Consider passing this state or encapsulating it in a class for better management

# --- Sensor State Class ---
class SensorState:
    def __init__(self):
        self.last_value = None
        self.last_timestamp = None
        self.previous_value = None
        self.previous_timestamp = None
        self.inferred_state = "Waiting for data..."
        self.event_detected_time = None # For time-limited events
        # Optional: self.history = deque(maxlen=10) # Deque needs import 'collections'

# --- Helper Functions ---
def get_sensor_group(sensor_name):
    \"\"\"Determines a logical group for a sensor based on its name.\"\"\"
    name_lower = sensor_name.lower()
    # Prioritize specific keywords
    if 'accel' in name_lower: return 'motion'
    if 'gyro' in name_lower: return 'motion'
    if 'gravity' in name_lower: return 'motion'
    if 'linear_acceleration' in name_lower: return 'motion'
    if 'orientation' in name_lower: return 'position'
    if 'rotation_vector' in name_lower: return 'position'
    if 'gps' == name_lower: return 'position' # Match exact name for gps
    if 'light' in name_lower: return 'environment' # Covers light, rear_light
    if 'pressure' in name_lower: return 'environment'
    if 'proximity' in name_lower: return 'environment'
    if 'temp' in name_lower: return 'environment' # Covers pressure_temp, gyro_temperature
    if 'magnetic' in name_lower: return 'magnetic'
    if 'step' in name_lower: return 'activity'
    if 'tilt' in name_lower: return 'activity'
    if 'twist' in name_lower: return 'activity'
    if 'brightness' in name_lower: return 'brightness'
    if 'camera_vsync' in name_lower: return 'meta'
    if 'dynamic_sensor_meta' in name_lower: return 'meta'
    # Fallback group
    return 'other'

def normalize_key(sensor_type):
    \"\"\"Normalizes sensor type keys for consistent prefix.\"\"\"
    if sensor_type == "gps":
        return "android.sensor.gps"
    elif sensor_type.startswith("com.google.sensor."):
        return sensor_type.replace("com.google.sensor.", "android.sensor.", 1)
    return sensor_type

def update_nested_data_with_grouping(data_dict, normalized_key_parts, value, is_status=False):
    \"\"\"Updates the nested dictionary, inserting group and ensuring leaf is SensorState or status string.\"\"\"
    if len(normalized_key_parts) < 3:
        logger.warning(f"Skipping update for short key: {normalized_key_parts}")
        return

    base_name = normalized_key_parts[-1]
    group = get_sensor_group(base_name)
    grouped_key_parts = normalized_key_parts[:-1] + [group, base_name]

    # Use the recursive update function, passing the status flag
    update_nested_data(data_dict, grouped_key_parts, value, is_status)

def update_nested_data(data_dict, key_parts, value, is_status=False):
    \"\"\"Recursively updates, ensuring leaf node is SensorState object unless it's a status update.\"\"\"
    key = key_parts[0]
    if len(key_parts) == 1:
        if is_status:
             # If it's a status update (like error), just set the string directly if node exists
             # Or if the node doesn't exist yet, create it with the status.
             if key not in data_dict or not isinstance(data_dict[key], SensorState):
                  data_dict[key] = SensorState() # Create state object first
             data_dict[key].inferred_state = value # Set status string
        else:
            # If it's actual sensor data, ensure the node is a SensorState object
            if key not in data_dict or not isinstance(data_dict[key], SensorState):
                data_dict[key] = SensorState()
            # Update the state object's attributes (done in handle_message)
            # Here we just ensure the object exists
    else:
        if key not in data_dict or not isinstance(data_dict[key], dict):
            data_dict[key] = {}
        # Pass is_status down recursively
        update_nested_data(data_dict[key], key_parts[1:], value, is_status)

def initialize_nested_keys(sensor_types):
    \"\"\"Initializes the nested dictionary ensuring leaf nodes are SensorState objects.\"\"\"
    initial_state = SensorState() # Use the class for initial value
    all_types = set(sensor_types) | {"gps"}

    for sensor_type in all_types:
        normalized = normalize_key(sensor_type)
        parts = normalized.split('.')
        if len(parts) >= 3:
            # Initialize with the SensorState object
            update_nested_data_with_grouping(nested_sensor_data, parts, initial_state)
        elif parts and parts[0]:
             logger.warning(f"Initializing short/unexpected key: {parts}")
             update_nested_data(nested_sensor_data, parts, initial_state)

# --- Inference Logic ---
def magnitude(vector):
    \"\"\"Calculate the magnitude of a 3D vector.\"\"\"
    if not isinstance(vector, list) or len(vector) < 3:
        return 0
    try:
        return math.sqrt(sum(x*x for x in vector[:3]))
    except (TypeError, ValueError):
        return 0

def haversine(lat1, lon1, lat2, lon2):
    \"\"\"Calculate the great-circle distance between two points on the earth.\"\"\"
    R = 6371e3  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2)**2 + \\
        math.cos(phi1) * math.cos(phi2) * \\
        math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def update_inferred_state(sensor_name, state: SensorState):
    \"\"\"Update inferred_state for ACTIONABLE sensors, generic state for others.\"\"\"
    now = datetime.now()
    base_name = sensor_name.lower()

    # Handle event reset for event-based sensors
    is_event_sensor = any(evt in base_name for evt in ["detector", "twist"])
    if is_event_sensor and state.event_detected_time and (now - state.event_detected_time > EVENT_RESET_TIME):
        state.inferred_state = "Idle"
        state.event_detected_time = None
        # If we just reset, no further processing needed for this sensor type
        return
    elif is_event_sensor and state.inferred_state == "Idle":
        # Don't process event sensors further if they are idle
        return

    # Ensure we have data to process
    if state.last_value is None:
        # Keep "Waiting for data..." or error state if no value received yet
        return

    # --- Actionable/Interpretable Sensor Logic ---
    try:
        if base_name == 'accelerometer' or base_name == 'linear_acceleration':
            mag = magnitude(state.last_value)
            state.inferred_state = f"Moving/Shaking (Mag: {mag:.2f})" if mag > MOTION_MAGNITUDE_THRESHOLD else "Stationary"
        elif base_name == 'gyroscope':
            mag = magnitude(state.last_value)
            state.inferred_state = f"Rotating (Mag: {mag:.2f})" if mag > ROTATION_MAGNITUDE_THRESHOLD else "Not Rotating"
        elif base_name == 'step_detector' or base_name == 'tilt_detector' or base_name == 'double_twist':
            # Event detectors often send 1.0 on event
            if state.last_value[0] == 1.0:
                event_name = base_name.replace("_", " ").replace("detector", "Detected").title()
                state.inferred_state = event_name
                state.event_detected_time = now # Set/reset event time
            # (Idle state handled by reset logic above)
        elif base_name == 'light':
            val = state.last_value[0]
            if val < LIGHT_DARK_THRESHOLD: state.inferred_state = f"Dark ({val:.1f} lx)"
            elif val < LIGHT_DIM_THRESHOLD: state.inferred_state = f"Dim ({val:.1f} lx)"
            else: state.inferred_state = f"Bright ({val:.1f} lx)"
        elif base_name == 'proximity':
             val = state.last_value[0]
             state.inferred_state = "Near" if val < PROXIMITY_NEAR_THRESHOLD else "Far"
        elif base_name == 'step_counter':
            state.inferred_state = f"Count: {state.last_value[0]:.0f}"
        elif base_name == 'gyro_temperature' or base_name == 'pressure_temp':
             state.inferred_state = f"{state.last_value[0]:.1f} C"
        elif base_name == 'pressure':
             state.inferred_state = f"{state.last_value[0]:.1f} hPa"
        elif base_name == 'gps':
            if state.previous_value and state.last_timestamp and state.previous_timestamp:
                lat1, lon1 = state.previous_value.get('latitude'), state.previous_value.get('longitude')
                lat2, lon2 = state.last_value.get('latitude'), state.last_value.get('longitude')
                speed = state.last_value.get('speed', 0.0)
                if all(isinstance(v, (int, float)) for v in [lat1, lon1, lat2, lon2, speed]):
                    dist = haversine(lat1, lon1, lat2, lon2)
                    time_diff = (state.last_timestamp - state.previous_timestamp).total_seconds()
                    if speed > STATIONARY_GPS_SPEED_THRESHOLD:
                         state.inferred_state = f"Moving ({speed:.1f} m/s)"
                    elif time_diff > 0 and (dist / time_diff) > STATIONARY_GPS_SPEED_THRESHOLD:
                         state.inferred_state = f"Moving (Dist: {dist:.1f}m)"
                    else:
                        state.inferred_state = f"Stationary (Acc: {state.last_value.get('accuracy', 'N/A'):.0f}m)"
                else:
                     state.inferred_state = "Waiting for valid GPS coords"
            else: # Only current value
                state.inferred_state = f"GPS Fix (Acc: {state.last_value.get('accuracy', 'N/A'):.0f}m)"
        # --- Generic State for Others --- (Only update if not handled above and not an event sensor)
        elif not is_event_sensor and state.inferred_state.startswith("Waiting"): # Only set once
             state.inferred_state = "Receiving Data"
        # If none of the above matched, and it wasn't reset, keep the current state
        # This prevents overwriting specific states with "Receiving Data" on subsequent updates

    except Exception as e:
        logger.error(f"Error during inference for {sensor_name}: {e}", exc_info=True)
        state.inferred_state = "[Inference Error]"

# --- Sensor Discovery ---
async def get_available_sensors(http_url):
    logger.info(f"Attempting to fetch sensor list from {http_url}")
    sensor_types = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(http_url, timeout=10) as response:
                if response.status == 200:
                    sensors_data = await response.json(content_type=None)
                    sensor_types = [sensor.get('type') for sensor in sensors_data if sensor.get('type')]
                    if not sensor_types:
                         logger.warning(f"Sensor list received from {http_url} but it was empty or filtered to empty.")
                    else:
                         logger.info(f"Discovered standard sensors: {sensor_types}")
                else:
                    logger.error(f"Failed to fetch sensor list. HTTP status: {response.status}")
    except Exception as e:
        logger.error(f"Unexpected error fetching sensor list: {e}")
    finally:
        # Initialize keys AFTER discovery attempt, regardless of success
        # This ensures the structure exists even if discovery fails
        initialize_nested_keys(sensor_types)
        return sensor_types # Return discovered types for MultiSensorClient

# --- WebSocket Client Classes ---

class MultiSensorClient:
    def __init__(self, base_uri, sensor_types):
        self.sensor_types = sensor_types # Keep original list for error handling
        types_json_string = json.dumps(self.sensor_types)
        query_params = urlencode({"types": types_json_string})
        self.uri = f"{base_uri}/sensors/connect?{query_params}"
        self.logger = logging.getLogger(f"{__name__}.MultiSensorClient")
        self.logger.debug(f"Initialized multi-sensor client for URI: {self.uri}")

    async def connect_and_receive(self):
        self.logger.info(f"Attempting multi-sensor connection to {self.uri}")
        status = "[Unknown Error]" # Default status
        try:
            async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as websocket:
                self.logger.info(f"Successfully connected multi-sensor socket to {self.uri}")
                try:
                    async for message in websocket:
                        self.handle_message(message)
                    # If loop exits cleanly, it means server closed OK
                    status = "[Disconnected]"
                    self.logger.info(f"Multi-sensor connection closed cleanly ({self.uri})")
                except websockets.exceptions.ConnectionClosedError as e:
                    self.logger.error(f"Multi-sensor connection error ({self.uri}): {e}")
                    status = f"[Error: ConnectionClosed]"
                except Exception as e:
                    self.logger.error(f"Multi-sensor unexpected error processing message ({self.uri}): {e}")
                    status = "[Processing Error]"
        except Exception as e:
            self.logger.error(f"Failed multi-sensor connection to {self.uri}: {e}")
            status = f"[Connection Failed]"
        finally:
            self.logger.warning(f"Multi-sensor connection attempt finished ({self.uri})")
            # Update status for all original sensors on disconnect/error/failure
            for s_type in self.sensor_types:
                 normalized = normalize_key(s_type)
                 parts = normalized.split('.')
                 if len(parts) >= 3:
                      # Update status via is_status=True flag
                      update_nested_data_with_grouping(nested_sensor_data, parts, status, is_status=True)
                 elif parts and parts[0]:
                      update_nested_data(nested_sensor_data, parts, status, is_status=True)

    def handle_message(self, message):
        try:
            timestamp = datetime.now()
            data = json.loads(message)
            original_sensor_type = data.get('type', 'Unknown Type')

            # Use the raw_data_logger from this module
            log_entry = {"timestamp": timestamp.isoformat(), "sensor_type": original_sensor_type, "raw_data": data}
            raw_data_logger.info(json.dumps(log_entry))

            normalized_sensor_type = normalize_key(original_sensor_type)
            parts = normalized_sensor_type.split('.')
            if len(parts) < 3:
                 self.logger.warning(f"Skipping update for short key in message: {parts}")
                 return

            # Find the correct SensorState node
            current_node = nested_sensor_data
            base_name = parts[-1]
            group = get_sensor_group(base_name)
            grouped_parts = parts[:-1] + [group, base_name]
            node_found = True
            for part in grouped_parts:
                if part in current_node and isinstance(current_node[part], (dict, SensorState)):
                     is_leaf_part = (part == grouped_parts[-1])
                     current_node_part = current_node[part]
                     if isinstance(current_node_part, SensorState) and not is_leaf_part:
                          self.logger.warning(f" Path conflict: Found SensorState at non-leaf part '{part}' in {grouped_parts}")
                          node_found = False; break
                     if is_leaf_part and isinstance(current_node_part, dict):
                           self.logger.warning(f" Path conflict: Found dict at leaf part '{part}' in {grouped_parts}")
                           node_found = False; break
                     current_node = current_node_part
                else:
                    self.logger.warning(f" Part '{part}' not found or wrong type in node at path {grouped_parts[:grouped_parts.index(part)]}. Keys: {list(current_node.keys())}")
                    node_found = False; break

            if node_found and isinstance(current_node, SensorState):
                state = current_node
                parsed_values = None
                raw_values = data.get('values')
                if isinstance(raw_values, list):
                    try:
                        parsed_values = [float(v) for v in raw_values]
                    except (ValueError, TypeError):
                        parsed_values = raw_values # Keep as is if conversion fails
                elif raw_values is not None:
                     # Attempt conversion for single non-list values too
                    try: parsed_values = [float(raw_values)]
                    except (ValueError, TypeError): parsed_values = [str(raw_values)] # Fallback to string list

                state.previous_value = state.last_value
                state.previous_timestamp = state.last_timestamp
                state.last_value = parsed_values
                state.last_timestamp = timestamp
                update_inferred_state(base_name, state)
            else:
                self.logger.warning(f"Could not find/update SensorState node for {grouped_parts}. Final check failed.")

        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse JSON: {message}")
        except Exception as e:
            self.logger.error(f"Error in handle_message (MultiSensor): {e}", exc_info=True)

class GpsClient:
    def __init__(self, base_uri):
        self.uri = f"{base_uri}/gps"
        self.websocket = None
        self._send_task = None
        self.logger = logging.getLogger(f"{__name__}.GpsClient")
        self.logger.debug(f"Initialized GPS client for URI: {self.uri}")

    async def _send_location_requests(self):
        try:
            while self.websocket:
                try:
                    # Use the module logger
                    self.logger.info("Sending getLastKnownLocation request")
                    await self.websocket.send("getLastKnownLocation")
                    await asyncio.sleep(GPS_SEND_INTERVAL)
                except websockets.exceptions.ConnectionClosed:
                    self.logger.warning("GPS send loop detected connection closed.")
                    break
                except Exception as e:
                     self.logger.error(f"Error sending GPS location request: {e}")
                     await asyncio.sleep(GPS_SEND_INTERVAL) # Still wait before retrying
            self.logger.info("GPS send location request task finished.")
        except asyncio.CancelledError:
             self.logger.info("GPS send location request task cancelled.")

    async def connect_and_receive(self):
        self.logger.info(f"Attempting GPS connection to {self.uri}")
        normalized_gps_key = normalize_key("gps")
        gps_parts = normalized_gps_key.split('.')
        status = "[Unknown Error]"

        try:
            async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as websocket:
                self.websocket = websocket
                self.logger.info(f"Successfully connected GPS socket to {self.uri}")
                self._send_task = asyncio.create_task(self._send_location_requests())
                try:
                    async for message in self.websocket:
                        self.handle_message(message)
                    status = "[Disconnected]"
                    self.logger.info(f"GPS connection closed cleanly ({self.uri})")
                except websockets.exceptions.ConnectionClosedError as e:
                    self.logger.error(f"GPS connection error ({self.uri}): {e}")
                    status = f"[Error: ConnectionClosed]"
                except Exception as e:
                    self.logger.error(f"GPS unexpected error processing message ({self.uri}): {e}")
                    status = "[Processing Error]"
                finally:
                    if self._send_task and not self._send_task.done():
                        self._send_task.cancel()
                        try:
                            await asyncio.wait_for(self._send_task, timeout=1)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            pass # Expected if it was cancelled
                    self.websocket = None
        except Exception as e:
            self.logger.error(f"Failed GPS connection to {self.uri}: {e}")
            status = f"[Connection Failed]"
        finally:
             self.logger.warning(f"GPS connection attempt finished ({self.uri})")
             # Update GPS status on disconnect/error/failure
             if len(gps_parts) >= 3:
                  # Update status via is_status=True flag
                  update_nested_data_with_grouping(nested_sensor_data, gps_parts, status, is_status=True)
             elif gps_parts and gps_parts[0]:
                  update_nested_data(nested_sensor_data, gps_parts, status, is_status=True)
             # Ensure task is cancelled if connect fails early
             if self._send_task and not self._send_task.done():
                 self._send_task.cancel()


    def handle_message(self, message):
        try:
            timestamp = datetime.now()
            data = json.loads(message)
            original_sensor_type = "gps"

            # Use the raw_data_logger from this module
            log_entry = {"timestamp": timestamp.isoformat(), "sensor_type": original_sensor_type, "raw_data": data}
            raw_data_logger.info(json.dumps(log_entry))

            normalized_sensor_type = normalize_key(original_sensor_type)
            parts = normalized_sensor_type.split('.')
            if len(parts) < 3:
                 self.logger.warning(f"Skipping GPS update for short key: {parts}")
                 return

            # Find the correct SensorState node
            current_node = nested_sensor_data
            base_name = parts[-1]
            group = get_sensor_group(base_name)
            grouped_parts = parts[:-1] + [group, base_name]
            node_found = True
            for part in grouped_parts:
                if part in current_node and isinstance(current_node[part], (dict, SensorState)):
                     is_leaf_part = (part == grouped_parts[-1])
                     current_node_part = current_node[part]
                     if isinstance(current_node_part, SensorState) and not is_leaf_part:
                          self.logger.warning(f" Path conflict: Found SensorState at non-leaf part '{part}' in {grouped_parts}")
                          node_found = False; break
                     if is_leaf_part and isinstance(current_node_part, dict):
                           self.logger.warning(f" Path conflict: Found dict at leaf part '{part}' in {grouped_parts}")
                           node_found = False; break
                     current_node = current_node_part
                else:
                    self.logger.warning(f" Part '{part}' not found or wrong type in node at path {grouped_parts[:grouped_parts.index(part)]}. Keys: {list(current_node.keys())}")
                    node_found = False; break

            if node_found and isinstance(current_node, SensorState):
                state = current_node
                parsed_values = data # GPS data is the whole dict

                state.previous_value = state.last_value
                state.previous_timestamp = state.last_timestamp
                state.last_value = parsed_values
                state.last_timestamp = timestamp
                update_inferred_state(base_name, state)
            else:
                 self.logger.warning(f"Could not find/update SensorState node for {grouped_parts}. Final check failed.")

        except json.JSONDecodeError:
            self.logger.error(f"[GPS] Failed to parse JSON: {message}")
        except Exception as e:
            self.logger.error(f"[GPS] Error in handle_message: {e}", exc_info=True)

# --- Example Main Execution (for testing) ---
async def run_standalone(http_endpoint, ws_base_uri, raw_log_file="raw_data.log"):
    \"\"\"Runs the sensor logic independently for testing purposes.\"\"\"
    # Configure logging for standalone run
    log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console_handler])

    # Configure raw data logger specifically
    # Ensure directory exists if path includes one
    log_dir = os.path.dirname(raw_log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    file_handler = logging.FileHandler(raw_log_file, mode='a')
    file_handler.setFormatter(logging.Formatter('%(message)s')) # Only message for raw data
    raw_data_logger.addHandler(file_handler)
    raw_data_logger.setLevel(logging.INFO)

    logger.info("--- Starting Sensor Logic Standalone ---")

    # Initialize nested data structure (important before discovery attempts)
    global nested_sensor_data
    nested_sensor_data = {} # Reset state for standalone run

    standard_sensor_types = await get_available_sensors(http_endpoint)
    # Note: get_available_sensors calls initialize_nested_keys

    tasks = []
    if standard_sensor_types:
        multi_client = MultiSensorClient(base_uri=ws_base_uri, sensor_types=standard_sensor_types)
        tasks.append(asyncio.create_task(multi_client.connect_and_receive(), name="MultiSensorClient"))
    else:
        logger.warning("No standard sensors discovered or an error occurred. Skipping multi-sensor client.")

    gps_client = GpsClient(base_uri=ws_base_uri)
    tasks.append(asyncio.create_task(gps_client.connect_and_receive(), name="GpsClient"))

    if not tasks:
        logger.error("Could not create any client tasks. Exiting.")
        return

    logger.info(f"Starting {len(tasks)} client tasks...")
    # Wait for any client task to complete (usually indicates an error or disconnect)
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in done:
        try:
            result = task.result()
            logger.info(f"Task {task.get_name()} finished with result: {result}")
        except asyncio.CancelledError:
             logger.info(f"Task {task.get_name()} was cancelled.")
        except Exception as e:
            logger.error(f"Task {task.get_name()} raised exception: {e}", exc_info=True)

    logger.info(f"First task completed. Cancelling {len(pending)} pending tasks...")
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    logger.info("All tasks have completed or been cancelled.")

if __name__ == "__main__":
    # Example usage: Replace with actual server details
    TEST_SERVER_ADDRESS = "10.0.0.2" # Or get from env/config
    TEST_HTTP_PORT = 9091
    TEST_WS_PORT = 8081
    TEST_HTTP_ENDPOINT = f"http://{TEST_SERVER_ADDRESS}:{TEST_HTTP_PORT}/sensors"
    TEST_WS_BASE_URI = f"ws://{TEST_SERVER_ADDRESS}:{TEST_WS_PORT}"
    TEST_RAW_LOG = "sensor_logic_raw_test.log"

    try:
        asyncio.run(run_standalone(TEST_HTTP_ENDPOINT, TEST_WS_BASE_URI, TEST_RAW_LOG))
    except KeyboardInterrupt:
        logger.info("Standalone run stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled exception in standalone run: {e}", exc_info=True)
    finally:
        logger.info("Standalone run finished.") 