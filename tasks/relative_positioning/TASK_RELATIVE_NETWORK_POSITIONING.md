# **Task: Implement Simplified Network-Based Location Tracking**

## **Overview**

Implement a simplified location tracking system on the server-side by leveraging annotated event data containing network scan results. The system will analyze historical network data associated with specific annotated locations to determine characteristic network fingerprints (based on "best fit" RSSI values). It will then use these fingerprints, combined with manually provided relative spatial positions, to estimate the current location based on incoming real-time network scan data.

## **Requirements**

* Process historical annotated event data to extract network scan results and associated location labels.  
* Identify all unique network identifiers (BSSID/MAC Address) seen across all relevant events.  
* For each unique location label, compile a list of all network identifiers present in events associated with that location.  
* For each location and each network identifier present, calculate a "best fit" RSSI value based on the RSSI values recorded in the associated events.  
* Allow for manual input of relative spatial positions for each unique location.  
* Implement a real-time inference mechanism that takes current network scan data and predicts the most likely location from the known annotated locations.  
* The inference should consider the "best fit" RSSI values for each location's fingerprint and the provided relative positions.

## **Design**

The design involves a two-phase process: a calibration/analysis phase and a real-time inference phase.

**Calibration/Analysis Phase:**

* A script or module will read the annotated event logs.  
* It will filter events to include only those with network scan data (android.network.\*).  
* It will parse the event descriptions to extract location labels (requires a consistent naming convention or simple parsing logic).  
* Data will be grouped by location label.  
* For each location, aggregate all associated network scan results.  
* Calculate the "best fit" RSSI for each network per location. This could be the average, median, or a percentile of the recorded RSSI values. The median might be more robust to outliers.  
* Store the calculated "best fit" fingerprints (Location \-\> List of (Network ID, Best Fit RSSI)).  
* A separate configuration or input mechanism will store the relative spatial positions of locations (e.g., a simple 2D coordinate system where distances are relative).

**Real-time Inference Phase:**

* When new network scan data arrives from the phone, extract the current network fingerprint (list of visible Network IDs and their current RSSI).  
* For each known annotated location:  
  * Compare the current fingerprint to the location's stored "best fit" fingerprint. A simple comparison metric could be the sum of the absolute differences between current RSSI and "best fit" RSSI for networks present in both fingerprints, plus a penalty for networks present in one but not the other.  
  * Incorporate the relative spatial positions. Locations closer to the currently predicted location (from the previous inference cycle, or a default starting point) might be given slightly higher probability or weighted differently in the comparison. This adds a basic spatial smoothing.  
* Select the location with the "best" comparison metric (lowest difference/penalty score).  
* Output the predicted location.

**⚠️ DESIGN REVIEW CHECKPOINT**: Before proceeding to implementation, please confirm this design approach.

## **Implementation Plan**

1. **Data Loading and Filtering:**  
   * Implement logic to read and parse the annotated event log files.  
   * Filter events to select those containing android.network.wifi and android.network.bluetooth data.  
2. **Extract Locations and Group Events:**  
   * Implement logic to extract location labels from event descriptions (e.g., regex, keyword matching).  
   * Group the filtered events based on the extracted location labels.  
3. **Identify Unique Networks:**  
   * Iterate through all filtered events to build a comprehensive list of unique WiFi BSSIDs and Bluetooth MAC addresses encountered.  
4. **Calculate Best Fit RSSI per Location:**  
   * For each unique location:  
     * Iterate through the events associated with that location.  
     * For each unique network ID found in those events, collect all recorded RSSI values.  
     * Calculate the "best fit" RSSI (e.g., median) for each network ID at this location.  
   * Store these location fingerprints.  
5. **Implement Relative Position Input:**  
   * Create a mechanism (e.g., a configuration file, database table, or simple input form) to store relative spatial positions for each location label (e.g., {'Living Room': (0, 0), 'Kitchen': (5, 2), ...}).  
6. **Implement Inference Logic:**  
   * Create a function that takes the current network scan data (list of (Network ID, Current RSSI)).  
   * For each stored location fingerprint:  
     * Calculate a similarity/difference score between the current scan data and the location's fingerprint.  
     * Incorporate the relative position data into the scoring (e.g., penalize predictions that are spatially distant from the previous prediction unless the network data strongly supports it).  
   * Return the location with the highest similarity or lowest difference score.  
7. **Integrate with Real-time Data Pipeline:**  
   * Modify the server's data ingestion process to feed incoming network scan data into the inference function.  
   * Log or expose the predicted location.

**⚠️ IMPLEMENTATION REVIEW CHECKPOINT**: After outlining implementation details but before writing code.

## **Technical Constraints**

* Accuracy is highly dependent on the quality and quantity of annotated data for each location.  
* RSSI values can fluctuate significantly, making the "best fit" calculation sensitive to variations.  
* Parsing location from free-text event descriptions requires consistent annotation or robust parsing.  
* The relative positioning system is manually defined and doesn't account for complex layouts or obstacles affecting signal propagation.  
* Does not account for temporary changes in the network environment (e.g., a neighbor's AP going offline).  
* Relies on the phone continuously scanning and sending data.

## **Testing Strategy**

* Unit tests:  
  * Test parsing location labels from example event descriptions.  
  * Test the calculation of "best fit" RSSI from a list of values (e.g., median calculation).  
  * Test the similarity/difference scoring logic with various input fingerprints and location fingerprints.  
* Integration tests:  
  * Process a set of historical annotated events and verify that the generated location fingerprints and best fit RSSI values are as expected.  
  * Test the inference function with network data captured at known locations and verify the predicted location matches the actual location.  
  * Test the impact of the spatial component on the inference results.  
* Manual verification:  
  * Annotate new events at known locations after the system is running. Verify that the automatically predicted location matches the manual annotation.  
  * Walk between locations while observing the predicted location output to see if it changes correctly.

## **Acceptance Criteria**

* \[x\] The system can successfully process annotated events and extract location labels and network data.  
* \[x\] The system can identify unique networks and group network data by location.  
* \[x\] The system can calculate a "best fit" RSSI for each network per location.  
* \[x\] The system can store and utilize manually provided relative spatial positions.  
* \[x\] The system can take real-time network scan data and output a predicted location from the set of annotated locations.  
* \[x\] The predicted location shows reasonable accuracy when tested with data from known locations.

## **References**

* [Median Calculation](https://en.wikipedia.org/wiki/Median)  
* [RSSI (Received Signal Strength Indicator)](https://en.wikipedia.org/wiki/Received_signal_strength_indication)  
* [Basic Distance Metrics (e.g., Euclidean distance for spatial component)](https://en.wikipedia.org/wiki/Euclidean_distance)