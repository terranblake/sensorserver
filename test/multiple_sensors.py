import asyncio
import websockets
import json
import logging
import os

# Configure logging for better visibility
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

# Sensor event callback functions (remain the same, they just print)
def on_accelerometer_event(values, timestamp):
    logger.info(f"Accelerometer values = {values} timestamp = {timestamp}")

def on_gyroscope_event(values, timestamp):
    logger.info(f"Gyroscope values = {values} timestamp = {timestamp}")

def on_magnetic_field_event(values, timestamp):
    logger.info(f"Magnetic field values = {values} timestamp = {timestamp}")

class SensorClient:
    def __init__(self, address, sensor_type, on_sensor_event):
        self.address = address
        self.sensor_type = sensor_type
        self.on_sensor_event = on_sensor_event
        self.uri = f"ws://{self.address}/sensor/connect?type={self.sensor_type}"
        logger.info(f"Initialized client for sensor: {self.sensor_type} at {self.uri}")

    async def connect_and_receive(self):
        logger.info(f"Attempting to connect to {self.uri}")
        # Use websockets.connect as an async context manager
        # This handles the connection, handshake, and closure
        try:
            async with websockets.connect(self.uri) as websocket:
                logger.info(f"Successfully connected to {self.uri}")
                # Listen for messages indefinitely
                try:
                    async for message in websocket:
                        # Process the received message
                        self.handle_message(message)
                except websockets.exceptions.ConnectionClosedOK:
                    # Connection closed cleanly by the server
                    logger.info(f"Connection closed for {self.sensor_type} ({self.uri})")
                except websockets.exceptions.ConnectionClosedError as e:
                    # Connection closed due to an error
                    logger.error(f"Connection error for {self.sensor_type} ({self.uri}): {e}")
                except Exception as e:
                    # Catch any other potential exceptions during message processing
                    logger.error(f"Unexpected error processing message for {self.sensor_type} ({self.uri}): {e}")

        except websockets.exceptions.WebSocketException as e:
            # Catch connection errors (e.g., server not reachable, handshake failed)
            logger.error(f"Failed to connect to {self.uri}: {e}")
        except Exception as e:
             # Catch any other potential exceptions during connection setup
             logger.error(f"Unexpected error during connection setup for {self.uri}: {e}")


    def handle_message(self, message):
        # This method is called by the async message loop
        try:
            data = json.loads(message)
            # Assuming the message format is consistent with the original code
            values = data.get('values')
            timestamp = data.get('timestamp')

            if values is not None and timestamp is not None:
                 # Call the user-provided callback
                self.on_sensor_event(values=values, timestamp=timestamp)
            else:
                 logger.warning(f"Received message with missing data from {self.sensor_type}: {message}")

        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON message from {self.sensor_type}: {message}")
        except Exception as e:
            logger.error(f"Error in handle_message for {self.sensor_type}: {e}")


# --- Main Execution ---

# The server address
address = "10.0.0.2:8080"

# Create client instances
accel_client = SensorClient(address=address, sensor_type="android.sensor.accelerometer", on_sensor_event=on_accelerometer_event)
gyro_client = SensorClient(address=address, sensor_type="android.sensor.gyroscope", on_sensor_event=on_gyroscope_event)
magnetic_client = SensorClient(address=address, sensor_type="android.sensor.magnetic_field", on_sensor_event=on_magnetic_field_event)

# Run connections concurrently using asyncio
async def main():
    # Create tasks for each sensor client connection
    tasks = [
        accel_client.connect_and_receive(),
        gyro_client.connect_and_receive(),
        magnetic_client.connect_and_receive()
    ]
    # Run tasks concurrently and wait for them to complete (or be cancelled)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Client stopped by user")