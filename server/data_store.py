import json
import os
import logging
from datetime import datetime, timezone, timedelta # Import timedelta
from typing import List, Dict, Any, Optional

# Configure basic logging for the module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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

    def __init__(self, log_directory: str = "logs"):
        """
        Initializes the DataStore, ensuring the log directory exists and setting up file paths.

        Args:
            log_directory: The base directory where log files will be stored.
                           Defaults to "logs".
        """
        self.log_dir = log_directory
        self.FILE_MAP = {
            "raw_data": os.path.join(self.log_dir, self.RAW_DATA_FILE_NAME),
            "inference_data": os.path.join(self.log_dir, self.INFERENCE_DATA_FILE_NAME),
            # Add other log files here as needed in the future
        }

        # Ensure the log directory exists
        os.makedirs(self.log_dir, exist_ok=True)
        logger.info(f"DataStore initialized. Log directory: {self.log_dir}")

    def get_data(
        self,
        types: List[str],
        started_at: str,
        ended_at: str,
        keys: Optional[List[str]] = None,
        files: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve data points for specified types/keys within a time window from log files.

        Args:
            types: List of standardized data point types to retrieve.
            started_at: Start of the time window (ISO 8601 string).
            ended_at: End of the time window (ISO 8601 string).
            keys: Optional list of keys to filter by within the specified types.
            files: Optional list of log file names to search within. Defaults to all relevant files.

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
                            if data_point['type'] not in types:
                                continue

                            # Filter by timestamp
                            created_at_str = data_point.get('created_at')
                            if created_at_str:
                                try:
                                    created_at_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                                    if not (start_dt <= created_at_dt <= end_dt):
                                        continue
                                except ValueError:
                                     logger.warning(f"Invalid 'created_at' timestamp format in {file_path}: {created_at_str}. Skipping data point.")
                                     continue
                            else:
                                logger.warning(f"Missing 'created_at' in data point in {file_path}: {line.strip()}. Skipping data point.")
                                continue


                            # Filter by key (if keys are specified)
                            if keys is not None:
                                data_point_key = data_point.get('key')
                                if data_point_key not in keys:
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


        return all_data_points

    def set_data(self, data_point: Dict[str, Any], files: List[str] = ['raw_data']) -> None:
        """
        Write a data point to specified log files.

        Args:
            data_point: The data point object to write ({type, key, value, created_at}).
                        If 'created_at' is missing, it will be added with the current time.
            files: List of log file names to write to (e.g., ['raw_data'], ['inference_data']).
                   Defaults to ['raw_data'].
        """
        # Ensure created_at is present, generate if not
        if 'created_at' not in data_point or data_point['created_at'] is None:
            data_point['created_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Validate data_point structure (basic check)
        if not all(prop in data_point for prop in ['created_at', 'type', 'value']):
            logger.error(f"Attempted to write invalid data_point structure: {data_point}. Missing required properties.")
            return # Do not write invalid data

        line_to_write = json.dumps(data_point)

        for file_name in files:
            if file_name not in self.FILE_MAP:
                logger.warning(f"Attempted to write to unknown log file: {file_name}. Skipping.")
                continue

            file_path = self.FILE_MAP[file_name]
            try:
                # Use 'a' mode for appending, ensure newline after each JSON object
                with open(file_path, 'a') as f:
                    f.write(line_to_write + '\n')
                logger.debug(f"Wrote data point to {file_name}: {data_point.get('type', 'Unknown Type')}/{data_point.get('key', 'None')}")
            except Exception as e:
                logger.error(f"Error writing data point to {file_path}: {e}, data: {data_point}", exc_info=True)


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
    data_store.set_data(sensor_pressure_data, files=['raw_data'])

    sensor_wifi_rssi_data = {
        'type': 'android.sensor.wifi_scan.rssi',
        'key': 'fa:8f:ca:55:8f:f1',
        'value': -65.0,
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z') # Explicitly providing timestamp
    }
    data_store.set_data(sensor_wifi_rssi_data, files=['raw_data'])

    # Example of setting inference data
    # Note: In a real system, the Inference Module would generate these data_points
    # from the structured inference_result and call data_store.set_data
    inference_location_prediction = {
        'type': 'inference.location.prediction',
        'key': 'location_inference_config_name', # Key could indicate the inference config instance name
        'value': 'kitchen',
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    data_store.set_data(inference_location_prediction, files=['raw_data', 'inference_data']) # Log to both

    inference_location_confidence = {
        'type': 'inference.location.confidence',
        'key': 'location_inference_config_name.location.kitchen', # Key indicates inference config name and which fingerprint/location this confidence is for
        'value': 0.95, # Using a higher confidence for example
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    data_store.set_data(inference_location_confidence, files=['raw_data', 'inference_data']) # Log to both

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
