import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

# Configure basic logging for the module
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Assume DataStore class is available (either imported or in the same project)
try:
    from data_store import DataStore
except ImportError:
    logger.error("DataStore module not found. Please ensure data_store.py is available.")
    # Define a dummy DataStore class if import fails
    class DataStore:
        def __init__(self, log_directory: str = "data_logs"):
            pass
        def get_data(self, types, started_at, ended_at, keys=None, files=None):
            logger.warning("Using dummy DataStore.get_data - no data will be retrieved.")
            return []
        def set_data(self, data_point, files=['raw_data']):
            logger.warning("Using dummy DataStore.set_data - no data will be written.")
            pass

# Assume FingerprintingModule class is available
# Import is needed for type hinting and potentially dummy class definition
try:
    from fingerprinting import FingerprintingModule
except ImportError:
    logger.error("FingerprintingModule module not found. Please ensure fingerprinting_module.py is available.")
    # Define a dummy FingerprintingModule class if import fails
    class FingerprintingModule:
        def __init__(self, data_store=None, storage_dir="fingerprints"):
            pass
        def load_calibrated_fingerprints(self):
            logger.warning("Using dummy FingerprintingModule.load_calibrated_fingerprints - no fingerprints will be loaded.")
            return {}
        def generate_fingerprint(self, fingerprint_type, inference_config_name, ended_at):
             logger.warning("Using dummy FingerprintingModule.generate_fingerprint - no fingerprint will be generated.")
             return None


class InferenceModule:
    """
    Manages inference configurations, calculates similarity scores, predicts outcomes,
    and logs results as data_points to the DataStore.
    """

    # Define the file name for inference configurations
    INFERENCE_CONFIGS_FILE = "inference_configurations.json"

    def __init__(self, data_store: DataStore, config_dir: str = "configs"): # Removed fingerprinting_module from __init__
        """
        Initializes the InferenceModule.

        Args:
            data_store: An instance of the DataStore.
            config_dir: The directory where inference configurations JSON file will be stored.
                        Defaults to "configs".
        """
        self.data_store = data_store
        self._fingerprinting_module: Optional[FingerprintingModule] = None # Store as an attribute, allow None initially
        self.config_dir = config_dir
        self.inference_configs_path = os.path.join(self.config_dir, self.INFERENCE_CONFIGS_FILE)

        # Ensure the config directory exists
        os.makedirs(self.config_dir, exist_ok=True)
        logger.info(f"InferenceModule initialized. Configuration directory: {self.config_dir}")

        # Load inference configurations on initialization
        self._inference_configurations: Dict[str, Dict[str, Any]] = self._load_configurations()

    def set_fingerprinting_module(self, fingerprinting_module: FingerprintingModule) -> None:
        """
        Sets the FingerprintingModule instance after initialization.
        This resolves the circular dependency.
        """
        self._fingerprinting_module = fingerprinting_module
        logger.info("FingerprintingModule instance set in InferenceModule.")


    def _load_configurations(self) -> Dict[str, Dict[str, Any]]:
        """Loads inference configurations from the JSON file."""
        if not os.path.exists(self.inference_configs_path):
            logger.info("Inference configurations file not found. Starting with empty collection.")
            return {}

        try:
            with open(self.inference_configs_path, 'r') as f:
                configurations = json.load(f)
                logger.info(f"Successfully loaded {len(configurations)} inference configurations from {self.inference_configs_path}")
                return configurations
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {self.inference_configs_path}: {e}")
            return {} # Return empty on error
        except Exception as e:
            logger.error(f"Unexpected error loading inference configurations: {e}", exc_info=True)
            return {} # Return empty on error

    def _save_configurations(self) -> None:
        """Saves the current inference configurations to the JSON file."""
        try:
            with open(self.inference_configs_path, 'w') as f:
                json.dump(self._inference_configurations, f, indent=2)
            logger.info(f"Successfully saved {len(self._inference_configurations)} inference configurations to {self.inference_configs_path}")
        except Exception as e:
            logger.error(f"Error saving inference configurations to {self.inference_configs_path}: {e}", exc_info=True)

    def load_inference_configurations(self) -> Dict[str, Dict[str, Any]]:
        """
        Load all inference configurations from storage.

        Returns:
            A dictionary of inference configurations, keyed by name.
        """
        # Return the already loaded configurations
        return self._inference_configurations

    def save_inference_configuration(self, inference_config: Dict[str, Any]) -> None:
        """
        Save a new inference configuration.
        If a configuration with the same name already exists, it will be overwritten.

        Args:
            inference_config: The inference configuration object to save.
        """
        config_name = inference_config.get('name')
        if not config_name:
            logger.error("Inference configuration object is missing 'name'. Cannot save configuration.")
            return

        # Ensure required properties are present (basic check)
        required_props = ['inference_type', 'data_point_types', 'included_paths', 'sensor_weights', 'window_duration_seconds', 'confidence_threshold', 'significant_difference']
        if not all(prop in inference_config for prop in required_props):
             logger.error(f"Inference configuration '{config_name}' is missing required properties. Cannot save.")
             return

        # Add/update timestamps
        now_str = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        if 'created_at' not in inference_config:
            inference_config['created_at'] = now_str
        inference_config['updated_at'] = now_str

        self._inference_configurations[config_name] = inference_config
        self._save_configurations()
        logger.info(f"Saved inference configuration: {config_name}")

    def update_inference_configuration(self, config_name: str, inference_config: Dict[str, Any]) -> None:
        """
        Update an existing inference configuration.

        Args:
            config_name: The name of the inference configuration to update.
            inference_config: The new inference configuration object.
        """
        # Early exit if configuration name not found
        if config_name not in self._inference_configurations:
            logger.warning(f"Inference configuration '{config_name}' not found. Cannot update.")
            return

        # Ensure the updated configuration has the correct name - Early exit if mismatch
        if inference_config.get('name') != config_name:
             logger.warning(f"Updated inference configuration object name '{inference_config.get('name')}' does not match target name '{config_name}'. Cannot update with mismatched name.")
             return

        # Ensure required properties are present (basic check)
        required_props = ['inference_type', 'data_point_types', 'included_paths', 'sensor_weights', 'window_duration_seconds', 'confidence_threshold', 'significant_difference']
        if not all(prop in inference_config for prop in required_props):
             logger.error(f"Inference configuration '{config_name}' is missing required properties. Cannot update.")
             return

        # Update the updated_at timestamp
        inference_config['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        self._inference_configurations[config_name] = inference_config
        self._save_configurations()
        logger.info(f"Updated inference configuration: {config_name}")


    def run_inference(
        self,
        inference_config_name: str,
        current_time: str
    ) -> List[Dict[str, Any]]:
        """
        Execute an inference run for a specific configuration.

        Args:
            inference_config_name: The name of the inference configuration to run.
            current_time: The current timestamp (ISO 8601 string) to use as the end of the data window.

        Returns:
            A list of data_point objects representing the inference output (prediction, confidence, etc.).
        """
        # Early exit if FingerprintingModule is not set
        if self._fingerprinting_module is None:
            logger.error("FingerprintingModule is not set. Cannot run inference.")
            return []

        # 1. Load the specified inference configuration
        inference_config = self._inference_configurations.get(inference_config_name)
        if not inference_config:
            logger.error(f"Inference configuration '{inference_config_name}' not found. Cannot run inference.")
            return [] # Return empty list if config not found

        inference_type = inference_config.get('inference_type')
        included_paths = inference_config.get('included_paths', [])
        # Use data_point_types from config for querying, as per contract
        data_point_types_to_query = inference_config.get('data_point_types', [])
        window_duration_seconds = inference_config.get('window_duration_seconds')
        sensor_weights = inference_config.get('sensor_weights', {})
        confidence_threshold = inference_config.get('confidence_threshold', 0.0)
        significant_difference = inference_config.get('significant_difference', 1.0)

        if not included_paths or window_duration_seconds is None or not data_point_types_to_query:
             logger.error(f"Inference configuration '{inference_config_name}' is missing required parameters ('included_paths', 'window_duration_seconds', or 'data_point_types').")
             return []

        try:
            current_time_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            started_at_dt = current_time_dt - timedelta(seconds=window_duration_seconds)
            started_at_str = started_at_dt.isoformat().replace('+00:00', 'Z')
        except ValueError as e:
            logger.error(f"Invalid 'current_time' timestamp format: {e}")
            return []

        logger.info(f"Running inference '{inference_config_name}' ({inference_type}) using data points ${data_point_types_to_query} from {started_at_str} to {current_time}")

        # 2. Get the current data window from the DataStore
        # Use the data_point_types_to_query from the config
        current_data_window_points = self.data_store.get_data(
            types=data_point_types_to_query, # Use the full data point types
            started_at=started_at_str,
            ended_at=current_time,
            files=['raw_data'] # Assume current data is in 'raw_data.log'
        )

        if not current_data_window_points:
            logger.warning(f"No current data points found for inference '{inference_config_name}' in the window {started_at_str} to {current_time}.")
            return [] # Cannot run inference without current data

        # 3. Load all calibrated fingerprints
        calibrated_fingerprints = self._fingerprinting_module.load_calibrated_fingerprints() # Use the set instance

        # Filter calibrated fingerprints to those relevant for this inference type
        # A calibrated fingerprint is relevant if its type matches the inference type namespace
        # e.g., inference_type 'location' matches fingerprint types 'location.kitchen', 'location.office'
        relevant_calibrated_fingerprints = {
            fp_type: fp_data for fp_type, fp_data in calibrated_fingerprints.items()
            if fp_type.startswith(f"{inference_type}.") # Assuming fingerprint types are namespaced like 'inference_type.name'
        }

        if not relevant_calibrated_fingerprints:
            logger.warning(f"No relevant calibrated fingerprints found for inference type '{inference_type}'. Cannot perform comparison.")
            # Even without calibrated fingerprints, we might want to log the current data snapshot
            # For now, return empty if no calibrated fingerprints to compare against.
            return []

        logger.info(f"Loaded {len(relevant_calibrated_fingerprints)} relevant calibrated fingerprints for inference '{inference_config_name}' ({inference_type})")

        # 4. Iterate through relevant calibrated fingerprints and calculate scores
        inference_comparisons: List[Dict[str, Any]] = []
        best_prediction_target_id: Optional[str] = None
        best_prediction_confidence = -1.0 # Confidence is 0-1, so -1 is a good initial low value

        for fp_type, calibrated_fp_data in relevant_calibrated_fingerprints.items():
            target_id = fp_type.split('.', 1)[-1] # Extract name from type (e.g., 'kitchen' from 'location.kitchen')

            # Calculate score and confidence for this comparison
            score_details = self._calculate_score(
                current_data_window_points,
                calibrated_fp_data,
                inference_config
            )

            comparison_result = {
                'target_type': fp_type,
                'target_id': target_id,
                'total_score': score_details['total_score'],
                'confidence_score': score_details['confidence_score'],
                'path_contributions': score_details['path_contributions']
            }
            inference_comparisons.append(comparison_result)

            # Update best prediction if this comparison has higher confidence
            if score_details['confidence_score'] > best_prediction_confidence:
                best_prediction_confidence = score_details['confidence_score']
                best_prediction_target_id = target_id


        # 5. Determine overall prediction and confidence
        overall_predicted_value = None
        if best_prediction_target_id is not None and best_prediction_confidence >= confidence_threshold:
             # Optional: Add check for significant difference from second best if needed
             # For simplicity now, just use the threshold
             overall_predicted_value = best_prediction_target_id
             logger.info(f"Inference '{inference_config_name}' predicted: '{overall_predicted_value}' with confidence {best_prediction_confidence:.2f}")
        else:
             logger.info(f"Inference '{inference_config_name}' did not yield a confident prediction. Best confidence: {best_prediction_confidence:.2f}")


        # 6. Create the structured inference result
        inference_result: Dict[str, Any] = {
            'inference_name': inference_config_name,
            'inference_type': inference_type, # Include inference_type in the result
            'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'overall_prediction': {
                'value': overall_predicted_value,
                'confidence': best_prediction_confidence
            },
            'comparisons': inference_comparisons
        }

        # 7. Convert inference result to data_point format for logging
        output_data_points = self._convert_result_to_data_points(inference_result)

        # 8. Log inference result data_points to the DataStore
        for dp in output_data_points:
             # Log prediction and confidence to raw_data and inference_data
             if dp['type'] in [f'inference.{inference_type}.prediction', f'inference.{inference_type}.confidence']:
                  self.data_store.set(dp, files=['raw_data', 'inference_data'])
             else:
                  self.data_store.set(dp, files=['inference_data']) # Log other inference metrics only to inference_data log


        logger.info(f"Inference run '{inference_config_name}' completed. Logged {len(output_data_points)} data points.")
        return output_data_points


    def _calculate_score(
        self,
        current_data_points: List[Dict[str, Any]],
        calibrated_fingerprint: Dict[str, Any],
        inference_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Placeholder for the core generalized scoring algorithm.
        Compares current data points against a calibrated fingerprint
        based on the inference configuration's included_paths and sensor_weights.

        Args:
            current_data_points: List of data_point objects for the current time window.
            calibrated_fingerprint: The calibrated fingerprint object to compare against.
            inference_config: The inference configuration being used.

        Returns:
            A dictionary containing:
            - total_score: The overall similarity/difference score.
            - confidence_score: A confidence score between 0 and 1.
            - path_contributions: Detailed breakdown of contributions per included path.
        """
        # --- PLACEHOLDER SCORING LOGIC ---
        # This is where the actual scoring algorithm needs to be implemented.
        # It should be generalized to work with any data point types defined in included_paths.
        # It needs to compare current_data_points (potentially aggregated into a temporary fingerprint
        # or processed directly) against the statistics in calibrated_fingerprint['statistics'].
        # The sensor_weights from inference_config['sensor_weights'] should be applied.
        # The output should include a total_score and a confidence_score (0-1).
        # path_contributions should detail how each included_path contributed.

        # Dummy implementation returning placeholder values
        total_score = 0.0
        confidence_score = 0.0
        path_contributions: Dict[str, Dict[str, Any]] = {}

        included_paths = inference_config.get('included_paths', [])
        sensor_weights = inference_config.get('sensor_weights', {})
        calibrated_stats = calibrated_fingerprint.get('statistics', {})

        # Example: Calculate a simple weighted difference (replace with actual logic)
        current_stats: Dict[str, Dict[str, Any]] = {} # You would aggregate current_data_points here

        # Dummy aggregation for current data points for this placeholder
        current_values_by_path: Dict[str, List[Any]] = {}
        for dp in current_data_points:
             # Create the aggregated path: '{type}.{key}' or '{type}'
             aggregated_path = dp['type']
             if dp.get('key') is not None:
                 aggregated_path = f"{dp['type']}.{dp['key']}"

             if aggregated_path in included_paths: # Only consider included paths
                  if aggregated_path not in current_values_by_path:
                       current_values_by_path[aggregated_path] = []
                  current_values_by_path[aggregated_path].append(dp['value'])

        for path in included_paths:
             weight = sensor_weights.get(path, 0.0)
             calibrated_stat = calibrated_stats.get(path)
             current_values = current_values_by_path.get(path, [])

             unweighted_metric = 0.0 # Placeholder for the calculated metric (e.g., normalized difference)
             weighted_contribution = 0.0

             if calibrated_stat and current_values:
                  # Dummy calculation: Use the first current value and compare to median
                  current_value = current_values[0] if current_values else None # Use the first value as a simple example
                  median = calibrated_stat.get('median_value')
                  stddev = calibrated_stat.get('std_dev_value', 0.0) # Default stddev to 0 if missing

                  if median is not None and isinstance(current_value, (int, float)) and stddev >= 0:
                       # Avoid division by zero or very small numbers
                       safe_stddev = max(stddev, 0.01)
                       unweighted_metric = abs(current_value - median) / safe_stddev # Simple normalized difference
                       weighted_contribution = unweighted_metric * weight # Weighted difference

             path_contributions[path] = {
                 'weighted_contribution': weighted_contribution,
                 'unweighted_metric': unweighted_metric,
                 'weight': weight
             }
             total_score += weighted_contribution # Summing weighted differences as a simple placeholder score

        # Dummy confidence score calculation (invert and clamp a normalized total score)
        # This will need to be replaced with a proper confidence mapping based on the actual scoring algorithm
        # Example: Assuming a maximum reasonable total score based on expected differences and weights
        # This is highly dependent on the actual scoring logic and data ranges.
        # For this placeholder, let's assume a max possible unweighted metric of 100 per path
        # and a max weight of 1.0, summed over included paths.
        max_unweighted_metric_per_path = 100.0 # Arbitrary
        max_total_unweighted_score = max_unweighted_metric_per_path * len(included_paths)
        max_total_weighted_score = sum(sensor_weights.values()) * max_unweighted_metric_per_path if included_paths else 0

        # Use the sum of weights for normalization if weights are intended to sum to 1.0
        total_weight_sum = sum(sensor_weights.values()) if sensor_weights else 1.0
        normalized_total_score = total_score / (max_total_weighted_score if max_total_weighted_score > 0 else 1.0) # Normalize by a max expected score


        confidence_score = 1.0 - normalized_total_score # Simple inversion (higher score -> lower confidence)
        confidence_score = max(0.0, min(confidence_score, 1.0)) # Ensure 0-1 range


        logger.debug(f"Calculated score for {calibrated_fingerprint.get('type')}: Total={total_score:.2f}, Confidence={confidence_score:.2f}")

        return {
            'total_score': total_score,
            'confidence_score': confidence_score,
            'path_contributions': path_contributions
        }


    def _convert_result_to_data_points(self, inference_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Converts a structured inference result into a list of data_point objects for logging.

        Args:
            inference_result: The structured inference result dictionary.

        Returns:
            A list of data_point objects.
        """
        output_data_points: List[Dict[str, Any]] = []
        created_at = inference_result.get('created_at', datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        inference_name = inference_result.get('inference_name', 'unknown_inference')
        inference_type = inference_result.get('inference_type', 'unknown_type') # Get inference_type from the result


        # Log overall prediction and confidence (to raw_data and inference_data)
        overall_prediction = inference_result.get('overall_prediction', {})
        predicted_value = overall_prediction.get('value')
        confidence = overall_prediction.get('confidence')

        # Log prediction data_point
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.prediction', # Use the correct inference_type
            'key': inference_name, # Key is the inference config name
            'value': predicted_value # Can be None if no confident prediction
        })

        # Log confidence data_point
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.confidence', # Use the correct inference_type
            'key': inference_name, # Key is the inference config name
            'value': confidence # Can be -1.0 or other indicator if no confident prediction
        })


        # Log the full structured result (to inference_data)
        # The value will be the entire inference_result dictionary
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.result', # Use the correct inference_type
            'key': inference_name, # Key is the inference config name
            'value': inference_result # The full structured result
        })


        # You could add more data points here for specific metrics from comparisons if needed
        # For example, logging the confidence for each individual comparison target:
        # for comparison in inference_result.get('comparisons', []):
        #      output_data_points.append({
        #          'created_at': created_at,
        #          'type': f'inference.{inference_type}.comparison_confidence',
        #          'key': f"{inference_name}.{comparison.get('target_id', 'unknown_target')}",
        #          'value': comparison.get('confidence_score')
        #      })


        return output_data_points


# Example Usage (for testing purposes)
if __name__ == '__main__':
    # This example requires a running DataStore instance and FingerprintingModule instance
    # For standalone testing, we'll use dummy instances
    logger.info("Running InferenceModule example (using dummy DataStore and FingerprintingModule)")

    # Create dummy DataStore, InferenceModule, and FingerprintingModule instances
    dummy_data_store = DataStore()
    # Create InferenceModule instance first, allowing FingerprintingModule to be set later
    inference_module = InferenceModule(data_store=dummy_data_store, config_dir="test_configs")
    # Create FingerprintingModule instance, allowing InferenceModule to be set later
    # Note: FingerprintingModule constructor also needs DataStore
    fingerprinting_module = FingerprintingModule(data_store=dummy_data_store, storage_dir="test_fingerprints")


    # Now wire the instances together using the setter methods
    inference_module.set_fingerprinting_module(fingerprinting_module)
    # Assuming FingerprintingModule also has a setter for InferenceModule
    # fingerprinting_module.set_inference_module(inference_module) # This line is in fingerprinting.py example


    # Create a dummy inference configuration
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
    # This simulates loading the config from a file
    inference_module._inference_configurations = {'location_inference_config': dummy_inference_config}
    logger.info("Simulated loading dummy inference configuration into InferenceModule instance.")


    # Create a dummy calibrated fingerprint for comparison
    dummy_calibrated_fingerprint = {
        'type': 'location.kitchen',
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'inference_ref': 'location_inference_config',
        'generation_params': {
             'data_point_types': ['android.sensor.pressure', 'android.sensor.wifi_scan.rssi'],
             'started_at': (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace('+00:00', 'Z'),
             'ended_at': (datetime.now(timezone.utc) - timedelta(minutes=9)).isoformat().replace('+00:00', 'Z'),
        },
        'raw_data_ref': 'dummy_compressed_data_ref',
        'statistics': {
            'android.sensor.pressure': {'median_value': 1012.0, 'std_dev_value': 0.1, 'num_samples': 100},
            'android.sensor.wifi_scan.rssi.fa:8f:ca:55:8f:f1': {'median_value': -70.0, 'std_dev_value': 5.0, 'num_samples': 50},
            # Add other network stats here
        }
    }
    # Simulate loading this calibrated fingerprint into the FingerprintingModule instance
    fingerprinting_module._calibrated_fingerprints = {'location.kitchen': dummy_calibrated_fingerprint}
    logger.info("Simulated loading dummy calibrated fingerprint into FingerprintingModule instance.")


    # Create a dummy list of current data points (simulating a real-time window)
    current_time_str = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    past_time_str = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace('+00:00', 'Z')

    dummy_current_data_points = [
        {'created_at': past_time_str, 'type': 'android.sensor.pressure', 'key': None, 'value': 1012.1},
        {'created_at': past_time_str, 'type': 'android.sensor.wifi_scan.rssi', 'key': 'fa:8f:ca:55:8f:f1', 'value': -71.0},
        {'created_at': past_time_str, 'type': 'android.sensor.wifi_scan.rssi', 'key': 'other:network', 'value': -85.0},
        # Add other relevant data points here
    ]
    logger.info(f"Created dummy current data points ({len(dummy_current_data_points)}).")

    # --- FIX: Write dummy current data points to DataStore before running inference ---
    logger.info("Writing dummy current data points to DataStore...")
    for dp in dummy_current_data_points:
        dummy_data_store.set_data(dp, files=['raw_data'])
    # --- End FIX ---


    # Instantiate the InferenceModule (already done above)
    # Instantiate the FingerprintingModule (already done above)
    # Wire the instances (already done above)

    # Example: Run inference
    print("\nAttempting to run inference...")
    inference_output_points = inference_module.run_inference(
        inference_config_name='location_inference_config',
        current_time=current_time_str
    )

    print(f"\nInference run completed. Generated {len(inference_output_points)} output data points:")
    for dp in inference_output_points:
        print(dp)

