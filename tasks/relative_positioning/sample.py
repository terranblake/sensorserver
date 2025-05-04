import json
import datetime
import statistics
import math
import time
import os

# Define the paths to your log files
RAW_DATA_LOG = 'raw_data.log'
EVENT_DATA_LOG = 'event_data.log'
CALIBRATION_DATA_FILE = 'location_fingerprints.json'
RELATIVE_POSITIONS_FILE = 'relative_positions.json' # Optional, for future use

# --- Data Loading and Parsing ---

def load_log_entries(log_file):
    """Loads JSON entries from a log file."""
    entries = []
    try:
        with open(log_file, 'r') as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON from {log_file}: {e} in line: {line.strip()}")
    except FileNotFoundError:
        print(f"Log file not found: {log_file}")
    return entries

def parse_timestamp(timestamp_str):
    """Parses the timestamp string into a datetime object."""
    # Assuming the format is 'YYYY-MM-DDTHH:MM:SS.ffffff'
    try:
        return datetime.datetime.fromisoformat(timestamp_str)
    except ValueError as e:
        print(f"Error parsing timestamp: {timestamp_str} - {e}")
        return None

# --- Event Data Processing ---

def extract_location_from_description(description):
    """
    Simple logic to extract a location from the event description.
    This needs to be robust based on your annotation style.
    Example: Assumes location is mentioned after "from" or is a key phrase.
    """
    description_lower = description.lower()
    # Example: Look for "from [location]" or just common location names
    location_keywords = ["my room", "kitchen", "living room", "basement bathroom", "office"] # Add your locations
    for keyword in location_keywords:
        if keyword in description_lower:
            return keyword.strip() # Return the matched keyword as the location

    # Fallback or more complex parsing can be added here
    return "Unknown Location" # Default if no known location is found

def get_annotated_network_events(event_entries):
    """Filters event entries for network sensor annotations and extracts location."""
    network_events = []
    for event in event_entries:
        if 'selected_sensors' in event and event.get('description'):
            # Check if any selected sensor is a network sensor type
            is_network_event = any(
                sensor_type.startswith('android.sensor.network.')
                for sensor_type in event['selected_sensors']
            )
            # Also check if the description implies a location
            location = extract_location_from_description(event['description'])
            if is_network_event and location != "Unknown Location":
                 event_timestamp = parse_timestamp(event['timestamp'])
                 if event_timestamp:
                    network_events.append({
                        'timestamp': event_timestamp,
                        'location': location,
                        'description': event['description'] # Keep original description for context
                    })
    return network_events

# --- Matching Raw Data to Events ---

def find_closest_network_data(event_timestamp, raw_entries, time_window_seconds=5):
    """
    Finds network scan entries in raw_entries that are within a time window
    of the event_timestamp.
    """
    closest_network_data = []
    window_start = event_timestamp - datetime.timedelta(seconds=time_window_seconds)
    window_end = event_timestamp + datetime.timedelta(seconds=time_window_seconds)

    # Filter raw entries by time window and sensor type
    relevant_raw_entries = [
        entry for entry in raw_entries
        if 'timestamp' in entry and 'sensor_type' in entry
        and (entry['sensor_type'] == 'android.sensor.wifi_scan' or entry['sensor_type'] == 'android.sensor.network_scan')
        and parse_timestamp(entry['timestamp']) is not None # Ensure timestamp is valid
        and window_start <= parse_timestamp(entry['timestamp']) <= window_end
    ]

    # Extract and consolidate network scan results
    for entry in relevant_raw_entries:
        if entry['sensor_type'] == 'android.sensor.wifi_scan' and 'raw_data' in entry and 'values' in entry['raw_data']:
             # Handle the format where wifi_scan is top-level raw_data
             wifi_results = entry['raw_data'].get('values', [])
             closest_network_data.extend([
                 {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                 for res in wifi_results if res and res.get('bssid') and res.get('rssi') is not None
             ])
        elif entry['sensor_type'] == 'android.sensor.network_scan' and 'raw_data' in entry and 'values' in entry['raw_data']:
             # Handle the format where network_scan contains wifi and bluetooth results
             network_values = entry['raw_data'].get('values', {})
             wifi_results = network_values.get('wifiResults', [])
             bluetooth_results = network_values.get('bluetoothResults', [])

             closest_network_data.extend([
                 {'type': 'wifi', 'id': res.get('bssid'), 'ssid': res.get('ssid'), 'rssi': res.get('rssi')}
                 for res in wifi_results if res and res.get('bssid') and res.get('rssi') is not None
             ])
             closest_network_data.extend([
                 {'type': 'bluetooth', 'id': res.get('address'), 'name': res.get('name'), 'rssi': res.get('rssi')}
                 for res in bluetooth_results if res and res.get('address') and res.get('rssi') is not None
             ])

    # Remove duplicates based on type and ID, keeping potentially the latest or just one instance
    # For simplicity here, we'll just use a set of (type, id) tuples
    unique_network_data = {}
    for item in closest_network_data:
        if item['id']: # Ensure ID is not None
            unique_network_data[(item['type'], item['id'])] = item # Overwrites if duplicate, simple approach

    return list(unique_network_data.values())


# --- Building Location Fingerprints (Calibration) ---

def build_location_fingerprints(annotated_network_events, raw_entries):
    """
    Builds a fingerprint for each location based on the median RSSI
    and standard deviation of networks found during annotated events.
    """
    location_network_data = {} # {location: { (type, id): [rssi1, rssi2, ...], ... }, ...}

    for event in annotated_network_events:
        location = event['location']
        event_timestamp = event['timestamp']

        # Find network data around the event timestamp
        network_data_around_event = find_closest_network_data(event_timestamp, raw_entries)

        if not network_data_around_event:
            print(f"Warning: No network data found near event at {event_timestamp} for location '{location}'")
            continue

        if location not in location_network_data:
            location_network_data[location] = {}

        for network in network_data_around_event:
            network_key = (network['type'], network['id'])
            if network_key not in location_network_data[location]:
                location_network_data[location][network_key] = []
            location_network_data[location][network_key].append(network['rssi'])

    # Calculate best fit (median) and standard deviation RSSI for each network at each location
    location_fingerprints = {} # {location: { (type, id): {'median_rssi': ..., 'std_dev_rssi': ...}, ... }, ...}
    for location, network_data in location_network_data.items():
        location_fingerprints[location] = {}
        for network_key, rssi_values in network_data.items():
            if len(rssi_values) > 0:
                median_rssi = statistics.median(rssi_values)
                # Calculate standard deviation, handle case with only one data point
                std_dev_rssi = 0.0
                if len(rssi_values) > 1:
                    std_dev_rssi = statistics.stdev(rssi_values)

                location_fingerprints[location][network_key] = {
                    'median_rssi': median_rssi,
                    'std_dev_rssi': std_dev_rssi
                }

    return location_fingerprints

def save_fingerprints(fingerprints, filename):
    """Saves location fingerprints to a JSON file."""
    # Convert tuple keys to strings for JSON serialization
    serializable_fingerprints = {
        location: {
            f"{ntype}_{nid}": data # Use a consistent string key format
            for (ntype, nid), data in networks.items()
        }
        for location, networks in fingerprints.items()
    }
    with open(filename, 'w') as f:
        json.dump(serializable_fingerprints, f, indent=4)
    print(f"Saved fingerprints to {filename}")

def load_fingerprints(filename):
    """Loads location fingerprints from a JSON file."""
    try:
        with open(filename, 'r') as f:
            serializable_fingerprints = json.load(f)
            # Convert string keys back to tuple keys
            fingerprints = {
                location: {
                    tuple(key.split('_', 1)): data # Split string key back to tuple
                    for key, data in serializable_fingerprints[location].items()
                }
                for location in serializable_fingerprints
            }
            print(f"Loaded fingerprints from {filename}")
            return fingerprints
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Could not load fingerprints from {filename}: {e}")
        return None

# --- Real-time Inference ---

def calculate_similarity(current_network_data, location_fingerprint, missing_penalty_factor=1.0, extra_penalty_factor=1.0):
    """
    Calculates a similarity score between current network data and a location fingerprint.
    Lower score means higher similarity (like a distance metric).
    Uses standard deviation to weight the difference penalty.
    """
    score = 0.0
    current_networks = {(net['type'], net['id']): net['rssi'] for net in current_network_data if net.get('id') and net.get('rssi') is not None}
    fingerprint_networks = location_fingerprint

    # Compare networks present in both
    for network_key, fingerprint_data in fingerprint_networks.items():
        best_fit_rssi = fingerprint_data['median_rssi']
        std_dev_rssi = fingerprint_data['std_dev_rssi']

        if network_key in current_networks:
            current_rssi = current_networks[network_key]
            # Calculate difference. Normalize by std dev if std dev > 0
            diff = abs(current_rssi - best_fit_rssi)
            # Weight the difference penalty inversely by stability (lower std dev = higher weight)
            # Add a small epsilon to avoid division by zero if std_dev_rssi is 0
            weighted_diff = diff / (std_dev_rssi + 1e-6)
            score += weighted_diff ** 2 # Use squared weighted difference

        else:
            # Penalty for networks expected at this location but not currently visible
            # Penalty could be related to expected signal strength and its stability
            # Example: A strong, stable expected signal that's missing is a higher penalty
            penalty = abs(best_fit_rssi) * (1 + (1 / (std_dev_rssi + 1e-6))) # Example penalty
            score += penalty * missing_penalty_factor


    # Penalty for networks currently visible but not in the location's fingerprint
    for network_key, current_rssi in current_networks.items():
        if network_key not in fingerprint_networks:
            # Penalty for unexpected networks. Could be related to signal strength.
            penalty = abs(current_rssi) # Example penalty based on signal strength
            score += penalty * extra_penalty_factor

    # You could normalize the score by the number of networks for comparison across locations
    # num_networks = max(len(current_networks), len(fingerprint_networks))
    # if num_networks > 0:
    #     score /= num_networks

    return score

# Optional: Load relative positions (requires a file format)
def load_relative_positions(filename):
    """Loads relative positions from a JSON file."""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Could not load relative positions from {filename}: {e}")
        return None

def predict_location(current_network_data, location_fingerprints, relative_positions=None, previous_location=None):
    """
    Predicts the current location based on network data and location fingerprints.
    Optionally incorporates relative positions and previous location for smoothing.
    """
    if not location_fingerprints:
        return "No calibrated locations available."

    best_match_location = None
    min_score = float('inf')

    for location, fingerprint in location_fingerprints.items():
        score = calculate_similarity(current_network_data, fingerprint)

        # --- Optional: Incorporate Relative Positions ---
        # This part requires relative_positions data and a strategy to use it.
        # For example, you could add a penalty if the predicted location is
        # very far spatially from the previous predicted location, unless the
        # network similarity score is extremely good.
        # if relative_positions and previous_location and previous_location in relative_positions and location in relative_positions:
        #     prev_pos = relative_positions[previous_location]
        #     curr_pos = relative_positions[location]
        #     # Calculate spatial distance (e.g., Euclidean)
        #     spatial_distance = math.dist(prev_pos, curr_pos)
        #     # Add a penalty based on distance - needs careful tuning
        #     # score += spatial_distance * spatial_penalty_factor
        # ------------------------------------------------

        if score < min_score:
            min_score = score
            best_match_location = location

    # You could potentially return the score or a confidence level along with the location
    # A lower min_score indicates a better match.
    # Confidence could be related to the difference between the best score and the second-best score.

    return best_match_location # , min_score # Optionally return score

# --- Assess Data Needs ---

def assess_data_needs(annotated_network_events, min_events_per_location=3):
    """
    Assesses if more annotation data is needed for any location.
    Simple check: count events per location.
    """
    location_event_counts = {}
    for event in annotated_network_events:
        location = event['location']
        location_event_counts[location] = location_event_counts.get(location, 0) + 1

    locations_needing_data = []
    for location, count in location_event_counts.items():
        if count < min_events_per_location:
            locations_needing_data.append(f"{location} ({count} events)")

    if locations_needing_data:
        print("\n--- Data Assessment ---")
        print("More annotated data may be needed for the following locations (fewer than minimum events):")
        for loc_info in locations_needing_data:
            print(f"- {loc_info}")
        print("Consider annotating more events from these locations.")
        print("-----------------------")
    else:
        print("\n--- Data Assessment ---")
        print("Sufficient annotated data found for all tracked locations (based on minimum event count).")
        print("-----------------------")


# --- Function to suggest adding networks/devices ---

def suggest_network_improvement(location_fingerprints, high_std_dev_threshold=10.0, min_networks_for_check=2):
    """
    Suggests locations that might benefit from a more consistent network/device
    presence to improve inference, considering RSSI consistency (standard deviation).
    """
    print("\n--- Network Improvement Suggestions (Based on Consistency) ---")
    locations_to_improve = []

    for location, fingerprint in location_fingerprints.items():
        inconsistent_networks_count = 0
        total_networks = len(fingerprint)

        if total_networks == 0:
            locations_to_improve.append(f"{location} (No networks found in fingerprint)")
            continue

        # Calculate the average standard deviation for this location's networks
        total_std_dev = 0.0
        for network_data in fingerprint.values():
             total_std_dev += network_data['std_dev_rssi']
             if network_data['std_dev_rssi'] > high_std_dev_threshold:
                 inconsistent_networks_count += 1

        average_std_dev = total_std_dev / total_networks

        # Suggest improvement if average standard deviation is high OR
        # if a significant number/percentage of networks are highly inconsistent
        # (You might need to tune the thresholds and logic here)
        if average_std_dev > high_std_dev_threshold * 0.75 or inconsistent_networks_count > total_networks * 0.5:
             locations_to_improve.append(f"{location} (Avg Std Dev: {average_std_dev:.2f}, Inconsistent Networks: {inconsistent_networks_count}/{total_networks})")
        elif total_networks < min_networks_for_check:
             # Also suggest if there are very few networks, even if they seem consistent in limited data
             locations_to_improve.append(f"{location} (Only {total_networks} networks in fingerprint)")


    if locations_to_improve:
        print("Consider adding a consistent network source (e.g., a low-cost WiFi extender, a dedicated Bluetooth beacon) in these locations to improve fingerprint uniqueness and consistency:")
        for loc_info in locations_to_improve:
            print(f"- {loc_info}")
        print("-------------------------------------")
    else:
        print("All tracked locations appear to have reasonably consistent network fingerprints.")
        print("-------------------------------------")


if __name__ == "__main__":
    # Ensure dummy log files exist for demonstration if they don't
    if not os.path.exists(RAW_DATA_LOG):
        print(f"Creating dummy {RAW_DATA_LOG}")
        with open(RAW_DATA_LOG, 'w') as f:
            # Add your sample raw data here, one JSON object per line
            f.write('{"timestamp": "2025-05-03T18:41:11.802416", "sensor_type": "android.sensor.rotation_vector", "raw_data": {"values": [0.010309263, -0.004685708, -0.6620065, 0.7494126, 0.5235988], "accuracy": 3, "timestamp": 194828158143967, "type": "android.sensor.rotation_vector"}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.802559", "sensor_type": "android.sensor.gyroscope_uncalibrated", "raw_data": {"values": [0.0030543262, -0.00015271631, -0.00076358154, 0.0035778964, 0.00030645894, -0.0007631152], "accuracy": 3, "timestamp": 194828170867203, "type": "android.sensor.gyroscope_uncalibrated"}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.802691", "sensor_type": "android.sensor.wifi_scan", "raw_data": {"type": "android.sensor.wifi_scan", "values": [{"bssid": "24:e5:0f:5a:89:12", "frequency": 5745, "rssi": -90, "ssid": "MargaritaVille", "timestamp": 1746315673899}, {"bssid": "e0:22:04:3a:b8:ed", "frequency": 2462, "rssi": -80, "ssid": "ATT4TyT5Gj", "timestamp": 1746315673899}, {"bssid": "d6:35:1d:cc:de:04", "frequency": 2437, "rssi": -69, "ssid": "OJ-GFI2", "timestamp": 1746315673899}, {"bssid": "d6:35:1d:cc:de:0c", "frequency": 5180, "rssi": -73, "ssid": "OJ-GFI", "timestamp": 1746315673899}, {"bssid": "ce:26:04:33:cb:f2", "frequency": 5240, "rssi": -50, "ssid": "FromTheLandOfKansas-vpn-5g", "timestamp": 1746315673899}, {"bssid": "a2:b5:3c:26:01:26", "frequency": 5745, "rssi": -67, "ssid": "OJ-GFI", "timestamp": 1746315673899}, {"bssid": "a2:b5:3c:26:01:1e", "frequency": 2462, "rssi": -68, "ssid": "OJ-GFI2", "timestamp": 1746315673899}, {"bssid": "24:e5:0f:5a:8a:7a", "frequency": 5745, "rssi": -84, "ssid": "MargaritaVille", "timestamp": 1746315673899}]}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.802966", "sensor_type": "android.sensor.network_scan", "raw_data": {"type": "android.sensor.network_scan", "values": {"bluetoothResults": [{"address": "8C:79:F5:80:25:CA", "name": "[TV] Samsung 7 Series (55 Cur", "rssi": -85, "timestamp": 1746313493721}, {"address": "7E:2E:05:FC:D8:91", "name": "Unknown Device", "rssi": -79, "timestamp": 1746313493726}, {"address": "88:D0:39:B2:AC:B3", "name": "VIZIO M21d", "rssi": -81, "timestamp": 1746313494289}, {"address": "F0:EF:86:F7:BC:DD", "name": "N02QL", "rssi": -90, "timestamp": 1746313495126}, {"address": "69:D1:CC:08:C6:12", "name": "Unknown Device", "rssi": -43, "timestamp": 1746313495135}, {"address": "43:2E:24:4C:C6:85", "name": "Unknown Device", "rssi": -43, "timestamp": 1746313495140}, {"address": "65:A0:11:69:C2:74", "name": "Unknown Device", "rssi": -43, "timestamp": 1746313496195}, {"address": "69:11:E8:EA:C6:06", "name": "Unknown Device", "rssi": -91, "timestamp": 1746313496898}, {"address": "A4:6D:D4:51:9C:E7", "name": "ResMed 701060", "rssi": -90, "timestamp": 1746313496909}, {"address": "4F:AE:CB:C5:86:27", "name": "Unknown Device", "rssi": -94, "timestamp": 1746313501459}, {"address": "29:2B:47:CF:8A:70", "name": "Unknown Device", "rssi": -60, "timestamp": 1746313502510}], "timestamp": 1746315673899, "wifiResults": [{"bssid": "24:e5:0f:5a:89:12", "frequency": 5745, "rssi": -90, "ssid": "MargaritaVille", "timestamp": 1746315673899}, {"bssid": "e0:22:04:3a:b8:ed", "frequency": 2462, "rssi": -80, "ssid": "ATT4TyT5Gj", "timestamp": 1746315673899}, {"bssid": "d6:35:1d:cc:de:04", "frequency": 2437, "rssi": -69, "ssid": "OJ-GFI2", "timestamp": 1746315673899}, {"bssid": "d6:35:1d:cc:de:0c", "frequency": 5180, "rssi": -73, "ssid": "OJ-GFI", "timestamp": 1746315673899}, {"bssid": "ce:26:04:33:cb:f2", "frequency": 5240, "rssi": -50, "ssid": "FromTheLandOfKansas-vpn-5g", "timestamp": 1746315673899}, {"bssid": "a2:b5:3c:26:01:26", "frequency": 5745, "rssi": -67, "ssid": "OJ-GFI", "timestamp": 1746315673899}, {"bssid": "a2:b5:3c:26:01:1e", "frequency": 2462, "rssi": -68, "ssid": "OJ-GFI2", "timestamp": 1746315673899}, {"bssid": "24:e5:0f:5a:8a:7a", "frequency": 5745, "rssi": -84, "ssid": "MargaritaVille", "timestamp": 1746315673899}]}}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.803387", "sensor_type": "android.sensor.accelerometer_uncalibrated", "raw_data": {"values": [0.040677983, 0.16689938, 9.836894, 0.1078788, -0.04596099, 0.09891297], "accuracy": 3, "timestamp": 194828175104437, "type": "android.sensor.accelerometer_uncalibrated"}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.803517", "sensor_type": "android.sensor.linear_acceleration", "raw_data": {"values": [-0.00724056, -0.001364395, -0.06949711], "accuracy": 3, "timestamp": 194828175104437, "type": "android.sensor.linear_acceleration"}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.803630", "sensor_type": "android.sensor.rotation_vector", "raw_data": {"values": [0.010308645, -0.004695408, -0.66200846, 0.7494108, 0.5235988], "accuracy": 3, "timestamp": 194828175104437, "type": "android.sensor.rotation_vector"}}\n')
            f.write('{"timestamp": "2025-05-03T18:41:11.803742", "sensor_type": "android.sensor.pressure", "raw_data": {"values": [977.8028], "accuracy": 3, "timestamp": 194828174190629, "type": "android.sensor.pressure"}}\n')
            f.write('{"timestamp": "2025-05-03T18:46:16.305493", "sensor_type": "android.sensor.network_scan", "raw_data": {"type": "android.sensor.network_scan", "values": {"bluetoothResults": [{"address": "8C:79:F5:80:25:CA", "name": "[TV] Samsung 7 Series (55 Cur", "rssi": -80, "timestamp": 1746313493721}], "timestamp": 1746315673899, "wifiResults": [{"bssid": "d6:35:1d:cc:de:04", "frequency": 2437, "rssi": -65, "ssid": "OJ-GFI2", "timestamp": 1746315673899}, {"bssid": "ce:26:04:33:cb:f2", "frequency": 5240, "rssi": -45, "ssid": "FromTheLandOfKansas-vpn-5g", "timestamp": 1746315673899}]}}}\n') # Added a network scan entry near one of the event timestamps
            f.write('{"timestamp": "2025-05-03T18:47:02.258809", "sensor_type": "android.sensor.network_scan", "raw_data": {"type": "android.sensor.network_scan", "values": {"bluetoothResults": [{"address": "1A:2B:3C:4D:5E:6F", "name": "Basement Beacon", "rssi": -30, "timestamp": 1746313493721}], "timestamp": 1746315673899, "wifiResults": [{"bssid": "aa:bb:cc:dd:ee:ff", "frequency": 2412, "rssi": -70, "ssid": "BasementWiFi", "timestamp": 1746315673899}]}}}\n') # Added a network scan entry near another event timestamp


    if not os.path.exists(EVENT_DATA_LOG):
        print(f"Creating dummy {EVENT_DATA_LOG}")
        with open(EVENT_DATA_LOG, 'w') as f:
            # Add your sample event data here, one JSON object per line
            f.write('{"timestamp": "2025-05-03T18:46:07.144579", "ip_address": "127.0.0.1", "description": "testing the server from my room", "selected_sensors": ["android.sensor.motion.gravity", "android.sensor.motion.gyro_temperature"]}\n')
            f.write('{"timestamp": "2025-05-03T18:46:16.305493", "ip_address": "127.0.0.1", "description": "testing the server from the kitchen", "selected_sensors": ["android.sensor.network.bluetooth_scan", "android.sensor.network.network_scan", "android.sensor.network.wifi_scan"]}\n')
            f.write('{"timestamp": "2025-05-03T18:46:46.015454", "ip_address": "127.0.0.1", "description": "testing the server from my room", "selected_sensors": []}\n')
            f.write('{"timestamp": "2025-05-03T18:47:02.258809", "ip_address": "127.0.0.1", "description": "testing the server from the basement bathroom", "selected_sensors": ["android.sensor.network.bluetooth_scan", "android.sensor.network.network_scan", "android.sensor.network.wifi_scan"]}\n')


    main()
    # After calibration, you can also run the network improvement suggestion
    calibrated_fingerprints = load_fingerprints(CALIBRATION_DATA_FILE)
    if calibrated_fingerprints:
        suggest_network_improvement(calibrated_fingerprints)

