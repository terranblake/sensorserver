/**
 * API.js - Handles all API interactions between frontend and backend
 * Provides standardized methods for data fetching, sensor updates, and WebSocket connections
 */

class API {
    constructor() {
        this.baseUrl = window.location.origin;
        this.wsUrl = `ws://${window.location.hostname}:5001`;
        this.wsConnection = null;
        this.wsCallbacks = {
            'data_point': [],
            'error': [],
            'open': [],
            'close': [],
            'inference_complete': [],
            'sensor_data': []
        };
    }

    // --- WebSocket Real-time Updates ---
    
    /**
     * Connect to the real-time WebSocket server for sensor updates
     */
    connectWebSocket() {
        if (this.wsConnection) {
            return; // Already connected
        }
        
        this.wsConnection = new WebSocket(this.wsUrl);
        
        this.wsConnection.onopen = (event) => {
            console.log('WebSocket connection established');
            this._notifyCallbacks('open', event);
        };
        
        this.wsConnection.onmessage = (event) => {
            // console.log("[API.js] onmessage received raw data:", event.data);
            try {
                const data = JSON.parse(event.data);
                console.log("[API.js] onmessage parsed data:", data); // Re-enabled log
                const eventType = data.type || 'data_point'; // Determine event type from message, default if missing
                console.log(`[API.js] Determined eventType: '${eventType}'. Notifying callbacks...`); // Re-added LOG
                this._notifyCallbacks(eventType, data);
            } catch (error) {
                console.error('Error parsing WebSocket message:', error);
                this._notifyCallbacks('error', {
                    error: 'Failed to parse WebSocket message',
                    original: event.data
                });
            }
        };
        
        this.wsConnection.onerror = (error) => {
            console.error('WebSocket error:', error);
            this._notifyCallbacks('error', error);
        };
        
        this.wsConnection.onclose = (event) => {
            console.log('WebSocket connection closed');
            this.wsConnection = null;
            this._notifyCallbacks('close', event);
            
            // Auto-reconnect after delay
            setTimeout(() => this.connectWebSocket(), 5000);
        };
    }
    
    /**
     * Register callback for WebSocket events
     * @param {string} event - Event type: 'data_point', 'error', 'open', 'close'
     * @param {Function} callback - Function to call when event occurs
     */
    onWebSocketEvent(event, callback) {
        // Ensure the array for this event type exists
        if (!this.wsCallbacks[event]) {
            console.log(`[API.js] Creating new callback array for event type: '${event}'`); // Log creation
            this.wsCallbacks[event] = [];
        }
        // Now push the callback
        this.wsCallbacks[event].push(callback);
    }
    
    /**
     * Remove callback for WebSocket events
     * @param {string} event - Event type
     * @param {Function} callback - Function to remove
     */
    removeWebSocketCallback(event, callback) {
        if (this.wsCallbacks[event]) {
            const index = this.wsCallbacks[event].indexOf(callback);
            if (index !== -1) {
                this.wsCallbacks[event].splice(index, 1);
            }
        }
    }
    
    /**
     * Notify all registered callbacks for an event
     * @private
     */
    _notifyCallbacks(event, data) {
        console.log(`[API.js] Inside _notifyCallbacks for event: '${event}'`); // Re-added LOG
        if (this.wsCallbacks[event]) {
            // Log the callbacks array before iterating
            console.log(`[API.js] Callbacks found for '${event}':`, this.wsCallbacks[event], `(Count: ${this.wsCallbacks[event].length})`); // ADDED LOG
            this.wsCallbacks[event].forEach(callback => {
                try {
                    callback(data);
                } catch (error) {
                    console.error(`Error in WebSocket ${event} callback:`, error);
                }
            });
        }
    }
    
    // --- Data Store API ---
    
    /**
     * Fetch data points from the Data Store
     * @param {string[]} types - Data point types to fetch
     * @param {string} startedAt - Start timestamp (ISO 8601)
     * @param {string} endedAt - End timestamp (ISO 8601)
     * @param {string[]} [keys] - Optional data point keys to filter by
     * @param {string[]} [files] - Optional log files to search
     * @returns {Promise<Object>} - Promise resolving to data points
     */
    async fetchDataPoints(types, startedAt, endedAt, keys = null, files = null) {
        const params = new URLSearchParams();
        
        // Add required parameters
        types.forEach(type => params.append('types', type));
        params.append('started_at', startedAt);
        params.append('ended_at', endedAt);
        
        // Add optional parameters if provided
        if (keys) {
            keys.forEach(key => params.append('keys', key));
        }
        
        if (files) {
            files.forEach(file => params.append('files', file));
        }
        
        const response = await fetch(`${this.baseUrl}/api/data?${params.toString()}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    // --- Fingerprinting API ---
    
    /**
     * Fetch all calibrated fingerprints
     * @returns {Promise<Object>} - Promise resolving to calibrated fingerprints
     */
    async fetchCalibratedFingerprints() {
        const response = await fetch(`${this.baseUrl}/api/fingerprints`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Generate a fingerprint
     * @param {string} fingerprintType - Type for the generated fingerprint
     * @param {string} inferenceConfigName - Name of the inference configuration to use
     * @param {string} endedAt - End timestamp (ISO 8601)
     * @returns {Promise<Object>} - Promise resolving to the generated fingerprint
     */
    async generateFingerprint(fingerprintType, inferenceConfigName, endedAt) {
        const response = await fetch(`${this.baseUrl}/api/fingerprints/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                type: fingerprintType,
                inference_config_name: inferenceConfigName,
                ended_at: endedAt
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Save a fingerprint as a calibrated fingerprint
     * @param {Object} fingerprint - Fingerprint object to save
     * @returns {Promise<Object>} - Promise resolving to the save result
     */
    async saveFingerprint(fingerprint) {
        const response = await fetch(`${this.baseUrl}/api/fingerprints/calibrate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                fingerprint: fingerprint
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    // --- Inference API ---
    
    /**
     * Fetch all inference configurations
     * @returns {Promise<Object>} - Promise resolving to inference configurations
     */
    async fetchInferenceConfigurations() {
        const response = await fetch(`${this.baseUrl}/api/inference_configs`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Save a new inference configuration
     * @param {Object} config - Inference configuration to save
     * @returns {Promise<Object>} - Promise resolving to the save result
     */
    async saveInferenceConfiguration(config) {
        const response = await fetch(`${this.baseUrl}/api/inference_configs`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(config)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Update an existing inference configuration
     * @param {string} configName - Name of the configuration to update
     * @param {Object} config - Updated configuration object
     * @returns {Promise<Object>} - Promise resolving to the update result
     */
    async updateInferenceConfiguration(configName, config) {
        const response = await fetch(`${this.baseUrl}/api/inference_configs/${configName}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(config)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Run an inference with a specific configuration
     * @param {string} configName - Name of the inference configuration to run
     * @returns {Promise<Object>} - Promise resolving to the run result
     */
    async runInference(configName) {
        const response = await fetch(`${this.baseUrl}/api/inference/run/${configName}`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Fetch inference run history for a specific configuration
     * @param {string} configName - Name of the inference configuration
     * @param {number} count - Number of history items to fetch
     * @returns {Promise<Object>} - Promise resolving to run history data
     */
    async fetchInferenceHistory(configName, count = 50) {
        const response = await fetch(`${this.baseUrl}/api/inference/history/${configName}?count=${count}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    // --- State API ---
    
    /**
     * Fetch current state data
     * @returns {Promise<Object>} - Promise resolving to current state data
     */
    async fetchStateData() {
        const response = await fetch(`${this.baseUrl}/state/data`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Submit a manual event 
     * @param {string} description - Event description
     * @param {string[]} selectedSensors - Array of selected sensor paths
     * @returns {Promise<Object>} - Promise resolving to the submit result
     */
    async submitEvent(description, selectedSensors) {
        const formData = new FormData();
        formData.append('description', description);
        
        selectedSensors.forEach(sensor => {
            formData.append('selected_sensors', sensor);
        });
        
        const response = await fetch(`${this.baseUrl}/submit_event`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    /**
     * Start auto-logging of state changes
     * @param {number} duration - Duration in seconds
     * @returns {Promise<Object>} - Promise resolving to the start result
     */
    async startAutoLogging(duration) {
        const formData = new FormData();
        formData.append('duration', duration);
        
        const response = await fetch(`${this.baseUrl}/start_auto_event_logging`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
    
    // --- Log API ---
    
    /**
     * Fetch log data
     * @param {string} logType - Type of log to fetch
     * @param {number} count - Number of lines to fetch
     * @returns {Promise<Object>} - Promise resolving to log data
     */
    async fetchLogs(logType, count = 200) {
        const response = await fetch(`${this.baseUrl}/logs/data?type=${logType}&count=${count}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${await response.text()}`);
        }
        
        return await response.json();
    }
}

// Export as a singleton
const api = new API();
export default api; 