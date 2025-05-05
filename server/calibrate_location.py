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
import sys

# --- Configuration (Moved from all_sensors.py) ---
# Server details (Will be passed in or configured differently later)
# SERVER_ADDRESS = "10.0.0.2"
# HTTP_PORT = 9091
# WS_PORT = 8081
# HTTP_ENDPOINT = f"http://{SERVER_ADDRESS}:{HTTP_PORT}/sensors"
# WS_BASE_URI = f"ws://{SERVER_ADDRESS}:{WS_PORT}"
GPS_SEND_INTERVAL = 1 # Seconds between sending getLastKnownLocation

# Sensor type constants
PRESSURE_SENSOR_TYPE = 'android.sensor.pressure'

# Construct path relative to the expected execution directory of server.py (project root)
CALIBRATION_DATA_FILE = os.path.join('server', 'location_fingerprints.json')

# Inference Thresholds
MOTION_MAGNITUDE_THRESHOLD = 0.3 # For accelerometer magnitude
ROTATION_MAGNITUDE_THRESHOLD = 0.2 # For gyroscope magnitude
STATIONARY_GPS_SPEED_THRESHOLD = 0.1 # m/s
STATIONARY_GPS_DIST_THRESHOLD = 1.0 # meters between subsequent points
EVENT_RESET_TIME = timedelta(seconds=1.5) # How long event states like "Step Detected" persist
LIGHT_DARK_THRESHOLD = 10
LIGHT_DIM_THRESHOLD = 100
PROXIMITY_NEAR_THRESHOLD = 4.0 # Assume values < threshold are NEAR (specific to device)

# Basic confidence check: score needs to be below a threshold, or significantly better than the next best
    # This requires tuning based on observed scores
CONFIDENCE_THRESHOLD = 500 # Example threshold - NEEDS TUNING
SIGNIFICANT_DIFFERENCE = 1.5 # Example: Best score must be 1.5x lower than second best

# --- Logging Setup (Module Specific) ---
# Basic logger for this module
logger = logging.getLogger(__name__)
# Specific logger for raw data - will be configured by the main application
raw_data_logger = logging.getLogger("raw_data")
raw_data_logger.propagate = False # Prevent duplication if root logger is configured
# Specific logger for state changes - will be configured by the main application
state_data_logger = logging.getLogger("state_data")
state_data_logger.propagate = False

# Import the shared event from the server module
# This assumes server.py is run from the project root and sensor_logic is in a 'server' subdir
# Adjust the import if the structure is different
try:
    from server import auto_logging_event, event_data_logger # Import event and logger
except ImportError as e:
    # Fallback if run standalone or structure changes - create a dummy event/logger
    logger.warning(f"Could not import from server module ({e}). Auto-event logging disabled.")
    import threading
    auto_logging_event = threading.Event() # Dummy event, always False
    event_data_logger = logging.getLogger("dummy_event_data") # Dummy logger
    event_data_logger.addHandler(logging.NullHandler())

# --- Shared State ---
# Use a standard dict for nested structure
nested_sensor_data = {}
location_fingerprints = {} # For storing loaded location fingerprints
# Store the latest data needed for on-demand score calculation
latest_network_data_for_scoring = []
latest_pressure_for_scoring = None
latest_data_timestamp = None

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
            # Convert string keys back to tuple keys for network data
            fingerprints = {}
            for location, data in serializable_fingerprints.items():
                fingerprints[location] = {}
                # Process network fingerprints
                for key_str, net_data in data.items():
                    if key_str != "pressure_value": # Exclude pressure for tuple key conversion
                        try:
                            ntype, nid = key_str.split('_', 1)
                            # Ensure network data has expected keys, provide defaults if missing
                            net_data.setdefault('median_value', -999) # Default to very weak if missing
                            net_data.setdefault('std_dev_value', 100) # Default to high uncertainty if missing
                            fingerprints[location][(ntype, nid)] = net_data
                        except ValueError:
                            logger.warning(f"Skipping invalid network fingerprint key format: {key_str} in location {location}")
                        except Exception as e:
                            logger.error(f"Error processing network fingerprint key {key_str} for {location}: {e}")
                # Process pressure fingerprint separately, keeping string key
                if "pressure_value" in data:
                     # Ensure pressure data has expected keys, provide defaults if missing
                     pressure_data = data["pressure_value"]
                     pressure_data.setdefault('median_value', 1013.25) # Default to standard pressure
                     pressure_data.setdefault('std_dev_value', 5.0) # Default std dev
                     fingerprints[location]["pressure_value"] = pressure_data


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

# Maximum age (seconds) for a pressure reading to be considered current for prediction
MAX_PRESSURE_AGE_SECONDS = 5

def calculate_similarity(current_network_data, current_pressure_value, location_fingerprint, network_weight=1.0, pressure_weight=5.0):
    """
    Calculates a similarity score between current sensor data (network + pressure)
    and a location fingerprint. Lower score is better.
    Pressure component is weighted higher.

    Returns a dictionary with detailed score components and the total score.
    """
    network_score_match_part = 0.0 # Score for matching networks
    missing_net_penalty = 0.0
    extra_net_penalty = 0.0
    pressure_diff_score = 0.0
    missing_pressure_penalty = 0.0
    extra_pressure_penalty = 0.0

    # --- Network Score Calculation ---
    # Convert current network data to a dictionary for quick lookup: {(type, id): rssi}
    current_networks = {}
    for net in current_network_data:
        net_type = net.get('type')
        net_id = str(net.get('id'))
        net_rssi = net.get('rssi')
        if net_type and net_id and isinstance(net_rssi, (int, float)):
            current_networks[(net_type, net_id)] = net_rssi

    # Separate fingerprint into network and pressure parts
    # Access pressure data using the string key "pressure_value"
    fingerprint_pressure_data = location_fingerprint.get("pressure_value")
    # Network fingerprints are all other items
    fingerprint_networks = {k: v for k, v in location_fingerprint.items() if k != "pressure_value"}


    if not fingerprint_networks:
        # High penalty if fingerprint has no networks but we see some
        extra_net_penalty = len(current_networks) * abs(-75) # Avg RSSI penalty per extra
    else:
        for network_key, fingerprint_data in fingerprint_networks.items():
            # Access median and std dev using "median_value" and "std_dev_value"
            median_rssi = fingerprint_data.get('median_value', -999)
            std_dev_rssi = fingerprint_data.get('std_dev_value', 100)
            if network_key in current_networks:
                current_rssi = current_networks[network_key]
                diff = abs(current_rssi - median_rssi)
                # Ensure std_dev_rssi is not too small to avoid division by near zero
                safe_std_dev_rssi = max(std_dev_rssi, 0.1)
                weighted_diff = diff / (safe_std_dev_rssi + 1e-6)
                network_score_match_part += weighted_diff ** 2
            else:
                base_penalty = max(0, 100 + median_rssi)
                safe_std_dev_rssi = max(std_dev_rssi, 0.1)
                stability_factor = 1 / (safe_std_dev_rssi + 1e-6)
                penalty = (base_penalty * stability_factor) ** 1.5
                missing_net_penalty += penalty

        for network_key, current_rssi in current_networks.items():
            if network_key not in fingerprint_networks:
                base_penalty = max(0, 100 + current_rssi)
                extra_net_penalty += base_penalty

    # Combine network scores, applying overall weight
    network_score_unweighted = network_score_match_part + missing_net_penalty + extra_net_penalty
    network_score_weighted = network_score_unweighted * network_weight

    # --- Pressure Score Calculation ---
    if fingerprint_pressure_data:
        # Access median and std dev using "median_value" and "std_dev_value"
        median_pressure = fingerprint_pressure_data.get('median_value', 1013.25) # Default to standard pressure
        std_dev_pressure = fingerprint_pressure_data.get('std_dev_value', 5.0) # Default std dev

        if current_pressure_value is not None and isinstance(current_pressure_value, (int, float)):
             pressure_diff = abs(current_pressure_value - median_pressure)
             # Normalize by std dev, ensure minimum divisor
             safe_std_dev_pressure = max(std_dev_pressure, 0.01) # Smaller min divisor for pressure
             weighted_pressure_diff = pressure_diff / (safe_std_dev_pressure + 1e-6)
             pressure_diff_score = weighted_pressure_diff ** 2 # Squared difference
        # If current_pressure_value is None, pressure_diff_score remains 0.0

        if current_pressure_value is None: # Penalty for missing current pressure when fingerprint expects it
             safe_std_dev_pressure = max(std_dev_pressure, 0.01)
             stability_factor = 1 / (safe_std_dev_pressure + 1e-6)
             missing_pressure_penalty = (1.0 * stability_factor) ** 2 # Base penalty of 1 hPa, scaled by stability squared
        # If current_pressure_value is not None, missing_pressure_penalty remains 0.0

    elif current_pressure_value is not None and isinstance(current_pressure_value, (int, float)):
        # Penalty for having current pressure when fingerprint doesn't expect it
        extra_pressure_penalty = 1.0**2 # Simple base penalty
    # If neither has pressure data, extra_pressure_penalty and missing_pressure_penalty remain 0.0

    # Combine pressure scores, applying overall weight
    pressure_score_unweighted = pressure_diff_score + missing_pressure_penalty + extra_pressure_penalty
    pressure_score_weighted = pressure_score_unweighted * pressure_weight

    # --- Total Score ---
    total_score = network_score_weighted + pressure_score_weighted

    # Return detailed breakdown
    return {
        "total_score": total_score,
        "network": {
            "weighted_match_part": network_score_match_part * network_weight, # Return weighted components for easier comparison
            "weighted_missing_penalty": missing_net_penalty * network_weight,
            "weighted_extra_penalty": extra_net_penalty * network_weight,
            "unweighted_match_part": network_score_match_part,
            "unweighted_missing_penalty": missing_net_penalty,
            "unweighted_extra_penalty": extra_net_penalty,
            "weighted_total": network_score_weighted,
            "unweighted_total": network_score_unweighted,
            "weight": network_weight
        },
        "pressure": {
            "weighted_diff_score": pressure_diff_score * pressure_weight, # Return weighted components
            "weighted_missing_penalty": missing_pressure_penalty * pressure_weight,
            "weighted_extra_penalty": extra_pressure_penalty * pressure_weight,
            "unweighted_diff_score": pressure_diff_score,
            "unweighted_missing_penalty": missing_pressure_penalty,
            "unweighted_extra_penalty": extra_pressure_penalty,
            "weighted_total": pressure_score_weighted,
            "unweighted_total": pressure_score_unweighted,
            "weight": pressure_weight
        },
         "raw_values": { # Include raw values used for calculation for context
             "current_pressure_value": current_pressure_value,
             # Access fingerprint pressure median and stddev using the correct keys
             "fingerprint_pressure_median": fingerprint_pressure_data.get('median_value') if fingerprint_pressure_data else None,
             "fingerprint_pressure_stddev": fingerprint_pressure_data.get('std_dev_value') if fingerprint_pressure_data else None,
             "current_networks_count": len(current_networks),
             "fingerprint_networks_count": len(fingerprint_networks)
         }
    }


def predict_location(current_network_data, current_pressure_value=None):
    """
    Predicts the current location based on network data, optional pressure data,
    and loaded location fingerprints.
    Returns the name of the best matching location or None.
    """
    if not location_fingerprints:
        logger.debug("Prediction skipped: No location fingerprints loaded.")
        return None # No fingerprints loaded

    # Prediction requires network data
    if not current_network_data:
        logger.debug("Prediction skipped: No current network data provided.")
        return None

    best_match_location = None
    min_score = float('inf')
    scores = {} # Store just the total scores for prediction logic

    logger.debug(f"Predicting location based on {len(current_network_data)} current networks and pressure {current_pressure_value:.2f} hPa" if current_pressure_value else f"Predicting location based on {len(current_network_data)} current networks (no current pressure)")
    for location, fingerprint in location_fingerprints.items():
        # Call calculate_similarity and get the detailed result
        score_details = calculate_similarity(current_network_data, current_pressure_value, fingerprint)
        score = score_details["total_score"] # Extract the total score

        scores[location] = score # Store only the total score for sorting

        # Log the detailed breakdown for this location
        logger.info(f"Location: '{location}' Score Details: {json.dumps(score_details, indent=2)}")


        if score < min_score:
            min_score = score
            best_match_location = location

    # sort scores by score
    sorted_scores = sorted(scores.items(), key=lambda item: item[1])
    # The detailed logging above already shows all scores, so this loop is less critical for debugging now
    # for location, score in sorted_scores:
    #     logger.info(f"Location: '{location}' total score: {score:.2f}")

    if best_match_location and min_score < CONFIDENCE_THRESHOLD:
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

def get_all_location_scores(current_network_data, current_pressure_value=None):
    """
    Calculates and returns the detailed similarity scores for all known locations.
    Returns a dictionary {location: score_details_dict} or None if prediction is not possible.
    """
    if not location_fingerprints:
        logger.debug("Score calculation skipped: No location fingerprints loaded.")
        return None

    if not current_network_data:
        logger.debug("Score calculation skipped: No current network data provided.")
        return None

    all_scores_details = {}
    logger.debug(f"Calculating all detailed scores based on {len(current_network_data)} networks and pressure {current_pressure_value:.2f} hPa" if current_pressure_value else f"Calculating all detailed scores based on {len(current_network_data)} networks (no pressure)")
    for location, fingerprint in location_fingerprints.items():
        # Use the same calculate_similarity function used for prediction, which now returns details
        score_details = calculate_similarity(current_network_data, current_pressure_value, fingerprint)
        all_scores_details[location] = score_details
        # Detailed logging for each location is now handled within calculate_similarity

    return all_scores_details

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

        # --- Auto-Event Logging --- START
        if auto_logging_event.is_set():
            try:
                auto_log_entry = {
                    "timestamp": now.isoformat(),
                    "event_type": "auto_state_change",
                    "sensor_path": normalized_sensor_path,
                    "new_state": new_state,
                    "previous_state": previous_state
                }
                event_data_logger.info(json.dumps(auto_log_entry))
                logger.debug(f"Auto-logged state change for {normalized_sensor_path} to {new_state}")
            except Exception as log_err:
                 logger.error(f"Failed to auto-log state change event: {log_err}", exc_info=True)
        # --- Auto-Event Logging --- END

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

                # --- Store latest data for scoring API --- START
                if is_predictive_scan:
                    global latest_network_data_for_scoring, latest_data_timestamp
                    latest_network_data_for_scoring = current_scan_data_for_prediction
                    latest_data_timestamp = timestamp # Store timestamp of this data

                # Store latest pressure value separately if available
                if normalized_sensor_type == PRESSURE_SENSOR_TYPE and state.last_value and isinstance(state.last_value[0], (int, float)):
                    global latest_pressure_for_scoring
                    latest_pressure_for_scoring = state.last_value[0]

                    # Also trigger prediction on pressure updates if we have network data
                    if latest_network_data_for_scoring:
                        logger.debug(f"Triggering prediction on pressure update: {latest_pressure_for_scoring:.2f} hPa, network data available from {len(latest_network_data_for_scoring)} networks")
                        predicted_loc = predict_location(latest_network_data_for_scoring, latest_pressure_for_scoring)

                        # Find the location.predicted state node and update it (same as below in the network trigger section)
                        loc_node = nested_sensor_data.get('location', {}).get('predicted')
                        if isinstance(loc_node, SensorState):
                            new_loc_state_value = predicted_loc if predicted_loc else "Unknown"
                            # Update only if changed to avoid triggering unnecessary logs/updates
                            if loc_node.last_value != new_loc_state_value:
                                loc_node.previous_value = loc_node.last_value
                                loc_node.previous_timestamp = loc_node.last_timestamp
                                loc_node.last_value = new_loc_state_value
                                loc_node.last_timestamp = timestamp
                                loc_node.inferred_state = new_loc_state_value
                                # Log state change
                                prev_loc_state_value = loc_node.previous_value if loc_node.previous_value else "Unknown"
                                log_entry = {
                                    "timestamp": timestamp.isoformat(),
                                    "sensor_path": "location.predicted",
                                    "previous_state": prev_loc_state_value,
                                    "new_state": new_loc_state_value
                                }
                                state_data_logger.info(json.dumps(log_entry))
                        else:
                            logger.warning("Could not find location.predicted SensorState node to update from pressure trigger.")
                # --- Store latest data for scoring API --- END

                # --- Perform Prediction & Update State (using latest stored data) --- START
                # This part remains similar, but uses the globally stored latest data for consistency
                if is_predictive_scan and latest_network_data_for_scoring:
                    # Check age of data used for prediction
                    pressure_to_predict_with = None
                    if latest_pressure_for_scoring is not None and latest_data_timestamp is not None:
                        if (timestamp - latest_data_timestamp).total_seconds() < MAX_PRESSURE_AGE_SECONDS:
                            pressure_to_predict_with = latest_pressure_for_scoring
                        else:
                            logger.debug(f"Latest pressure data is too old for prediction ({ (timestamp - latest_data_timestamp).total_seconds():.1f}s)")
                            # If pressure is stale, reset the global value too
                            latest_pressure_for_scoring = None

                    predicted_loc = predict_location(latest_network_data_for_scoring, pressure_to_predict_with)

                    # Find the location.predicted state node and update it
                    loc_node = nested_sensor_data.get('location', {}).get('predicted')
                    if isinstance(loc_node, SensorState):
                        new_loc_state_value = predicted_loc if predicted_loc else "Unknown"
                        # Update only if changed to avoid triggering unnecessary logs/updates
                        if loc_node.last_value != new_loc_state_value:
                            loc_node.previous_value = loc_node.last_value
                            loc_node.previous_timestamp = loc_node.last_timestamp
                            loc_node.last_value = new_loc_state_value
                            loc_node.last_timestamp = timestamp # Use current message timestamp for state update time
                            loc_node.inferred_state = new_loc_state_value
                            # Log state change (handled by update_inferred_state implicitly if we call it? No, need explicit log)
                            prev_loc_state_value = loc_node.previous_value if loc_node.previous_value else "Unknown"
                            log_entry = {
                                "timestamp": timestamp.isoformat(),
                                "sensor_path": "location.predicted",
                                "previous_state": prev_loc_state_value,
                                "new_state": new_loc_state_value
                            }
                            state_data_logger.info(json.dumps(log_entry))
                    else:
                        logger.warning("Could not find location.predicted SensorState node to update.")
                # --- Perform Prediction & Update State --- END

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
