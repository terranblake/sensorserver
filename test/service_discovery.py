import asyncio
import aiohttp
import json
import logging

# Configure logging for better visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def discover_sensors(address: str) -> list[str]:
    """
    Discovers available sensor types from the server using an HTTP endpoint.

    Args:
        address: The server address (host:port).

    Returns:
        A list of sensor type strings discovered, or an empty list if discovery fails.
    """
    # Assuming the server exposes an HTTP endpoint like http://host:port/sensors
    discovery_url = f"http://{address}:9090/sensors"
    logger.info(f"Attempting to discover sensors from {discovery_url}")

    try:
        # Use aiohttp to perform an asynchronous HTTP GET request
        async with aiohttp.ClientSession() as session:
            async with session.get(discovery_url) as response:
                # Raise an exception for HTTP error status codes (400s or 500s)
                response.raise_for_status()
                # Assuming the response body is a JSON array of sensor type strings
                sensors_list = await response.json()

                if isinstance(sensors_list, list):
                    logger.info(f"Discovery successful.")
                    return sensors_list
                else:
                    logger.error(f"Discovery endpoint did not return a list of strings: {sensors_list}")
                    return []

    except aiohttp.ClientError as e:
        # Catch errors related to the HTTP request (network issues, server errors, etc.)
        logger.error(f"Failed to discover sensors from {discovery_url}: {e}")
        return []
    except json.JSONDecodeError:
         # Handle cases where the HTTP response body is not valid JSON
         logger.error(f"Failed to parse JSON response from discovery endpoint: {await response.text()}")
         return []
    except Exception as e:
        # Catch any other unexpected errors during discovery
        logger.error(f"Unexpected error during sensor discovery from {discovery_url}: {e}")
        return []

# --- Main Execution ---

async def main():
    """
    Main function to perform sensor discovery.
    """
    server_address = "192.168.18.3:8080" # The server address (host:port)

    # Perform sensor discovery
    discovered_sensor_types = await discover_sensors(server_address)

    # Print the results
    if discovered_sensor_types:
        logger.info("Discovered the following sensor types:")
        for sensor_type in discovered_sensor_types:
            print(f"- {sensor_type}")
    else:
        logger.warning("No sensor types were discovered.")


if __name__ == "__main__":
    try:
        # Run the main async function to start the discovery process
        asyncio.run(main())
    except KeyboardInterrupt:
        # Allow the user to stop the script gracefully with Ctrl+C
        logger.info("Discovery script stopped by user (KeyboardInterrupt)")
    except Exception as e:
        # Catch any unhandled exceptions that bubble up
        logger.critical(f"An unhandled error occurred: {e}")