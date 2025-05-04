#!/usr/bin/env python3

"""
Calibration script for network-based location positioning.

Reads raw network sensor data and annotated location events from log files,
calculates location fingerprints based on network signal characteristics,
and saves these fingerprints to a JSON file.
"""

import json
import datetime
import statistics
import math
import time
import os
import logging
import sys # Import sys for sys.exit()

# --- Configuration ---
# Log files used by the server
RAW_DATA_LOG = 'raw_data.log'
EVENT_DATA_LOG = 'event_data.log'
# Output file for fingerprints
CALIBRATION_DATA_FILE = 'location_fingerprints.json'
# Time window (seconds) around an event to look for network data
TIME_WINDOW_SECONDS = 10 # Increased slightly from sample
# Specific time window (seconds) for pressure data
PRESSURE_TIME_WINDOW_SECONDS = 2

# Define sensor types relevant for calibration
NETWORK_SENSOR_TYPES = {'android.sensor.wifi_scan', 'android.sensor.network_scan'}
PRESSURE_SENSOR_TYPE = 'android.sensor.pressure'
RELEVANT_SENSOR_TYPES = NETWORK_SENSOR_TYPES | {PRESSURE_SENSOR_TYPE}

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[console_handler])
logger = logging.getLogger(__name__)

# --- Data Loading and Parsing (Adapted from sample) ---

def load_log_entries(log_file):
    """Loads JSON entries from a log file. Returns None if file not found."""
    entries = []
    logger.info(f"Attempting to load log file: {log_file}")
    if not os.path.exists(log_file):
        logger.error(f"Required log file not found: {log_file}")
        return None # Indicate failure
    try:
        with open(log_file, 'r') as f:
            for i, line in enumerate(f):
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"Error decoding JSON from {log_file} (line {i+1}): {e} in line: {line.strip()}")
    except FileNotFoundError:
        logger.error(f"Log file not found: {log_file}")
    logger.info(f"Loaded {len(entries)} entries from {log_file}")
    return entries

def parse_timestamp(timestamp_str):
    """Parses the timestamp string into a datetime object."""
    if not timestamp_str: return None
    try:
        # Handle potential timezone info (e.g., +00:00) or 'Z'
        if timestamp_str.endswith('Z'):
             timestamp_str = timestamp_str[:-1] + '+00:00'
        # Handle formats with or without microseconds
        if '.' in timestamp_str:
            # Try parsing with microseconds
             try:
                 return datetime.datetime.fromisoformat(timestamp_str)
             except ValueError:
                  # Try parsing without microseconds if the first attempt failed
                  base_ts_str = timestamp_str.split('.')[0]
                  # Re-append timezone if present
                  if '+' in timestamp_str or '-' in timestamp_str.split('T')[-1]:
                        tz_part = timestamp_str.split('T')[-1]
                        tz_offset_part = ""
                        if '+' in tz_part: tz_offset_part = '+' + tz_part.split('+')[-1]
                        elif '-' in tz_part: tz_offset_part = '-' + tz_part.split('-')[-1]
                        base_ts_str += tz_offset_part
                  return datetime.datetime.fromisoformat(base_ts_str)
        else:
             # Parse without microseconds
             return datetime.datetime.fromisoformat(timestamp_str)

    except ValueError as e:
        logger.warning(f"Error parsing timestamp: {timestamp_str} - {e}")
        return None

# --- Event Data Processing (Adapted from sample) ---

def extract_location_from_description(description):
    """
    Simple logic to extract a location from the event description.
    This needs to be robust based on your annotation style.
    Looks for keywords. Case-insensitive matching.
    """
    if not description: return "Unknown Location"
    description_lower = description.lower()
    # Define known location keywords precisely
    # Consider making this configurable or using more advanced NLP if needed
    location_keywords = [
        "my room", "bedroom", "kitchen", "living room", "basement bathroom",
        "office", "backyard near gate", "backyard near door", "basement at desk",
        "laundry room", "crawl space", "basement stairs", "front door",
        "dining room", "entryway stairs", "middle of living room",
        "living room stairs", "office hallway", "office bathroom", "bedroom stairs"
    ] # Add more specific locations based on event_data.log

    found_location = "Unknown Location"
    for keyword in location_keywords:
        if keyword in description_lower:
            # Basic check to avoid partial matches like "room" in "bathroom"
            # This is simple; more robust checks might use word boundaries
            if found_location == "Unknown Location" or len(keyword) > len(found_location):
                 # Prefer longer/more specific matches if multiple keywords are present
                 found_location = keyword.strip()

    # Specific overrides or common phrases
    if "in office" in description_lower and "hallway" not in description_lower and "bathroom" not in description_lower:
        found_location = "office"
    if "in bedroom" in description_lower and "stairs" not in description_lower:
        found_location = "bedroom"
    if "in kitchen" in description_lower:
        found_location = "kitchen"

    logger.debug(f"Extracted location '{found_location}' from description: '{description}'")
    return found_location


def get_annotated_network_events(event_entries):
    """Filters event entries for network sensor annotations and extracts location."""
    network_events = []
    logger.info("Extracting annotated network events...")
    for event in event_entries:
        # Check if *any* sensor was selected, even if not network,
        # as the annotation time itself is the key point.
        # We will find network data near this time from the raw logs.
        if event.get('description'):
            location = extract_location_from_description(event['description'])
            if location != "Unknown Location":
                event_timestamp = parse_timestamp(event.get('timestamp'))
                if event_timestamp:
                    network_events.append({
                        'timestamp': event_timestamp,
                        'location': location,
                        'description': event['description'] # Keep original description for context
                    })
                else:
                     logger.warning(f"Skipping event due to unparsable timestamp: {event.get('timestamp')}")
            else:
                 logger.debug(f"Skipping event, location unknown: {event.get('description')}")
        else:
            logger.debug(f"Skipping event, no description found: {event}")

    logger.info(f"Found {len(network_events)} events with valid timestamps and known locations.")
    # Log counts per location
    location_counts = {}
    for ev in network_events:
        loc = ev['location']
        location_counts[loc] = location_counts.get(loc, 0) + 1
    logger.info(f"Event counts per location: {location_counts}")
    return network_events

# --- Matching Raw Data to Events (Adapted from sample) ---

def find_closest_calibration_data(event_timestamp, raw_entries, time_window_seconds):
    """
    Finds relevant sensor entries (network scans, pressure) in raw_entries
    that are within a time window of the event_timestamp.
    Consolidates Wifi, Bluetooth, and Pressure data.
    """
    closest_data = []
    if not event_timestamp: return closest_data

    # Calculate windows based on the event timestamp
    network_window_start = event_timestamp - datetime.timedelta(seconds=TIME_WINDOW_SECONDS)
    network_window_end = event_timestamp + datetime.timedelta(seconds=TIME_WINDOW_SECONDS)
    pressure_window_start = event_timestamp - datetime.timedelta(seconds=PRESSURE_TIME_WINDOW_SECONDS)
    pressure_window_end = event_timestamp + datetime.timedelta(seconds=PRESSURE_TIME_WINDOW_SECONDS)

    # Filter raw entries by time window and relevant sensor types
    relevant_raw_entries = []
    for entry in raw_entries:
         entry_timestamp_str = entry.get('timestamp')
         entry_sensor_type = entry.get('sensor_type')
         if entry_timestamp_str and entry_sensor_type:
              parsed_entry_ts = parse_timestamp(entry_timestamp_str)
              if parsed_entry_ts and entry_sensor_type in RELEVANT_SENSOR_TYPES:
                   # Apply specific window based on sensor type
                   include_entry = False
                   if entry_sensor_type in NETWORK_SENSOR_TYPES:
                        if network_window_start <= parsed_entry_ts <= network_window_end:
                             include_entry = True
                   elif entry_sensor_type == PRESSURE_SENSOR_TYPE:
                        if pressure_window_start <= parsed_entry_ts <= pressure_window_end:
                             include_entry = True

                   if include_entry:
                        relevant_raw_entries.append(entry)

    logger.debug(f"Found {len(relevant_raw_entries)} relevant raw sensor entries within respective time windows for event at {event_timestamp}")

    # Extract and consolidate sensor data
    consolidated_data = []
    for entry in relevant_raw_entries:
        entry_sensor_type = entry['sensor_type']
        raw_data = entry.get('raw_data', {})
        values = raw_data.get('values') # This can be a list (wifi, pressure) or dict (network)

        # --- Network Data Extraction ---
        if entry_sensor_type == 'android.sensor.wifi_scan' and isinstance(values, list):
             consolidated_data.extend([
                 {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                 for res in values if isinstance(res, dict) and res.get('bssid') and res.get('rssi') is not None
             ])
        elif entry_sensor_type == 'android.sensor.network_scan' and isinstance(values, dict):
             wifi_results = values.get('wifiResults', [])
             bluetooth_results = values.get('bluetoothResults', [])
             consolidated_data.extend([
                 {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                 for res in wifi_results if isinstance(res, dict) and res.get('bssid') and res.get('rssi') is not None
             ])
             consolidated_data.extend([
                 {'type': 'bluetooth', 'id': res.get('address'), 'name': res.get('name'), 'rssi': res.get('rssi')}
                 for res in bluetooth_results if isinstance(res, dict) and res.get('address') and res.get('rssi') is not None
             ])
        # --- Pressure Data Extraction ---
        elif entry_sensor_type == PRESSURE_SENSOR_TYPE and isinstance(values, list) and len(values) > 0:
             # Pressure usually has one value
             pressure_value = values[0]
             if isinstance(pressure_value, (int, float)):
                  consolidated_data.append({'type': 'pressure', 'id': 'value', 'value': pressure_value})
             else:
                  logger.warning(f"Non-numeric pressure value found: {pressure_value} in entry: {entry}")

    # --- Remove Duplicates --- #
    # For network: Keep the one with the strongest signal (highest RSSI)
    # For pressure: Keep the most recent one (though duplicates are less likely)
    unique_data = {}
    # Sort by timestamp primarily to keep latest pressure if duplicates exist
    # relevant_raw_entries was not sorted, so sort consolidated_data based on original entry time if possible
    # For simplicity, we'll just overwrite for network and keep first for pressure (or average later)
    # Let's refine: keep *all* pressure values for stats calculation later.

    final_consolidated_data = []
    network_duplicates_check = {}
    pressure_values_temp = []

    for item in consolidated_data:
        item_type = item.get('type')
        if item_type in ('wifi', 'bluetooth'):
            item_id = item.get('id')
            item_rssi = item.get('rssi')
            if item_id and item_rssi is not None:
                network_key = (item_type, item_id)
                if network_key not in network_duplicates_check or item_rssi > network_duplicates_check[network_key]['rssi']:
                    network_duplicates_check[network_key] = item
        elif item_type == 'pressure':
            # Keep all valid pressure readings for now; will be averaged per location later
            if item.get('value') is not None:
                final_consolidated_data.append(item) # Add pressure directly

    # Add the unique network entries
    final_consolidated_data.extend(list(network_duplicates_check.values()))

    logger.debug(f"Consolidated to {len(final_consolidated_data)} unique sensor readings for event at {event_timestamp}")
    return final_consolidated_data


# --- Building Location Fingerprints (Adapted from sample) ---

def build_location_fingerprints(annotated_network_events, raw_entries, time_window_seconds):
    """
    Builds a fingerprint for each location based on the median and
    standard deviation of sensor readings (Network RSSI, Pressure)
    found during annotated events.
    """
    logger.info("Building location fingerprints (including pressure)...")
    location_data = {} # {location: { (type, id_or_metric): [value1, value2, ...], ... }, ...}

    for i, event in enumerate(annotated_network_events):
        location = event['location']
        event_timestamp = event['timestamp']
        logger.debug(f"Processing event {i+1}/{len(annotated_network_events)}: Loc='{location}', Time={event_timestamp}")

        # Find sensor data around the event timestamp
        data_around_event = find_closest_calibration_data(event_timestamp, raw_entries, time_window_seconds)

        if not data_around_event:
            logger.warning(f"No relevant sensor data found near event at {event_timestamp} for location '{location}'")
            continue

        if location not in location_data:
            location_data[location] = {}

        for reading in data_around_event:
            reading_type = reading.get('type')
            reading_id = str(reading.get('id')) # Ensure ID is string (will be 'value' for pressure)
            value = None

            if reading_type in ('wifi', 'bluetooth'):
                value = reading.get('rssi')
                data_key = (reading_type, reading_id)
            elif reading_type == 'pressure':
                value = reading.get('value')
                data_key = (reading_type, reading_id) # Key will be ('pressure', 'value')
            else:
                continue # Skip unknown types

            if value is None:
                 logger.warning(f"Missing value for {reading_type} / {reading_id} in event {i+1}")
                 continue

            if data_key not in location_data[location]:
                location_data[location][data_key] = []
            location_data[location][data_key].append(value)

    # Calculate median and standard deviation for each reading type at each location
    location_fingerprints = {} # {location: { (type, id): {'median': ..., 'std_dev': ...}, ... }, ...}
    logger.info("Calculating fingerprint statistics (median, std dev) for network and pressure...")
    for location, collected_data in location_data.items():
        location_fingerprints[location] = {}
        logger.debug(f"Calculating stats for location: '{location}' ({len(collected_data)} sensor keys)")
        for data_key, values in collected_data.items():
            sensor_type, sensor_id = data_key # Unpack the key
            metric_name = "RSSI" if sensor_type in ('wifi', 'bluetooth') else "Pressure"

            if len(values) > 0:
                try:
                    # Ensure all values are numeric
                    numeric_values = [v for v in values if isinstance(v, (int, float))]
                    if len(numeric_values) != len(values):
                        logger.warning(f"Non-numeric values found for {data_key} at {location}. Original: {len(values)}, Numeric: {len(numeric_values)}. Using only numeric.")
                    if not numeric_values:
                        logger.warning(f"No numeric values left for {data_key} at {location}. Skipping stats.")
                        continue

                    median_val = statistics.median(numeric_values)
                    std_dev_val = 0.0
                    if len(numeric_values) > 1:
                         std_dev_val = statistics.stdev(numeric_values)

                    location_fingerprints[location][data_key] = {
                        'median': median_val,
                        'std_dev': std_dev_val,
                        'num_samples': len(numeric_values) # Use count of numeric samples
                    }
                    logger.debug(f"  Sensor {data_key}: Median {metric_name}={median_val:.2f}, StdDev={std_dev_val:.2f}, Samples={len(numeric_values)}")

                except statistics.StatisticsError as e:
                     logger.error(f"Statistics error for {data_key} at {location}: {e}. Values: {numeric_values}")
                except Exception as e:
                     logger.error(f"Unexpected error calculating stats for {data_key} at {location}: {e}. Values: {numeric_values}")
            else:
                 logger.warning(f"  Sensor {data_key}: No values collected.")

    # Log summary of generated fingerprints
    for loc, nets in location_fingerprints.items():
        logger.info(f"Generated fingerprint for '{loc}' with {len(nets)} sensor metrics.")

    return location_fingerprints

def save_fingerprints(fingerprints, filename):
    """Saves location fingerprints to a JSON file."""
    logger.info(f"Saving fingerprints to {filename}...")
    # Convert tuple keys to strings for JSON serialization
    serializable_fingerprints = {}
    for location, sensors_data in fingerprints.items():
        serializable_fingerprints[location] = {}
        for (stype, sid_or_metric), data in sensors_data.items():
             # Use a consistent string key format, ensuring id/metric is string
             key_str = f"{stype}_{str(sid_or_metric)}"
             # Rename keys slightly for clarity in JSON output
             output_data = {
                 'median_value': data.get('median'),
                 'std_dev_value': data.get('std_dev'),
                 'num_samples': data.get('num_samples')
             }
             serializable_fingerprints[location][key_str] = output_data

    try:
        with open(filename, 'w') as f:
            json.dump(serializable_fingerprints, f, indent=4)
        logger.info(f"Successfully saved fingerprints to {filename}")
    except IOError as e:
        logger.error(f"Failed to write fingerprints to {filename}: {e}")
    except TypeError as e:
        logger.error(f"Serialization error saving fingerprints: {e}")


# --- Main Execution ---

def main():
    """Main function to run the calibration process."""
    logger.info("--- Starting Location Fingerprint Calibration ---")

    # 1. Load Raw Data
    raw_entries = load_log_entries(RAW_DATA_LOG)
    if raw_entries is None:
        logger.critical(f"Failed to load {RAW_DATA_LOG}. Cannot proceed.")
        sys.exit(1) # Exit with error code
    if not raw_entries:
        logger.error("Raw data log is empty. Cannot proceed.")
        sys.exit(1)

    # 2. Load Event Data
    event_entries = load_log_entries(EVENT_DATA_LOG)
    if event_entries is None:
        logger.critical(f"Failed to load {EVENT_DATA_LOG}. Cannot proceed.")
        sys.exit(1) # Exit with error code
    if not event_entries:
        logger.error("Event data log is empty. Cannot proceed.")
        sys.exit(1)

    # 3. Extract Annotated Events (relevant locations and timestamps)
    annotated_network_events = get_annotated_network_events(event_entries)
    if not annotated_network_events:
        logger.error("No valid annotated network events found. Cannot create fingerprints.")
        return

    # 4. Build Fingerprints
    location_fingerprints = build_location_fingerprints(annotated_network_events, raw_entries, TIME_WINDOW_SECONDS)

    # 5. Save Fingerprints
    if location_fingerprints:
        save_fingerprints(location_fingerprints, CALIBRATION_DATA_FILE)
    else:
        logger.warning("No location fingerprints were generated. Nothing to save.")

    logger.info("--- Calibration Process Finished ---")

if __name__ == "__main__":
    # Removed dummy file creation logic
    main() 