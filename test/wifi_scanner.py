#!/usr/bin/env python3
"""
Wi-Fi Network Scanner with Authentication Response Time Measurement
Cross-platform implementation using platform-specific tools when available.
Requires root/administrator privileges.
"""

import time
import argparse
import os
import sys
import subprocess
import platform
from datetime import datetime
import csv
import re

# Check if scapy is installed, provide helpful error if not
try:
    from scapy.all import *
    from scapy.layers.dot11 import Dot11, Dot11Auth, Dot11Beacon, Dot11Elt, RadioTap
except ImportError:
    print("Error: Scapy library not installed. Install it with: pip install scapy")
    sys.exit(1)

class WiFiScanner:
    def __init__(self, interface=None, output_file=None, scan_time=30, timeout=2):
        self.interface = interface
        self.output_file = output_file
        self.scan_time = scan_time
        self.timeout = timeout
        self.networks = {}
        self.results = []
        self.os_type = platform.system()  # 'Linux', 'Darwin' (macOS), 'Windows'
        
        # Check if running as root/admin
        if self.os_type != 'Windows' and os.geteuid() != 0:
            sys.exit("This script must be run as root or with administrator privileges.")
        elif self.os_type == 'Windows' and not self._is_admin_windows():
            sys.exit("This script must be run with administrator privileges.")
            
        # Auto-detect wireless interface if not provided
        if not self.interface:
            self.interface = self._detect_wireless_interface()
            if not self.interface:
                sys.exit("Error: Could not automatically detect wireless interface. Please specify one with -i.")
            print(f"[*] Using detected wireless interface: {self.interface}")
    
    def _is_admin_windows(self):
        """Check if running with administrator privileges on Windows"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            return False
    
    def _detect_wireless_interface(self):
        """Auto-detect available wireless interface based on OS"""
        if self.os_type == 'Linux':
            # Try using iw command first
            try:
                output = subprocess.check_output("iw dev | grep Interface", shell=True).decode()
                interfaces = re.findall(r'Interface\s+(.+)', output)
                if interfaces:
                    return interfaces[0].strip()
            except:
                pass
            
            # Try using ip link command
            try:
                output = subprocess.check_output("ip link | grep -E 'wlan|wlp'", shell=True).decode()
                interfaces = re.findall(r'\d+:\s+(\w+):', output)
                if interfaces:
                    return interfaces[0].strip()
            except:
                pass
                
        elif self.os_type == 'Darwin':  # macOS
            try:
                # List network service order
                output = subprocess.check_output("networksetup -listallhardwareports", shell=True).decode()
                # Look for Wi-Fi and associated device
                match = re.search(r'Hardware Port: Wi-Fi\nDevice: (en\d+)', output)
                if match:
                    return match.group(1)
            except:
                pass
                
        elif self.os_type == 'Windows':
            try:
                # Get interface from Windows
                from scapy.arch.windows import get_windows_if_list
                interfaces = get_windows_if_list()
                # Filter for wireless interfaces
                for interface in interfaces:
                    if 'Wi-Fi' in interface.get('name', '') or 'Wireless' in interface.get('name', ''):
                        return interface.get('name')
            except:
                pass
        
        return None
    
    def _check_dependencies(self):
        """Check if necessary system tools are available"""
        required_tools = {
            'Linux': ['iw', 'ip'],
            'Darwin': ['airport'],
            'Windows': []  # Windows uses native APIs via Python
        }
        
        missing = []
        for tool in required_tools.get(self.os_type, []):
            try:
                subprocess.check_output(f"which {tool}", shell=True)
            except:
                missing.append(tool)
        
        if missing:
            print(f"[!] Warning: Required tools not found: {', '.join(missing)}")
            print("[!] Some functionality may be limited")
            
            if self.os_type == 'Linux':
                print("[!] Install missing tools with: sudo apt-get install wireless-tools iw")
            elif self.os_type == 'Darwin' and 'airport' in missing:
                print("[!] The Airport utility should be available at /System/Library/PrivateFrameworks/Apple80211.framework/Resources/airport")
                
        return len(missing) == 0
    
    def set_monitor_mode(self):
        """Set wireless interface to monitor mode based on OS"""
        print(f"[*] Attempting to set {self.interface} to monitor mode...")
        
        if self.os_type == 'Linux':
            try:
                # First try using iw
                subprocess.run(f"ip link set {self.interface} down", shell=True, check=True)
                subprocess.run(f"iw {self.interface} set monitor control", shell=True, check=True)
                subprocess.run(f"ip link set {self.interface} up", shell=True, check=True)
                print(f"[*] Interface {self.interface} set to monitor mode using iw")
                return True
            except subprocess.CalledProcessError:
                try:
                    # Fall back to iwconfig if available
                    subprocess.run(f"ifconfig {self.interface} down", shell=True, check=True)
                    subprocess.run(f"iwconfig {self.interface} mode monitor", shell=True, check=True)
                    subprocess.run(f"ifconfig {self.interface} up", shell=True, check=True)
                    print(f"[*] Interface {self.interface} set to monitor mode using iwconfig")
                    return True
                except:
                    print(f"[!] Failed to set {self.interface} to monitor mode")
                    return False
                    
        elif self.os_type == 'Darwin':  # macOS
            try:
                airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Resources/airport"
                subprocess.run(f"{airport_path} {self.interface} sniff", shell=True)
                print(f"[*] Enabled sniffing mode on {self.interface}")
                return True
            except:
                print("[!] Failed to enable monitor mode on macOS")
                print("[*] Note: macOS has limited support for monitor mode")
                return False
                
        elif self.os_type == 'Windows':
            print("[*] Windows doesn't support true monitor mode - using promiscuous mode")
            print("[*] Some network discovery features may be limited")
            # Windows doesn't truly support monitor mode, but Scapy will attempt to use promiscuous mode
            return True
            
        return False
    
    def restore_interface(self):
        """Restore wireless interface to managed mode"""
        print(f"[*] Restoring {self.interface} to managed mode...")
        
        if self.os_type == 'Linux':
            try:
                # First try using iw
                subprocess.run(f"ip link set {self.interface} down", shell=True)
                subprocess.run(f"iw {self.interface} set type managed", shell=True)
                subprocess.run(f"ip link set {self.interface} up", shell=True)
                return
            except:
                try:
                    # Fall back to iwconfig
                    subprocess.run(f"ifconfig {self.interface} down", shell=True)
                    subprocess.run(f"iwconfig {self.interface} mode managed", shell=True)
                    subprocess.run(f"ifconfig {self.interface} up", shell=True)
                except:
                    print(f"[!] Warning: Failed to restore {self.interface} to managed mode")
                    
        elif self.os_type == 'Darwin':  # macOS
            # On macOS, the airport sniff command terminates when the script ends
            # or can be terminated with Ctrl+C, no explicit cleanup needed
            pass
    
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
                    print(f"[+] Discovered network: {ssid} ({bssid})")
            except Exception as e:
                # Silently ignore malformed packets
                pass
    
    def scan_networks(self):
        """Scan for available Wi-Fi networks"""
        print(f"[*] Scanning for networks on {self.interface} for {self.scan_time} seconds...")
        
        if self.os_type == 'Windows' or self.os_type == 'Darwin':
            print("[*] Note: Limited scanning capabilities on this OS. Results may vary.")
        
        # Start sniffing
        try:
            sniff(iface=self.interface, prn=self.packet_handler, timeout=self.scan_time)
        except Exception as e:
            print(f"[!] Error during scanning: {e}")
            print("[*] If you're seeing 'Permission denied' errors, try running with sudo/administrator rights")
            
        if not self.networks:
            # If no networks found, try an alternative method based on OS
            if self.os_type == 'Linux':
                try:
                    print("[*] Trying alternative scanning method...")
                    output = subprocess.check_output(f"iw dev {self.interface} scan", shell=True).decode()
                    for line in output.split('\n'):
                        if "BSS" in line and "on" in line:
                            bssid = line.split('(')[0].split(' ')[1].strip()
                            self.networks[bssid] = {'bssid': bssid, 'ssid': "Unknown", 'channel': 0}
                        elif "SSID: " in line:
                            ssid = line.split('SSID: ')[1].strip()
                            if bssid in self.networks and ssid:
                                self.networks[bssid]['ssid'] = ssid
                except:
                    pass
            elif self.os_type == 'Darwin':
                try:
                    airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Resources/airport"
                    output = subprocess.check_output(f"{airport_path} -s", shell=True).decode()
                    lines = output.split('\n')[1:]  # Skip header
                    for line in lines:
                        if not line.strip():
                            continue
                        parts = re.split(r'\s+', line.strip())
                        if len(parts) >= 2:
                            ssid = parts[0]
                            bssid = parts[1]
                            self.networks[bssid] = {'bssid': bssid, 'ssid': ssid, 'channel': 0}
                except:
                    pass
            elif self.os_type == 'Windows':
                try:
                    output = subprocess.check_output("netsh wlan show networks mode=bssid", shell=True).decode('utf-8', errors='ignore')
                    sections = output.split('SSID ')
                    
                    for section in sections[1:]:  # Skip the first empty part
                        lines = section.split('\n')
                        if len(lines) > 0:
                            ssid = lines[0].split(' : ')[1].strip()
                            for line in lines:
                                if 'BSSID' in line:
                                    bssid = line.split(' : ')[1].strip()
                                    self.networks[bssid] = {'bssid': bssid, 'ssid': ssid, 'channel': 0}
                except:
                    pass
        
        print(f"[*] Found {len(self.networks)} networks")
        return self.networks
    
    def send_auth_request(self, bssid, ssid, channel):
        """Send authentication request to a network and measure response time"""
        # Try to set channel if possible
        if self.os_type == 'Linux' and channel > 0:
            try:
                subprocess.run(f"iw dev {self.interface} set channel {channel}", shell=True)
            except:
                pass
        
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
            # Check dependencies
            self._check_dependencies()
            
            # Set monitor mode
            monitor_enabled = self.set_monitor_mode()
            if not monitor_enabled and self.os_type == 'Linux':
                print("[!] Warning: Failed to enable monitor mode. Results may be limited.")
            
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
                print("[!] No networks found. Try running with sudo/administrator privileges.")
            
        except KeyboardInterrupt:
            print("\n[!] Scan interrupted by user")
        except Exception as e:
            print(f"[!] Error: {e}")
        finally:
            # Restore interface mode
            self.restore_interface()


def main():
    parser = argparse.ArgumentParser(description='Wi-Fi Network Scanner with Authentication Response Time Measurement')
    parser.add_argument('-i', '--interface', help='Wireless interface to use (auto-detected if not specified)')
    parser.add_argument('-o', '--output', help='Output CSV file path')
    parser.add_argument('-t', '--time', type=int, default=30, help='Scan duration in seconds (default: 30)')
    parser.add_argument('--timeout', type=float, default=2, help='Timeout for authentication responses in seconds (default: 2)')
    
    args = parser.parse_args()
    
    scanner = WiFiScanner(
        interface=args.interface,
        output_file=args.output,
        scan_time=args.time,
        timeout=args.timeout
    )
    scanner.run()


if __name__ == "__main__":
    main()