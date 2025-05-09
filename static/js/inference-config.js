/**
 * inference-config.js - Handles inference configuration management
 * Allows viewing, creating, editing, and running inference configurations
 */

import api from './api.js';
import state from './state.js';

class InferenceConfigManager {
    constructor() {
        // Initialize elements object, but don't query DOM yet
        this.elements = {}; 
        
        this.currentConfig = null;
        this.isEditing = false;
    }

    /**
     * Initialize the inference config manager
     */
    async initialize() {
        // Query DOM elements needed for this page HERE, after DOM is loaded
        this.elements = {
            configList: document.getElementById('configList'),
            configForm: document.getElementById('configForm'),
            saveConfigButton: document.getElementById('saveConfigButton'),
            runConfigButton: document.getElementById('runInferenceButton'),
            configNameInput: document.getElementById('configName'),
            inferenceTypeInput: document.getElementById('configType'),
            windowDurationInput: document.getElementById('configWindowDuration'),
            dataPointTypesInput: document.getElementById('configDataPointTypes'),
            includedPathsInput: document.getElementById('configIncludedPaths'),
            sensorWeightsInput: document.getElementById('configSensorWeights'),
            confidenceThresholdInput: document.getElementById('configConfidenceThreshold'),
            significantDifferenceInput: document.getElementById('configSignificantDifference'),
            minStdDevRssiInput: document.getElementById('configMinStdDevRssi'),
            baseMissingPenaltyInput: document.getElementById('configBaseMissingPenalty'),
            minStdDevPressureInput: document.getElementById('configMinStdDevPressure'),
            confidenceScalingInput: document.getElementById('configConfidenceScaling'),
            configError: document.getElementById('configError'),
            selectPrompt: document.getElementById('selectPrompt'),
            newButton: document.getElementById('newConfigButton'),
            statusDisplay: document.getElementById('configStatus'),
            isNewInput: document.getElementById('configIsNew'),
            fingerprintsTabPane: document.getElementById('fingerprints-tab-pane'),
            associatedFingerprintsList: document.getElementById('associatedFingerprintsList'),
            runsTabPane: document.getElementById('runs-tab-pane'),
            inferenceRunHistory: document.getElementById('inferenceRunHistory'),
        };
        console.log("InferenceConfigManager: initialize() called, elements queried."); 

        if (!this.elements.configList) {
            console.error("InferenceConfigManager: Element with ID 'inferenceConfigList' not found AFTER querying in initialize(). Exiting.");
            return; // Still check if essential element is missing
        }
        
        // Bind event handlers
        if (this.elements.configForm) {
            this.elements.configForm.addEventListener('submit', this.handleFormSubmit.bind(this));
        }
        
        if (this.elements.runConfigButton) {
            this.elements.runConfigButton.addEventListener('click', this.runInference.bind(this));
        }
        
        if (this.elements.newButton) {
            this.elements.newButton.addEventListener('click', this.handleNewConfigClick.bind(this));
        }
        
        // Subscribe to state changes
        state.subscribe('inferenceConfigs', this.updateConfigList.bind(this));
        
        // Initialize state
        state.initialize();

        // Register listener for inference completion events
        api.onWebSocketEvent('inference_complete', this.handleInferenceCompletion.bind(this));
    }

    /**
     * Update the configuration list display
     * @param {Array} configs - Array of inference configurations
     */
    updateConfigList(configs) {
        if (!this.elements.configList) {
            return;
        }
        
        // Clear the list
        this.elements.configList.innerHTML = '';
        
        if (!configs || configs.length === 0) {
            const noDataItem = document.createElement('li');
            noDataItem.className = 'list-group-item';
            noDataItem.textContent = 'No inference configurations available.';
            this.elements.configList.appendChild(noDataItem);
            return;
        }
        
        // Sort configs by name
        const sortedConfigs = [...configs].sort((a, b) => 
            a.name.localeCompare(b.name));
        
        // Create list items
        sortedConfigs.forEach(config => {
            const item = document.createElement('li');
            item.className = 'list-group-item d-flex justify-content-between align-items-center';
            
            // Left side: Config name and type
            const infoDiv = document.createElement('div');
            
            const nameSpan = document.createElement('h5');
            nameSpan.className = 'mb-1';
            nameSpan.textContent = config.name;
            infoDiv.appendChild(nameSpan);
            
            const typeSpan = document.createElement('small');
            typeSpan.className = 'text-muted';
            typeSpan.textContent = `Type: ${config.inference_type}`;
            infoDiv.appendChild(typeSpan);
            
            item.appendChild(infoDiv);
            
            // Right side: Actions
            const actions = document.createElement('div');
            
            // View button
            const viewBtn = document.createElement('button');
            viewBtn.className = 'btn btn-sm btn-info me-2';
            viewBtn.textContent = 'View';
            viewBtn.addEventListener('click', () => this.viewConfig(config));
            actions.appendChild(viewBtn);
            
            // Edit button
            const editBtn = document.createElement('button');
            editBtn.className = 'btn btn-sm btn-warning me-2';
            editBtn.textContent = 'Edit';
            editBtn.addEventListener('click', () => this.editConfig(config));
            actions.appendChild(editBtn);
            
            // Run button
            const runBtn = document.createElement('button');
            runBtn.className = 'btn btn-sm btn-success';
            runBtn.textContent = 'Run';
            runBtn.addEventListener('click', () => this.runInference(config.name));
            actions.appendChild(runBtn);
            
            item.appendChild(actions);
            this.elements.configList.appendChild(item);
        });
    }

    /**
     * Display a configuration in the editor
     * @param {Object} config - Configuration to display
     */
    viewConfig(config) {
        console.log("viewConfig called for:", config?.name);
        if (!this.elements.configForm) {
            console.error("Config form element not found!");
            return;
        }
        
        try {
            // Make form visible and hide prompt
            this.elements.configForm.style.display = 'block';
            if (this.elements.selectPrompt) {
                this.elements.selectPrompt.style.display = 'none';
            }

            // Populate the form fields
            this.populateFormFields(config);

            // Format the config for display (Optional: Maybe remove raw JSON display if form is sufficient)
            // const formattedJson = JSON.stringify(config, null, 2);
            // if (this.elements.configEditor) this.elements.configEditor.textContent = formattedJson;
            
            // Store the current config
            this.currentConfig = config;
            
            // Trigger loading data for other tabs
            this.fetchAndRenderFingerprints(config.name);
            this.fetchAndRenderRunHistory(config.name);
            
            // Update form fields if they exist
            this.populateFormFields(config);
            
            // Hide error if any
            if (this.elements.configError) {
                this.elements.configError.style.display = 'none';
            }
            
            // We're viewing, not editing
            this.isEditing = false;
            
            // Update buttons
            if (this.elements.saveConfigButton) {
                this.elements.saveConfigButton.textContent = 'Save as New';
            }
            
            if (this.elements.configNameInput) {
                this.elements.configNameInput.disabled = false;
            }
        } catch (error) {
            console.error('Error viewing config:', error);
            
            if (this.elements.configError) {
                this.elements.configError.textContent = `Error viewing config: ${error.message}`;
                this.elements.configError.style.display = 'block';
            }
        }
    }

    /**
     * Fetch and render associated fingerprints for a given config name
     * @param {string} configName - Name of the inference configuration
     */
    async fetchAndRenderFingerprints(configName) {
        if (!this.elements.associatedFingerprintsList) return;
        this.elements.associatedFingerprintsList.innerHTML = '<span class="list-group-item">Loading fingerprints...</span>';
        console.log(`Fetching fingerprints associated with: ${configName}`);
        try {
            const allFingerprintsData = await api.fetchCalibratedFingerprints();
            const allFingerprints = allFingerprintsData.calibrated_fingerprints || [];
            
            const associatedFps = allFingerprints.filter(fp => fp.inference_ref === configName);

            this.elements.associatedFingerprintsList.innerHTML = ''; // Clear loading

            if (associatedFps.length === 0) {
                this.elements.associatedFingerprintsList.innerHTML = '<span class="list-group-item">No associated fingerprints found.</span>';
                return;
            }

            const fragment = document.createDocumentFragment();
            associatedFps.forEach(fp => {
                const item = document.createElement('div');
                item.className = 'list-group-item';
                item.textContent = `Type: ${fp.type}, Created: ${new Date(fp.created_at).toLocaleString()}`;
                // TODO: Add more details or actions if needed
                fragment.appendChild(item);
            });
            this.elements.associatedFingerprintsList.appendChild(fragment);

        } catch (error) {
            console.error(`Error fetching/rendering fingerprints for ${configName}:`, error);
            this.elements.associatedFingerprintsList.innerHTML = `<span class="list-group-item text-danger">Error loading fingerprints: ${error.message}</span>`;
        }
    }

    /**
     * Fetch and render run history for a given config name
     * @param {string} configName - Name of the inference configuration
     */
    async fetchAndRenderRunHistory(configName) {
        if (!this.elements.inferenceRunHistory) return;
        this.elements.inferenceRunHistory.innerHTML = '<span class="list-group-item">Loading run history...</span>';
        console.log(`Fetching run history for: ${configName}`);
        try {
            // Use the newly added API method
            const historyData = await api.fetchInferenceHistory(configName);
            const runs = historyData.runs || [];

            this.elements.inferenceRunHistory.innerHTML = ''; // Clear loading

            if (runs.length === 0) {
                 this.elements.inferenceRunHistory.innerHTML = '<span class="list-group-item">No run history found for this configuration.</span>';
                 return;
            }

            const fragment = document.createDocumentFragment();
            // Display newest first
            runs.forEach(run => {
                const item = document.createElement('div');
                item.className = 'list-group-item p-2 border-bottom'; // Add some padding/border
                const runTime = new Date(run.created_at).toLocaleString();
                const prediction = run.overall_prediction?.value || 'N/A';
                const confidence = run.overall_prediction?.confidence !== undefined ? (run.overall_prediction.confidence * 100).toFixed(1) + '%' : 'N/A';
                
                item.innerHTML = `
                    <strong>${runTime}</strong><br>
                    Prediction: <strong>${prediction}</strong> (Confidence: ${confidence})
                    <small class="d-block text-muted">Details: ${run.comparisons?.length || 0} comparisons</small>
                `;
                // TODO: Maybe add a button/link to view full run details later
                fragment.appendChild(item);
            });
            this.elements.inferenceRunHistory.appendChild(fragment);

        } catch (error) {
             console.error(`Error fetching/rendering run history for ${configName}:`, error);
             this.elements.inferenceRunHistory.innerHTML = `<span class="list-group-item text-danger">Error loading run history: ${error.message}</span>`;
        }
    }

    /**
     * Edit a configuration
     * @param {Object} config - Configuration to edit
     */
    editConfig(config) {
        console.log("editConfig called for:", config?.name);
        // First view the config
        this.viewConfig(config);
        
        // Then set editing mode
        this.isEditing = true;
        
        // Update button text
        if (this.elements.saveConfigButton) {
            this.elements.saveConfigButton.textContent = 'Update Configuration';
        }
        
        // Disable name input when editing
        if (this.elements.configNameInput) {
            this.elements.configNameInput.disabled = true;
        }
    }

    /**
     * Populate form fields with config values
     * @param {Object} config - Configuration with values to populate
     */
    populateFormFields(config) {
        if (!config) return;
        
        const elements = this.elements;
        
        // Set simple text fields
        if (elements.configNameInput) elements.configNameInput.value = config.name || '';
        if (elements.inferenceTypeInput) elements.inferenceTypeInput.value = config.inference_type || '';
        if (elements.windowDurationInput) elements.windowDurationInput.value = config.window_duration_seconds || '';
        if (elements.confidenceThresholdInput) elements.confidenceThresholdInput.value = config.confidence_threshold || '';
        
        // Set array fields as newline-separated strings
        if (elements.dataPointTypesInput && config.data_point_types) {
            elements.dataPointTypesInput.value = config.data_point_types.join('\n');
        }
        
        if (elements.includedPathsInput && config.included_paths) {
            elements.includedPathsInput.value = config.included_paths.join('\n');
        }
        
        // Set weights as JSON string
        if (elements.sensorWeightsInput && config.sensor_weights) {
            elements.sensorWeightsInput.value = JSON.stringify(config.sensor_weights, null, 2);
        }
    }

    /**
     * Collect values from form fields into a config object
     * @returns {Object} - Config object from form values
     */
    collectFormValues() {
        const elements = this.elements;
        const config = {};
        
        // Get simple text fields
        config.name = elements.configNameInput ? elements.configNameInput.value.trim() : '';
        config.inference_type = elements.inferenceTypeInput ? elements.inferenceTypeInput.value.trim() : '';
        config.window_duration_seconds = elements.windowDurationInput ? 
            parseFloat(elements.windowDurationInput.value) : 0;
        config.confidence_threshold = elements.confidenceThresholdInput ? 
            parseFloat(elements.confidenceThresholdInput.value) : 0;
        // Add collection for significant_difference
        config.significant_difference = elements.significantDifferenceInput ? 
            parseFloat(elements.significantDifferenceInput.value) : null; // Or a default like 1.5

        // Add collection for other optional parameters
        config.min_std_dev_rssi = elements.minStdDevRssiInput && elements.minStdDevRssiInput.value ? 
            parseFloat(elements.minStdDevRssiInput.value) : null; // Use null if empty
        config.base_missing_network_penalty_diff = elements.baseMissingPenaltyInput && elements.baseMissingPenaltyInput.value ?
            parseFloat(elements.baseMissingPenaltyInput.value) : null; // Use null if empty
        config.min_std_dev_pressure = elements.minStdDevPressureInput && elements.minStdDevPressureInput.value ?
            parseFloat(elements.minStdDevPressureInput.value) : null; // Use null if empty
        config.confidence_scaling_factor = elements.confidenceScalingInput && elements.confidenceScalingInput.value ?
            parseFloat(elements.confidenceScalingInput.value) : null; // Use null if empty
        
        // Get array fields from newline-separated strings
        if (elements.dataPointTypesInput) {
            config.data_point_types = elements.dataPointTypesInput.value
                .split('\n')
                .filter(line => line.trim().length > 0);
        }
        
        if (elements.includedPathsInput) {
            config.included_paths = elements.includedPathsInput.value
                .split('\n')
                .filter(line => line.trim().length > 0);
        }
        
        // Parse weights JSON
        if (elements.sensorWeightsInput) {
            try {
                config.sensor_weights = JSON.parse(elements.sensorWeightsInput.value);
            } catch (error) {
                console.error('Error parsing sensor weights JSON:', error);
                throw new Error('Invalid JSON in sensor weights field.');
            }
        }
        
        // Add timestamps
        config.created_at = config.created_at || new Date().toISOString();
        config.updated_at = new Date().toISOString();
        
        return config;
    }

    /**
     * Handle form submission
     * @param {Event} event - Form submit event
     */
    async handleFormSubmit(event) {
        event.preventDefault();
        
        try {
            // Collect form values
            const config = this.collectFormValues();
            
            // Validate config
            if (!config.name) {
                throw new Error('Configuration name is required.');
            }
            
            if (!config.inference_type) {
                throw new Error('Inference type is required.');
            }
            
            if (!config.data_point_types || config.data_point_types.length === 0) {
                throw new Error('At least one data point type is required.');
            }
            
            if (!config.included_paths || config.included_paths.length === 0) {
                throw new Error('At least one included path is required.');
            }
            
            if (!config.sensor_weights || Object.keys(config.sensor_weights).length === 0) {
                throw new Error('Sensor weights are required.');
            }
            
            // Check if editing or creating new
            let result;
            if (this.isEditing) {
                // Update existing config
                result = await api.updateInferenceConfiguration(config.name, config);
                console.log('Config updated:', result);
                // alert(`Configuration "${config.name}" updated successfully.`); // Replaced by status display
            } else {
                // Save as new config
                result = await api.saveInferenceConfiguration(config);
                console.log('Config saved:', result);
                // alert(`Configuration "${config.name}" saved successfully.`); // Replaced by status display
            }

            // Display success status
            this.showStatusMessage(`Configuration '${config.name}' ${this.isEditing ? 'updated' : 'saved'} successfully.`, 'success');
            
            // Refresh inference configs from the state (which should trigger updateConfigList)
            state.refreshInferenceConfigs(); // Assuming state.js has this method
        } catch (error) {
            console.error('Error saving config:', error);
            
            if (this.elements.configError) {
                this.elements.configError.textContent = `Error: ${error.message}`;
                this.elements.configError.style.display = 'block';
            }
            
            // alert(`Error saving configuration: ${error.message}`); // Replaced by status display
        }
    }

    /**
     * Display a status message below the form
     * @param {string} message - Message to display
     * @param {'success'|'error'} type - Type of message
     */
    showStatusMessage(message, type = 'error') {
        if (this.elements.statusDisplay && type === 'success') {
            this.elements.statusDisplay.textContent = message;
            this.elements.statusDisplay.className = 'text-success mt-2';
            this.elements.statusDisplay.style.display = 'block';
            if (this.elements.configError) this.elements.configError.style.display = 'none';
            // Hide success message after a few seconds
            setTimeout(() => {
                 if (this.elements.statusDisplay) this.elements.statusDisplay.style.display = 'none';
            }, 5000);
        } else if (this.elements.configError && type === 'error') {
            this.elements.configError.textContent = message;
            this.elements.configError.className = 'text-danger mt-2';
            this.elements.configError.style.display = 'block';
             if (this.elements.statusDisplay) this.elements.statusDisplay.style.display = 'none';
        }
    }

    /**
     * Reset the form for a new configuration
     */
    resetForm() {
        // Clear form fields
        const elements = this.elements;
        
        if (elements.configNameInput) elements.configNameInput.value = '';
        if (elements.inferenceTypeInput) elements.inferenceTypeInput.value = '';
        if (elements.windowDurationInput) elements.windowDurationInput.value = '';
        if (elements.dataPointTypesInput) elements.dataPointTypesInput.value = '';
        if (elements.includedPathsInput) elements.includedPathsInput.value = '';
        if (elements.sensorWeightsInput) elements.sensorWeightsInput.value = '{}';
        if (elements.confidenceThresholdInput) elements.confidenceThresholdInput.value = '';
        
        // Clear config editor
        if (elements.configEditor) elements.configEditor.textContent = '';
        
        // Reset state
        this.currentConfig = null;
        this.isEditing = false;
        
        // Reset buttons
        if (elements.saveConfigButton) elements.saveConfigButton.textContent = 'Save Configuration';
        if (elements.configNameInput) elements.configNameInput.disabled = false;
        
        // Hide errors
        if (elements.configError) elements.configError.style.display = 'none';
    }

    /**
     * Handle click on the 'New Configuration' button
     */
    handleNewConfigClick() {
        console.log("handleNewConfigClick called");
        this.resetForm(); // Clear fields and set isEditing = false

        // Ensure form is visible and prompt is hidden
        if (this.elements.configForm) {
            this.elements.configForm.style.display = 'block';
        }
        if (this.elements.selectPrompt) {
            this.elements.selectPrompt.style.display = 'none';
        }
        
        // Explicitly set button text and name field state (although resetForm should handle it)
        if (this.elements.saveConfigButton) {
            this.elements.saveConfigButton.textContent = 'Save New Configuration';
        }
        if (this.elements.configNameInput) {
            this.elements.configNameInput.disabled = false;
        }

        // Set hidden input to indicate new (optional, if using it)
        if (this.elements.isNewInput) {
            this.elements.isNewInput.value = 'true';
        }
    }

    /**
     * Run inference with a specific configuration
     * @param {string} configName - Name of the config to run
     */
    async runInference(configName) {
        // If called from a button click with a config object
        if (typeof configName === 'object') {
            configName = this.currentConfig ? this.currentConfig.name : null;
        }
        
        if (!configName) {
            alert('No configuration selected to run.');
            return;
        }
        
        try {
            // Call API to trigger background inference run
            const result = await api.runInference(configName);
            console.log('Inference run accepted:', result);
            
            // Show status message indicating acceptance
            this.showStatusMessage(`Inference run accepted for configuration "${configName}".`, 'success');
            
            // Clear previous errors
            if(this.elements.configError) this.elements.configError.style.display = 'none';
            
        } catch (error) {
            console.error('Error triggering inference run:', error);
            this.showStatusMessage(`Error triggering inference: ${error.message}`, 'error');
        }
    }

    /**
     * Handle incoming inference completion WebSocket messages
     * @param {object} eventData - Data from the WebSocket message
     */
    handleInferenceCompletion(eventData) {
        console.log("Received inference_complete event:", eventData);
        const { config_name, success, error } = eventData;

        // Log values being compared
        // console.log(`Comparing event config_name: '${config_name}' with this.currentConfig?.name: '${this.currentConfig?.name}'`);

        // Optional: Show a persistent status update or notification?
        // For now, just log it and refresh if the completed run matches the current view

        if (this.currentConfig && this.currentConfig.name === config_name) {
            console.log(`Inference completed for currently viewed config '${config_name}'. Refreshing runs tab.`);
            this.fetchAndRenderRunHistory(config_name);
            
            // Optionally show a success/error message based on the event data
            if (!success) {
                this.showStatusMessage(`Background inference run for '${config_name}' failed: ${error || 'Unknown error'}`, 'error');
            }
        } else {
             console.log(`Inference completed for config '${config_name}', which is not currently viewed.`);
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    console.log("InferenceConfigManager: DOMContentLoaded event fired.");
    const configManager = new InferenceConfigManager();
    configManager.initialize();
});

export default InferenceConfigManager; 