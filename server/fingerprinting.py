import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
import statistics # For calculating median and standard deviation
import zlib # For compressing raw data reference (simple example)

# Configure basic logging for the module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Assume DataStore class is available (either imported or in the same project)
# For this example, we'll assume it's imported from data_store.py
try:
    from data_store import DataStore
except ImportError:
    logger.error("DataStore module not found. Please ensure data_store.py is available.")
    # Define a dummy DataStore class if import fails, to allow the code structure to be reviewed
    class DataStore:
        def __init__(self, log_directory: str = "data_logs"):
            pass
        def get_data(self, types, started_at, ended_at, keys=None, files=None):
            logger.warning("Using dummy DataStore.get_data - no data will be retrieved.")
            return []
        def set_data(self, data_point, files=['raw_data']):
            logger.warning("Using dummy DataStore.set_data - no data will be written.")
            pass

# Assume InferenceModule class is available
# Import is needed for type hinting and potentially dummy class definition
try:
    from inference import InferenceModule
except ImportError:
    logger.error("InferenceModule module not found. Please ensure inference_module.py is available.")
    # Define a dummy InferenceModule class if import fails
    class InferenceModule:
        def __init__(self, data_store=None, config_dir="configs"):
            # Added dummy attributes to match expected usage in FingerprintingModule
            self._inference_configurations = {}
            self._fingerprinting_module = None # Dummy setter target
            pass
        def load_inference_configurations(self):
            logger.warning("Using dummy InferenceModule.load_inference_configurations - no configs will be loaded.")
            # Return the dummy configurations
            return self._inference_configurations
        def set_fingerprinting_module(self, fingerprinting_module):
             self._fingerprinting_module = fingerprinting_module
             logger.warning("Using dummy InferenceModule.set_fingerprinting_module.")
        def run_inference(self, inference_config_name, current_time):
             logger.warning("Using dummy InferenceModule.run_inference.")
             return []


class FingerprintingModule:
    """
    Manages calibrated fingerprints and generates real-time fingerprints
    from data points in the DataStore. Fingerprints are linked to inference configurations.
    """

    # Define the file name for calibrated fingerprints
    CALIBRATED_FINGERPRINTS_FILE = "calibrated_fingerprints.json"

    def __init__(self, data_store: DataStore, storage_dir: str = "fingerprints"): # Removed inference_module from __init__
        """
        Initializes the FingerprintingModule.

        Args:
            data_store: An instance of the DataStore.
            storage_dir: The directory where calibrated fingerprints JSON file will be stored.
                         Defaults to "fingerprints".
        """
        self.data_store = data_store
        self._inference_module: Optional[InferenceModule] = None # Store as an attribute, allow None initially
        self.storage_dir = storage_dir
        self.calibrated_fingerprints_path = os.path.join(self.storage_dir, self.CALIBRATED_FINGERPRINTS_FILE)

        # Ensure the storage directory exists
        os.makedirs(self.storage_dir, exist_ok=True)
        logger.info(f"FingerprintingModule initialized. Storage directory: {self.storage_dir}")

        # Load calibrated fingerprints on initialization
        self._calibrated_fingerprints: Dict[str, Dict[str, Any]] = self._load_from_storage()

    def set_inference_module(self, inference_module: InferenceModule) -> None:
        """
        Sets the InferenceModule instance after initialization.
        This resolves the circular dependency.
        """
        self._inference_module = inference_module
        logger.info("InferenceModule instance set in FingerprintingModule.")

    def _load_from_storage(self) -> Dict[str, Dict[str, Any]]:
        """Loads calibrated fingerprints from the JSON file."""
        if not os.path.exists(self.calibrated_fingerprints_path):
            logger.info("Calibrated fingerprints file not found. Starting with empty collection.")
            return {}

        try:
            with open(self.calibrated_fingerprints_path, 'r') as f:
                # We need a custom decoder or logic to handle the tuple keys if they were saved as strings
                # For simplicity here, we assume they were saved in a JSON-compatible format
                # If using the combined path format '{type}.{key}', standard json.load works.
                calibrated_data = json.load(f)
                logger.info(f"Successfully loaded {len(calibrated_data)} calibrated fingerprints from {self.calibrated_fingerprints_path}")
                return calibrated_data
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {self.calibrated_fingerprints_path}: {e}")
            return {} # Return empty on error
        except Exception as e:
            logger.error(f"Unexpected error loading calibrated fingerprints: {e}", exc_info=True)
            return {} # Return empty on error

    def _save_to_storage(self) -> None:
        """Saves the current calibrated fingerprints to the JSON file."""
        try:
            # Ensure the data is in a JSON-serializable format
            # If using tuple keys internally, convert them to strings for saving
            serializable_data = {}
            for fp_type, fp_data in self._calibrated_fingerprints.items():
                 serializable_data[fp_type] = fp_data # Assuming fingerprint structure is already serializable

            with open(self.calibrated_fingerprints_path, 'w') as f:
                json.dump(serializable_data, f, indent=2)
            logger.info(f"Successfully saved {len(self._calibrated_fingerprints)} calibrated fingerprints to {self.calibrated_fingerprints_path}")
        except Exception as e:
            logger.error(f"Error saving calibrated fingerprints to {self.calibrated_fingerprints_path}: {e}", exc_info=True)


    def generate_fingerprint(
        self,
        fingerprint_type: str,
        inference_config_name: str,
        ended_at: str
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a fingerprint from data points in the Data Store, based on an inference config.

        Args:
            fingerprint_type: The type to assign to the generated fingerprint (e.g., 'location.current', 'location.kitchen').
            inference_config_name: The name of the inference configuration to use for data point types and window duration.
            ended_at: End of the data window (ISO 8601 string). Start time is calculated using window_duration.

        Returns:
            A fingerprint object, or None if the inference configuration is not found or no data is available.
        """
        # Early exit if InferenceModule is not set
        if self._inference_module is None:
            logger.error("InferenceModule is not set. Cannot generate fingerprint as config is needed.")
            return None

        # 1. Get inference configuration to determine data types and window duration
        inference_configs = self._inference_module.load_inference_configurations() # Use the set instance
        inference_config = inference_configs.get(inference_config_name)

        if not inference_config:
            logger.error(f"Inference configuration '{inference_config_name}' not found. Cannot generate fingerprint.")
            return None

        data_point_types_to_include = inference_config.get('data_point_types', [])
        window_duration_seconds = inference_config.get('window_duration_seconds')

        if not data_point_types_to_include or window_duration_seconds is None:
             logger.error(f"Inference configuration '{inference_config_name}' is missing 'data_point_types' or 'window_duration_seconds'.")
             return None

        try:
            ended_at_dt = datetime.fromisoformat(ended_at.replace('Z', '+00:00'))
            started_at_dt = ended_at_dt - timedelta(seconds=window_duration_seconds)
            started_at_str = started_at_dt.isoformat().replace('+00:00', 'Z')
        except ValueError as e:
            logger.error(f"Invalid 'ended_at' timestamp format: {e}")
            return None

        logger.info(f"Generating fingerprint for type '{fingerprint_type}' using config '{inference_config_name}' from {started_at_str} to {ended_at}")

        # 2. Retrieve raw data points from DataStore within the time window
        # We need data points for ALL types defined in the inference config's data_point_types
        raw_data_points = self.data_store.get_data(
            types=data_point_types_to_include,
            started_at=started_at_str,
            ended_at=ended_at,
            files=['raw_data'] # Assume raw sensor data is in 'raw_data.log'
        )

        if not raw_data_points:
            logger.warning(f"No raw data points found for fingerprint generation in the window {started_at_str} to {ended_at} for types {data_point_types_to_include}.")
            # Still return a fingerprint structure, but with empty statistics
            return self._create_fingerprint_object(
                fingerprint_type=fingerprint_type,
                inference_config_name=inference_config_name,
                started_at=started_at_str,
                ended_at=ended_at,
                statistics={},
                raw_data_points=[] # Store empty list or None if no data
            )


        # 3. Calculate statistics for each unique data_point type and key
        statistics: Dict[str, Dict[str, Any]] = {}
        data_points_by_path: Dict[str, List[Any]] = {} # To collect values per aggregated path

        for dp in raw_data_points:
            # Create the aggregated path: '{type}.{key}' or '{type}'
            aggregated_path = dp['type']
            if dp.get('key') is not None:
                aggregated_path = f"{dp['type']}.{dp['key']}"

            if aggregated_path not in data_points_by_path:
                data_points_by_path[aggregated_path] = []

            data_points_by_path[aggregated_path].append(dp['value'])

        for aggregated_path, values in data_points_by_path.items():
            if not values:
                continue

            # Attempt to calculate statistics only if values are numeric
            numeric_values = [v for v in values if isinstance(v, (int, float))]

            if len(numeric_values) > 0:
                stats: Dict[str, Any] = {
                    'num_samples': len(numeric_values)
                }
                # Calculate median and std dev only if there's enough data
                if len(numeric_values) > 0: # Median requires at least one sample
                     try:
                         stats['median_value'] = statistics.median(numeric_values)
                     except statistics.StatisticsError:
                          stats['median_value'] = None # Handle case with non-numeric or empty list after filtering
                          logger.warning(f"Could not calculate median for {aggregated_path}. Non-numeric values or insufficient data.")
                if len(numeric_values) > 1: # Std dev requires at least two samples
                     try:
                         stats['std_dev_value'] = statistics.stdev(numeric_values)
                     except statistics.StatisticsError:
                          stats['std_dev_value'] = 0.0 # Default to 0 if stdev cannot be calculated (e.g., all values are same)
                          logger.debug(f"Could not calculate standard deviation for {aggregated_path}. All values are the same or insufficient data.")
                else:
                     stats['std_dev_value'] = 0.0 # Default to 0 if only one sample


                statistics[aggregated_path] = stats
            else:
                 logger.debug(f"No numeric values found for {aggregated_path} to calculate statistics.")


        # 4. Create the fingerprint object
        fingerprint = self._create_fingerprint_object(
            fingerprint_type=fingerprint_type,
            inference_config_name=inference_config_name,
            started_at=started_at_str,
            ended_at=ended_at,
            statistics=statistics,
            raw_data_points=raw_data_points # Pass raw data points for compression
        )

        logger.info(f"Generated fingerprint for type '{fingerprint_type}' with {len(statistics)} aggregated paths.")
        return fingerprint

    def _create_fingerprint_object(
        self,
        fingerprint_type: str,
        inference_config_name: str,
        started_at: str, # Keep window timestamps in generation_params for context
        ended_at: str,     # Keep window timestamps in generation_params for context
        statistics: Dict[str, Dict[str, Any]],
        raw_data_points: List[Dict[str, Any]] # List of data_point dicts
    ) -> Dict[str, Any]:
        """Helper to create a fingerprint dictionary based on the contract."""
        created_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Compress raw data points to store reference
        raw_data_json_string = json.dumps(raw_data_points)
        compressed_raw_data = zlib.compress(raw_data_json_string.encode('utf-8'))
        raw_data_ref = compressed_raw_data.hex() # Store as hex string

        return {
            'type': fingerprint_type,
            'created_at': created_at,
            'updated_at': created_at, # Initially created and updated at the same time
            'inference_ref': inference_config_name, # Use inference_ref
            'generation_params': { # Group generation parameters
                'data_point_types': list(set([dp_type.split('.')[0] for dp_type in statistics.keys()])), # Infer unique base types
                'started_at': started_at,
                'ended_at': ended_at,
            },
            'raw_data_ref': raw_data_ref,
            'statistics': statistics
        }


    def load_calibrated_fingerprints(self) -> Dict[str, Dict[str, Any]]:
        """
        Load all calibrated fingerprints from storage.

        Returns:
            A dictionary of calibrated fingerprints, keyed by fingerprint type.
        """
        # Return the already loaded fingerprints
        return self._calibrated_fingerprints

    def save_calibrated_fingerprint(self, fingerprint: Dict[str, Any]) -> None:
        """
        Save a generated fingerprint as a calibrated fingerprint.
        If a fingerprint with the same type already exists, it will be overwritten.

        Args:
            fingerprint: The fingerprint object to save.
        """
        fp_type = fingerprint.get('type')
        if not fp_type:
            logger.error("Fingerprint object is missing 'type'. Cannot save calibrated fingerprint.")
            return

        self._calibrated_fingerprints[fp_type] = fingerprint
        self._save_to_storage()
        logger.info(f"Saved calibrated fingerprint: {fp_type}")


    def update_calibrated_fingerprint(self, fingerprint_type: str, fingerprint: Dict[str, Any]) -> None:
        """
        Update an existing calibrated fingerprint.

        Args:
            fingerprint_type: The type of the fingerprint to update.
            fingerprint: The new fingerprint object to replace the existing one.
        """
        # Early exit if fingerprint type not found
        if fingerprint_type not in self._calibrated_fingerprints:
            logger.warning(f"Calibrated fingerprint type '{fingerprint_type}' not found. Cannot update.")
            return

        # Ensure the updated fingerprint has the correct type - Early exit if mismatch
        if fingerprint.get('type') != fingerprint_type:
             logger.warning(f"Updated fingerprint object type '{fingerprint.get('type')}' does not match target type '{fingerprint_type}'. Cannot update with mismatched type.")
             return

        # Update the updated_at timestamp
        fingerprint['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        self._calibrated_fingerprints[fingerprint_type] = fingerprint
        self._save_to_storage()
        logger.info(f"Updated calibrated fingerprint: {fingerprint_type}")


# Example Usage (for testing purposes)
if __name__ == '__main__':
    # This example requires a running DataStore instance and InferenceModule instance
    # For standalone testing, we'll use dummy instances
    logger.info("Running FingerprintingModule example (using dummy DataStore and InferenceModule)")

    # Create dummy DataStore, InferenceModule, and FingerprintingModule instances
    dummy_data_store = DataStore()
    # Create InferenceModule instance first, allowing FingerprintingModule to be set later
    dummy_inference_module = InferenceModule(data_store=dummy_data_store, config_dir="test_configs")
    # Create FingerprintingModule instance, allowing InferenceModule to be set later
    fingerprinting_module = FingerprintingModule(data_store=dummy_data_store, storage_dir="test_fingerprints")

    # Now wire the instances together using the setter methods
    fingerprinting_module.set_inference_module(dummy_inference_module)
    dummy_inference_module.set_fingerprinting_module(fingerprinting_module) # Assuming InferenceModule also has a setter


    # Add a dummy inference configuration to the dummy InferenceModule instance
    dummy_inference_config = {
        'name': 'location_inference_config',
        'inference_type': 'location',
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'data_point_types': ['android.sensor.pressure', 'android.sensor.wifi_scan.rssi'],
        'included_paths': ['android.sensor.pressure', 'android.sensor.wifi_scan.rssi'], # Keep included_paths for inference config
        'sensor_weights': {'android.sensor.pressure': 0.6, 'android.sensor.wifi_scan.rssi': 0.4},
        'window_duration_seconds': 10,
        'confidence_threshold': 0.7,
        'significant_difference': 1.5
    }
    # Add the dummy config to the specific instance's configurations
    dummy_inference_module._inference_configurations = {'location_inference_config': dummy_inference_config}
    logger.info("Added dummy inference configuration to dummy InferenceModule instance.")


    # Example: Generate a fingerprint (will use dummy DataStore)
    current_time_str = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    generated_fp = fingerprinting_module.generate_fingerprint(
        fingerprint_type='location.current',
        inference_config_name='location_inference_config',
        ended_at=current_time_str
    )

    # Refactored example usage with early exit
    if generated_fp is None:
        print("\nFingerprint generation failed.")
        # Exit the example if generation failed
        exit()


    print("\nGenerated Fingerprint:")
    print(json.dumps(generated_fp, indent=2))

    # Example: Save the generated fingerprint as a calibrated one
    # We need to give it a specific type like 'location.kitchen'
    calibrated_fp_type = 'location.example_room'
    generated_fp['type'] = calibrated_fp_type # Set the type for saving
    fingerprinting_module.save_calibrated_fingerprint(generated_fp)

    # Example: Load calibrated fingerprints
    loaded_fingerprints = fingerprinting_module.load_calibrated_fingerprints()
    print(f"\nLoaded Calibrated Fingerprints ({len(loaded_fingerprints)}):")
    for fp_type, fp_data in loaded_fingerprints.items():
        print(f"  Type: {fp_type}")
        # print(json.dumps(fp_data, indent=2)) # Uncomment to see full data

    # Example: Update a calibrated fingerprint
    if calibrated_fp_type in loaded_fingerprints:
        # Simulate some change in the fingerprint data (e.g., updated stats)
        updated_fp_data = loaded_fingerprints[calibrated_fp_type].copy()
        # Note: Accessing stats by aggregated path now
        if 'android.sensor.pressure' in updated_fp_data['statistics']:
            updated_fp_data['statistics']['android.sensor.pressure']['median_value'] = 980.5 # Simulate a change

        fingerprinting_module.update_calibrated_fingerprint(calibrated_fp_type, updated_fp_data)

        loaded_fingerprints_after_update = fingerprinting_module.load_calibrated_fingerprints()
        print(f"\nLoaded Calibrated Fingerprints After Update ({len(loaded_fingerprints_after_update)}):")
        for fp_type, fp_data in loaded_fingerprints_after_update.items():
            print(f"  Type: {fp_type}, Updated At: {fp_data.get('updated_at')}")
            # print(json.dumps(fp_data, indent=2)) # Uncomment to see full data

