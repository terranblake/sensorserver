# TASK: Web Interface and Enhanced Logging

**Goal:** Enhance the sensor monitoring script to include structured logging for state changes, a web interface for event annotation, and a log viewer.

**Status:** To Do

**Breakdown:**

1.  **Refactor Core Logic:**
    *   Move `SensorState` class, sensor discovery (`get_available_sensors`), WebSocket clients (`MultiSensorClient`, `GpsClient`), state management (`nested_sensor_data`), helper functions (`normalize_key`, `get_sensor_group`, `update_nested_data*`), and inference logic (`update_inferred_state`) from `test/all_sensors.py` into a new dedicated module (e.g., `sensor_logic.py`).
    *   Ensure the core logic can be run independently (e.g., via a main function within the module for testing) and update the shared state (`nested_sensor_data`).

2.  **Implement State Change Logging:**
    *   Modify `update_inferred_state` or its callers in `sensor_logic.py` to compare the newly inferred state with the existing state in the `SensorState` object.
    *   If the `inferred_state` string changes, log the following to a new file (`state_data.log`) in a structured format (e.g., JSON Lines):
        *   ISO Timestamp
        *   Normalized Sensor Path (e.g., `android.sensor.motion.accelerometer`)
        *   Previous State String
        *   New State String
    *   Set up a dedicated logger instance for `state_data.log`.

3.  **Set up Web Server Framework:**
    *   Create `server.py`.
    *   Choose a web framework (Flask recommended for simplicity with templating).
    *   Add necessary dependencies (Flask) to `requirements.txt`.
    *   Set up basic Flask application structure.

4.  **Integrate Sensor Logic with Server:**
    *   Determine method for sharing `nested_sensor_data` between the sensor logic (running asynchronously) and the web server (handling synchronous requests).
        *   *Option A (Simpler):* Run the asyncio sensor loop in a separate thread started by Flask. Use `threading.Lock` to protect access to `nested_sensor_data` from both the sensor thread and Flask request handlers.
        *   *Option B (More Robust):* Use inter-process communication (e.g., `multiprocessing.Queue` or Redis) if running sensor logic and server as separate processes.
    *   Implement chosen sharing mechanism.
    *   Modify the main execution flow: Flask starts the server, which in turn starts the sensor logic thread/process.

5.  **Develop Frontend Templates:**
    *   Create `templates/` directory.
    *   `base.html`: Basic HTML structure, potentially including common CSS/JS links.
    *   `event_page.html`: Template to display the sensor state tree and the event annotation form.
    *   `log_page.html`: Template for displaying logs and filter controls.

6.  **Implement Event Page:**
    *   Create Flask route (`/` or `/event`) for the event page.
    *   In the route handler:
        *   Acquire lock for `nested_sensor_data`.
        *   Read the current state.
        *   Release lock.
        *   Render `event_page.html`, passing the sensor state data (formatted suitably for recursive display in Jinja2 or handled by JavaScript).
    *   Update `event_page.html`:
        *   Display the sensor tree recursively (Jinja2 macro or JavaScript).
        *   Add checkboxes next to each sensor leaf node.
        *   Add a textarea for the description.
        *   Add a submit button.

7.  **Implement Event Submission and Logging:**
    *   Create Flask route (`/submit_event`, method POST).
    *   In the route handler:
        *   Get description, list of selected sensor paths, and request IP address.
        *   Create a timestamp.
        *   Log event data to `event_data.log` in a structured format (JSON Lines recommended), including timestamp, IP, description, selected sensors.
    *   Redirect back to the event page or show a success message.
    *   Set up a dedicated logger instance for `event_data.log`.

8.  **Implement Log Viewer Page Backend:**
    *   Create Flask route (`/logs`).
    *   Render `log_page.html`.
    *   Create Flask route (`/logs/data`, potentially accepting query parameters for filtering type and count/page).
    *   Implement logic to read recent lines from `raw_data.log`, `state_data.log`, `event_data.log` based on filters.
    *   Return log data as JSON.

9.  **Implement Log Viewer Page Frontend:**
    *   Update `log_page.html`:
        *   Add filter controls (radio buttons/dropdown for type).
        *   Add area to display logs.
        *   Use JavaScript (e.g., Fetch API) to:
            *   Fetch historical data from `/logs/data` on load and when filters change.
            *   (Optional - Realtime) Connect to a streaming endpoint (see step 10) to display new logs.

10. **Implement Log Streaming (Optional but Recommended):**
    *   Choose a streaming method (Server-Sent Events - SSE recommended for simplicity).
    *   Create Flask route (`/logs/stream`).
    *   Implement logic to monitor the three log files for changes (e.g., using a library like `watchdog` or periodically checking file size/modification time and reading new lines).
    *   When new lines are detected, format them (e.g., add type) and send them as SSE events.

**Considerations:**

*   **Concurrency:** Careful handling of shared state (`nested_sensor_data`) and log file access is crucial.
*   **Error Handling:** Robust error handling in both sensor logic and web server routes.
*   **Scalability:** The initial threaded approach might have limitations under heavy load; consider separate processes if needed later.
*   **Frontend Complexity:** Keep the initial frontend simple; focus on functionality.
*   **Dependencies:** Manage dependencies using `requirements.txt`. 