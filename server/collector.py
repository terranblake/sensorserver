import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import os
# Configure basic logging for the module
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from data_store import DataStore

class Collector:
    """
    Collects raw sensor data from the Sensor Server, converts it into the
    standardized data_point format, and logs it to the Data Store.
    Also handles converting structured inference results to data_points for logging.
    """

    def __init__(self, data_store: DataStore):
        """
        Initializes the Collector.

        Args:
            data_store: An instance of the DataStore.
        """
        self.data_store = data_store
        logger.info("Collector initialized.")

    def receive_raw_data(self, raw_data: Dict[str, Any]) -> None:
        """
        Receives raw sensor data from the Sensor Server and processes it by dispatching
        to type-specific handler functions. Converts raw data to data_point format
        and logs to the DataStore.

        Args:
            raw_data: A dictionary representing the raw data received from a sensor.
                      Expected format is similar to what your client sends.
        """
        logger.debug(f"Received raw data: {raw_data}")

        # Validate basic structure of raw data - Early exit
        raw_type = raw_data.get('type')
        raw_values = raw_data.get('values') # Note: GPS data doesn't use 'values'
        raw_name = raw_data.get('name')  # Get sensor name if available

        if not raw_type:
            logger.warning(f"Received raw data missing 'type'. Skipping processing: {raw_data}")
            return

        # Dispatch to type-specific handler function
        data_points_to_log: List[Dict[str, Any]] = []

        # Handle sensors with 'values' list
        if raw_values is not None:
            if raw_type == 'android.sensor.pressure':
                data_points_to_log = self._handle_pressure_data(raw_type, raw_name, raw_values, self._get_created_at(raw_data)) # Pass raw_type
            elif raw_type in ['android.sensor.accelerometer', 'android.sensor.accelerometer_uncalibrated', 'android.sensor.linear_acceleration', 'android.sensor.gravity', 'android.sensor.magnetic_field']:
                 data_points_to_log = self._handle_vector_sensor_data(raw_type, raw_name, raw_values, self._get_created_at(raw_data))
            elif raw_type in ['android.sensor.gyroscope', 'android.sensor.gyroscope_uncalibrated']:
                 data_points_to_log = self._handle_vector_sensor_data(raw_type, raw_name, raw_values, self._get_created_at(raw_data)) # Gyro is also a vector
            elif raw_type in ['android.sensor.rotation_vector', 'android.sensor.game_rotation_vector', 'android.sensor.geomagnetic_rotation_vector', 'android.sensor.orientation']:
                 data_points_to_log = self._handle_vector_sensor_data(raw_type, raw_name, raw_values, self._get_created_at(raw_data)) # Other vector/rotation sensors
            elif raw_type == 'android.sensor.wifi_scan':
                data_points_to_log = self._handle_wifi_scan_data(raw_name, raw_values)
            elif raw_type == 'android.sensor.bluetooth_scan':
                data_points_to_log = self._handle_bluetooth_scan_data(raw_name, raw_values)
            elif raw_type == 'android.sensor.network_scan':
                data_points_to_log = self._handle_network_scan_data(raw_name, raw_values)
            elif raw_type in ['com.google.sensor.gyro_temperature', 'com.google.sensor.pressure_temp']:
                data_points_to_log = self._handle_temperature_data(raw_type, raw_name, raw_values, self._get_created_at(raw_data))
            # Add more elif blocks for other sensors with 'values' list

        # Handle sensors with a different structure (like GPS)
        elif raw_type == 'gps':
             # For GPS, the raw_data dict itself contains the data fields
             data_points_to_log = self._handle_gps_data(raw_type, raw_data, self._get_created_at(raw_data)) # Pass raw_type
        # Handle unsupported types
        if not data_points_to_log:
            logger.warning(f"Unsupported raw data type: {raw_type}. Skipping conversion.")
            # Optionally log the raw data with an 'unsupported' type if needed
            # data_points_to_log.append({
            #     'created_at': created_at,
            #     'type': f'unsupported_raw_data.{raw_type}',
            #     'key': None,
            #     'value': raw_data # Store raw data directly if needed
            # })

        # Log converted data points to the DataStore
        if data_points_to_log:
            logger.debug(f"Converted raw data to {len(data_points_to_log)} data points.")
            for dp in data_points_to_log:
                self.data_store.set_data(dp, files=['raw_data'])
        else:
            logger.debug(f"No data points generated from raw data type: {raw_type}.")

    def _get_created_at(self, raw_data: Dict[str, Any]) -> str:
        """
        Handles timestamp data from raw data.
        """
        raw_timestamp_str = raw_data.get('timestamp') # Use timestamp from raw data if available
        formatted_timestamp = datetime.fromtimestamp(raw_timestamp_str / 1000).isoformat()

        # Use the timestamp from the raw data if provided, otherwise generate current time
        created_at = None
        if formatted_timestamp:
            try:
                # Attempt to parse the raw timestamp
                datetime.fromisoformat(formatted_timestamp.replace('Z', '+00:00'))
                created_at = formatted_timestamp # Use raw timestamp if valid
            except ValueError:
                logger.warning(f"Invalid timestamp format in raw data: {formatted_timestamp}. Using current time.")

        return created_at if created_at else datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


    def receive_inference_result(self, inference_result: Dict[str, Any]) -> None:
        """
        Receives structured inference results from the Inference Module, converts
        them to data_point format, and logs them to the DataStore.

        Args:
            inference_result: The structured inference result dictionary.
                              (Matches the inference_result contract).
        """
        logger.debug(f"Received inference result for logging: {inference_result.get('inference_name', 'unknown')}")

        # Convert inference result to data_point format for logging
        output_data_points = self._convert_result_to_data_points(inference_result)

        # Log inference result data_points to the DataStore
        if output_data_points:
             logger.debug(f"Converted inference result to {len(output_data_points)} data points for logging.")
             for dp in output_data_points:
                 # Log prediction and confidence to raw_data and inference_data
                 # Log the full structured result to inference_data
                 inference_type = inference_result.get('inference_type', 'unknown_type')
                 if dp['type'] in [f'inference.{inference_type}.prediction', f'inference.{inference_type}.confidence']:
                      self.data_store.set_data(dp, files=['raw_data', 'inference_data'])
                 elif dp['type'] == f'inference.{inference_type}.result':
                      self.data_store.set_data(dp, files=['inference_data'])
                 else:
                      # Log any other unexpected data points from conversion
                      logger.warning(f"Logging unexpected inference data point type: {dp.get('type')}. Logging to inference_data.")
                      self.data_store.set_data(dp, files=['inference_data'])

        else:
             logger.warning("No data points generated from inference result for logging.")


    def _handle_pressure_data(self, sensor_type: str, raw_name: str, raw_values: Any, created_at: str) -> List[Dict[str, Any]]:
        """Handles conversion for android.sensor.pressure data."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, list) or not raw_values:
            logger.warning(f"Pressure data 'values' is not a non-empty list: {raw_values}. Skipping data point.")
            return data_points # Early exit

        # Assuming pressure has a single value in the list
        pressure_value = raw_values[0]
        if not isinstance(pressure_value, (int, float)):
             logger.warning(f"Pressure value is not numeric: {pressure_value}. Skipping data point.")
             return data_points # Early exit

        data_points.append({
            'created_at': created_at,
            'type': sensor_type, # Use the sensor type as the data point type
            'key': raw_name, # Set key to None for scalar sensors without instance ID in raw data
            'value': float(pressure_value) # Ensure float type
        })

        # TODO: If raw data includes a sensor instance ID, use that for the 'key' instead of None


        return data_points

    def _handle_vector_sensor_data(self , sensor_type: str, raw_name: str, raw_values: Any, created_at: str) -> List[Dict[str, Any]]:
        """Handles conversion for vector-based sensor data (Accelerometer, Gyroscope, etc.)."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, list) or len(raw_values) < 3:
            logger.warning(f"Vector sensor data '{sensor_type}' 'values' is not a list with at least 3 elements: {raw_values}. Skipping data points.")
            return data_points # Early exit

        # Define keys for primary vector components (x, y, z)
        vector_keys = ['x', 'y', 'z']
        # Some sensors might have more values (e.g., uncalibrated sensors include bias, rotation vectors include scalar component and accuracy)
        # We'll log the first 3 as x, y, z for consistency with common usage.
        # You might extend this to log other components if needed.

        for i in range(3): # Process up to the first 3 values
             if i < len(raw_values): # Ensure index is within bounds
                 value = raw_values[i]
                 if not isinstance(value, (int, float)):
                      logger.warning(f"Non-numeric value for {sensor_type} component {vector_keys[i]}: {value}. Skipping data point.")
                      continue # Skip this component, continue with others

                 data_points.append({
                     'created_at': created_at,
                     'type': f'{sensor_type}.{vector_keys[i]}', # e.g., 'android.sensor.accelerometer.x'
                     'key': raw_name, # Set key to None for vector components without instance ID in raw data
                     'value': float(value)
                 })
             else:
                  # Log a warning if expected x, y, or z is missing
                  logger.warning(f"Missing expected value for {sensor_type} component {vector_keys[i]}.")

        # TODO: If raw data includes a sensor instance ID, use that for the 'key' instead of None


        # Handle additional values for specific sensor types
        if sensor_type in ['android.sensor.accelerometer_uncalibrated', 'android.sensor.gyroscope_uncalibrated', 'android.sensor.magnetic_field_uncalibrated']:
            # Assuming uncalibrated sensors have bias values after x, y, z
            if len(raw_values) >= 6:
                bias_keys = ['bias_x', 'bias_y', 'bias_z']
                for i in range(3):
                    bias_value = raw_values[i+3]
                    if isinstance(bias_value, (int, float)):
                        data_points.append({
                            'created_at': created_at,
                            'type': f'{sensor_type}.{bias_keys[i]}',
                            'key': raw_name, # Set key to None for bias components
                            'value': float(bias_value)
                        })
                    else:
                         logger.warning(f"Non-numeric bias value for {sensor_type} component {bias_keys[i]}: {bias_value}. Skipping data point.")
            elif len(raw_values) > 3:
                 logger.warning(f"Partial bias data for {sensor_type}. Expected 6 values, got {len(raw_values)}. Skipping bias logging.")


        elif sensor_type in ['android.sensor.rotation_vector', 'android.sensor.geomagnetic_rotation_vector', 'android.sensor.game_rotation_vector']:
            # Assuming rotation vectors have a scalar component (cos(theta/2)) after x, y, z
            if len(raw_values) >= 4:
                 scalar_value = raw_values[3]
                 if isinstance(scalar_value, (int, float)):
                      data_points.append({
                          'created_at': created_at,
                          'type': f'{sensor_type}.scalar',
                          'key': raw_name, # Set key to None for scalar component
                          'value': float(scalar_value)
                      })
                 else:
                      logger.warning(f"Non-numeric scalar value for {sensor_type}: {scalar_value}. Skipping data point.")

            # Assuming geomagnetic_rotation_vector and rotation_vector have estimated heading accuracy after scalar
            if sensor_type in ['android.sensor.rotation_vector', 'android.sensor.geomagnetic_rotation_vector']:
                 if len(raw_values) >= 5:
                      accuracy_value = raw_values[4]
                      if isinstance(accuracy_value, (int, float)):
                           data_points.append({
                               'created_at': created_at,
                               'type': f'{sensor_type}.accuracy',
                               'key': raw_name, # Set key to None for accuracy
                               'value': float(accuracy_value)
                           })
                      else:
                           logger.warning(f"Non-numeric accuracy value for {sensor_type}: {accuracy_value}. Skipping data point.")
                 elif len(raw_values) > 4:
                      logger.warning(f"Partial accuracy data for {sensor_type}. Expected 5 values, got {len(raw_values)}. Skipping accuracy logging.")

            elif sensor_type == 'android.sensor.game_rotation_vector' and len(raw_values) > 4:
                 logger.warning(f"Unexpected additional data for {sensor_type}. Expected 4 values, got {len(raw_values)}. Skipping extra data logging.")


        elif sensor_type == 'android.sensor.orientation':
             # Orientation usually has 3 values (azimuth, pitch, roll)
             if len(raw_values) > 3:
                  logger.warning(f"Unexpected additional data for {sensor_type}. Expected 3 values, got {len(raw_values)}. Skipping extra data logging.")


        # For other vector sensors (accelerometer, gravity, magnetic_field) with exactly 3 values,
        # no additional specific handling is needed beyond the initial x, y, z loop.
        elif sensor_type in ['android.sensor.accelerometer', 'android.sensor.gravity', 'android.sensor.magnetic_field']:
             if len(raw_values) > 3:
                  logger.warning(f"Unexpected additional data for {sensor_type}. Expected 3 values, got {len(raw_values)}. Skipping extra data logging.")


        return data_points


    def _handle_gps_data(self, sensor_type: str, raw_name: str, raw_data: Dict[str, Any], created_at: str) -> List[Dict[str, Any]]:
        """Handles conversion for gps data."""
        data_points: List[Dict[str, Any]] = []

        # Define the keys we expect in GPS raw data and their data point types
        gps_fields = {
            'latitude': 'android.sensor.gps.latitude',
            'longitude': 'android.sensor.gps.longitude',
            'altitude': 'android.sensor.gps.altitude',
            'accuracy': 'android.sensor.gps.accuracy', # Horizontal accuracy
            'verticalAccuracyMeters': 'android.sensor.gps.vertical_accuracy',
            'speed': 'android.sensor.gps.speed',
            'speedAccuracyMetersPerSecond': 'android.sensor.gps.speed_accuracy',
            'bearing': 'android.sensor.gps.bearing',
            'bearingAccuracyDegrees': 'android.sensor.gps.bearing_accuracy',
            'time': 'android.sensor.gps.time', # Include time if needed
            'elapsedRealtimeNanos': 'android.sensor.gps.elapsed_realtime_nanos', # Include elapsed realtime
            'elapsedRealtimeAgeMillis': 'android.sensor.gps.elapsed_realtime_age_millis', # Include elapsed realtime age
            'elapsedRealtimeUncertaintyNanos': 'android.sensor.gps.elapsed_realtime_uncertainty_nanos', # Include elapsed realtime uncertainty
            'lastKnowLocation': 'android.sensor.gps.last_known_location' # Include boolean last known location
        }

        for field, dp_type in gps_fields.items():
            value = raw_data.get(field)

            if value is not None:
                # Basic check for numeric types where expected
                numeric_fields = [
                    'latitude', 'longitude', 'altitude', 'accuracy', 'verticalAccuracyMeters',
                    'speed', 'speedAccuracyMetersPerSecond', 'bearing', 'bearingAccuracyDegrees',
                    'time', 'elapsedRealtimeNanos', 'elapsedRealtimeAgeMillis', 'elapsedRealtimeUncertaintyNanos'
                ]
                boolean_fields = ['lastKnowLocation']

                if field in numeric_fields:
                     if not isinstance(value, (int, float)):
                          logger.warning(f"GPS field '{field}' has non-numeric value: {value}. Skipping data point.")
                          continue # Skip this field
                     value = float(value) # Ensure float type
                elif field in boolean_fields:
                     if not isinstance(value, bool):
                          logger.warning(f"GPS field '{field}' has non-boolean value: {value}. Skipping data point.")
                          continue
                     # Value is already boolean, no conversion needed

                data_points.append({
                    'created_at': created_at,
                    'type': dp_type, # e.g., 'android.sensor.gps.latitude'
                    'key': raw_name, # Set key to None for GPS fields without instance ID in raw data
                    'value': value
                })
            # No warning for missing optional fields, as some might not always be present

        # TODO: If raw data includes a sensor instance ID, use that for the 'key' instead of None


        return data_points


    def _handle_wifi_scan_data(self, _raw_name: str, raw_values: Any) -> List[Dict[str, Any]]:
        """Handles conversion for android.sensor.wifi_scan data."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, list):
             logger.warning(f"WiFi scan 'values' is not a list: {raw_values}. Skipping data points.")
             return data_points # Early exit

        for network_data in raw_values:
            if not isinstance(network_data, dict):
                logger.warning(f"WiFi network data is not a dictionary: {network_data}. Skipping.")
                continue # Skip this item, continue with others

            # Use 'bssid' for WiFi ID if available, otherwise try 'id'
            net_id = network_data.get('bssid')
            if net_id is None:
                net_id = network_data.get('id') # Try 'id' if 'bssid' is missing

            created_at = self._get_created_at(network_data)

            net_rssi = network_data.get('rssi') # Signal strength
            net_frequency = network_data.get('frequency')
            net_channel = network_data.get('channel')

            if not net_id or net_rssi is None:
                 # Updated warning to reflect keys being checked
                 logger.warning(f"WiFi network data missing 'bssid'/'id' or 'rssi': {network_data}. Skipping.")
                 continue # Skip this item

            # Log RSSI as a data point
            if isinstance(net_rssi, (int, float)):
                data_points.append({
                    'created_at': created_at,
                    'type': 'android.sensor.wifi_scan.rssi',
                    'key': str(net_id), # Use ID as key
                    'value': float(net_rssi) # Ensure float type
                })
            else:
                 logger.warning(f"Non-numeric RSSI for wifi {net_id}: {net_rssi}. Skipping RSSI data point.")

            # Log Frequency as a data point (if available and numeric)
            if net_frequency is not None:
                 if isinstance(net_frequency, (int, float)):
                      data_points.append({
                          'created_at': created_at,
                          'type': 'android.sensor.wifi_scan.frequency',
                          'key': str(net_id), # Use ID as key
                          'value': float(net_frequency)
                      })
                 else:
                      logger.warning(f"Non-numeric Frequency for wifi {net_id}: {net_frequency}. Skipping Frequency data point.")

            # Log Channel as a data point (if available and numeric)
            if net_channel is not None:
                 if isinstance(net_channel, (int, float)):
                      data_points.append({
                          'created_at': created_at,
                          'type': 'android.sensor.wifi_scan.channel',
                          'key': str(net_id), # Use ID as key
                          'value': float(net_channel)
                      })
                 else:
                      logger.warning(f"Non-numeric Channel for wifi {net_id}: {net_channel}. Skipping Channel data point.")

        return data_points

    def _handle_bluetooth_scan_data(self, _raw_name: str, raw_values: Any) -> List[Dict[str, Any]]:
        """Handles conversion for android.sensor.bluetooth_scan data."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, list):
             logger.warning(f"Bluetooth scan 'values' is not a list: {raw_values}. Skipping data points.")
             return data_points # Early exit

        for device_data in raw_values:
            if not isinstance(device_data, dict):
                logger.warning(f"Bluetooth device data is not a dictionary: {device_data}. Skipping.")
                continue # Skip this item, continue with others

            # Use 'address' for Bluetooth ID if available, otherwise try 'id'
            net_id = device_data.get('address')
            if net_id is None:
                net_id = device_data.get('id') # Try 'id' if 'address' is missing

            created_at = self._get_created_at(device_data)
            net_rssi = device_data.get('rssi') # Signal strength

            if not net_id or net_rssi is None:
                 # Updated warning to reflect keys being checked
                 logger.warning(f"Bluetooth device data missing 'address'/'id' or 'rssi': {device_data}. Skipping.")
                 continue # Skip this item

            # Log RSSI as a data point
            if isinstance(net_rssi, (int, float)):
                data_points.append({
                    'created_at': created_at,
                    'type': 'android.sensor.bluetooth_scan.rssi',
                    'key': str(net_id), # Use ID as key
                    'value': float(net_rssi) # Ensure float type
                })
            else:
                 logger.warning(f"Non-numeric RSSI for bluetooth {net_id}: {net_rssi}. Skipping RSSI data point.")


        return data_points

    def _handle_network_scan_data(self, _raw_name: str, raw_values: Any) -> List[Dict[str, Any]]:
        """Handles conversion for android.sensor.network_scan data (combined WiFi/Bluetooth)."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, dict):
             logger.warning(f"Network scan 'values' is not a dictionary: {raw_values}. Skipping data points.")
             return data_points # Early exit

        wifi_results = raw_values.get('wifiResults', [])
        bluetooth_results = raw_values.get('bluetoothResults', [])

        # Process wifi_results (list of dicts) - call the specific handler
        if isinstance(wifi_results, list):
             data_points.extend(self._handle_wifi_scan_data(_raw_name, wifi_results))
        else:
             logger.warning(f"network_scan 'wifiResults' is not a list: {wifi_results}. Skipping WiFi results.")

        # Process bluetooth_results (list of dicts) - call the specific handler
        if isinstance(bluetooth_results, list):
             data_points.extend(self._handle_bluetooth_scan_data(_raw_name, bluetooth_results))
        else:
             logger.warning(f"network_scan 'bluetoothResults' is not a list: {bluetooth_results}. Skipping Bluetooth results.")

        return data_points

    def _handle_temperature_data(self, sensor_type: str, raw_name: str, raw_values: Any, created_at: str) -> List[Dict[str, Any]]:
        """Handles conversion for temperature data from gyro and pressure sensors."""
        data_points: List[Dict[str, Any]] = []

        if not isinstance(raw_values, list) or not raw_values:
            logger.warning(f"Temperature data '{sensor_type}' 'values' is not a non-empty list: {raw_values}. Skipping data point.")
            return data_points # Early exit

        # Temperature value is the first element in the values array
        temp_value = raw_values[0]
        if not isinstance(temp_value, (int, float)):
             logger.warning(f"Temperature value is not numeric: {temp_value}. Skipping data point.")
             return data_points # Early exit

        data_points.append({
            'created_at': created_at,
            'type': sensor_type,
            'key': raw_name, # Use the sensor name as the key
            'value': float(temp_value) # Ensure float type
        })

        return data_points

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
        inference_type = inference_result.get('inference_type', 'unknown_type')

        # Log overall prediction and confidence (to raw_data and inference_data)
        overall_prediction = inference_result.get('overall_prediction', {})
        predicted_value = overall_prediction.get('value')
        confidence = overall_prediction.get('confidence')

        # Log prediction data_point
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.prediction',
            'key': inference_name, # Key is the inference config name
            'value': predicted_value # Can be None if no confident prediction
        })

        # Log confidence data_point
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.confidence',
            'key': inference_name, # Key is the inference config name
            'value': confidence # Can be -1.0 or other indicator if no confident prediction
        })


        # Log the full structured result (to inference_data)
        # The value will be the entire inference_result dictionary
        output_data_points.append({
            'created_at': created_at,
            'type': f'inference.{inference_type}.result', # Type indicates this is a full result
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
    # This example requires a running DataStore instance
    logger.info("Running Collector example (using dummy DataStore)")

    # Create a dummy DataStore instance
    dummy_data_store = DataStore()

    # Instantiate the Collector
    collector = Collector(data_store=dummy_data_store)

    # Simulate receiving some raw data
    raw_pressure_data = {
        'type': 'android.sensor.pressure',
        'values': [1013.1],
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    collector.receive_raw_data(raw_pressure_data)

    raw_wifi_data = {
        'type': 'android.sensor.wifi_scan',
        'values': [
            # Use 'bssid' instead of 'id' in these examples for consistency with network_scan format
            {'type': 'wifi', 'bssid': 'aa:bb:cc:dd:ee:ff', 'rssi': -55, 'frequency': 2412, 'channel': 1},
            {'type': 'wifi', 'bssid': '11:22:33:44:55:66', 'rssi': -70, 'frequency': 5180, 'channel': 36},
        ],
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    collector.receive_raw_data(raw_wifi_data)

    raw_bluetooth_data = {
        'type': 'android.sensor.bluetooth_scan',
        'values': [
            # Use 'address' instead of 'id' in these examples for consistency with network_scan format
            {'type': 'bluetooth', 'address': 'ff:ee:dd:cc:bb:aa', 'rssi': -80, 'name': 'My Speaker'},
            {'type': 'bluetooth', 'address': '99:88:77:66:55:44', 'rssi': -90, 'name': 'Fitness Tracker'},
        ],
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    collector.receive_raw_data(raw_bluetooth_data)

    raw_network_scan_data = {
         'type': 'android.sensor.network_scan',
         'values': {
              'wifiResults': [
                  {'bssid': 'cc:dd:ee:ff:11:22', 'ssid': 'HomeNet', 'rssi': -60, 'frequency': 2437, 'channel': 6},
              ],
              'bluetoothResults': [
                  {'address': '1a:2b:3c:4d:5e:6f', 'name': 'SmartLock', 'rssi': -75},
              ]
         },
         'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }
    collector.receive_raw_data(raw_network_scan_data)

    # Simulate receiving raw data for accel, gyro, and GPS
    raw_accel_data = {"values": [-0.5515456, 8.256434, 9.052647], "accuracy": 1, "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), "type": "android.sensor.accelerometer"}
    collector.receive_raw_data(raw_accel_data)

    raw_gyro_data = {"values": [-0.5510005, -1.1102476, 0.34422258], "accuracy": 3, "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), "type": "android.sensor.gyroscope"}
    collector.receive_raw_data(raw_gyro_data)

    raw_gps_data = {"longitude": -94.72710985728278, "latitude": 39.008755212939704, "altitude": 289.6259594694927, "bearing": 0.0, "accuracy": 6.659035, "speed": 0.0, "time": 1746376604000, "lastKnowLocation": True, "speedAccuracyMetersPerSecond": 0.0, "bearingAccuracyDegrees": 0.0, "elapsedRealtimeNanos": 255761225672000, "verticalAccuracyMeters": 4.1526594, "elapsedRealtimeAgeMillis": 465875, "elapsedRealtimeUncertaintyNanos": 100000.0, "type": "gps", "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    collector.receive_raw_data(raw_gps_data)


    print("\nCollector example finished. Check log files for data points.")
