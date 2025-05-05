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
DEVICE_MANAGER_LISTEN_HTTP_PORT = 9090
DEVICE_MANAGER_LISTEN_WS_PORT = 8080
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
server_log_handler = logging.FileHandler("main_app.log", mode='a') # Log main app events
server_log_handler.setFormatter(log_formatter)

# Clear default handlers from root logger if basicConfig was called elsewhere
logging.getLogger().handlers = []
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), handlers=[console_handler, server_log_handler])
logger = logging.getLogger(__name__)

# Note: DataStore, Collector, FingerprintingModule, and InferenceModule
# are responsible for their own logging to specific files as per the contract.
# We don't need raw_data_logger, state_data_logger, event_data_logger here anymore.

# Silence some noisy libraries
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING) # Silence websockets server logs
logging.getLogger("aiohttp.access").setLevel(logging.WARNING) # Silence aiohttp access logs


# --- Core Module Instantiation and Wiring ---
# Instantiate core modules
# These should be instantiated once at the top level (when imported)
data_store = DataStore(log_directory=LOG_DIR)
collector = Collector(data_store=data_store)
# Instantiate InferenceModule and FingerprintingModule, wiring them using setters
# Note: We need to instantiate them before wiring
inference_module = InferenceModule(data_store=data_store, config_dir=CONFIG_DIR)
fingerprinting_module = FingerprintingModule(data_store=data_store, storage_dir=FINGERPRINT_STORAGE_DIR)

# Wire the dependencies
inference_module.set_fingerprinting_module(fingerprinting_module)
fingerprinting_module.set_inference_module(inference_module)

logger.info("Core modules instantiated and wired.")


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


# --- Flask App Setup ---
app = Flask(__name__)

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
    # The index page might display a summary or link to other pages
    return render_template('index.html') # Assuming you'll create an index.html

@app.route('/raw_data_viewer')
def raw_data_viewer():
    # Page to view raw data logs
    return render_template('raw_data_viewer.html') # Assuming you'll create this template

@app.route('/fingerprinting_manager')
def fingerprinting_manager():
    # Page to manage fingerprints
    return render_template('fingerprinting_manager.html') # Assuming you'll create this template

@app.route('/inference_viewer')
def inference_viewer():
    # Page to view inference results and configurations
    return render_template('inference_viewer.html') # Assuming you'll create this template


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

# Note: Additional API endpoints might be needed for specific frontend needs,
# e.g., getting latest prediction/confidence, getting data for charts, etc.


# --- Main Execution ---

if __name__ == '__main__':
    logger.info("Starting background tasks...")

    # Instantiate the DeviceManager inside the main execution block
    device_manager = DeviceManager(
        collector=collector,
        device_host=DEVICE_MANAGER_LISTEN_HOST,
        device_http_port=DEVICE_MANAGER_LISTEN_HTTP_PORT,
        device_ws_port=DEVICE_MANAGER_LISTEN_WS_PORT,
        frontend_ws_port=DEVICE_MANAGER_FRONTEND_WS_PORT
    )

    # Start the DeviceManager servers in a background thread (only once)
    # This thread will run the asyncio event loop for the WSS/HTTP servers
    # The DeviceManager passes received data to the collector instance
    device_manager_thread = threading.Thread(
        target=run_asyncio_loop,
        args=(device_manager.start(),), # Pass the async start coroutine
        name="DeviceManagerThread",
        daemon=True # Allow the main program to exit even if this thread is running
    )
    device_manager_thread.start()
    logger.info("Device manager thread started.")

    logger.info(f"Starting Flask web server on {FLASK_HOST}:{FLASK_PORT}...")
    # Turn off Flask's reloader when running background threads this way
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True, use_reloader=False)
