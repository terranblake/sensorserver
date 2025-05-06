#!/usr/bin/env python3
"""
Wi-Fi Network Scanner with Authentication Response Time Measurement
Optimized for Raspberry Pi
Requires root privileges
"""

import time
import argparse
import os
import sys
import subprocess
from datetime import datetime
import csv

# Check if scapy is installed
try:
    from scapy.all import *
    from scapy.layers.dot11 import Dot11, Dot11Auth, Dot11Beacon, Dot11Elt, RadioTap
except ImportError:
    print("Error: Scapy library not installed. Install it with: sudo pip3 install scapy")
    sys.exit(1)

class RPiWiFiScanner:
    def __init__(self, interface='wlan0', output_file=None, scan_time=30, timeout=2):
        self.interface = interface
        self.output_file = output_file
        self.scan_time = scan_time
        self.timeout = timeout
        self.networks = {}
        self.results = []
        
        # Check if running as root
        if os.geteuid() != 0:
            sys.exit("This script must be run as root. Try using sudo.")
            
        # Check if interface exists
        try:
            subprocess.check_output(f"ip link show {self.interface}", shell=True)
        except subprocess.CalledProcessError:
            print(f"Error: Interface {self.interface} not found!")
            available = subprocess.getoutput("ip link | grep -E 'wlan' | awk -F': ' '{print $2}'")
            if available:
                print(f"Available wireless interfaces: {available}")
                sys.exit(f"Please specify a valid interface with -i")
            else:
                sys.exit("No wireless interfaces found!")
    
    def install_dependencies(self):
        """Install required dependencies if needed"""
        try:
            # Check for iw
            subprocess.check_output("which iw", shell=True)
        except subprocess.CalledProcessError:
            print("Installing required dependencies...")
            os.system("apt-get update && apt-get install -y iw wireless-tools")
    
    def set_monitor_mode(self):
        """Set wireless interface to monitor mode"""
        print(f"[*] Setting {self.interface} to monitor mode...")
        
        # First try using iw (modern method)
        try:
            subprocess.run(f"ip link set {self.interface} down", shell=True, check=True)
            subprocess.run(f"iw {self.interface} set monitor control", shell=True, check=True)
            subprocess.run(f"ip link set {self.interface} up", shell=True, check=True)
            print(f"[+] Successfully set {self.interface} to monitor mode")
            return True
        except subprocess.CalledProcessError:
            # Fall back to iwconfig (older method)
            try:
                subprocess.run(f"ifconfig {self.interface} down", shell=True, check=True)
                subprocess.run(f"iwconfig {self.interface} mode monitor", shell=True, check=True)
                subprocess.run(f"ifconfig {self.interface} up", shell=True, check=True)
                print(f"[+] Successfully set {self.interface} to monitor mode (using iwconfig)")
                return True
            except:
                print(f"[!] Failed to set {self.interface} to monitor mode")
                print(f"[!] Try manually checking your wireless adapter's compatibility")
                return False
    
    def restore_interface(self):
        """Restore wireless interface to managed mode"""
        print(f"[*] Restoring {self.interface} to managed mode...")
        
        try:
            # First try using iw
            subprocess.run(f"ip link set {self.interface} down", shell=True)
            subprocess.run(f"iw {self.interface} set type managed", shell=True)
            subprocess.run(f"ip link set {self.interface} up", shell=True)
        except:
            try:
                # Fall back to iwconfig
                subprocess.run(f"ifconfig {self.interface} down", shell=True)
                subprocess.run(f"iwconfig {self.interface} mode managed", shell=True)
                subprocess.run(f"ifconfig {self.interface} up", shell=True)
            except:
                print(f"[!] Warning: Failed to restore {self.interface} to managed mode")
                print(f"[!] You may need to reboot or manually restore it")
    
    def packet_handler(self, pkt):
        """Process captured packets to identify networks"""
        if pkt.haslayer(Dot11Beacon):
            try:
                bssid = pkt[Dot11].addr2
                if bssid not in self.networks:
                    # Extract the SSID
                    ssid = pkt[Dot11Elt].info.decode('utf-8', errors='ignore')
                    if not ssid:
                        ssid = "Hidden Network"
                    
                    # Try to extract channel
                    try:
                        channel = int(ord(pkt[Dot11Elt:3].info))
                    except:
                        channel = 0
                    
                    # Try to extract signal strength
                    try:
                        signal_strength = -(256-ord(pkt[RadioTap].dBm_AntSignal))
                    except:
                        signal_strength = 0
                    
                    self.networks[bssid] = {
                        'ssid': ssid,
                        'bssid': bssid,
                        'channel': channel,
                        'signal_strength': signal_strength
                    }
                    print(f"[+] Discovered network: {ssid} ({bssid}) Ch:{channel} Sig:{signal_strength}dBm")
            except Exception as e:
                # Silently ignore malformed packets
                pass
    
    def scan_networks(self):
        """Scan for available Wi-Fi networks"""
        print(f"[*] Scanning for networks on {self.interface} for {self.scan_time} seconds...")
        
        # Start sniffing
        try:
            sniff(iface=self.interface, prn=self.packet_handler, timeout=self.scan_time)
        except Exception as e:
            print(f"[!] Error during scanning: {e}")
            
        # If no networks found, try an alternative method
        if not self.networks:
            print("[*] No networks found with Scapy, trying alternative method...")
            try:
                output = subprocess.check_output(f"sudo iw dev {self.interface} scan", shell=True).decode()
                for line in output.split('\n'):
                    if "BSS" in line and "on" in line:
                        bssid = line.split('(')[0].split(' ')[1].strip()
                        self.networks[bssid] = {'bssid': bssid, 'ssid': "Unknown", 'channel': 0}
                    elif "SSID: " in line and bssid in self.networks:
                        ssid = line.split('SSID: ')[1].strip()
                        if ssid:
                            self.networks[bssid]['ssid'] = ssid
                            print(f"[+] Discovered network: {ssid} ({bssid})")
            except Exception as e:
                print(f"[!] Alternative scanning failed: {e}")
        
        print(f"[*] Found {len(self.networks)} networks")
        return self.networks
    
    def send_auth_request(self, bssid, ssid, channel):
        """Send authentication request to a network and measure response time"""
        # Set channel if possible
        if channel > 0:
            try:
                subprocess.run(f"iw dev {self.interface} set channel {channel}", shell=True)
                print(f"[*] Switched to channel {channel} for {ssid}")
            except:
                print(f"[!] Failed to set channel {channel}")
        
        # Create a random MAC address for the source
        src_mac = RandMAC()
        
        # Craft authentication request frame
        auth_req = RadioTap() / Dot11(
            type=0, subtype=11,  # Authentication frame
            addr1=bssid,         # Destination MAC (AP)
            addr2=src_mac,       # Source MAC
            addr3=bssid          # BSSID
        ) / Dot11Auth(seqnum=1)
        
        response_received = False
        rtt = None
        start_time = time.time()
        
        # Define the packet handler for capturing responses
        def auth_response_handler(pkt):
            nonlocal response_received, rtt
            if pkt.haslayer(Dot11Auth) and pkt.addr1 == src_mac and pkt.addr2 == bssid:
                rtt = (time.time() - start_time) * 1000  # Convert to milliseconds
                response_received = True
                return True  # Stop sniffing
        
        # Send the authentication request and start sniffing for the response
        try:
            sendp(auth_req, iface=self.interface, verbose=0)
            
            # Start a sniffer to capture the authentication response
            sniff(iface=self.interface, prn=auth_response_handler, timeout=self.timeout, 
                  stop_filter=lambda x: response_received)
        except Exception as e:
            print(f"[!] Error sending auth request to {ssid}: {e}")
        
        # Record the result
        result = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ssid': ssid,
            'bssid': bssid,
            'channel': channel,
            'response_received': response_received,
            'rtt_ms': rtt if response_received else None
        }
        
        status = "Success" if response_received else "Timeout"
        rtt_str = f"{rtt:.2f} ms" if rtt else "N/A"
        print(f"[*] Auth request to {ssid} ({bssid}): {status} - RTT: {rtt_str}")
        
        return result
    
    def test_all_networks(self):
        """Send authentication requests to all discovered networks"""
        print("[*] Testing authentication response times for discovered networks...")
        
        for bssid, network in self.networks.items():
            result = self.send_auth_request(
                bssid=network['bssid'],
                ssid=network['ssid'],
                channel=network.get('channel', 0)
            )
            self.results.append(result)
            # Add a short delay between requests
            time.sleep(0.5)
        
        return self.results
    
    def save_results(self):
        """Save the results to a CSV file"""
        if not self.output_file or not self.results:
            return
        
        try:
            with open(self.output_file, 'w', newline='') as csvfile:
                fieldnames = ['timestamp', 'ssid', 'bssid', 'channel', 'response_received', 'rtt_ms']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for result in self.results:
                    writer.writerow(result)
                
                print(f"[+] Results saved to {self.output_file}")
        except Exception as e:
            print(f"[!] Error saving results: {e}")
    
    def run(self):
        """Run the complete scanning and testing process"""
        try:
            # Install dependencies if needed
            self.install_dependencies()
            
            # Set monitor mode
            monitor_enabled = self.set_monitor_mode()
            if not monitor_enabled:
                print("[!] Warning: Monitor mode might not be fully enabled. Results may be limited.")
            
            # Scan for networks
            self.scan_networks()
            
            if self.networks:
                # Test authentication response times
                self.test_all_networks()
                
                # Save results if output file specified
                if self.output_file:
                    self.save_results()
                
                # Print summary
                total_networks = len(self.networks)
                responsive_networks = sum(1 for r in self.results if r['response_received'])
                if responsive_networks > 0:
                    avg_rtt = sum(r['rtt_ms'] for r in self.results if r['rtt_ms'] is not None) / responsive_networks
                else:
                    avg_rtt = 0
                
                print("\n[*] Summary:")
                print(f"    Total networks discovered: {total_networks}")
                print(f"    Networks that responded: {responsive_networks}")
                print(f"    Average response time: {avg_rtt:.2f} ms")
            else:
                print("[!] No networks found. Try using an external Wi-Fi adapter.")
            
        except KeyboardInterrupt:
            print("\n[!] Scan interrupted by user")
        except Exception as e:
            print(f"[!] Error: {e}")
        finally:
            # Restore interface mode
            self.restore_interface()


def main():
    parser = argparse.ArgumentParser(description='Raspberry Pi Wi-Fi Scanner with Authentication Response Time Measurement')
    parser.add_argument('-i', '--interface', default='wlan0', help='Wireless interface to use (default: wlan0)')
    parser.add_argument('-o', '--output', help='Output CSV file path')
    parser.add_argument('-t', '--time', type=int, default=30, help='Scan duration in seconds (default: 30)')
    parser.add_argument('--timeout', type=float, default=2, help='Timeout for authentication responses in seconds (default: 2)')
    
    args = parser.parse_args()
    
    scanner = RPiWiFiScanner(
        interface=args.interface,
        output_file=args.output,
        scan_time=args.time,
        timeout=args.timeout
    )
    scanner.run()


if __name__ == "__main__":
    main()