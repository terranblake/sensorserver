#!/usr/bin/env python3

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
import statistics # For median/stdev in fingerprint loading

# --- Configuration (Moved from all_sensors.py) ---
# Server details (Will be passed in or configured differently later)
# SERVER_ADDRESS = "10.0.0.2"
# HTTP_PORT = 9091
# WS_PORT = 8081
# HTTP_ENDPOINT = f"http://{SERVER_ADDRESS}:{HTTP_PORT}/sensors"
# WS_BASE_URI = f"ws://{SERVER_ADDRESS}:{WS_PORT}"
GPS_SEND_INTERVAL = 1 # Seconds between sending getLastKnownLocation
# LOG_FILE = "sensor_data.log" # Specific log files handled differently now
CALIBRATION_DATA_FILE = 'location_fingerprints.json'

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
# Specific logger for state changes - will be configured by the main application
state_data_logger = logging.getLogger("state_data")
state_data_logger.propagate = False

# --- Shared State ---
# Use a standard dict for nested structure
nested_sensor_data = {}
location_fingerprints = {} # For storing loaded location fingerprints
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
    """Determines a logical group for a sensor based on its name."""
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
    # Network sensors
    if 'wifi_scan' in name_lower: return 'network'
    if 'bluetooth_scan' in name_lower: return 'network'
    if 'network_scan' in name_lower: return 'network'
    # Fallback group
    return 'other'

def normalize_key(sensor_type):
    """Normalizes sensor type keys for consistent prefix."""
    if sensor_type == "gps":
        return "android.sensor.gps"
    elif sensor_type.startswith("com.google.sensor."):
        return sensor_type.replace("com.google.sensor.", "android.sensor.", 1)
    # Network sensors should already have the android.sensor prefix
    return sensor_type

def update_nested_data_with_grouping(data_dict, normalized_key_parts, value, is_status=False):
    """Updates the nested dictionary, inserting group and ensuring leaf is SensorState or status string."""
    if len(normalized_key_parts) < 3:
        logger.warning(f"Skipping update for short key: {normalized_key_parts}")
        return

    base_name = normalized_key_parts[-1]
    group = get_sensor_group(base_name)
    grouped_key_parts = normalized_key_parts[:-1] + [group, base_name]

    # Use the recursive update function, passing the status flag
    update_nested_data(data_dict, grouped_key_parts, value, is_status)

def update_nested_data(data_dict, key_parts, value, is_status=False):
    """Recursively updates, ensuring leaf node is SensorState object unless it's a status update."""
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
    """Initializes the nested dictionary ensuring leaf nodes are SensorState objects."""
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

    # Add entry for predicted location
    update_nested_data(nested_sensor_data, ['location', 'predicted'], initial_state)

# --- Location Prediction Functions (Adapted from sample/calibration) ---
def load_fingerprints(filename):
    """Loads location fingerprints from a JSON file."""
    global location_fingerprints # Modify the global dict
    logger.info(f"Attempting to load fingerprints from {filename}")
    try:
        with open(filename, 'r') as f:
            serializable_fingerprints = json.load(f)
            # Convert string keys back to tuple keys
            fingerprints = {}
            for location, networks_data in serializable_fingerprints.items():
                fingerprints[location] = {}
                for key_str, data in networks_data.items():
                    try:
                        ntype, nid = key_str.split('_', 1)
                        # Ensure data has expected keys, provide defaults if missing
                        data.setdefault('median_rssi', -999) # Default to very weak if missing
                        data.setdefault('std_dev_rssi', 100) # Default to high uncertainty if missing
                        fingerprints[location][(ntype, nid)] = data
                    except ValueError:
                        logger.warning(f"Skipping invalid fingerprint key format: {key_str} in location {location}")
                    except Exception as e:
                        logger.error(f"Error processing fingerprint key {key_str} for {location}: {e}")

            location_fingerprints = fingerprints # Update the global variable
            logger.info(f"Successfully loaded and processed fingerprints for {len(location_fingerprints)} locations from {filename}")
            return True # Indicate success
    except FileNotFoundError:
        logger.warning(f"Fingerprint file {filename} not found. Location prediction disabled.")
        location_fingerprints = {} # Ensure it's empty
        return False
    except json.JSONDecodeError as e:
        logger.error(f"Could not decode JSON from {filename}: {e}. Location prediction disabled.")
        location_fingerprints = {} # Ensure it's empty
        return False
    except Exception as e:
        logger.error(f"Unexpected error loading fingerprints from {filename}: {e}", exc_info=True)
        location_fingerprints = {} # Ensure it's empty
        return False

def calculate_similarity(current_network_data, location_fingerprint, missing_penalty_factor=1.0, extra_penalty_factor=0.5):
    """
    Calculates a similarity score between current network data and a location fingerprint.
    Lower score means higher similarity (like a distance metric).
    Uses standard deviation to weight the difference penalty.
    """
    score = 0.0
    # Convert current data to a dictionary for quick lookup: {(type, id): rssi}
    current_networks = {}
    for net in current_network_data:
        # Ensure type, id, and rssi are present and valid
        net_type = net.get('type')
        net_id = str(net.get('id')) # Ensure ID is string
        net_rssi = net.get('rssi')
        if net_type and net_id and isinstance(net_rssi, (int, float)):
            current_networks[(net_type, net_id)] = net_rssi
        else:
            logger.debug(f"Skipping invalid network data item in similarity calc: {net}")

    fingerprint_networks = location_fingerprint # This should already be {(type, id): {median_rssi: ..., std_dev_rssi: ...}}

    if not fingerprint_networks: # Handle empty fingerprint
        # If fingerprint is empty, score is high if current networks exist, low otherwise
        return len(current_networks) * abs(extra_penalty_factor * -75) # Penalty per extra network (avg RSSI)

    # Compare networks present in both
    matched_keys = 0
    for network_key, fingerprint_data in fingerprint_networks.items():
        # Safely access median and std_dev, providing defaults if missing
        best_fit_rssi = fingerprint_data.get('median_rssi', -999)
        std_dev_rssi = fingerprint_data.get('std_dev_rssi', 100)

        if network_key in current_networks:
            matched_keys += 1
            current_rssi = current_networks[network_key]
            # Calculate difference. Normalize by std dev if std dev > 0
            diff = abs(current_rssi - best_fit_rssi)
            # Weight the difference penalty inversely by stability (lower std dev = higher weight)
            # Add a small epsilon to avoid division by zero and reduce impact of tiny std dev
            weighted_diff = diff / (max(std_dev_rssi, 0.1) + 1e-6) # Use max(std_dev, 0.1) to avoid overly sensitive weighting
            score += weighted_diff ** 2 # Use squared weighted difference
            logger.debug(f"  Match {network_key}: Current={current_rssi}, Fingerprint={best_fit_rssi:.1f}, StdDev={std_dev_rssi:.1f}, WeightedDiff^2={weighted_diff**2:.2f}")
        else:
            # Penalty for networks expected at this location but not currently visible
            # A strong, stable expected signal that's missing is a higher penalty
            # Penalty increases as median_rssi is stronger (less negative)
            # Penalty increases as std_dev_rssi is lower (more stable)
            # Example: base penalty on median, scaled by inverse std dev
            base_penalty = max(0, 100 + best_fit_rssi) # Scale strength (e.g., -30dBm -> 70, -90dBm -> 10)
            stability_factor = 1 / (max(std_dev_rssi, 0.1) + 1e-6)
            penalty = (base_penalty * stability_factor) ** 1.5 # Apply power to emphasize
            score += penalty * missing_penalty_factor
            logger.debug(f"  Missing {network_key}: Fingerprint={best_fit_rssi:.1f}, StdDev={std_dev_rssi:.1f}, Penalty={penalty * missing_penalty_factor:.2f}")

    # Penalty for networks currently visible but not in the location's fingerprint
    extra_keys = 0
    for network_key, current_rssi in current_networks.items():
        if network_key not in fingerprint_networks:
            extra_keys += 1
            # Penalty for unexpected networks. Base penalty on signal strength.
            base_penalty = max(0, 100 + current_rssi)
            score += base_penalty * extra_penalty_factor
            logger.debug(f"  Extra {network_key}: Current={current_rssi}, Penalty={base_penalty * extra_penalty_factor:.2f}")

    # Optional: Normalization - reduce score if many networks matched well?
    # Or increase score if fingerprint has many networks but few were matched/extra?
    # Example: Normalize by total number of networks considered (fingerprint + extras)
    num_fingerprint_nets = len(fingerprint_networks)
    total_considered = num_fingerprint_nets + extra_keys
    if total_considered > 0:
        # Lower score slightly if the ratio of matched keys is high
        match_ratio = matched_keys / num_fingerprint_nets if num_fingerprint_nets > 0 else 0
        # score *= (1.0 - (match_ratio * 0.1)) # Mild adjustment based on match ratio
        pass # Keep normalization simple for now

    logger.debug(f"  Final Score: {score:.2f} (Matched: {matched_keys}, Missing: {num_fingerprint_nets-matched_keys}, Extra: {extra_keys})")
    return score

def predict_location(current_network_data):
    """
    Predicts the current location based on network data and loaded location fingerprints.
    Returns the name of the best matching location or None.
    """
    if not location_fingerprints:
        logger.debug("Prediction skipped: No location fingerprints loaded.")
        return None # No fingerprints loaded

    if not current_network_data:
        logger.debug("Prediction skipped: No current network data provided.")
        return None # Cannot predict without current data

    best_match_location = None
    min_score = float('inf')
    scores = {}

    logger.debug(f"Predicting location based on {len(current_network_data)} current networks...")
    for location, fingerprint in location_fingerprints.items():
        logger.debug(f"Calculating score for location: '{location}'")
        score = calculate_similarity(current_network_data, fingerprint)
        scores[location] = score
        logger.info(f"Location '{location}' score: {score:.2f}")

        if score < min_score:
            min_score = score
            best_match_location = location

    # Basic confidence check: score needs to be below a threshold, or significantly better than the next best
    # This requires tuning based on observed scores
    CONFIDENCE_THRESHOLD = 500 # Example threshold - NEEDS TUNING
    SIGNIFICANT_DIFFERENCE = 1.5 # Example: Best score must be 1.5x lower than second best

    if best_match_location and min_score < CONFIDENCE_THRESHOLD:
        sorted_scores = sorted(scores.items(), key=lambda item: item[1])
        if len(sorted_scores) > 1:
             second_best_score = sorted_scores[1][1]
             # Check if the best score is significantly better than the second best
             if min_score * SIGNIFICANT_DIFFERENCE < second_best_score:
                  logger.info(f"Predicted location: '{best_match_location}' (Score: {min_score:.2f}, Confident - significantly better than '{sorted_scores[1][0]}' score {second_best_score:.2f})")
                  return best_match_location
             else:
                  logger.info(f"Predicted location: '{best_match_location}' (Score: {min_score:.2f}, Low Confidence - similar to '{sorted_scores[1][0]}' score {second_best_score:.2f})")
                  return None # Low confidence due to similar scores
        else:
            # Only one location, prediction is confident by default if below threshold
            logger.info(f"Predicted location: '{best_match_location}' (Score: {min_score:.2f}, Confident - only location)")
            return best_match_location
    else:
        logger.info(f"No confident location prediction (Best: '{best_match_location}', Min Score: {min_score:.2f}, Threshold: {CONFIDENCE_THRESHOLD})")
        return None # Score too high or no best match found

# --- Inference Logic ---
def magnitude(vector):
    """Calculate the magnitude of a 3D vector."""
    if not isinstance(vector, list) or len(vector) < 3:
        return 0
    try:
        return math.sqrt(sum(x*x for x in vector[:3]))
    except (TypeError, ValueError):
        return 0

def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on the earth."""
    R = 6371e3  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2)**2 +
         math.cos(phi1) * math.cos(phi2) *
         math.sin(delta_lambda / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def update_inferred_state(normalized_sensor_path: str, state: SensorState):
    """Update inferred_state, logging changes if they occur."""
    now = datetime.now()
    # Extract base name for existing logic (e.g., 'accelerometer')
    base_name = normalized_sensor_path.split('.')[-1]
    previous_state = state.inferred_state # Store state before potential changes

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
        # Network sensor types
        elif base_name == 'wifi_scan':
            if isinstance(state.last_value, list):
                count = len(state.last_value) if state.last_value else 0
                state.inferred_state = f"WiFi Networks: {count}"
            else:
                state.inferred_state = "Scanning for WiFi networks..."
        elif base_name == 'bluetooth_scan':
            if isinstance(state.last_value, list):
                count = len(state.last_value) if state.last_value else 0
                state.inferred_state = f"Bluetooth Devices: {count}"
            else:
                state.inferred_state = "Scanning for Bluetooth devices..."
        elif base_name == 'network_scan':
            if isinstance(state.last_value, dict):
                wifi_count = len(state.last_value.get('wifiResults', [])) 
                bt_count = len(state.last_value.get('bluetoothResults', []))
                state.inferred_state = f"WiFi: {wifi_count}, BT: {bt_count}"
            else:
                state.inferred_state = "Scanning for networks..."
        # --- Generic State for Others --- (Only update if not handled above and not an event sensor)
        elif not is_event_sensor and state.inferred_state.startswith("Waiting"): # Only set once
             state.inferred_state = "Receiving Data"
        # If none of the above matched, and it wasn't reset, keep the current state
        # This prevents overwriting specific states with "Receiving Data" on subsequent updates

    except Exception as e:
        logger.error(f"Error during inference for {base_name}: {e}", exc_info=True)
        state.inferred_state = "[Inference Error]"

    # --- State Change Logging ---
    new_state = state.inferred_state
    if previous_state != new_state:
        log_entry = {
            "timestamp": now.isoformat(),
            "sensor_path": normalized_sensor_path,
            "previous_state": previous_state,
            "new_state": new_state
        }
        state_data_logger.info(json.dumps(log_entry))

# --- Sensor Discovery ---
async def get_available_sensors(http_url):
    logger.info(f"Attempting to fetch sensor list from {http_url}")
    sensor_types = []
    try:
        # Increase default timeout slightly
        async with aiohttp.ClientSession() as session:
            async with session.get(http_url, timeout=20) as response: # Increased timeout to 20s
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
        # Log the specific exception
        logger.error(f"Unexpected error fetching sensor list: {e}", exc_info=True)
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
            # Add explicit open_timeout
            async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20, open_timeout=20) as websocket: # Added open_timeout=20
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
            # Also update location prediction status
            update_nested_data(nested_sensor_data, ['location', 'predicted'], status, is_status=True)

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

                # --- Handle Location Prediction --- START
                # Check if this is a network scan type that can be used for prediction
                is_predictive_scan = (
                     normalized_sensor_type == 'android.sensor.wifi_scan' or
                     normalized_sensor_type == 'android.sensor.network_scan'
                )

                current_scan_data_for_prediction = []
                if is_predictive_scan:
                    # Extract network data in the format required by calculate_similarity
                    # This logic is similar to find_closest_network_data but simpler as it acts on a single message
                    if normalized_sensor_type == 'android.sensor.wifi_scan' and isinstance(raw_values, list):
                        current_scan_data_for_prediction.extend([
                            {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                            for res in raw_values if isinstance(res, dict) and res.get('bssid') and res.get('rssi') is not None
                        ])
                    elif normalized_sensor_type == 'android.sensor.network_scan' and isinstance(raw_values, dict):
                        wifi_results = raw_values.get('wifiResults', [])
                        bluetooth_results = raw_values.get('bluetoothResults', [])
                        current_scan_data_for_prediction.extend([
                            {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                            for res in wifi_results if isinstance(res, dict) and res.get('bssid') and res.get('rssi') is not None
                        ])
                        current_scan_data_for_prediction.extend([
                            {'type': 'bluetooth', 'id': res.get('address'), 'name': res.get('name'), 'rssi': res.get('rssi')}
                            for res in bluetooth_results if isinstance(res, dict) and res.get('address') and res.get('rssi') is not None
                        ])
                    logger.debug(f"Extracted {len(current_scan_data_for_prediction)} networks for prediction from {normalized_sensor_type}")

                # --- Handle standard sensor value parsing --- START
                if isinstance(raw_values, list):
                    try:
                        parsed_values = [float(v) for v in raw_values]
                    except (ValueError, TypeError):
                        parsed_values = raw_values # Keep as is if conversion fails
                elif raw_values is not None:
                     # Attempt conversion for single non-list values too
                    try: parsed_values = [float(raw_values)]
                    except (ValueError, TypeError): parsed_values = [str(raw_values)] # Fallback to string list
                # --- Handle standard sensor value parsing --- END

                state.previous_value = state.last_value
                state.previous_timestamp = state.last_timestamp
                # For network scans used in prediction, last_value could be the raw dict/list or parsed count
                # For prediction, we use current_scan_data_for_prediction. For display/state, use parsed_values.
                state.last_value = raw_values if is_predictive_scan else parsed_values
                state.last_timestamp = timestamp
                # Pass the full normalized path instead of just the base name
                update_inferred_state(normalized_sensor_type, state)

                # --- Perform and Update Location Prediction --- START
                if is_predictive_scan and current_scan_data_for_prediction:
                    predicted_loc = predict_location(current_scan_data_for_prediction)
                    # Find the location.predicted state node
                    loc_node = nested_sensor_data.get('location', {}).get('predicted')
                    if isinstance(loc_node, SensorState):
                         loc_node.previous_value = loc_node.last_value
                         loc_node.previous_timestamp = loc_node.last_timestamp
                         loc_node.last_value = predicted_loc if predicted_loc else "Unknown"
                         loc_node.last_timestamp = timestamp
                         loc_node.inferred_state = predicted_loc if predicted_loc else "Unknown"
                         # Log state change for location
                         log_entry = {
                             "timestamp": timestamp.isoformat(),
                             "sensor_path": "location.predicted",
                             "previous_state": loc_node.previous_value if loc_node.previous_value else "Unknown",
                             "new_state": loc_node.last_value
                         }
                         state_data_logger.info(json.dumps(log_entry))
                    else:
                        logger.warning("Could not find location.predicted SensorState node to update.")
                # --- Perform and Update Location Prediction --- END

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
            # Add explicit open_timeout
            async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20, open_timeout=20) as websocket: # Added open_timeout=20
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
            self.logger.error(f"Failed GPS connection to {self.uri}: {e}", exc_info=True)
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
                # Pass the full normalized path instead of just the base name
                update_inferred_state(normalized_sensor_type, state)
            else:
                 self.logger.warning(f"Could not find/update SensorState node for {grouped_parts}. Final check failed.")

        except json.JSONDecodeError:
            self.logger.error(f"[GPS] Failed to parse JSON: {message}")
        except Exception as e:
            self.logger.error(f"[GPS] Error in handle_message: {e}", exc_info=True)

# --- Main Sensor Client Execution Logic ---
async def run_sensor_clients(http_endpoint, ws_base_uri):
    """Runs the sensor discovery and websocket client tasks."""
    # Logging is now configured by the calling application (e.g., server.py)
    logger.info("--- Starting Sensor Logic Clients ---")

    # Initialize nested data structure (important before discovery attempts)
    global nested_sensor_data
    nested_sensor_data = {} # Ensure clean state if function is ever recalled

    # Load fingerprints before initializing keys/starting clients
    fingerprints_loaded = load_fingerprints(CALIBRATION_DATA_FILE)
    if not fingerprints_loaded:
        logger.warning("Fingerprints failed to load. Location prediction will be disabled.")

    standard_sensor_types = await get_available_sensors(http_endpoint)
    # Note: get_available_sensors calls initialize_nested_keys, which now includes location.predicted

    tasks = []
    if standard_sensor_types:
        multi_client = MultiSensorClient(base_uri=ws_base_uri, sensor_types=standard_sensor_types)
        tasks.append(asyncio.create_task(multi_client.connect_and_receive(), name="MultiSensorClient"))
    else:
        logger.warning("No standard sensors discovered or an error occurred. Skipping multi-sensor client.")

    gps_client = GpsClient(base_uri=ws_base_uri)
    tasks.append(asyncio.create_task(gps_client.connect_and_receive(), name="GpsClient"))

    if not tasks:
        logger.error("Could not create any client tasks. Sensor logic will exit.")
        return

    logger.info(f"Starting {len(tasks)} sensor client tasks...")
    # Wait for any client task to complete (usually indicates an error or disconnect)
    # This keeps the function running until a client stops/errors
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in done:
        try:
            result = task.result()
            logger.info(f"Sensor task {task.get_name()} finished unexpectedly with result: {result}")
        except asyncio.CancelledError:
             logger.info(f"Sensor task {task.get_name()} was cancelled.")
        except Exception as e:
            logger.error(f"Sensor task {task.get_name()} raised exception: {e}", exc_info=True)

    logger.warning(f"First sensor task completed. Cancelling {len(pending)} pending tasks...")
    for task in pending:
        task.cancel()
        try:
            await task # Wait for cancellation to complete
        except asyncio.CancelledError:
            pass # Expected
        except Exception as e:
             logger.error(f"Error during cancellation of task {task.get_name()}: {e}", exc_info=True)


    logger.info("All sensor client tasks have completed or been cancelled.") 