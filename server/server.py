#!/usr/bin/env python3

import logging
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
import threading
import asyncio
import copy
from datetime import datetime, timedelta
import json
import os
import time

# --- Import Sensor Logic ---
# sensor_logic.py is in the same directory
import sensor_logic

# --- Configuration ---
# TODO: Move to a config file or environment variables
SERVER_ADDRESS = "10.0.0.2"  # This needs to match your Android device's actual IP
HTTP_PORT = 9090  # Android app's DEFAULT_HTTP_PORT_NO in AppSettings.kt
WS_PORT = 8080    # Android app's DEFAULT_WEBSOCKET_PORT_NO in AppSettings.kt
HTTP_ENDPOINT = f"http://{SERVER_ADDRESS}:{HTTP_PORT}/sensors"
WS_BASE_URI = f"ws://{SERVER_ADDRESS}:{WS_PORT}"
RAW_LOG_FILE = "raw_data.log"
STATE_LOG_FILE = "state_data.log"
EVENT_LOG_FILE = "event_data.log" # For future use
FLASK_HOST = '0.0.0.0'
FLASK_PORT = 5000

# --- Logging Setup ---
# Basic setup, will be refined
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
# File handler for server logs
server_log_handler = logging.FileHandler("server.log", mode='a')
server_log_handler.setFormatter(log_formatter)

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), handlers=[console_handler, server_log_handler])
logger = logging.getLogger(__name__)

# Configure loggers from sensor_logic to use files
raw_data_logger = logging.getLogger("raw_data")
raw_file_handler = logging.FileHandler(RAW_LOG_FILE, mode='a')
raw_file_handler.setFormatter(logging.Formatter('%(message)s'))
raw_data_logger.addHandler(raw_file_handler)
raw_data_logger.setLevel(logging.INFO)

state_data_logger = logging.getLogger("state_data")
state_file_handler = logging.FileHandler(STATE_LOG_FILE, mode='a')
state_file_handler.setFormatter(logging.Formatter('%(message)s')) # JSON Lines
state_data_logger.addHandler(state_file_handler)
state_data_logger.setLevel(logging.INFO)

# Configure event data logger
event_data_logger = logging.getLogger("event_data")
event_file_handler = logging.FileHandler(EVENT_LOG_FILE, mode='a')
event_file_handler.setFormatter(logging.Formatter('%(message)s')) # JSON Lines
event_data_logger.addHandler(event_file_handler)
event_data_logger.setLevel(logging.INFO)
event_data_logger.propagate = False # Prevent duplication

# Silence some noisy libraries
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)

# --- Shared State & Lock ---
# Access shared data directly from the sensor_logic module
# The lock is primarily for Flask routes accessing the data while sensor_logic modifies it
data_lock = threading.Lock()
# Shared event for auto-logging control
auto_logging_event = threading.Event() # Starts clear/False
auto_logging_timer = None # Holds the timer object

# --- Flask App Setup ---
app = Flask(__name__)

# --- Sensor Logic Thread ---
def run_sensor_loop():
    """Target function for the sensor logic thread."""
    logger.info("Sensor logic thread started.")
    try:
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Run the sensor logic's main function (or a dedicated entry point)
        # Pass necessary config. We might need to refactor run_standalone or create a new entry point.
        # For now, adapting the existing run_standalone concept:
        loop.run_until_complete(sensor_logic.run_sensor_clients(HTTP_ENDPOINT, WS_BASE_URI))
    except Exception as e:
        logger.critical(f"Sensor logic thread encountered a critical error: {e}", exc_info=True)
    finally:
        logger.info("Sensor logic thread finished.")
        if 'loop' in locals() and loop.is_running():
            loop.close()

# --- Routes ---
@app.route('/')
def index():
    # Example: Safely read sensor data for display
    with data_lock:
        # Create a deep copy to be safe when passing to template
        # Accessing the data structure from the imported module
        current_state = copy.deepcopy(sensor_logic.nested_sensor_data) 
    # Render the event page template, passing the state data
    logger.debug(f"Rendering event_page.html with initial state: {json.dumps(make_state_serializable(current_state), indent=2)}") # DEBUG
    return render_template('event_page.html', sensor_state_data=current_state)

@app.route('/logs')
def logs():
    # Placeholder for the log viewer page - Render the log page template
    return render_template('log_page.html')

@app.route('/submit_event', methods=['POST'])
def submit_event():
    """Handles event form submission."""
    try:
        description = request.form.get('description')
        selected_sensors = request.form.getlist('selected_sensors')
        ip_address = request.remote_addr
        timestamp = datetime.now().isoformat()

        if not description:
            # Basic validation: description is required
            # Consider adding flash messages for better UX
            logger.warning("Event submission rejected: Description missing.")
            return redirect(url_for('index')) # Redirect back

        event_log_entry = {
            "timestamp": timestamp,
            "ip_address": ip_address,
            "description": description,
            "selected_sensors": selected_sensors
        }
        event_data_logger.info(json.dumps(event_log_entry))
        logger.info(f"Logged event from {ip_address}: {description[:50]}... ({len(selected_sensors)} sensors)")

    except Exception as e:
        logger.error(f"Error processing event submission: {e}", exc_info=True)
        # Optionally, add a flash message about the error

    # Redirect back to the main page regardless of success/failure for simplicity
    return redirect(url_for('index'))

@app.route('/inference')
def inference():
    """Displays the location inference results page."""
    # Initial state can be passed if needed, but likely JS will fetch dynamically
    return render_template('inference_page.html')

@app.route('/start_auto_event_logging', methods=['POST'])
def start_auto_event_logging():
    """Handles request to start automatic event logging for a duration."""
    global auto_logging_timer # Allow modification of the global timer object
    try:
        duration_str = request.form.get('duration')
        if not duration_str:
            return jsonify({"error": "Missing duration"}), 400
        
        duration = float(duration_str)
        if duration <= 0 or duration > 300: # Add a reasonable upper limit (e.g., 5 minutes)
            return jsonify({"error": "Invalid duration (must be > 0 and <= 300 seconds)"}), 400

        # Cancel any existing timer
        with data_lock: # Lock needed if timer callback modifies shared state (it clears the event)
            if auto_logging_timer:
                auto_logging_timer.cancel()
                logger.info("Cancelled previous auto-logging timer.")

            # Define the action for the timer: clear the event
            def clear_event():
                global auto_logging_timer
                auto_logging_event.clear()
                auto_logging_timer = None # Clear the timer variable once done
                logger.info(f"Auto-logging period of {duration}s finished. Event cleared.")

            # Set the event to signal start
            auto_logging_event.set()
            logger.info(f"Starting auto-logging of state changes for {duration} seconds.")

            # Start the new timer
            auto_logging_timer = threading.Timer(duration, clear_event)
            auto_logging_timer.start()

        return jsonify({"status": "started", "duration": duration}), 200

    except ValueError:
        return jsonify({"error": "Invalid duration format"}), 400
    except Exception as e:
        logger.error(f"Error starting auto-event logging: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

# --- Log Reading Helper ---
def read_last_n_lines(filename, n=100):
    """Reads the last N lines from a file efficiently."""
    try:
        with open(filename, 'rb') as f:
            # Go to the end of the file
            f.seek(0, os.SEEK_END)
            filesize = f.tell()
            # Number of bytes to read (estimate based on avg line length ~100 bytes)
            # Adjust buffer size as needed
            buffer_size = min(filesize, n * 150)
            
            lines = []
            blocks = []
            # Read blocks from the end until we have enough lines or reach the beginning
            while len(lines) < n and f.tell() > 0:
                seek_pos = max(0, f.tell() - buffer_size)
                f.seek(seek_pos, os.SEEK_SET)
                block_data = f.read(f.tell() - seek_pos if f.tell() > seek_pos else buffer_size) # Read what's available
                if not block_data: break # Reached beginning
                
                blocks.insert(0, block_data) # Prepend block
                # Decode carefully, handling potential errors
                lines = b''.join(blocks).decode('utf-8', errors='replace').splitlines()
                # Move cursor back for next read, avoiding re-reading the same block start
                if seek_pos > 0:
                    f.seek(seek_pos)
                else:
                    break # Reached beginning

            return lines[-n:]
    except FileNotFoundError:
        logger.warning(f"Log file not found: {filename}")
        return []
    except Exception as e:
        logger.error(f"Error reading log file {filename}: {e}")
        return []

# --- Helper for JSON Serialization ---
def make_state_serializable(node):
    """Recursively converts SensorState objects in a nested dict to plain dicts."""
    if isinstance(node, sensor_logic.SensorState):
        # Convert SensorState instance to a dictionary
        # Include only the fields the frontend needs (adjust as necessary)
        return {
            'inferred_state': node.inferred_state,
            'last_timestamp': node.last_timestamp.isoformat() if node.last_timestamp else None,
            # Add other relevant fields if needed by JS, e.g.:
            # 'last_value': node.last_value, 
            # 'previous_value': node.previous_value,
            # 'event_detected_time': node.event_detected_time.isoformat() if node.event_detected_time else None
        }
    elif isinstance(node, dict):
        # Recursively process dictionary items
        return {key: make_state_serializable(value) for key, value in node.items()}
    elif isinstance(node, list):
        # Recursively process list items
        return [make_state_serializable(item) for item in node]
    else:
        # Return other types (str, int, float, bool, None) as is
        return node

# --- API Routes ---
@app.route('/logs/data')
def logs_data():
    """API endpoint to fetch log data."""
    log_type = request.args.get('type', 'all')
    try:
        count = int(request.args.get('count', 100))
    except ValueError:
        count = 100

    log_files = {
        'raw': RAW_LOG_FILE,
        'state': STATE_LOG_FILE,
        'event': EVENT_LOG_FILE,
        'server': "server.log" # Assuming server.log is the file used
    }

    lines = []
    if log_type == 'all':
        # Read from all logs - potentially inefficient for large counts
        # Consider limiting 'all' or implementing pagination later
        limit_per_file = max(10, count // len(log_files)) 
        for type_key, filename in log_files.items():
            file_lines = read_last_n_lines(filename, limit_per_file)
            # Add type information for frontend filtering/display
            lines.extend([{ "type": type_key, "content": line} for line in file_lines])
        # Sort merged lines roughly by timestamp (assuming ISO format start)
        # This is imperfect but better than random order
        lines.sort(key=lambda x: x['content'], reverse=True)
        lines = lines[:count] # Apply overall limit after merging
    elif log_type in log_files:
        filename = log_files[log_type]
        file_lines = read_last_n_lines(filename, count)
        lines = [{ "type": log_type, "content": line} for line in file_lines]
        lines.reverse() # Show newest last typically
    else:
        return jsonify({"error": "Invalid log type specified"}), 400

    return jsonify({"logs": lines})

@app.route('/state/data')
def state_data():
    """API endpoint to fetch current sensor state data and location scores."""
    all_scores = None
    with data_lock:
        # Create a deep copy for thread safety before returning
        current_state = copy.deepcopy(sensor_logic.nested_sensor_data)
        # Get latest data needed for scoring
        network_data = sensor_logic.latest_network_data_for_scoring
        pressure_value = sensor_logic.latest_pressure_for_scoring
        data_timestamp = sensor_logic.latest_data_timestamp

    # Calculate scores outside the lock
    if network_data:
        # Check timestamp validity if needed (e.g., ensure data isn't too old)
        # For now, assume data stored is recent enough if present
        logger.debug(f"Calculating scores for API based on data from {data_timestamp}")
        all_scores = sensor_logic.get_all_location_scores(network_data, pressure_value)
    else:
        logger.debug("Skipping score calculation for API: No recent network data available.")

    # Convert SensorState objects before jsonify
    serializable_state = make_state_serializable(current_state)

    # Add the calculated scores to the response
    if all_scores is not None:
         serializable_state['location_scores'] = all_scores
    else:
         serializable_state['location_scores'] = {} # Ensure key exists

    logger.debug(f"Returning /state/data with scores: {json.dumps(serializable_state, indent=2)}") # DEBUG LOG
    return jsonify(serializable_state)

@app.route('/logs/stream')
def logs_stream():
    """Server-Sent Events endpoint to stream new log lines."""
    log_files_to_monitor = {
        'raw': RAW_LOG_FILE,
        'state': STATE_LOG_FILE,
        'event': EVENT_LOG_FILE,
        'server': "server.log"
    }
    # Keep track of the last read position (or size) for each file
    last_positions = { filename: 0 for filename in log_files_to_monitor.values() }
    # Initialize positions to current end of file
    for filename in last_positions:
        try:
            last_positions[filename] = os.path.getsize(filename)
        except OSError:
            last_positions[filename] = 0 # File might not exist yet

    def generate_log_updates():
        while True:
            new_data_found = False
            for log_type, filename in log_files_to_monitor.items():
                try:
                    current_size = os.path.getsize(filename)
                    last_pos = last_positions.get(filename, 0)
                    
                    if current_size > last_pos:
                        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                            for line in new_lines:
                                line = line.strip()
                                if line:
                                    log_entry = { "type": log_type, "content": line }
                                    # Format as SSE message: data: {json_string}\n\n
                                    sse_data = f"data: {json.dumps(log_entry)}\n\n"
                                    yield sse_data
                                    new_data_found = True
                        last_positions[filename] = current_size
                    elif current_size < last_pos:
                         # File was likely rotated or truncated, reset position
                         last_positions[filename] = current_size 

                except FileNotFoundError:
                    # If file appeared after start, update position to 0
                    if last_positions.get(filename, -1) != 0:
                         last_positions[filename] = 0
                    continue # Skip if file doesn't exist
                except Exception as e:
                    # Log error but continue trying
                    logger.error(f"Error reading {filename} for SSE: {e}")
            
            # If no new data was found across all files, sleep briefly
            if not new_data_found:
                time.sleep(1) # Check every 1 second

    # Return a streaming response
    return Response(generate_log_updates(), mimetype='text/event-stream')

# --- Main Execution ---
if __name__ == '__main__':
    logger.info("Starting sensor logic thread...")
    sensor_thread = threading.Thread(target=run_sensor_loop, name="SensorLogicThread", daemon=True)
    sensor_thread.start()

    logger.info(f"Starting Flask server on {FLASK_HOST}:{FLASK_PORT}...")
    # Turn off Flask's reloader when running sensor thread this way
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True, use_reloader=False) 