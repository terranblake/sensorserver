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
HTTP_PORT = 9091  # Android app's DEFAULT_HTTP_PORT_NO in AppSettings.kt
WS_PORT = 8081    # Android app's DEFAULT_WEBSOCKET_PORT_NO in AppSettings.kt
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

logging.basicConfig(level=logging.INFO, handlers=[console_handler, server_log_handler])
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
# State for automatic logging
auto_log_state = {
    'active': False,
    'end_time': None,
    'description': None # Store description used for auto-log period
}

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
        # Run the sensor logic's main function
        # Pass lock and auto-log state reference to sensor logic
        loop.run_until_complete(sensor_logic.run_sensor_clients(HTTP_ENDPOINT, WS_BASE_URI, data_lock, auto_log_state))
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

# --- Auto Logging Control --- #
auto_log_timer = None # Keep track of the timer

def stop_auto_log():
    """Callback function to stop auto-logging."""
    global auto_log_timer
    with data_lock:
        if auto_log_state['active']:
             logger.info("Automatic sensor logging period finished.")
             auto_log_state['active'] = False
             auto_log_state['end_time'] = None
             auto_log_state['description'] = None # Clear description
             auto_log_timer = None # Clear the timer reference

@app.route('/start_auto_log', methods=['POST'])
def start_auto_log():
    """Starts a period of automatic sensor change logging."""
    global auto_log_timer
    try:
        duration = float(request.form.get('duration', 5.0)) # Default 5s
        description = request.form.get('description', '').strip() # Get description

        if duration <= 0 or duration > 300: # Basic validation (e.g., max 5 mins)
            return jsonify({"error": "Invalid duration (must be > 0 and <= 300 seconds)."}), 400

        with data_lock:
            # Cancel previous timer if one exists
            if auto_log_timer is not None:
                 auto_log_timer.cancel()
                 logger.info("Cancelled previous auto-log timer.")

            now = datetime.now()
            auto_log_state['active'] = True
            auto_log_state['end_time'] = now + timedelta(seconds=duration)
            auto_log_state['description'] = description # Store the description
            logger.info(f"Starting automatic sensor logging for {duration} seconds until {auto_log_state['end_time']} with description: {description if description else '<none>'}.")

            # Schedule stop function
            auto_log_timer = threading.Timer(duration, stop_auto_log)
            auto_log_timer.daemon = True # Allow program to exit even if timer is pending
            auto_log_timer.start()

        return jsonify({"success": True, "message": f"Auto-logging started for {duration}s."})

    except ValueError:
        return jsonify({"error": "Invalid duration format."}), 400
    except Exception as e:
        logger.error(f"Error starting auto-log: {e}", exc_info=True)
        return jsonify({"error": "Internal server error starting auto-log."}), 500

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
    """API endpoint to fetch current sensor state data."""
    with data_lock:
        # Create a deep copy for thread safety before returning
        current_state = copy.deepcopy(sensor_logic.nested_sensor_data)
    # Convert SensorState objects before jsonify
    serializable_state = make_state_serializable(current_state)
    logger.debug(f"Returning /state/data: {json.dumps(serializable_state, indent=2)}") # DEBUG LOG
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
    # Debug=True is useful for development but can cause issues with threads/timers
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False) 