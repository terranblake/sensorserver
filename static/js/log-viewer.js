/**
 * log-viewer.js - Handles the log viewer page functionality
 * Manages log fetching, display, filtering, and real-time updates
 */

import api from './api.js';
import state from './state.js';

class LogViewer {
    constructor() {
        this.elements = {
            logOutput: document.getElementById('logOutput'),
            logTypeSelect: document.getElementById('logType')
        };
        
        this.currentLogType = 'all';
        this.eventSource = null;
    }

    /**
     * Initialize the log viewer
     */
    initialize() {
        if (!this.elements.logOutput || !this.elements.logTypeSelect) {
            console.error('Required elements not found');
            return;
        }
        
        // Bind event handlers
        this.elements.logTypeSelect.addEventListener('change', this.handleLogTypeChange.bind(this));
        
        // Set initial log type
        this.currentLogType = this.elements.logTypeSelect.value;
        
        // Fetch initial logs
        this.fetchLogs();
        
        // Connect to SSE stream
        this.connectEventSource();
    }

    /**
     * Format a log entry for display
     * @param {Object} logEntry - Log entry to format
     * @returns {string} - Formatted log entry
     */
    formatLogLine(logEntry) {
        // logEntry is expected to be { type: '...', content: '...' }
        let formatted = `[${logEntry.type.toUpperCase()}] `;
        try {
            // Attempt to parse JSON for structured logs
            const data = JSON.parse(logEntry.content);
            // Simple pretty print
            formatted += JSON.stringify(data, null, 2);
        } catch (e) {
            // If not JSON, just use the raw content
            formatted += logEntry.content;
        }
        return formatted;
    }

    /**
     * Fetch logs from the server
     */
    async fetchLogs() {
        this.elements.logOutput.textContent = 'Loading...'; // Show loading indicator
        
        try {
            const logData = await api.fetchLogs(this.currentLogType, 200); // Fetch last 200 lines
            
            if (logData.logs && logData.logs.length > 0) {
                // Clear previous logs
                this.elements.logOutput.textContent = '';
                
                // Format and append new logs (newest at the bottom)
                logData.logs.forEach(log => {
                    this.appendLogLine(log);
                });
                
                // Scroll to the bottom
                this.elements.logOutput.scrollTop = this.elements.logOutput.scrollHeight;
            } else if (logData.error) {
                this.elements.logOutput.textContent = `Error loading logs: ${logData.error}`;
            } else {
                this.elements.logOutput.textContent = 'No logs found for this type.';
            }
        } catch (error) {
            console.error('Error fetching logs:', error);
            this.elements.logOutput.textContent = `Error fetching logs: ${error.message}`;
        }
    }

    /**
     * Append a single log line to the display
     * @param {Object} logEntry - Log entry to append
     */
    appendLogLine(logEntry) {
        const lineElement = document.createElement('div');
        lineElement.textContent = this.formatLogLine(logEntry);
        this.elements.logOutput.appendChild(lineElement);
    }

    /**
     * Handle log type change
     */
    handleLogTypeChange() {
        this.currentLogType = this.elements.logTypeSelect.value;
        
        // Close existing SSE connection
        this.closeEventSource();
        
        // Fetch historical logs
        this.fetchLogs().then(() => {
            // Reconnect after fetching historical data
            this.connectEventSource();
        });
    }

    /**
     * Connect to Server-Sent Events stream
     */
    connectEventSource() {
        console.log("Connecting to SSE stream...");
        
        this.eventSource = new EventSource("/logs/stream");
        
        this.eventSource.onmessage = (event) => {
            try {
                const logEntry = JSON.parse(event.data);
                const currentFilter = this.currentLogType;
                
                // Only append if it matches the current filter or filter is 'all'
                if (currentFilter === 'all' || currentFilter === logEntry.type) {
                    this.appendLogLine(logEntry);
                    
                    // Auto-scroll if near the bottom
                    const isScrolledToBottom = this.elements.logOutput.scrollHeight - 
                        this.elements.logOutput.clientHeight <= 
                        this.elements.logOutput.scrollTop + 1;
                        
                    if (isScrolledToBottom) {
                        this.elements.logOutput.scrollTop = this.elements.logOutput.scrollHeight;
                    }
                }
            } catch (e) {
                console.error("Error parsing SSE data:", e, "Data:", event.data);
            }
        };
        
        this.eventSource.onerror = (err) => {
            console.error("EventSource failed:", err);
            const disconnectMsg = document.createElement('div');
            disconnectMsg.textContent = "--- Log stream disconnected. Refresh page to reconnect. ---";
            disconnectMsg.style.color = 'red';
            this.elements.logOutput.appendChild(disconnectMsg);
            this.closeEventSource();
        };
    }

    /**
     * Close SSE connection
     */
    closeEventSource() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
            console.log("Closed SSE connection");
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    const logViewer = new LogViewer();
    logViewer.initialize();
});

export default LogViewer; 