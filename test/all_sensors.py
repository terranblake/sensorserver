import asyncio
import logging
import time # Keep for timestamp in final status display?
from datetime import datetime # Keep for timestamp
import sys
import os # For clearing screen
from collections import defaultdict, deque # Deque might still be needed for history if used
import copy # For deepcopying nested_sensor_data for display

# --- Import Core Logic ---
from sensor_logic import (
    SensorState, # Potentially needed if display logic accesses attributes directly
    get_available_sensors,
    MultiSensorClient,
    GpsClient,
    nested_sensor_data # Access the shared state dictionary
    # Don't import helpers like normalize_key, update_nested_data*, get_sensor_group etc.
    # Don't import inference logic like update_inferred_state, magnitude, haversine
)

# --- Configuration (Keep relevant parts for this script) ---
SERVER_ADDRESS = "10.0.0.2"
HTTP_PORT = 9091
WS_PORT = 8081
HTTP_ENDPOINT = f"http://{SERVER_ADDRESS}:{HTTP_PORT}/sensors"
WS_BASE_URI = f"ws://{SERVER_ADDRESS}:{WS_PORT}"
# GPS_SEND_INTERVAL now in sensor_logic
LOG_FILE = "sensor_data.log" # Main log file for this script's operations
TERMINAL_REFRESH_RATE = 0.5 # Faster refresh for more responsive state
TREE_INDENT = "  " # Indentation string for the tree

# --- Logging Setup (Configure for this script) ---
# Configure the root logger for console output and general script logs
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
# File handler for general script logs
script_log_handler = logging.FileHandler(LOG_FILE, mode='a')
script_log_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[console_handler, script_log_handler])
logger = logging.getLogger(__name__) # Logger for this script

# Configure the raw_data_logger from sensor_logic to use a file
# Note: sensor_logic.py already has a logger named 'raw_data'
raw_data_logger = logging.getLogger("raw_data")
raw_data_file_handler = logging.FileHandler("raw_data.log", mode='a') # New dedicated raw data log
raw_data_file_handler.setFormatter(logging.Formatter('%(message)s')) # Only message
raw_data_logger.addHandler(raw_data_file_handler)
raw_data_logger.setLevel(logging.INFO)
raw_data_logger.propagate = False # Keep this

# Silence DEBUG messages from the websockets library (if needed, though ws import removed)
# logging.getLogger("websockets.client").setLevel(logging.INFO)
# Might need to silence aiohttp if too verbose
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)


# --- Shared State ---
# nested_sensor_data is now imported from sensor_logic
# data_lock = asyncio.Lock() # If locking is needed, import Lock and manage access here

# --- Sensor State Class ---
# Class SensorState moved to sensor_logic.py

# --- Helper Functions ---
# get_sensor_group moved to sensor_logic.py
# normalize_key moved to sensor_logic.py
# update_nested_data_with_grouping moved to sensor_logic.py
# update_nested_data moved to sensor_logic.py
# initialize_nested_keys moved to sensor_logic.py

# --- Inference Logic ---
# magnitude moved to sensor_logic.py
# haversine moved to sensor_logic.py
# update_inferred_state moved to sensor_logic.py

# --- Sensor Discovery ---
# get_available_sensors moved to sensor_logic.py

# --- Tree Display (Remains Here) ---
def print_tree(node, indent=""):
    """Recursively prints the nested dictionary, showing inferred state for SensorState leaves."""
    if not isinstance(node, dict):
        print(f"{indent}└─ Error: Expected dict, got {type(node)}")
        return

    # Sort keys for consistent display
    try:
        sorted_keys = sorted(node.keys())
    except Exception as e:
        print(f"{indent}└─ Error sorting keys: {e} (Keys: {list(node.keys())})")
        return

    for key in sorted_keys:
        value = node[key]
        # Check if value is a SensorState instance imported from sensor_logic
        if isinstance(value, SensorState):
            # Leaf node: print the inferred state
            print(f"{indent}{key}: {value.inferred_state}")
        elif isinstance(value, dict):
            print(f"{indent}{key}:")
            print_tree(value, indent + TREE_INDENT)
        else:
            # Should not happen with SensorState structure, but print if it does
            print(f"{indent}{key}: [Unexpected Value Type: {type(value)}] {value}")

# --- Simple Terminal Display (Remains Here) ---
async def display_simple_status():
    """Task to continuously print the status tree to the terminal."""
    logger.info("Tree display task started.")
    clear_command = 'cls' if os.name == 'nt' else 'clear'
    while True:
        os.system(clear_command)
        print(f"--- Sensor Status @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} --- (Press Ctrl+C to stop)")
        try:
             # Use the imported nested_sensor_data
             # Acquire lock if implemented
             # async with data_lock:
             data_copy = copy.deepcopy(nested_sensor_data)
             # Release lock
             if not data_copy:
                 print("Waiting for sensor discovery or first data...")
             else:
                 print_tree(data_copy)
        except Exception as e:
             print(f"Error generating tree: {e}")
             logger.error(f"Error displaying tree: {e}", exc_info=True)
        print("------------------------------------------")
        print(f"(Updating every {TERMINAL_REFRESH_RATE}s, logging script status to {LOG_FILE}, raw data to raw_data.log)", flush=True)
        await asyncio.sleep(TERMINAL_REFRESH_RATE)

# --- WebSocket Client Classes ---
# Class MultiSensorClient moved to sensor_logic.py
# Class GpsClient moved to sensor_logic.py

# --- Main Execution (Remains Here, uses imported components) ---
async def main():
    logger.info("--- Starting Sensor Monitor (Refactored) ---")
    # Initialize nested data structure (happens within get_available_sensors)
    # global nested_sensor_data # Not needed if just accessing imported module variable
    # nested_sensor_data = {} # Initialization done in sensor_logic

    # Perform sensor discovery using the imported function
    standard_sensor_types = await get_available_sensors(HTTP_ENDPOINT)
    # Note: get_available_sensors now calls initialize_nested_keys within sensor_logic

    tasks = []
    if standard_sensor_types:
        # Create client instances using imported classes
        multi_client = MultiSensorClient(base_uri=WS_BASE_URI, sensor_types=standard_sensor_types)
        tasks.append(asyncio.create_task(multi_client.connect_and_receive(), name="MultiSensorClient"))
    else:
        logger.warning("No standard sensors discovered or an error occurred. Skipping multi-sensor client.")

    # Create GPS client instance
    gps_client = GpsClient(base_uri=WS_BASE_URI)
    tasks.append(asyncio.create_task(gps_client.connect_and_receive(), name="GpsClient"))

    # Start the display task (defined in this file)
    display_task = asyncio.create_task(display_simple_status(), name="DisplaySimpleStatus")
    tasks.append(display_task)

    if len(tasks) <= 1: # Only display task running
        logger.error("Could not create any client tasks (only display task running). Exiting.")
        if display_task and not display_task.done(): display_task.cancel()
        return

    logger.info(f"Starting {len(tasks)} tasks (including display)...")
    # Wait for the first task to complete (likely a client error or the display task being cancelled)
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in done:
        try:
            # Attempt to retrieve result to trigger exceptions
            task.result()
            logger.info(f"Task {task.get_name()} finished unexpectedly.")
        except asyncio.CancelledError:
             logger.info(f"Task {task.get_name()} was cancelled.")
        except Exception as e:
            # Log exceptions from completed tasks (clients or display)
            logger.error(f"Task {task.get_name()} raised exception: {e}", exc_info=True) # Use exc_info=True here

    logger.info(f"First task completed. Cancelling {len(pending)} pending tasks...")
    for task in pending:
        task.cancel()
    # Wait for pending tasks to finish cancelling
    await asyncio.gather(*pending, return_exceptions=True)

    logger.info("All tasks have completed or been cancelled.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Client stopped by user (KeyboardInterrupt in __main__)")
    except Exception as e:
         logger.critical(f"Unhandled exception in main execution: {e}", exc_info=True)
    finally:
        # Final tree print uses the same print_tree function
        clear_command = 'cls' if os.name == 'nt' else 'clear'
        # Delay slightly to allow final logs to flush?
        time.sleep(0.1)
        os.system(clear_command)
        print("--- FINAL SENSOR STATUS ---")
        try:
             # async with data_lock: # If lock is used
             data_copy = copy.deepcopy(nested_sensor_data) # Access imported state
             if not data_copy:
                 print("No data received or structure empty.")
             else:
                 print_tree(data_copy)
        except Exception as e:
             print(f"Error generating final tree: {e}")
        print("--------------------------- (Script Ended)")
        logger.info("Shutting down application.")


# Removed sections:
# - SensorState class
# - Helper functions (get_sensor_group, normalize_key, update_nested_data*, initialize_nested_keys)
# - Inference logic (magnitude, haversine, update_inferred_state)
# - Sensor Discovery function (get_available_sensors)
# - WebSocket Client Classes (MultiSensorClient, GpsClient)
# - Redundant imports
# - Old logging setup (replaced)

# Note: The original file's code was refactored to use the new sensor_logic module.
# The refactored file is now stored in the same directory as the original file. 