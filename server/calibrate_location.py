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

# --- Configuration ---
# Log files used by the server
RAW_DATA_LOG = 'raw_data.log'
EVENT_DATA_LOG = 'event_data.log'
# Output file for fingerprints
CALIBRATION_DATA_FILE = 'location_fingerprints.json'
# Time window (seconds) around an event to look for network data
TIME_WINDOW_SECONDS = 10 # Increased slightly from sample

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[console_handler])
logger = logging.getLogger(__name__)

# --- Data Loading and Parsing (Adapted from sample) ---

def load_log_entries(log_file):
    """Loads JSON entries from a log file."""
    entries = []
    logger.info(f"Attempting to load log file: {log_file}")
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

def find_closest_network_data(event_timestamp, raw_entries, time_window_seconds):
    """
    Finds network scan entries in raw_entries that are within a time window
    of the event_timestamp. Consolidates Wifi and Bluetooth data.
    """
    closest_network_data = []
    if not event_timestamp: return closest_network_data

    window_start = event_timestamp - datetime.timedelta(seconds=time_window_seconds)
    window_end = event_timestamp + datetime.timedelta(seconds=time_window_seconds)

    # Filter raw entries by time window and sensor type
    relevant_raw_entries = []
    for entry in raw_entries:
         entry_timestamp_str = entry.get('timestamp')
         entry_sensor_type = entry.get('sensor_type')
         if entry_timestamp_str and entry_sensor_type:
              parsed_entry_ts = parse_timestamp(entry_timestamp_str)
              if parsed_entry_ts:
                   is_network_sensor = (
                        entry_sensor_type == 'android.sensor.wifi_scan' or
                        entry_sensor_type == 'android.sensor.network_scan'
                   )
                   if is_network_sensor and window_start <= parsed_entry_ts <= window_end:
                        relevant_raw_entries.append(entry)

    logger.debug(f"Found {len(relevant_raw_entries)} raw network entries within window for event at {event_timestamp}")

    # Extract and consolidate network scan results
    consolidated_data = []
    for entry in relevant_raw_entries:
        entry_sensor_type = entry['sensor_type']
        raw_data = entry.get('raw_data', {})
        values = raw_data.get('values') # This can be a list (wifi) or dict (network)

        if entry_sensor_type == 'android.sensor.wifi_scan' and isinstance(values, list):
             # Handle the format where wifi_scan values is a list of results
             consolidated_data.extend([
                 {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                 for res in values if isinstance(res, dict) and res.get('bssid') and res.get('rssi') is not None
             ])
        elif entry_sensor_type == 'android.sensor.network_scan' and isinstance(values, dict):
             # Handle the format where network_scan contains wifi and bluetooth results
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

    # Remove duplicates based on type and ID within this time window's data
    # Keep the one with the strongest signal (lowest RSSI absolute value / least negative)
    unique_network_data = {}
    for item in consolidated_data:
        if item.get('id') and item.get('rssi') is not None: # Ensure ID and RSSI exist
            network_key = (item['type'], item['id'])
            if network_key not in unique_network_data or item['rssi'] > unique_network_data[network_key]['rssi']:
                unique_network_data[network_key] = item

    closest_network_data = list(unique_network_data.values())
    logger.debug(f"Consolidated to {len(closest_network_data)} unique network devices for event at {event_timestamp}")
    return closest_network_data


# --- Building Location Fingerprints (Adapted from sample) ---

def build_location_fingerprints(annotated_network_events, raw_entries, time_window_seconds):
    """
    Builds a fingerprint for each location based on the median RSSI
    and standard deviation of networks found during annotated events.
    """
    logger.info("Building location fingerprints...")
    location_network_data = {} # {location: { (type, id): [rssi1, rssi2, ...], ... }, ...}

    for i, event in enumerate(annotated_network_events):
        location = event['location']
        event_timestamp = event['timestamp']
        logger.debug(f"Processing event {i+1}/{len(annotated_network_events)}: Loc='{location}', Time={event_timestamp}")

        # Find network data around the event timestamp
        network_data_around_event = find_closest_network_data(event_timestamp, raw_entries, time_window_seconds)

        if not network_data_around_event:
            logger.warning(f"No network data found near event at {event_timestamp} for location '{location}'")
            continue

        if location not in location_network_data:
            location_network_data[location] = {}

        for network in network_data_around_event:
            # Ensure network ID is treated as string for consistency
            network_id_str = str(network.get('id'))
            if not network_id_str: continue # Skip if ID is missing

            network_key = (network['type'], network_id_str)
            rssi_value = network.get('rssi')
            if rssi_value is None: continue # Skip if RSSI is missing

            if network_key not in location_network_data[location]:
                location_network_data[location][network_key] = []
            location_network_data[location][network_key].append(rssi_value)

    # Calculate median and standard deviation RSSI for each network at each location
    location_fingerprints = {} # {location: { (type, id): {'median_rssi': ..., 'std_dev_rssi': ...}, ... }, ...}
    logger.info("Calculating fingerprint statistics (median, std dev)...")
    for location, network_data in location_network_data.items():
        location_fingerprints[location] = {}
        logger.debug(f"Calculating stats for location: '{location}' ({len(network_data)} networks)")
        for network_key, rssi_values in network_data.items():
            if len(rssi_values) > 0:
                try:
                    median_rssi = statistics.median(rssi_values)
                    # Calculate standard deviation, handle case with only one data point
                    std_dev_rssi = 0.0
                    if len(rssi_values) > 1:
                         # Ensure all values are numbers before calculating stdev
                         if all(isinstance(x, (int, float)) for x in rssi_values):
                              std_dev_rssi = statistics.stdev(rssi_values)
                         else:
                              logger.warning(f"Non-numeric RSSI value found for {network_key} at {location}, cannot calculate std dev. Values: {rssi_values}")
                    elif len(rssi_values) == 1 and not isinstance(rssi_values[0], (int, float)):
                         logger.warning(f"Single non-numeric RSSI value found for {network_key} at {location}. Value: {rssi_values[0]}")
                         median_rssi = None # Cannot use non-numeric median

                    if median_rssi is not None:
                        location_fingerprints[location][network_key] = {
                            'median_rssi': median_rssi,
                            'std_dev_rssi': std_dev_rssi,
                            'num_samples': len(rssi_values) # Add sample count for info
                        }
                        logger.debug(f"  Network {network_key}: Median={median_rssi:.2f}, StdDev={std_dev_rssi:.2f}, Samples={len(rssi_values)}")
                    else:
                         logger.warning(f"  Skipping Network {network_key} due to non-numeric median.")

                except statistics.StatisticsError as e:
                     logger.error(f"Statistics error for {network_key} at {location}: {e}. Values: {rssi_values}")
                except Exception as e:
                     logger.error(f"Unexpected error calculating stats for {network_key} at {location}: {e}. Values: {rssi_values}")
            else:
                 logger.warning(f"  Network {network_key}: No RSSI values collected.")

    # Log summary of generated fingerprints
    for loc, nets in location_fingerprints.items():
        logger.info(f"Generated fingerprint for '{loc}' with {len(nets)} networks.")

    return location_fingerprints

def save_fingerprints(fingerprints, filename):
    """Saves location fingerprints to a JSON file."""
    logger.info(f"Saving fingerprints to {filename}...")
    # Convert tuple keys to strings for JSON serialization
    serializable_fingerprints = {}
    for location, networks in fingerprints.items():
        serializable_fingerprints[location] = {}
        for (ntype, nid), data in networks.items():
             # Use a consistent string key format, ensuring nid is string
             key_str = f"{ntype}_{str(nid)}"
             serializable_fingerprints[location][key_str] = data

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
    if not raw_entries:
        logger.error("No raw data loaded. Cannot proceed.")
        return

    # 2. Load Event Data
    event_entries = load_log_entries(EVENT_DATA_LOG)
    if not event_entries:
        logger.error("No event data loaded. Cannot proceed.")
        return

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
    # Ensure log files exist for demonstration if they don't
    # This part is less critical if run within the server context where logs exist
    if not os.path.exists(RAW_DATA_LOG):
        logger.warning(f"{RAW_DATA_LOG} not found. Creating dummy file.")
        # Create minimal dummy file if needed, calibration likely won't work well
        with open(RAW_DATA_LOG, 'w') as f:
             # Sample network scan entry
             dummy_scan = {
                "timestamp": datetime.datetime.now().isoformat(),
                "sensor_type": "android.sensor.network_scan",
                "raw_data": {
                    "type": "android.sensor.network_scan",
                    "values": {
                        "bluetoothResults": [{"address": "AA:BB:CC:DD:EE:FF", "name": "DummyBT", "rssi": -70, "timestamp": int(time.time()*1000)}],
                        "wifiResults": [{"bssid": "11:22:33:44:55:66", "frequency": 2412, "rssi": -60, "ssid": "DummyWiFi", "timestamp": int(time.time()*1000)}]
                    }
                }
             }
             f.write(json.dumps(dummy_scan) + '\n')

    if not os.path.exists(EVENT_DATA_LOG):
        logger.warning(f"{EVENT_DATA_LOG} not found. Creating dummy file.")
        # Create minimal dummy file if needed
        with open(EVENT_DATA_LOG, 'w') as f:
             dummy_event = {
                 "timestamp": datetime.datetime.now().isoformat(),
                 "ip_address": "127.0.0.1",
                 "description": "dummy event in the kitchen", # Ensure a keyword is present
                 "selected_sensors": ["android.sensor.network_scan"]
             }
             f.write(json.dumps(dummy_event) + '\n')

    main() 