/**
 * State.js - Manages frontend state and data transformations
 * Provides reactive state management and data processing helpers
 */

import api from './api.js';

class State {
    constructor() {
        this.listeners = {};
        this.data = {
            sensorState: null,
            locationScores: null,
            calibratedFingerprints: null,
            inferenceConfigs: null,
            logs: {},
            realtimeData: []
        };
        // Maximum number of real-time data points to keep in memory
        this.maxRealtimeDataPoints = 200;
    }

    /**
     * Subscribe to state changes
     * @param {string} key - State key to subscribe to
     * @param {Function} callback - Function to call when state changes
     * @returns {Function} - Function to unsubscribe
     */
    subscribe(key, callback) {
        if (!this.listeners[key]) {
            this.listeners[key] = [];
        }
        
        this.listeners[key].push(callback);
        
        // Return unsubscribe function
        return () => {
            this.listeners[key] = this.listeners[key].filter(cb => cb !== callback);
        };
    }

    /**
     * Update state
     * @param {string} key - State key to update
     * @param {*} value - New value
     */
    update(key, value) {
        this.data[key] = value;
        
        // Notify listeners
        if (this.listeners[key]) {
            this.listeners[key].forEach(callback => {
                try {
                    callback(value);
                } catch (error) {
                    console.error(`Error in state update callback for ${key}:`, error);
                }
            });
        }
    }

    /**
     * Get current state value
     * @param {string} key - State key to get
     * @returns {*} - Current value
     */
    get(key) {
        return this.data[key];
    }

    /**
     * Add a real-time data point, limiting to max size
     * @param {Object} dataPoint - Data point to add
     */
    addRealtimeDataPoint(dataPoint) {
        const currentData = [...this.data.realtimeData];
        
        // Add new data point
        currentData.push(dataPoint);
        
        // Limit to max size
        if (currentData.length > this.maxRealtimeDataPoints) {
            currentData.shift(); // Remove oldest
        }
        
        this.update('realtimeData', currentData);
    }

    /**
     * Initialize all state data from API
     */
    async initialize() {
        try {
            // Fetch sensor state
            const stateData = await api.fetchStateData();
            this.update('sensorState', stateData);
            
            // If state data includes location scores, update them
            if (stateData.location_scores) {
                this.update('locationScores', stateData.location_scores);
            }
            
            // Fetch calibrated fingerprints
            const fingerprints = await api.fetchCalibratedFingerprints();
            this.update('calibratedFingerprints', fingerprints.calibrated_fingerprints || []);
            
            // Fetch inference configurations
            const configs = await api.fetchInferenceConfigurations();
            this.update('inferenceConfigs', configs.inference_configurations || []);
            
            // Connect WebSocket for real-time updates
            api.connectWebSocket();
            
            // Register WebSocket callbacks
            api.onWebSocketEvent('data_point', (data) => {
                this.addRealtimeDataPoint(data);
            });
            
            console.log('State initialized successfully');
        } catch (error) {
            console.error('Error initializing state:', error);
        }
    }

    /**
     * Start periodic state refresh
     * @param {number} intervalMs - Refresh interval in milliseconds
     */
    startPeriodicRefresh(intervalMs = 2000) {
        setInterval(async () => {
            try {
                // Refresh sensor state
                const stateData = await api.fetchStateData();
                this.update('sensorState', stateData);
                
                // If state data includes location scores, update them
                if (stateData.location_scores) {
                    this.update('locationScores', stateData.location_scores);
                }
            } catch (error) {
                console.error('Error refreshing state:', error);
            }
        }, intervalMs);
    }

    /**
     * Fetch and update logs
     * @param {string} logType - Type of log to fetch
     */
    async fetchLogs(logType) {
        try {
            const logData = await api.fetchLogs(logType);
            
            // Update logs for this type
            const currentLogs = {...this.data.logs};
            currentLogs[logType] = logData.logs || [];
            
            this.update('logs', currentLogs);
        } catch (error) {
            console.error(`Error fetching logs of type ${logType}:`, error);
        }
    }
    
    /**
     * Helper to extract sensor state by path
     * @param {string} path - Dot-notation path to sensor state
     * @returns {*} - Sensor state value or null if not found
     */
    getSensorStateByPath(path) {
        if (!this.data.sensorState) {
            return null;
        }
        
        const parts = path.split('.');
        let current = this.data.sensorState;
        
        for (const part of parts) {
            if (current === null || current === undefined || typeof current !== 'object') {
                return null;
            }
            current = current[part];
        }
        
        return current;
    }
    
    /**
     * Generate a current timestamp in ISO 8601 format
     * @returns {string} - ISO 8601 timestamp
     */
    static getCurrentTimestamp() {
        return new Date().toISOString();
    }
    
    /**
     * Generate a timestamp for N seconds ago in ISO 8601 format
     * @param {number} secondsAgo - Number of seconds ago
     * @returns {string} - ISO 8601 timestamp
     */
    static getTimestampSecondsAgo(secondsAgo) {
        const date = new Date();
        date.setSeconds(date.getSeconds() - secondsAgo);
        return date.toISOString();
    }
}

// Export as singleton
const state = new State();
export default state; 