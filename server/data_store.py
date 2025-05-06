import json
import os
import logging
from datetime import datetime, timezone, timedelta # Import timedelta
from typing import List, Dict, Any, Optional
from collections import deque # Added for efficient file reading
from threading import Lock # Standard threading lock for in-memory structures

# Configure basic logging for the module
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DataStore:
    """
    Manages storage and retrieval of all time-series data points (raw sensor and inference).
    Stores data_point objects in time-series log files.
    """

    # Mapping of file names used in contracts to actual file paths
    # This will be populated in __init__ based on the provided log_dir
    FILE_MAP: Dict[str, str] = {}

    # Define standard file names (without directory)
    RAW_DATA_FILE_NAME = "raw_data.log"
    INFERENCE_DATA_FILE_NAME = "inference_data.log"
    # Note: calibrated_fingerprints.json is managed by the Fingerprinting Module

    def __init__(self, log_directory: str = "data_logs"):
        """
        Initializes the DataStore, ensuring the log directory exists and setting up file paths.

        Args:
            log_directory: The base directory where log files will be stored.
        """
        self.log_dir = log_directory
        self.FILE_MAP = {
            "raw_data": os.path.join(self.log_dir, self.RAW_DATA_FILE_NAME),
            "inference_data": os.path.join(self.log_dir, self.INFERENCE_DATA_FILE_NAME),
            # Add other log files here as needed in the future
        }

        # Ensure the log directory exists
        self._ensure_directory()
        # Use standard threading lock for internal data structures if needed in future
        self._internal_lock = Lock()
        logger.info(f"DataStore initialized. Log directory: {self.log_dir}")

    def _ensure_directory(self):
        """Ensures the log directory exists."""
        os.makedirs(self.log_dir, exist_ok=True)

    def _get_log_file_path(self, filename: str) -> str:
        """Constructs the full path for a given log filename."""
        # Ensure filename is simple (e.g., remove path separators)
        safe_filename = os.path.basename(filename)
        # Ensure filename ends with .log or .jsonl (or similar)
        if not safe_filename.endswith(('.log', '.jsonl', '.json')):
            # Append .log if no recognized extension
             safe_filename += '.log' 
        return os.path.join(self.log_dir, safe_filename)

    def get_data(
        self,
        types: List[str],
        started_at: str,
        ended_at: str,
        keys: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        limit: int = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve data points for specified types/keys within a time window from log files.

        Args:
            types: List of standardized data point types to retrieve.
            started_at: Start of the time window (ISO 8601 string).
            ended_at: End of the time window (ISO 8601 string).
            keys: Optional list of keys to filter by within the specified types.
            files: Optional list of log file names to search within. Defaults to all relevant files.
            limit: Optional limit on the number of retrieved data points.

        Returns:
            A list of data_point objects matching the criteria.
        """
        all_data_points: List[Dict[str, Any]] = []
        log_files_to_read = [self.FILE_MAP[f] for f in files] if files else list(self.FILE_MAP.values())

        try:
            start_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00')) # Handle potential 'Z'
            end_dt = datetime.fromisoformat(ended_at.replace('Z', '+00:00'))     # Handle potential 'Z'
        except ValueError as e:
            logger.error(f"Invalid timestamp format provided: {e}")
            return [] # Return empty list for invalid timestamps


        for file_path in log_files_to_read:
            if not os.path.exists(file_path):
                logger.debug(f"Log file not found: {file_path}. Skipping.")
                continue

            try:
                with open(file_path, 'r') as f:
                    for line in f:
                        try:
                            data_point = json.loads(line)
                            # Validate data_point structure (basic check)
                            if not all(prop in data_point for prop in ['created_at', 'type', 'value']):
                                logger.warning(f"Skipping invalid data_point structure in {file_path}: {line.strip()}")
                                continue

                            # Filter by type
                            # should only compare the beginning of the type string
                            if not any(data_point['type'].startswith(t) for t in types):
                                # logger.debug(f"Skipping data point type '{data_point['type']}' not in {types}")
                                continue

                            # Filter by timestamp
                            created_at_str = data_point.get('created_at')
                            created_at_dt = None # Initialize before try block
                            if created_at_str:
                                try:
                                    # Ensure the parsed datetime is timezone-aware (assume UTC if missing)
                                    parsed_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                                    if parsed_dt.tzinfo is None:
                                         # If naive after parsing, assume it represents UTC time
                                         created_at_dt = parsed_dt.replace(tzinfo=timezone.utc)
                                    else:
                                         # If already aware, use it directly
                                         created_at_dt = parsed_dt
                                         
                                except ValueError:
                                     logger.warning(f"Invalid 'created_at' timestamp format in {file_path}: {created_at_str}. Skipping data point.")
                                     continue # Skip to next line if timestamp is invalid
                            else:
                                logger.warning(f"Missing 'created_at' in data point in {file_path}: {line.strip()}. Skipping data point.")
                                continue # Skip to next line if timestamp is missing

                            # Check if created_at_dt was successfully assigned (should be aware UTC)
                            if created_at_dt is None:
                                logger.warning(f"Could not determine valid timestamp for line: {line.strip()}. Skipping.")
                                continue
                                
                            # Perform comparison (both start_dt/end_dt and created_at_dt are aware)
                            if not (start_dt <= created_at_dt <= end_dt):
                                # logger.debug(f"Skipping data point created_at '{created_at_dt}' outside time window {start_dt} - {end_dt}")
                                continue

                            # Filter by key (if keys are specified)
                            if keys is not None:
                                data_point_key = data_point.get('key')
                                if data_point_key not in keys:
                                    logger.debug(f"Skipping data point key '{data_point_key}' not in {keys}")
                                    continue

                            # If all filters pass, add the data point
                            all_data_points.append(data_point)

                        except json.JSONDecodeError:
                            logger.error(f"Error decoding JSON from log file: {file_path}, line: {line.strip()}")
                        except Exception as e:
                            logger.error(f"Error processing log line in {file_path}: {e}, line: {line.strip()}", exc_info=True)
            except Exception as e:
                 logger.error(f"Error reading log file {file_path}: {e}", exc_info=True)


        # Data points are not guaranteed to be in chronological order from reading multiple files,
        # but for window-based queries, the order within the window might be less critical.
        # If strict chronological order is needed, add sorting here:
        # all_data_points.sort(key=lambda x: x['created_at'])

        # Removed file_path from this log as data might come from multiple files
        logger.info(f"Finished retrieving data points for types {types}, keys {keys}, window {started_at} to {ended_at}. Total retrieved: {len(all_data_points)}")

        if limit:
            all_data_points = all_data_points[:limit]

        return all_data_points

    def set(self, data_point: dict, files: list = ['raw_data']):
        """
        Writes a data point dictionary to the specified log files.

        Args:
            data_point: The data point dictionary to log.
            files: A list of base filenames (without .log extension) to write to.
                   Defaults to ['raw_data'].
        """
        if not isinstance(data_point, dict):
            logger.error(f"Invalid data_point type: {type(data_point)}. Expected dict.")
            return
            
        # Ensure timestamp exists
        if 'created_at' not in data_point:
            data_point['created_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            
        log_line = json.dumps(data_point) + '\n'

        for file_key in files:
            filepath = self._get_log_file_path(f"{file_key}.log")
            
            try:
                # Simplified write without file lock
                with open(filepath, 'a') as f:
                    f.write(log_line)
                logger.debug(f"Wrote data point type '{data_point.get('type')}' to {filepath}")

            except IOError as e:
                 logger.error(f"IOError writing to {filepath}: {e}", exc_info=True)
            except Exception as e:
                 logger.error(f"Unexpected error writing to {filepath}: {e}", exc_info=True)

    def get_unique_values(self, field_name: str, files: Optional[List[str]] = None) -> List[str]:
        """
        Retrieves unique values for a specified field from log files.

        Args:
            field_name: The name of the field to extract unique values from (e.g., 'device').
            files: Optional list of log file keys (e.g., 'raw_data') to search within. 
                   Defaults to all files known to DataStore.

        Returns:
            A sorted list of unique string values found for the field.
        """
        unique_values = set()
        log_files_to_read_keys = files if files else list(self.FILE_MAP.keys())
        log_files_to_read_paths = [self.FILE_MAP[f] for f in log_files_to_read_keys if f in self.FILE_MAP]

        logger.info(f"Scanning files {log_files_to_read_keys} for unique values of field '{field_name}'")

        for file_path in log_files_to_read_paths:
            if not os.path.exists(file_path):
                logger.debug(f"Log file not found for unique value scan: {file_path}. Skipping.")
                continue
            
            try:
                with open(file_path, 'r') as f:
                    for line in f:
                        try:
                            data_point = json.loads(line)
                            value = data_point.get(field_name)
                            if value is not None:
                                unique_values.add(str(value)) # Add as string
                        except json.JSONDecodeError:
                            # Ignore lines that are not valid JSON
                            pass 
                        except Exception as e:
                            logger.warning(f"Error processing line in {file_path} during unique value scan: {e}, line: {line.strip()}")
            except Exception as e:
                 logger.error(f"Error reading log file {file_path} during unique value scan: {e}", exc_info=True)

        sorted_unique_values = sorted(list(unique_values))
        logger.info(f"Found {len(sorted_unique_values)} unique values for field '{field_name}': {sorted_unique_values}")
        return sorted_unique_values

    def get_last_log_timestamp_for_device(self, device_ip: str, file_key: str = 'raw_data') -> Optional[str]:
        """
        Finds the latest 'created_at' timestamp for a given device IP in a specific log file.
        Reads the file backwards for efficiency.

        Args:
            device_ip: The IP address of the device to find the last log for.
            file_key: The key of the log file to search (e.g., 'raw_data').

        Returns:
            The ISO 8601 timestamp string of the last entry, or None if not found.
        """
        file_path = self.FILE_MAP.get(file_key)
        if not file_path or not os.path.exists(file_path):
            logger.warning(f"Log file key '{file_key}' not found or file does not exist: {file_path}")
            return None

        logger.debug(f"Searching backwards in {file_path} for last timestamp from device '{device_ip}'")
        try:
            with open(file_path, 'rb') as f: # Read bytes for efficient seeking
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                buffer_size = 8192 # Read in 8KB chunks
                overlap = 128 # Overlap to avoid cutting JSON objects
                buffer = b'' 

                while file_size > 0:
                    seek_pos = max(0, file_size - buffer_size)
                    read_size = min(buffer_size + overlap, file_size) # Read chunk + overlap
                    f.seek(seek_pos)
                    chunk = f.read(read_size)
                    file_size = seek_pos
                    
                    buffer = chunk + buffer # Prepend previous buffer remainder
                    
                    # Process lines in the current buffer (except maybe the first partial line)
                    lines = buffer.splitlines(True) # Keep ends
                    
                    if file_size > 0: # If not at the beginning, first line might be incomplete
                         buffer = lines.pop(0) # Keep potential partial line for next iteration
                    else:
                         buffer = b'' # Reached beginning, process everything
                         
                    # Iterate lines in reverse order within the buffer
                    for line_bytes in reversed(lines):
                        try:
                            line_str = line_bytes.decode('utf-8').strip()
                            if not line_str: continue
                            
                            data_point = json.loads(line_str)
                            if data_point.get('device') == device_ip:
                                timestamp = data_point.get('created_at')
                                if timestamp:
                                     logger.debug(f"Found last timestamp for {device_ip}: {timestamp}")
                                     return timestamp # Found the latest entry
                        except json.JSONDecodeError:
                            continue # Skip invalid JSON
                        except UnicodeDecodeError:
                             logger.warning(f"Skipping line with decode error in {file_path}")
                             continue
                        except Exception as e:
                             logger.warning(f"Error processing line for last timestamp: {e}")
                             continue # Skip problematic lines
                             
        except Exception as e:
            logger.error(f"Error reading file {file_path} backwards: {e}", exc_info=True)

        logger.warning(f"No log entry found for device '{device_ip}' in {file_path}")
        return None # Not found

# Example Usage (for testing purposes)
if __name__ == '__main__':
    # Instantiate the DataStore with a specific log directory
    data_store = DataStore(log_directory="logs")

    # Example of setting raw sensor data
    sensor_pressure_data = {
        'type': 'android.sensor.pressure',
        'key': None, # No key for simple scalar
        'value': 1012.5,
        # created_at will be added automatically
    }
    data_store.set(sensor_pressure_data, files=['raw_data'])

    sensor_wifi_rssi_data = {
        'type': 'android.sensor.wifi_scan.rssi',
        'key': 'fa:8f:ca:55:8f:f1',
        'value': -65.0,
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z') # Explicitly providing timestamp
    }
    data_store.set(sensor_wifi_rssi_data, files=['raw_data'])

    # Example of setting inference data
    # Note: In a real system, the Inference Module would generate these data_points
    # from the structured inference_result and call data_store.set
    inference_location_prediction = {
        'type': 'inference.location.prediction',
        'key': 'location_inference_config_name', # Key could indicate the inference config instance name
        'value': 'kitchen',
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    data_store.set(inference_location_prediction, files=['raw_data', 'inference_data']) # Log to both

    inference_location_confidence = {
        'type': 'inference.location.confidence',
        'key': 'location_inference_config_name.location.kitchen', # Key indicates inference config name and which fingerprint/location this confidence is for
        'value': 0.95, # Using a higher confidence for example
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    data_store.set(inference_location_confidence, files=['raw_data', 'inference_data']) # Log to both

    # Example of getting data
    # Note: You'll need to wait a moment after setting for data to be in the file
    # For real applications, you'd query over a time window where data was collected.
    # This example query might not return data immediately after setting due to file write timing.
    print("\nAttempting to retrieve data...")
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace('+00:00', 'Z') # Last 5 seconds

    retrieved_data = data_store.get_data(
        types=['android.sensor.pressure', 'inference.location.prediction'],
        started_at=past,
        ended_at=now,
        files=['raw_data']
    )
    print(f"Retrieved {len(retrieved_data)} data points from raw_data:")
    for dp in retrieved_data:
        print(dp)

    retrieved_inference_data = data_store.get_data(
        types=['inference.location.confidence'],
        started_at=past,
        ended_at=now,
        files=['inference_data'] # Query only inference data log
    )
    print(f"\nRetrieved {len(retrieved_inference_data)} inference data points from inference_data:")
    for dp in retrieved_inference_data:
        print(dp)

    # Example of getting data across multiple files and filtering by key
    retrieved_filtered_data = data_store.get_data(
        types=['android.sensor.wifi_scan.rssi', 'inference.location.confidence'],
        started_at=past,
        ended_at=now,
        keys=['fa:8f:ca:55:8f:f1', 'location_inference_config_name.location.kitchen'],
        files=['raw_data', 'inference_data'] # Search in both files
    )
    print(f"\nRetrieved {len(retrieved_filtered_data)} filtered data points from raw_data and inference_data:")
    for dp in retrieved_filtered_data:
        print(dp)
