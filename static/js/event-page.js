/**
 * event-page.js - Handles the event annotation page functionality
 * Manages sensor tree display, manual event submission, and auto-logging
 */

import api from './api.js';
import state from './state.js';

class EventPage {
    constructor() {
        this.elements = {
            sensorTreeContent: document.getElementById('sensor-tree-content'),
            errorDisplay: document.querySelector('.update-error'),
            autoLogButton: document.getElementById('auto-log-button'),
            autoLogDurationInput: document.getElementById('auto-log-duration'),
            autoLogStatus: document.getElementById('auto-log-status'),
            manualEventForm: document.getElementById('manual-event-form')
        };
        
        this.autoLogTimerId = null;
        this.checkedSensors = new Set();
    }

    /**
     * Initialize the event page
     */
    initialize() {
        // Bind event handlers
        if (this.elements.autoLogButton) {
            this.elements.autoLogButton.addEventListener('click', this.startAutoLogging.bind(this));
        }
        
        if (this.elements.manualEventForm) {
            this.elements.manualEventForm.addEventListener('submit', this.handleManualEventSubmit.bind(this));
        }
        
        // Subscribe to state changes
        state.subscribe('sensorState', this.updateSensorTree.bind(this));
        
        // Initialize state
        state.initialize().then(() => {
            // Start periodic refresh
            state.startPeriodicRefresh();
        });
    }

    /**
     * Recursively build HTML string for sensor tree
     * @param {Object} node - Node to build tree for
     * @param {string} path - Current path
     * @returns {string} - HTML string
     */
    buildSensorTreeHtml(node, path = '') {
        let html = '';
        
        if (typeof node === 'object' && node !== null) {
            // Check if it's a SensorState object (received as plain dict from API)
            if (node.hasOwnProperty('inferred_state') && node.hasOwnProperty('last_timestamp')) {
                const currentPath = path;
                const state = node.inferred_state || 'N/A';
                const badgeId = `badge-${currentPath}`;
                const checkboxId = `sensor-${currentPath}`;
                
                // Build the list item content directly
                html += `<span class="badge bg-secondary" id="${badgeId}">${state}</span>`;
                html += `<input type="checkbox" name="selected_sensors" value="${currentPath}" id="${checkboxId}" class="form-check-input ms-2">`;
                html += `<label for="${checkboxId}" class="form-check-label"></label>`;
            } else {
                // It's a regular dictionary node (group or category)
                html += '<ul>';
                const sortedKeys = Object.keys(node).sort();
                for (const key of sortedKeys) {
                    const value = node[key];
                    const newPath = path ? `${path}.${key}` : key;
                    // Build list item and recurse
                    html += `<li><strong>${key}:</strong> ${this.buildSensorTreeHtml(value, newPath)}</li>`;
                }
                html += '</ul>';
            }
        } else {
            // Fallback for unexpected data types
            html += String(node);
        }
        
        return html;
    }

    /**
     * Update the sensor tree display
     * @param {Object} stateData - Current sensor state data
     */
    updateSensorTree(stateData) {
        if (!this.elements.sensorTreeContent) {
            return; // Element not found
        }
        
        try {
            // 1. Save currently checked sensors
            this.saveCheckedSensors();
            
            // 2. Build new HTML tree
            const newHtml = this.buildSensorTreeHtml(stateData);
            
            // 3. Replace content
            this.elements.sensorTreeContent.innerHTML = newHtml;
            
            // 4. Restore checked state
            this.restoreCheckedSensors();
            
            // Clear any previous error message on success
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.style.display = 'none';
            }
        } catch (error) {
            console.error('Error updating sensor tree:', error);
            
            // Display error message in the UI
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.textContent = `Error updating status: ${error.message}`;
                this.elements.errorDisplay.style.display = 'block';
            }
        }
    }

    /**
     * Save currently checked sensors to memory
     */
    saveCheckedSensors() {
        if (!this.elements.sensorTreeContent) {
            return;
        }
        
        this.checkedSensors.clear();
        this.elements.sensorTreeContent.querySelectorAll('input[name="selected_sensors"]:checked').forEach(checkbox => {
            this.checkedSensors.add(checkbox.value);
        });
    }

    /**
     * Restore checked state from memory
     */
    restoreCheckedSensors() {
        if (!this.elements.sensorTreeContent) {
            return;
        }
        
        this.checkedSensors.forEach(sensorPath => {
            const checkbox = document.getElementById(`sensor-${sensorPath}`);
            if (checkbox) {
                checkbox.checked = true;
            }
        });
    }

    /**
     * Handle manual event submission
     * @param {Event} event - Form submit event
     */
    async handleManualEventSubmit(event) {
        event.preventDefault();
        
        const form = event.target;
        const description = form.querySelector('#description').value;
        const selectedSensors = Array.from(form.querySelectorAll('input[name="selected_sensors"]:checked')).map(el => el.value);
        
        if (!description) {
            alert('Please enter an event description.');
            return;
        }
        
        if (selectedSensors.length === 0) {
            alert('Please select at least one sensor.');
            return;
        }
        
        try {
            const result = await api.submitEvent(description, selectedSensors);
            console.log('Event submitted:', result);
            
            // Clear form
            form.querySelector('#description').value = '';
            this.elements.sensorTreeContent.querySelectorAll('input[name="selected_sensors"]:checked').forEach(checkbox => {
                checkbox.checked = false;
            });
            this.checkedSensors.clear();
            
            // Show success message
            alert('Event logged successfully.');
        } catch (error) {
            console.error('Error submitting event:', error);
            alert(`Error submitting event: ${error.message}`);
        }
    }

    /**
     * Start auto-logging of state changes
     */
    async startAutoLogging() {
        if (!this.elements.autoLogDurationInput || !this.elements.autoLogButton || !this.elements.autoLogStatus) {
            return;
        }
        
        const duration = parseInt(this.elements.autoLogDurationInput.value, 10);
        if (isNaN(duration) || duration <= 0) {
            alert('Please enter a valid positive duration.');
            return;
        }
        
        this.elements.autoLogButton.disabled = true;
        this.elements.autoLogStatus.textContent = `Logging changes for ${duration}s...`;
        this.elements.autoLogStatus.style.display = 'inline';
        
        try {
            const result = await api.startAutoLogging(duration);
            console.log('Auto-logging started:', result);
            
            // Clear previous timer if any (safety check)
            if (this.autoLogTimerId) {
                clearTimeout(this.autoLogTimerId);
            }
            
            // Set a timer to re-enable the button and hide status
            this.autoLogTimerId = setTimeout(() => {
                this.elements.autoLogButton.disabled = false;
                this.elements.autoLogStatus.style.display = 'none';
                this.autoLogTimerId = null;
            }, duration * 1000);
        } catch (error) {
            console.error('Error starting auto-logging:', error);
            alert(`Error starting auto-logging: ${error.message}`);
            // Re-enable button immediately on error
            this.elements.autoLogButton.disabled = false;
            this.elements.autoLogStatus.style.display = 'none';
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    const eventPage = new EventPage();
    eventPage.initialize();
});

export default EventPage; 