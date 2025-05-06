#!/usr/bin/env python3

import logging
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
import threading
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
import time
import websockets # Import websockets for the WSS server
import aiohttp # Import aiohttp for the HTTP client (if needed for discovery, or remove if not)
from aiohttp import web # Specific import for aiohttp web components
from urllib.parse import urlencode # Needed for URL encoding
from collections import deque # Added for efficiently reading last N lines
import re # Added for regular expression operations

# --- Import Core Modules ---
# Assume these files are in the same directory or accessible via Python path
try:
    from data_store import DataStore
    from collector import Collector
    from fingerprinting import FingerprintingModule
    from inference import InferenceModule
    from device_manager import DeviceManager
except ImportError as e:
    logging.critical(f"Failed to import core modules: {e}. Please ensure data_store.py, collector.py, fingerprinting.py, and inference.py are available.", exc_info=True)
    # Exit or handle the error appropriately in a real application
    exit()


# --- Configuration ---
# TODO: Move to a config file or environment variables
# Network server configuration (where the mobile app sends data)
# Use 0.0.0.0 to listen on all interfaces
SENSOR_NETWORK_HOST = '0.0.0.0'
SENSOR_HTTP_PORT = 9090  # Android app's DEFAULT_HTTP_PORT_NO in AppSettings.kt
SENSOR_WS_PORT = 8080    # Android app's DEFAULT_WEBSOCKET_PORT_NO in AppSettings.kt

# device manager configuration
DEVICE_MANAGER_LISTEN_HOST = '10.0.0.2'
DEVICE_MANAGER_LISTEN_HTTP_PORT = 9091
DEVICE_MANAGER_LISTEN_WS_PORT = 8081
DEVICE_MANAGER_FRONTEND_WS_PORT = 5001

# Web server (Flask) configuration
FLASK_HOST = '0.0.0.0'
FLASK_PORT = 5000

# Data storage configuration
LOG_DIR = "data_logs" # Matches default in DataStore

# Inference and Fingerprinting configuration directories (managed by modules)
CONFIG_DIR = "configs"
FINGERPRINT_STORAGE_DIR = "fingerprints"


# --- Logging Setup ---
# Basic setup for the main application logs
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
# File handler for server logs
server_log_path = os.path.join(LOG_DIR, "main_app.log") # ADDED: Define path using LOG_DIR
os.makedirs(LOG_DIR, exist_ok=True) # ADDED: Ensure LOG_DIR exists
server_log_handler = logging.FileHandler(server_log_path, mode='a') # Use the defined path
server_log_handler.setFormatter(log_formatter)

# Silence some noisy libraries
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING) # Silence websockets server logs
logging.getLogger("aiohttp.access").setLevel(logging.WARNING) # Silence aiohttp access logs


# --- Core Module Instantiation and Wiring ---
# Instantiate core modules
# These should be instantiated once at the top level (when imported)

data_store = DataStore(log_directory=LOG_DIR) # Removed log_queue argument
collector = Collector(data_store=data_store)
# Instantiate InferenceModule and FingerprintingModule, wiring them using setters
# Note: We need to instantiate them before wiring
inference_module = InferenceModule(data_store=data_store, config_dir=CONFIG_DIR)
fingerprinting_module = FingerprintingModule(data_store=data_store, storage_dir=FINGERPRINT_STORAGE_DIR)

# Wire the dependencies
inference_module.set_fingerprinting_module(fingerprinting_module)
fingerprinting_module.set_inference_module(inference_module)

# --- Background Task Runner ---
def run_asyncio_loop(coroutine):
    """Runs an asyncio coroutine in a new event loop."""
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    # Set this new loop as the current event loop for this thread
    asyncio.set_event_loop(loop)
    try:
        # Run the coroutine within this loop
        loop.run_until_complete(coroutine)
    except asyncio.CancelledError:
        logger.info("Asyncio loop cancelled.")
    except Exception as e:
        logger.critical(f"Asyncio loop encountered a critical error: {e}", exc_info=True)
    finally:
        # Clean up the loop
        loop.close()
        logger.info("Asyncio loop finished.")

# --- Auto-Logging Background Task ---
def _background_auto_logger(duration_seconds: int):
    """Periodically fetches state and logs it to state_data.log."""
    end_time = time.time() + duration_seconds
    logger.info(f"Starting background state logging for {duration_seconds}s.")
    
    while time.time() < end_time:
        try:
            # Fetch current state using the same logic as the /state/data endpoint
            # This avoids duplicating the state fetching logic
            # We need Flask app context if get_state_data uses request-specific things,
            # but it seems to primarily use data_store. A direct call might be better.
            
            # Re-implement state fetching logic directly here to avoid context issues
            now = datetime.now(timezone.utc)
            iso_now = now.isoformat().replace('+00:00', 'Z')
            medium_window_start = (now - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z')
            
            latest_prediction_dps = data_store.get_data(
                 types=['inference.location.prediction'], 
                 started_at=medium_window_start, 
                 ended_at=iso_now, 
                 limit=1
            )
            latest_prediction = latest_prediction_dps[0] if latest_prediction_dps else None
            
            # Simplified state for logging - adjust as needed
            current_state_snapshot = {
                 "prediction": latest_prediction['value'] if latest_prediction else "Unknown",
                 # Add other key state elements if needed
            }
            
            state_data_point = {
                "type": "state.snapshot",
                "key": None, # Or a specific key if needed
                "value": current_state_snapshot,
                "created_at": iso_now
            }
            
            data_store.set(state_data_point, files=['state_data']) # Log to state_data.log
            logger.debug(f"Logged state snapshot: {current_state_snapshot}")
            
        except Exception as e:
            logger.error(f"Error during background state logging: {e}", exc_info=True)
            
        # Wait for the next interval (e.g., every 1 second)
        time.sleep(1.0)
        
    logger.info(f"Finished background state logging after {duration_seconds}s.")

# --- Flask App Setup ---
app = Flask(__name__, static_folder='../static')

# --- Background Tasks / Threads ---
# This section will contain background tasks like:
# 1. The DeviceManager running the network servers.
# 2. A task that periodically triggers inference runs.
# 3. A task that periodically generates the "current fingerprint".


# Removed placeholder periodic inference task function
# Removed placeholder periodic current fingerprint generation task function


# --- Flask Routes ---
@app.route('/')
def index():
    # Main page, likely event annotation or a dashboard overview
    # Assuming event_page.html is the main view
    return render_template('event_page.html') 

@app.route('/logs')
def logs():
    # Page to view various system logs
    return render_template('log_page.html')

# --- New Routes for Management Pages ---

@app.route('/devices') # Added route for devices page
def devices():
    # Page to view device connection status
    return render_template('device_page.html')

@app.route('/fingerprinting')
def fingerprinting():
    # Page to manage calibrated fingerprints
    return render_template('fingerprint_page.html')

@app.route('/inference_configs')
def inference_configs():
    # Page to manage inference configurations
    return render_template('inference_config_page.html')


# --- API Endpoints (Interacting with Core Modules) ---

@app.route('/api/data', methods=['GET'])
def api_get_data():
    """API endpoint to fetch data points from the DataStore."""
    types = request.args.getlist('types')
    started_at = request.args.get('started_at')
    ended_at = request.args.get('ended_at')
    keys = request.args.getlist('keys') or None # Use None if list is empty
    files = request.args.getlist('files') or None # Use None if list is empty

    if not types or not started_at or not ended_at:
        return jsonify({"error": "Missing required parameters: types, started_at, ended_at"}), 400

    try:
        data_points = data_store.get_data(types, started_at, ended_at, keys, files)
        return jsonify({"data_points": data_points}), 200
    except Exception as e:
        logger.error(f"Error fetching data from DataStore: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/fingerprints', methods=['GET'])
def api_get_fingerprints():
    """API endpoint to fetch calibrated fingerprints."""
    try:
        calibrated_fps = fingerprinting_module.load_calibrated_fingerprints()
        # Convert dictionary to a list of fingerprints for easier frontend handling
        fingerprint_list = list(calibrated_fps.values())
        return jsonify({"calibrated_fingerprints": fingerprint_list}), 200
    except Exception as e:
        logger.error(f"Error fetching calibrated fingerprints: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/fingerprints/generate', methods=['POST'])
def api_generate_fingerprint():
    """API endpoint to generate a current fingerprint."""
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    fingerprint_type = data.get('type')
    inference_config_name = data.get('inference_config_name')
    ended_at = data.get('ended_at') # Should be provided by frontend

    if not fingerprint_type or not inference_config_name or not ended_at:
         return jsonify({"error": "Missing required parameters: type, inference_config_name, ended_at"}), 400

    try:
        generated_fp = fingerprinting_module.generate_fingerprint(
            fingerprint_type=fingerprint_type,
            inference_config_name=inference_config_name,
            ended_at=ended_at
        )
        if generated_fp:
            return jsonify({"current_fingerprint": generated_fp}), 200
        else:
            return jsonify({"error": "Failed to generate fingerprint (config not found or no data)"}), 400
    except Exception as e:
        logger.error(f"Error generating fingerprint: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/fingerprints/calibrate', methods=['POST'])
def api_calibrate_fingerprint():
    """API endpoint to save/update a calibrated fingerprint."""
    data = request.json
    if not data or 'fingerprint' not in data:
        return jsonify({"error": "Invalid JSON or missing 'fingerprint'"}), 400

    fingerprint = data['fingerprint']
    fp_type = fingerprint.get('type')

    if not fp_type:
        return jsonify({"error": "Fingerprint object missing 'type'"}), 400

    try:
        # Determine if saving a new or updating an existing
        existing_fingerprints = fingerprinting_module.load_calibrated_fingerprints()
        if fp_type in existing_fingerprints:
             fingerprinting_module.update_calibrated_fingerprint(fp_type, fingerprint)
             return jsonify({"status": "updated", "fingerprint_type": fp_type}), 200
        else:
             fingerprinting_module.save_calibrated_fingerprint(fingerprint)
             return jsonify({"status": "saved", "fingerprint_type": fp_type}), 201
    except Exception as e:
        logger.error(f"Error calibrating fingerprint: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/inference_configs', methods=['GET'])
def api_get_inference_configs():
    """API endpoint to fetch all inference configurations."""
    try:
        configs = inference_module.load_inference_configurations()
        # Convert dictionary to a list of configs for easier frontend handling
        config_list = list(configs.values())
        return jsonify({"inference_configurations": config_list}), 200
    except Exception as e:
        logger.error(f"Error fetching inference configurations: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/inference_configs', methods=['POST'])
def api_save_inference_config():
    """API endpoint to save a new inference configuration."""
    data = request.json
    if not data or 'name' not in data:
        return jsonify({"error": "Invalid JSON"}), 400

    config_name = data['name']

    try:
        # Basic validation (more comprehensive validation is in InferenceModule)
        if 'inference_type' not in data or 'included_paths' not in data or 'sensor_weights' not in data:
             return jsonify({"error": "Missing required inference config parameters"}), 400

        # Create a copy to avoid modifying the request data directly
        new_config = data.copy()
        inference_module.save_inference_configuration(new_config)
        return jsonify({"status": "saved", "config_name": config_name}), 201
    except Exception as e:
        logger.error(f"Error saving inference configuration: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/inference_configs/<string:config_name>', methods=['PUT'])
def api_update_inference_config(config_name):
    """API endpoint to update an existing inference configuration."""
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        # Basic validation (more comprehensive validation is in InferenceModule)
        if 'inference_type' not in data or 'included_paths' not in data or 'sensor_weights' not in data:
             return jsonify({"error": "Missing required inference config parameters"}), 400

        # Ensure the name in the data matches the URL parameter
        if data.get('name') != config_name:
             return jsonify({"error": "Config name in body must match URL"}), 400

        # Create a copy to avoid modifying the request data directly
        updated_config = data.copy()
        inference_module.update_inference_configuration(config_name, updated_config)
        return jsonify({"status": "updated", "config_name": config_name}), 200
    except Exception as e:
        logger.error(f"Error updating inference configuration '{config_name}': {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/inference/run/<string:config_name>', methods=['POST'])
def api_run_inference(config_name):
    """API endpoint to trigger an inference run for a specific configuration."""
    # Get current time for the inference window end
    current_time_str = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    try:
        # The run_inference method logs results internally to the DataStore
        inference_module.run_inference(config_name, current_time_str)
        return jsonify({"status": "inference run triggered", "config_name": config_name, "timestamp": current_time_str}), 200
    except Exception as e:
        logger.error(f"Error triggering inference run for '{config_name}': {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/inference/history/<string:config_name>', methods=['GET'])
def api_get_inference_history(config_name):
    """API endpoint to fetch inference run history for a specific configuration."""
    count_str = request.args.get('count', '50') # Get last 50 runs by default
    try:
        count = int(count_str)
        if count <= 0: count = 50
        
        logger.info(f"Fetching last {count} inference history runs for config: {config_name}")
        
        # Define the expected log file path
        history_file_path = data_store._get_log_file_path("inference_data.log")
        run_history = []

        if not os.path.exists(history_file_path):
            logger.warning(f"Inference history file not found: {history_file_path}")
            return jsonify({"runs": []}), 200 # Return empty list if file doesn't exist

        # Read the log file efficiently (maybe need read_last_n_lines or similar)
        # For simplicity, reading all lines and filtering - inefficient for large files!
        # TODO: Optimize log reading for history (e.g., use read_last_n_lines and filter)
        relevant_lines = []
        try:
            with open(history_file_path, 'r') as f:
                 # Read lines in reverse to get recent ones first potentially?
                 # Or use read_last_n_lines if available and reliable
                 all_lines = f.readlines() # Inefficient for large files!
                 for line in reversed(all_lines):
                     if len(relevant_lines) >= count:
                         break # Stop once we have enough matching runs
                     try:
                         data_point = json.loads(line)
                         # We stored the full result with type inference.{type}.result
                         # Check if the key matches the config name
                         if data_point.get('key') == config_name and data_point.get('type', '').endswith('.result'):
                             # The value of this data_point *is* the inference_result structure
                             inference_result = data_point.get('value')
                             if isinstance(inference_result, dict):
                                 relevant_lines.append(inference_result)
                             else:
                                 logger.warning(f"Found matching history entry for {config_name} but value is not a dict: {type(inference_result)}")
                                 
                     except json.JSONDecodeError:
                         continue # Skip non-JSON lines
                     except Exception as e:
                         logger.warning(f"Error processing history line: {e}")
                         
        except Exception as e:
            logger.error(f"Error reading inference history file {history_file_path}: {e}", exc_info=True)
            return jsonify({"error": "Failed to read inference history"}), 500
            
        # The lines were added in reverse chronological order due to reversed(all_lines)
        # No need to sort again if read in reverse.
        # If read normally, sort here: relevant_lines.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        return jsonify({"runs": relevant_lines}), 200 # Return the found runs

    except ValueError:
        return jsonify({"error": "Invalid count parameter"}), 400
    except Exception as e:
        logger.error(f"Error fetching inference history for '{config_name}': {e}", exc_info=True)
        return jsonify({"error": "Internal server error fetching history"}), 500

# Note: Additional API endpoints might be needed for specific frontend needs,
# e.g., getting latest prediction/confidence, getting data for charts, etc.

# --- Endpoints related to the old state/event/log system (Review/Keep/Remove) ---
# Keep state/data as it's used by multiple pages
@app.route('/state/data', methods=['GET'])
def get_state_data():
    # Combine relevant state information for the frontend
    logger.debug("Fetching state data for frontend...")
    try:
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat().replace('+00:00', 'Z')
        # Define time windows (adjust as needed)
        short_window_start = (now - timedelta(seconds=10)).isoformat().replace('+00:00', 'Z')
        medium_window_start = (now - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z')

        # --- Fetch Latest Data Points --- 
        # Fetch latest prediction
        # latest_prediction_dps = data_store.get_data(
        #     types=['inference.location.prediction'], 
        #     started_at=medium_window_start, 
        #     ended_at=iso_now, 
        #     limit=1
        # )
        # latest_prediction = latest_prediction_dps[0] if latest_prediction_dps else None

        # # Fetch latest location scores (all scores within the last minute)
        # # Assuming scores are stored like: type='inference.location.confidence', key='location_name', value=score
        # score_dps = data_store.get_data(
        #      types=['inference.location.confidence'], 
        #      started_at=medium_window_start, 
        #      ended_at=iso_now,
        #      # No limit - we want all recent scores to find the latest for each key
        # )
        # # Process scores: get the latest score for each location key
        # latest_scores_map = {}
        # if score_dps:
        #      # Sort by timestamp descending to easily find the latest
        #      score_dps.sort(key=lambda x: x['created_at'], reverse=True)
        #      for dp in score_dps:
        #          if dp['key'] not in latest_scores_map:
        #               latest_scores_map[dp['key']] = dp['value']
        
        # # --- Fetch Recent Sensor Data for Tree --- 
        # # Define types needed for the sensor tree display
        # sensor_tree_types = [
        #     'android.sensor.pressure', 
        #     'android.sensor.accelerometer', 
        #     'android.sensor.gyroscope', 
        #     'gps', 
        #     'android.sensor.wifi_scan', # May need processing based on key (BSSID)
        #     'android.sensor.bluetooth_scan' # May need processing
        # ]
        # # Fetch the *single* most recent data point for each type needed in the tree
        # # This is less efficient than getting all in one go and processing, but simpler
        # # A better approach might be a dedicated DataStore method: get_latest_per_type()
        # recent_sensor_dps = {}
        # for sensor_type in sensor_tree_types:
        #     latest_dp = data_store.get_data(
        #         types=[sensor_type],
        #         started_at=short_window_start, # Look in a short recent window
        #         ended_at=iso_now,
        #         limit=1
        #     )
        #     if latest_dp:
        #          recent_sensor_dps[sensor_type] = latest_dp[0]

        # # --- Construct the State Object (Matches Frontend Expectations) --- 
        # state_data = {
        #     "location": {
        #          "predicted": {
        #               "inferred_state": latest_prediction['value'] if latest_prediction else "Unknown",
        #               "last_timestamp": latest_prediction['created_at'] if latest_prediction else None
        #          }
        #     },
        #     "location_scores": latest_scores_map,
        #     "sensors": {
        #          # Example structure - adapt based on actual sensor types and desired display
        #          "environment": {
        #               "pressure": {
        #                   "inferred_state": recent_sensor_dps.get('android.sensor.pressure', {}).get('value', 'N/A'),
        #                   "last_timestamp": recent_sensor_dps.get('android.sensor.pressure', {}).get('created_at')
        #               }
        #          },
        #          "motion": {
        #               "accelerometer": {
        #                   "inferred_state": recent_sensor_dps.get('android.sensor.accelerometer', {}).get('value', {}),
        #                    # Value might be {x, y, z} - display appropriately or simplify
        #                   "last_timestamp": recent_sensor_dps.get('android.sensor.accelerometer', {}).get('created_at')
        #               },
        #               "gyroscope": {
        #                    "inferred_state": recent_sensor_dps.get('android.sensor.gyroscope', {}).get('value', {}),
        #                    "last_timestamp": recent_sensor_dps.get('android.sensor.gyroscope', {}).get('created_at')
        #               }
        #          },
        #          "position": {
        #              "gps": {
        #                   "inferred_state": recent_sensor_dps.get('gps', {}).get('value', {}),
        #                   # Value might be {lat, lon, alt, acc}
        #                   "last_timestamp": recent_sensor_dps.get('gps', {}).get('created_at')
        #               }
        #          },
        #          "wireless": {
        #               "wifi_scan": {
        #                    # Display count or signal strength of strongest?
        #                    "inferred_state": f"{len(data_store.get_data(types=['android.sensor.wifi_scan'], started_at=short_window_start, ended_at=iso_now))} networks seen",
        #                    "last_timestamp": recent_sensor_dps.get('android.sensor.wifi_scan', {}).get('created_at')
        #               },
        #               "bluetooth_scan": {
        #                    "inferred_state": f"{len(data_store.get_data(types=['android.sensor.bluetooth_scan'], started_at=short_window_start, ended_at=iso_now))} devices seen",
        #                    "last_timestamp": recent_sensor_dps.get('android.sensor.bluetooth_scan', {}).get('created_at')
        #               }
        #          }
        #     }
        # }
        logger.debug(f"Returning no state data because it should be streamed to the frontend as we receive it from the device manager") # Log truncated state
        return jsonify({})
    except Exception as e:
        logger.error(f"Error fetching state data: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch state data"}), 500


@app.route('/submit_event', methods=['POST'])
def submit_event():
    # Endpoint to manually log an event from the frontend
    description = request.form.get('description')
    selected_sensors = request.form.getlist('selected_sensors') # Get sensor paths

    if not description or not selected_sensors:
        return jsonify({"error": "Missing description or selected sensors"}), 400

    try:
        # Create a data point for the manual event
        event_data_point = {
            "type": "manual.event",
            "key": description, # Use description as key or generate unique ID
            "value": {
                 "description": description,
                 "involved_sensor_paths": selected_sensors,
                 # Optionally include current state snapshot here
            },
            "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        }
        
        # Log the event data point using the Collector/DataStore
        # Assuming a specific file for events might be good
        data_store.set(event_data_point, files=['raw_data', 'event_data']) # Log to event_data.log
        
        logger.info(f"Manual event logged: {description}, Sensors: {selected_sensors}")
        return jsonify({"status": "success", "message": "Event logged"}), 200
    except Exception as e:
        logger.error(f"Error submitting manual event: {e}", exc_info=True)
        return jsonify({"error": "Internal server error logging event"}), 500

@app.route('/start_auto_event_logging', methods=['POST'])
def start_auto_event_logging():
    # Endpoint to trigger automatic logging of state changes for a duration
    duration_str = request.form.get('duration')
    
    if not duration_str:
        return jsonify({"error": "Missing duration"}), 400
        
    try:
        duration = int(duration_str)
        if duration <= 0 or duration > 300: # Add a reasonable upper limit (e.g., 5 minutes)
            raise ValueError("Duration must be positive and not excessive (e.g., <= 300s)")

        # Start the background logging task in a separate thread
        log_thread = threading.Thread(
             target=_background_auto_logger, 
             args=(duration,),
             name=f"AutoLoggerThread-{duration}s",
             daemon=True # Allow main app to exit even if thread runs
        )
        log_thread.start()
        
        logger.info(f"Started background auto-logging thread for {duration} seconds.")
        return jsonify({"status": "success", "message": f"Auto-logging initiated for {duration} seconds."}), 200

    except ValueError as e:
        logger.warning(f"Invalid duration specified for auto-log: {duration_str} - {e}")
        return jsonify({"error": f"Invalid duration specified: {e}"}), 400
    except Exception as e:
        logger.error(f"Error starting auto-event logging: {e}", exc_info=True)
        return jsonify({"error": "Internal server error starting auto-log"}), 500


# --- Log Reading Helper ---
def read_last_n_lines(filepath, n): 
    """Reads the last n lines of a file efficiently."""
    try:
        with open(filepath, 'rb') as f:
            # Use deque to efficiently keep track of last N lines
            # Read in chunks to avoid loading huge files into memory
            buffer_size = 1024 * 8 # 8KB buffer
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            
            lines_found = []
            block = -1
            last_block = b''
            
            while len(lines_found) < n and file_size > 0:
                if file_size - buffer_size < 0: # Don't overshoot beginning
                    buffer_size = file_size
                    file_size = 0
                else:
                    file_size -= buffer_size
                    
                f.seek(file_size, os.SEEK_SET)
                current_block = f.read(buffer_size)
                
                # Prepend the last block's start if it was cut mid-line
                if last_block:
                    current_block += last_block
                    
                # Split into lines
                block_lines = current_block.splitlines(True) # Keep line endings
                
                # Check if first line is complete
                if block_lines and not block_lines[0].endswith((b'\n', b'\r')):
                    last_block = block_lines.pop(0) # Store incomplete line start
                else:
                    last_block = b'' # First line was complete

                # Add lines found (in reverse order)
                lines_found.extend(block_lines[::-1])

            # Decode and return last N lines in correct order
            return [line.decode('utf-8', errors='ignore').strip() for line in lines_found[:n][::-1]]
            
    except FileNotFoundError:
        logger.warning(f"Log file not found: {filepath}")
        return []
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}", exc_info=True)
        return []


@app.route('/logs/data', methods=['GET'])
def get_logs_data():
    # Endpoint to fetch historical log data for the log viewer
    log_type = request.args.get('type', 'all')
    count_str = request.args.get('count', '200')

    try:
        count = int(count_str)
        if count <= 0: count = 200 # Default if invalid count
        
        # Define log files/keys (adjust paths/keys as needed)
        # Using keys that DataStore might use internally is ideal
        log_map = {
             'raw': ['raw_data'],
             'state': ['state_data'], 
             'event': ['event_data'], 
             'server': ['main_app'], # Just main app logs for simplicity now
             'inference': ['inference_data', 'inference'], 
             # Add other module log keys if DataStore manages them
             'devicemanager': ['devicemanager'],
             'collector': ['collector'],
             'datastore': ['datastore'],
             'fingerprinting': ['fingerprinting'],
        }
        # 'all' includes all defined categories
        all_keys = set(k for keys in log_map.values() for k in keys)
        log_map['all'] = list(all_keys)
        
        files_to_query_keys = log_map.get(log_type, log_map['all'])

        all_log_entries = []
        for key in files_to_query_keys:
             try:
                 # Assume DataStore manages log files like `data_logs/{key}.log`
                 # This might need adjustment based on DataStore's actual behavior
                 log_file_path = data_store._get_log_file_path(f"{key}.log") 
                 
                 if os.path.exists(log_file_path):
                     lines = read_last_n_lines(log_file_path, count) # Use helper
                     for line in lines:
                          content = line.strip()
                          if not content: continue
                          
                          # Try parsing as JSON (likely data_point logs)
                          try:
                              log_entry = json.loads(content)
                              # Use the type from the data_point if available, else use file key
                              entry_type = log_entry.get('type', key) 
                              # Re-serialize value for consistent content string
                              all_log_entries.append({"type": entry_type, "content": json.dumps(log_entry)})
                          except json.JSONDecodeError:
                              # Treat as plain text log line
                              all_log_entries.append({"type": key, "content": content})
                 else:
                     logger.debug(f"Log file path not found for key '{key}': {log_file_path}")

             except Exception as file_error:
                 logger.warning(f"Could not process log file for key '{key}': {file_error}")

        # Sort combined logs by timestamp (best effort, requires parseable timestamp)
        def get_timestamp(log_item):
            try:
                # Try parsing content as JSON data_point
                dp = json.loads(log_item['content'])
                return dp.get('created_at', '0') # Default for sorting
            except:
                # Try parsing as standard log format (e.g., YYYY-MM-DD HH:MM:SS,ms)
                match = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})', log_item['content'])
                if match:
                    # Attempt to parse the extracted timestamp string
                    try:
                         # Convert to comparable format (ISO or timestamp)
                         # This assumes a specific format, adjust regex and parsing as needed
                         dt_obj = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S,%f')
                         return dt_obj.isoformat() + 'Z' # Make ISO format
                    except ValueError:
                         return '0' # Parsing failed
                return '0' # Default if no timestamp found

        try: 
            all_log_entries.sort(key=get_timestamp)
        except Exception as sort_error:
             logger.warning(f"Could not sort log entries by timestamp: {sort_error}")

        # Return the last 'count' entries from the combined & sorted list
        limited_logs = all_log_entries[-count:]

        return jsonify({"logs": limited_logs})

    except ValueError:
        return jsonify({"error": "Invalid count parameter"}), 400
    except Exception as e:
        logger.error(f"Error fetching logs data: {e}", exc_info=True)
        return jsonify({"error": "Internal server error fetching logs"}), 500

@app.route('/api/devices', methods=['GET'])
def api_get_devices():
    """API endpoint to fetch combined device info (from logs and live manager)."""
    devices_info = []
    try:
        unique_ips = data_store.get_unique_values(field_name='device', files=['raw_data'])
        logger.info(f"Found unique device IPs in logs: {unique_ips}")
        
        # Get the live DeviceManager instance (assuming it's stored in app.config)
        # This might return None if the DeviceManager hasn't started or isn't stored correctly
        live_device_manager = app.config.get('DEVICE_MANAGER') 
        live_device_details = {} 
        if live_device_manager:
             # We assume only one DeviceManager handling one device connection
             # In a multi-device scenario, this lookup would need refinement
             details = live_device_manager.get_device_details()
             if details and details.get('ip_address'):
                 live_device_details[details['ip_address']] = details
             else:
                  logger.warning("DeviceManager running but get_device_details returned no useful info.")
        else:
             logger.warning("DeviceManager instance not found in app config.")
             
        for ip in unique_ips:
            device_data = {
                "ip": ip,
                "name": None,
                "model": None,
                "status": "unknown", # Default status if not live
                "last_log": data_store.get_last_log_timestamp_for_device(ip, file_key='raw_data')
            }
            
            # Merge live info if available for this IP
            live_info = live_device_details.get(ip)
            if live_info:
                 device_data["name"] = live_info.get('name')
                 device_data["model"] = live_info.get('model')
                 device_data["status"] = live_info.get('status', 'unknown') # Get live status
                 
            devices_info.append(device_data)
            
        logger.info(f"Returning combined device info for {len(devices_info)} devices.")
        return jsonify({"devices": devices_info}), 200
        
    except Exception as e:
        logger.error(f"Error fetching unique devices: {e}", exc_info=True)
        return jsonify({"error": "Internal server error fetching devices"}), 500

# --- Main Execution ---

if __name__ == '__main__':
    # --- Setup Logging HERE ---
    logging.getLogger().handlers = [] # Clear any previous handlers
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), handlers=[console_handler, server_log_handler])
    logger = logging.getLogger(__name__) # Get logger after basicConfig
    # ---
    
    logger.info("Core modules instantiated and wired.") # Log after logger is set up
    logger.info("Starting background tasks...")

    # Instantiate the DeviceManager
    device_manager = DeviceManager(
        collector=collector,
        device_host=DEVICE_MANAGER_LISTEN_HOST,
        device_http_port=DEVICE_MANAGER_LISTEN_HTTP_PORT,
        device_ws_port=DEVICE_MANAGER_LISTEN_WS_PORT,
        frontend_ws_port=DEVICE_MANAGER_FRONTEND_WS_PORT
    )
    
    # Store the instance in app config for access from routes
    app.config['DEVICE_MANAGER'] = device_manager

    # Start the DeviceManager thread
    device_manager_thread = threading.Thread(
        target=run_asyncio_loop,
        args=(device_manager.start(),), 
        name="DeviceManagerThread",
        daemon=True 
    )
    device_manager_thread.start()
    logger.info("Device manager thread started.")

    logger.info(f"Starting Flask web server on {FLASK_HOST}:{FLASK_PORT}...")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True, use_reloader=False)
