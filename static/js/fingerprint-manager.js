/**
 * fingerprint-manager.js - Handles the fingerprint management functionality
 * Allows viewing, creating, and updating calibrated fingerprints
 */

import api from './api.js';
// import state from './state.js'; // No longer needed

class FingerprintManager {
    constructor() {
        this.elements = {
            fingerprintList: document.getElementById('fingerprintList'),
            configSelect: document.getElementById('inferenceConfigSelect'),
            generateButton: document.getElementById('generateFingerprintButton'),
            saveButton: document.getElementById('saveFingerprintButton'),
            fingerprint: document.getElementById('currentFingerprint'),
            errorDisplay: document.getElementById('fingerprintError'),
            nameInput: document.getElementById('fingerprintName')
        };
        
        this.currentFingerprint = null;
    }

    /**
     * Initialize the fingerprint manager
     */
    async initialize() { // Make initialize async to await fetches
        // Check if we're on the fingerprint page
        if (!this.elements.fingerprintList) {
            return; // Not on fingerprint page
        }
        
        // Bind event handlers
        if (this.elements.generateButton) {
            this.elements.generateButton.addEventListener('click', this.generateFingerprint.bind(this));
        }
        
        if (this.elements.saveButton) {
            this.elements.saveButton.addEventListener('click', this.saveFingerprint.bind(this));
        }
        
        // Fetch initial data directly
        try {
            const [fingerprintsData, configsData] = await Promise.all([
                api.fetchCalibratedFingerprints(),
                api.fetchInferenceConfigurations()
            ]);
            
            this.updateFingerprintList(fingerprintsData.calibrated_fingerprints || []);
            this.updateConfigSelect(configsData.inference_configurations || []);
            
        } catch (error) {
            console.error("Error initializing Fingerprint Manager:", error);
            if(this.elements.errorDisplay) {
                this.elements.errorDisplay.textContent = `Error loading initial data: ${error.message}`;
                this.elements.errorDisplay.style.display = 'block';
            }
        }
    }

    /**
     * Update the fingerprint list display
     * @param {Array} fingerprints - Array of calibrated fingerprints
     */
    updateFingerprintList(fingerprints) {
        if (!this.elements.fingerprintList) {
            return;
        }
        
        // Clear the list
        this.elements.fingerprintList.innerHTML = '';
        
        if (!fingerprints || fingerprints.length === 0) {
            const noDataItem = document.createElement('li');
            noDataItem.className = 'list-group-item';
            noDataItem.textContent = 'No calibrated fingerprints available.';
            this.elements.fingerprintList.appendChild(noDataItem);
            return;
        }
        
        // Sort fingerprints by type
        const sortedFingerprints = [...fingerprints].sort((a, b) => 
            a.type.localeCompare(b.type));
        
        // Create list items
        sortedFingerprints.forEach(fingerprint => {
            const item = document.createElement('li');
            item.className = 'list-group-item d-flex justify-content-between align-items-center';
            
            // Create type span
            const typeSpan = document.createElement('span');
            typeSpan.textContent = fingerprint.type;
            item.appendChild(typeSpan);
            
            // Create timestamp display, handling potential invalid dates
            const dateSpan = document.createElement('small');
            dateSpan.className = 'text-muted';
            const timestamp = new Date(fingerprint.created_at);
            if (!isNaN(timestamp.getTime())) { // Check if the date is valid
                dateSpan.textContent = `Created: ${timestamp.toLocaleString()}`;
            } else {
                dateSpan.textContent = `Created: (Invalid Date - ${fingerprint.created_at})`; // Show original string if invalid
                console.warn(`Failed to parse fingerprint created_at: ${fingerprint.created_at}`);
            }
            item.appendChild(dateSpan);
            
            // Create actions
            const actions = document.createElement('div');
            
            // View button
            const viewBtn = document.createElement('button');
            viewBtn.className = 'btn btn-sm btn-info me-2';
            viewBtn.textContent = 'View';
            viewBtn.addEventListener('click', () => this.viewFingerprint(fingerprint));
            actions.appendChild(viewBtn);
            
            // Update button
            const updateBtn = document.createElement('button');
            updateBtn.className = 'btn btn-sm btn-warning me-2';
            updateBtn.textContent = 'Update';
            updateBtn.addEventListener('click', () => this.prepareUpdateFingerprint(fingerprint));
            actions.appendChild(updateBtn);
            
            item.appendChild(actions);
            this.elements.fingerprintList.appendChild(item);
        });
    }

    /**
     * Update the inference config select dropdown
     * @param {Array} configs - Array of inference configurations
     */
    updateConfigSelect(configs) {
        if (!this.elements.configSelect) {
            return;
        }
        
        // Clear the select
        this.elements.configSelect.innerHTML = '';
        
        if (!configs || configs.length === 0) {
            const noOption = document.createElement('option');
            noOption.textContent = 'No inference configurations available';
            noOption.disabled = true;
            noOption.selected = true;
            this.elements.configSelect.appendChild(noOption);
            return;
        }
        
        // Sort configs by name
        const sortedConfigs = [...configs].sort((a, b) => 
            a.name.localeCompare(b.name));
        
        // Create options
        sortedConfigs.forEach(config => {
            const option = document.createElement('option');
            option.value = config.name;
            option.textContent = `${config.name} (${config.inference_type})`;
            this.elements.configSelect.appendChild(option);
        });
    }

    /**
     * Display a fingerprint
     * @param {Object} fingerprint - Fingerprint to display
     */
    viewFingerprint(fingerprint) {
        if (!this.elements.fingerprint) {
            return;
        }
        
        try {
            // Format the fingerprint for display
            const formattedJson = JSON.stringify(fingerprint, null, 2);
            this.elements.fingerprint.textContent = formattedJson;
            
            // Store the current fingerprint
            this.currentFingerprint = fingerprint;
            
            // Hide error if any
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.style.display = 'none';
            }
        } catch (error) {
            console.error('Error viewing fingerprint:', error);
            
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.textContent = `Error viewing fingerprint: ${error.message}`;
                this.elements.errorDisplay.style.display = 'block';
            }
        }
    }

    /**
     * Prepare to update an existing fingerprint
     * @param {Object} fingerprint - Fingerprint to update
     */
    prepareUpdateFingerprint(fingerprint) {
        // Set the name input to the fingerprint type
        if (this.elements.nameInput) {
            this.elements.nameInput.value = fingerprint.type;
        }
        
        // Set the config select to the fingerprint's inference config
        if (this.elements.configSelect) {
            const options = this.elements.configSelect.options;
            for (let i = 0; i < options.length; i++) {
                if (options[i].value === fingerprint.inference_ref) {
                    this.elements.configSelect.selectedIndex = i;
                    break;
                }
            }
        }
        
        // Show the current fingerprint
        this.viewFingerprint(fingerprint);
    }

    /**
     * Generate a new fingerprint based on selected config
     */
    async generateFingerprint() {
        if (!this.elements.configSelect || !this.elements.nameInput || !this.elements.fingerprint) {
            return;
        }
        
        const configName = this.elements.configSelect.value;
        const fingerprintName = this.elements.nameInput.value.trim();
        
        if (!configName) {
            alert('Please select an inference configuration.');
            return;
        }
        
        if (!fingerprintName) {
            alert('Please enter a name for the fingerprint.');
            return;
        }
        
        try {
            // Show loading indicator
            this.elements.fingerprint.textContent = 'Generating fingerprint...';
            
            // Generate current timestamp for the "now" window
            const endedAt = new Date().toISOString();
            
            // Call API to generate fingerprint
            const result = await api.generateFingerprint(fingerprintName, configName, endedAt);
            
            if (result.current_fingerprint) {
                // Display the generated fingerprint
                this.viewFingerprint(result.current_fingerprint);
                
                // Enable save button
                if (this.elements.saveButton) {
                    this.elements.saveButton.disabled = false;
                }
                
                console.log('Fingerprint generated:', result.current_fingerprint);
            } else {
                throw new Error('No fingerprint data returned from server');
            }
        } catch (error) {
            console.error('Error generating fingerprint:', error);
            
            this.elements.fingerprint.textContent = `Error generating fingerprint: ${error.message}`;
            
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.textContent = `Error generating fingerprint: ${error.message}`;
                this.elements.errorDisplay.style.display = 'block';
            }
            
            // Disable save button
            if (this.elements.saveButton) {
                this.elements.saveButton.disabled = true;
            }
        }
    }

    /**
     * Save the current fingerprint as a calibrated fingerprint
     */
    async saveFingerprint() {
        if (!this.currentFingerprint) {
            alert('No fingerprint to save. Generate one first.');
            return;
        }
        
        try {
            // Call API to save fingerprint
            const result = await api.saveFingerprint(this.currentFingerprint);
            
            console.log('Fingerprint saved:', result);
            
            // Show success message
            alert(`Fingerprint ${result.status} successfully.`);
            
            // Refresh calibrated fingerprints directly after saving
            const fingerprintsData = await api.fetchCalibratedFingerprints();
            this.updateFingerprintList(fingerprintsData.calibrated_fingerprints || []);

        } catch (error) {
            console.error('Error saving fingerprint:', error);
            
            if (this.elements.errorDisplay) {
                this.elements.errorDisplay.textContent = `Error saving fingerprint: ${error.message}`;
                this.elements.errorDisplay.style.display = 'block';
            }
            
            alert(`Error saving fingerprint: ${error.message}`);
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    const fingerprintManager = new FingerprintManager();
    fingerprintManager.initialize();
});

export default FingerprintManager; 