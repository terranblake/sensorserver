/**
 * device-page.js - Handles device list, status updates, and live data streaming.
 */

import api from './api.js';

class DevicePage {
    constructor() {
        this.elements = {
            deviceListContainer: document.getElementById('deviceList'),
            dataStreamOutput: document.getElementById('dataStreamOutput'),
            streamingStatus: document.getElementById('streamingStatus'),
        };
        this.devices = {}; // Stores device IP -> { element: HTMLElement, status: string, name: string, model: string, lastLog: string, lastUpdate: Date }
        this.selectedDeviceIp = null; // Use IP for selection
        this.maxStreamLines = 200; // Max lines to keep in the stream output
    }

    async initialize() {
        if (!this.elements.deviceListContainer || !this.elements.dataStreamOutput) {
            console.warn('Required elements not found on this page.');
            return;
        }

        // Fetch initial device list
        await this.fetchAndRenderDeviceList();

        // Register WebSocket callbacks
        api.onWebSocketEvent('open', () => console.log('WS Connection Open - Device Page'));
        api.onWebSocketEvent('close', () => console.log('WS Connection Closed - Device Page'));
        api.onWebSocketEvent('error', (err) => console.error('WS Error - Device Page:', err));
        api.onWebSocketEvent('device_connection', this.handleDeviceStatusUpdate.bind(this));
        api.onWebSocketEvent('sensor_data', this.handleSensorDataUpdate.bind(this));

        // Ensure WebSocket connection attempt is initiated
        api.connectWebSocket(); // Explicitly call connect here just in case base.html script order changes
        console.log('Device page initialized and WebSocket connection initiated.');
    }

    async fetchAndRenderDeviceList() {
        try {
            const response = await fetch(`${api.baseUrl}/api/devices`); 
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP error ${response.status}: ${errorText}`);
            }
            const data = await response.json();
            const devicesData = data.devices || []; // Array of {ip, name, model, status, last_log}
            
            console.log('Fetched devices data:', devicesData);
            this.elements.deviceListContainer.innerHTML = ''; 
            const fragment = document.createDocumentFragment(); 
            this.devices = {}; // Reset internal state

            if (devicesData.length === 0) {
                 this.elements.deviceListContainer.innerHTML = '<span class="list-group-item">No devices found. Connect a device or check logs.</span>';
                 return;
            }
            
            // Sort devices by IP
            devicesData.sort((a, b) => a.ip.localeCompare(b.ip));

            devicesData.forEach(deviceInfo => {
                const ip = deviceInfo.ip;
                const listItem = this.createDeviceListItem(deviceInfo);
                fragment.appendChild(listItem);
                // Store by IP address
                this.devices[ip] = { 
                    element: listItem, 
                    status: deviceInfo.status || 'unknown', // Use status from API if available
                    statusElement: listItem.querySelector('.device-status-icon'),
                    lastUpdateElement: listItem.querySelector('.device-last-update'),
                    name: deviceInfo.name,
                    model: deviceInfo.model,
                    lastLog: deviceInfo.last_log
                };
                // Set initial status indicator based on fetched data
                this.updateDeviceStatusIndicator(ip, this.devices[ip].status);
            });
            this.elements.deviceListContainer.appendChild(fragment);

        } catch (error) {
            console.error('Error fetching device list:', error);
            this.elements.deviceListContainer.innerHTML = '<span class="list-group-item text-danger">Error loading device list.</span>';
        }
    }

    createDeviceListItem(deviceInfo) {
        const { ip, name, model, last_log } = deviceInfo;
        const displayName = name ? `${name} (${model || 'Unknown Model'})` : (model || 'Unknown Device');
        const displayIp = ip;
        const lastLogTime = last_log ? new Date(last_log).toLocaleString() : 'Never';
        
        const listItem = document.createElement('a');
        listItem.href = '#';
        listItem.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
        listItem.dataset.deviceId = ip; // Store IP in dataset

        const infoDiv = document.createElement('div');
        const deviceNameSpan = document.createElement('span');
        deviceNameSpan.className = 'fw-bold d-block'; // Make name bold and block
        deviceNameSpan.textContent = displayName;
        const deviceIpSpan = document.createElement('small');
        deviceIpSpan.className = 'text-muted d-block';
        deviceIpSpan.textContent = displayIp;
        const lastUpdateSpan = document.createElement('small');
        lastUpdateSpan.className = 'device-last-update d-block text-muted';
        lastUpdateSpan.textContent = `Last log: ${lastLogTime}`; // Show last log time initially
        infoDiv.appendChild(deviceNameSpan);
        infoDiv.appendChild(deviceIpSpan);
        infoDiv.appendChild(lastUpdateSpan);
        
        const statusIcon = document.createElement('i');
        statusIcon.className = 'fas fa-circle device-status-icon text-secondary'; 
        statusIcon.title = 'Status: Unknown';

        listItem.appendChild(infoDiv);
        listItem.appendChild(statusIcon);

        listItem.addEventListener('click', (e) => {
            e.preventDefault();
            this.selectDevice(ip); // Select using IP
        });
        return listItem;
    }

    selectDevice(deviceIp) {
        if (this.selectedDeviceIp === deviceIp) return; // Do nothing if already selected

        this.selectedDeviceIp = deviceIp;
        console.log(`Device selected: ${deviceIp}`);
        this.elements.dataStreamOutput.innerHTML = ''; // Clear previous stream
        // Display IP prominently, add name/model if known
        const deviceEntry = this.devices[deviceIp];
        const displayName = deviceEntry?.name ? `${deviceEntry.name} (${deviceEntry.model || 'Unknown Model'})` : (deviceEntry?.model || 'Unknown Device');
        this.elements.streamingStatus.textContent = `Streaming live data for ${deviceIp} (${displayName})...`;

        // Update active state in the list
        Object.entries(this.devices).forEach(([ip, dev]) => {
            dev.element.classList.toggle('active', ip === deviceIp);
        });
    }

    updateDeviceStatusIndicator(deviceIp, status, message = '') {
        const device = this.devices[deviceIp]; // Find device by IP
        if (!device || !device.statusElement) {
            console.warn(`Attempted to update status for unknown device element: ${deviceIp}`);
            return;
        }

        let iconBaseClass = 'fas fa-circle device-status-icon';
        let iconColorClass = 'text-secondary'; // Default: unknown
        let title = 'Status: Unknown';

        switch (status) {
            case 'connected':
                iconColorClass = 'text-success';
                title = 'Status: Connected';
                break;
            case 'disconnected':
                iconColorClass = 'text-warning';
                title = 'Status: Disconnected';
                break;
            case 'error':
                iconColorClass = 'text-danger';
                title = `Status: Error (${message || 'Unknown'})`;
                break;
        }
        device.statusElement.className = `${iconBaseClass} ${iconColorClass}`;
        device.statusElement.title = title;
        device.status = status; // Update internal state
        console.log(`Updated status for ${deviceIp}: ${status}`);
    }

    handleDeviceStatusUpdate(statusUpdate) {
        console.log('Received device_connection update:', statusUpdate);
        const deviceIp = statusUpdate.device; // Expecting IP address here now
        
        if (!deviceIp) {
            console.warn('device_connection message missing device identifier (IP).');
            return;
        }
        
        // If the device IP isn't in our list, add it.
        if (!this.devices[deviceIp]) {
             console.log(`Received status for unknown device IP: ${deviceIp}. Adding to list.`);
             // We don't have name/model here, just IP
             const newDeviceData = { ip: deviceIp, name: null, model: null, last_log: null };
             const listItem = this.createDeviceListItem(newDeviceData);
             this.elements.deviceListContainer.insertBefore(listItem, this.elements.deviceListContainer.firstChild);
             this.devices[deviceIp] = { 
                 element: listItem, 
                 status: 'unknown',
                 statusElement: listItem.querySelector('.device-status-icon'),
                 lastUpdateElement: listItem.querySelector('.device-last-update'),
                 name: null, model: null, lastLog: null // Initialize with nulls
             };
        } // Note: Name/Model/LastLog will be updated if list is re-fetched later
        
        // Update using the IP address
        this.updateDeviceStatusIndicator(deviceIp, statusUpdate.status, statusUpdate.message);
    }

    handleSensorDataUpdate(sensorUpdate) {
        const deviceIp = sensorUpdate.device; // Expecting IP address here now
        const data = sensorUpdate.data;
        
        if (!deviceIp || !data) {
            console.warn('Received sensor_data message missing device IP or data.', sensorUpdate);
            return;
        }
        
        // Update last seen timestamp for the specific device (using IP)
        const deviceState = this.devices[deviceIp];
        if (deviceState && deviceState.lastUpdateElement) {
            const now = new Date();
            deviceState.lastUpdate = now;
            deviceState.lastUpdateElement.textContent = `Last update: ${now.toLocaleTimeString()}`; // Update based on WS message arrival
            if (deviceState.status !== 'connected') {
                 this.updateDeviceStatusIndicator(deviceIp, 'connected');
            }
        } else {
            // Device might not be in list if WS connects before initial fetch completes
            console.warn(`Received sensor data for unknown device IP: ${deviceIp}. Attempting to add.`);
             if (!this.devices[deviceIp]) {
                  const newDeviceData = { ip: deviceIp, name: null, model: null, last_log: null };
                  const listItem = this.createDeviceListItem(newDeviceData);
                  this.elements.deviceListContainer.insertBefore(listItem, this.elements.deviceListContainer.firstChild);
                  this.devices[deviceIp] = { 
                      element: listItem, 
                      status: 'connected', 
                      statusElement: listItem.querySelector('.device-status-icon'),
                      lastUpdateElement: listItem.querySelector('.device-last-update'),
                      name: null, model: null, lastLog: null
                  };
                  this.updateDeviceStatusIndicator(deviceIp, 'connected'); 
             }
        }

        // Only display data for the currently selected device (check against IP)
        if (deviceIp !== this.selectedDeviceIp) {
            return; 
        }
        
        if (!this.elements.dataStreamOutput) return;

        // Format the data point for display (keep IP in line for clarity)
        console.log('DP: Formatting and appending data to output element');
        const timestamp = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(); 
        const dataValue = data.values !== undefined ? data.values : data.value;
        const keyPart = data.key ? `.${data.key}` : '';
        const formattedData = `[${deviceIp} @ ${timestamp}] ${data.type}${keyPart}: ${JSON.stringify(dataValue)}\n`; 

        // Append to the output, managing buffer size
        const wasScrolledToBottom = this.elements.dataStreamOutput.scrollHeight - this.elements.dataStreamOutput.clientHeight <= this.elements.dataStreamOutput.scrollTop + 1;
        
        this.elements.dataStreamOutput.textContent += formattedData;

        const lines = this.elements.dataStreamOutput.textContent.split('\n');
        if (lines.length > this.maxStreamLines + 20) { // Keep buffer slightly larger than max
            this.elements.dataStreamOutput.textContent = lines.slice(-this.maxStreamLines).join('\n');
        }

        // Auto-scroll to bottom only if already near the bottom
        if (wasScrolledToBottom) {
            this.elements.dataStreamOutput.scrollTop = this.elements.dataStreamOutput.scrollHeight;
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    const devicePage = new DevicePage();
    devicePage.initialize();
}); 