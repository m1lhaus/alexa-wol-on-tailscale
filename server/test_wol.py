"""
Test script for Wake-on-LAN functionality. This script sends a magic packet to the specified MAC address and 
broadcast IP to wake up a device on the network.

How to run:
export $(grep -v '^#' .env | xargs) && python3 server/test_wol.py

Important: 
You need to run this script from a machine that has L2 network presence! Not from a dev container (behind a NAT), 
but from the host instead! 
"""

import os

from app import send_magic_packet

MAC_ADDRESS: str = os.environ["WOL_MAC"]
BROADCAST_IP: str = os.environ.get("WOL_BROADCAST", "255.255.255.255")

if __name__ == "__main__":
    send_magic_packet(MAC_ADDRESS, BROADCAST_IP)
    print(f"Magic packet sent to {MAC_ADDRESS} via {BROADCAST_IP}")
