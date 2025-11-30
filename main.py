#!/usr/bin/env python3
"""
SSL Nucleus 2 ipMIDI Bridge for macOS

Bidirectional bridge using raw UDP multicast for ipMIDI.

Requirements:
    pip install mido python-rtmidi

Usage:
    python3 main.py
"""

import socket
import struct
import threading
import subprocess
import re
import time
import mido
from mido import Message

# ipMIDI Configuration
IPMIDI_MULTICAST_GROUP = "225.0.0.37"
IPMIDI_PORT_BASE = 21928
IPMIDI_PORTS = [1, 2, 3]


def find_link_local_ip():
    """Auto-detect the link-local (169.254.x.x) interface for Nucleus connection."""
    try:
        result = subprocess.run(['ifconfig'], capture_output=True, text=True)
        # Find all 169.254.x.x addresses
        matches = re.findall(r'inet (169\.254\.\d+\.\d+)', result.stdout)
        if matches:
            return matches[0]
    except Exception as e:
        print(f"  Warning: Could not auto-detect IP: {e}")
    return None


# Auto-detect or fallback
LOCAL_IP = find_link_local_ip()

# Virtual MIDI port name
VIRTUAL_PORT_NAME = "Nucleus 2 Bridge"

# Verbosity: 0=quiet, 1=basic, 2=verbose
VERBOSITY = 0

# Translation mode: convert Nucleus MCU messages to CC for Ableton InstantMapping
# Set to False for Mackie Control mode (bidirectional with feedback)
TRANSLATE_TO_CC = False

# CC mapping for faders (pitchwheel channel -> CC number)
# Nucleus sends pitchwheel on channels 0-7 for faders 1-8, channel 8 for master
FADER_CC_MAP = {
    0: 1,   # Fader 1 -> CC 1
    1: 2,   # Fader 2 -> CC 2
    2: 3,   # Fader 3 -> CC 3
    3: 4,   # Fader 4 -> CC 4
    4: 5,   # Fader 5 -> CC 5
    5: 6,   # Fader 6 -> CC 6
    6: 7,   # Fader 7 -> CC 7
    7: 8,   # Fader 8 -> CC 8
    8: 9,   # Master -> CC 9
}


class ipMIDIReceiver:
    def __init__(self, port_number):
        self.port_number = port_number
        self.udp_port = IPMIDI_PORT_BASE + port_number - 1
        self.socket = None
        self.running = False

    def setup_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.socket.bind(('', self.udp_port))
        mreq = struct.pack("4s4s",
                          socket.inet_aton(IPMIDI_MULTICAST_GROUP),
                          socket.inet_aton(LOCAL_IP))
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.socket.settimeout(1.0)

    def receive_loop(self, callback):
        self.running = True
        while self.running:
            try:
                data, addr = self.socket.recvfrom(1024)
                if data:
                    callback(data, self.port_number)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"  Receive error port {self.port_number}: {e}")

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()


class ipMIDISender:
    def __init__(self, port_number):
        self.port_number = port_number
        self.udp_port = IPMIDI_PORT_BASE + port_number - 1
        self.socket = None

    def setup_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                              socket.inet_aton(LOCAL_IP))

    def send(self, data):
        if self.socket:
            self.socket.sendto(data, (IPMIDI_MULTICAST_GROUP, self.udp_port))

    def stop(self):
        if self.socket:
            self.socket.close()


class NucleusBridge:
    def __init__(self):
        self.receivers = []
        self.senders = []
        self.midi_out = None
        self.midi_in = None
        self.running = False
        self.rx_count = 0
        self.tx_count = 0
        # Feedback loop prevention: track recent messages
        self.recent_to_daw = {}      # msg_key -> timestamp
        self.recent_to_nucleus = {}  # msg_key -> timestamp
        self.debounce_time = 0.05    # 50ms debounce window (shorter for responsiveness)

    def msg_key(self, msg):
        """Create a hashable key for a message (includes value to avoid blocking different states)."""
        if msg.type == 'note_on':
            return ('note_on', msg.channel, msg.note, msg.velocity)
        elif msg.type == 'note_off':
            return ('note_off', msg.channel, msg.note)
        elif msg.type == 'control_change':
            return ('cc', msg.channel, msg.control, msg.value)
        elif msg.type == 'pitchwheel':
            # Don't include pitch value - faders send continuous values
            return ('pitch', msg.channel)
        return None

    def is_echo(self, msg, recent_dict):
        """Check if this message is an echo of a recent message."""
        key = self.msg_key(msg)
        if key is None:
            return False
        now = time.time()
        if key in recent_dict:
            if now - recent_dict[key] < self.debounce_time:
                return True
        return False

    def mark_sent(self, msg, recent_dict):
        """Mark a message as recently sent."""
        key = self.msg_key(msg)
        if key:
            recent_dict[key] = time.time()

    def parse_midi_bytes(self, data):
        """Parse raw MIDI bytes into mido Messages."""
        messages = []
        i = 0
        while i < len(data):
            status = data[i]

            if 0x80 <= status <= 0x8F:  # Note Off
                if i + 2 < len(data):
                    messages.append(Message('note_off', channel=status & 0x0F,
                                           note=data[i+1], velocity=data[i+2]))
                    i += 3
                else:
                    break
            elif 0x90 <= status <= 0x9F:  # Note On
                if i + 2 < len(data):
                    vel = data[i+2]
                    if vel == 0:
                        messages.append(Message('note_off', channel=status & 0x0F,
                                               note=data[i+1], velocity=0))
                    else:
                        messages.append(Message('note_on', channel=status & 0x0F,
                                               note=data[i+1], velocity=vel))
                    i += 3
                else:
                    break
            elif 0xA0 <= status <= 0xAF:  # Poly Aftertouch
                if i + 2 < len(data):
                    messages.append(Message('polytouch', channel=status & 0x0F,
                                           note=data[i+1], value=data[i+2]))
                    i += 3
                else:
                    break
            elif 0xB0 <= status <= 0xBF:  # Control Change
                if i + 2 < len(data):
                    messages.append(Message('control_change', channel=status & 0x0F,
                                           control=data[i+1], value=data[i+2]))
                    i += 3
                else:
                    break
            elif 0xC0 <= status <= 0xCF:  # Program Change
                if i + 1 < len(data):
                    messages.append(Message('program_change', channel=status & 0x0F,
                                           program=data[i+1]))
                    i += 2
                else:
                    break
            elif 0xD0 <= status <= 0xDF:  # Channel Aftertouch
                if i + 1 < len(data):
                    messages.append(Message('aftertouch', channel=status & 0x0F,
                                           value=data[i+1]))
                    i += 2
                else:
                    break
            elif 0xE0 <= status <= 0xEF:  # Pitch Bend
                if i + 2 < len(data):
                    value = data[i+1] | (data[i+2] << 7)
                    messages.append(Message('pitchwheel', channel=status & 0x0F,
                                           pitch=value - 8192))
                    i += 3
                else:
                    break
            elif status == 0xF0:  # SysEx
                end = data.find(0xF7, i)
                if end != -1:
                    messages.append(Message('sysex', data=tuple(data[i+1:end])))
                    i = end + 1
                else:
                    break
            elif status == 0xF8:  # Clock
                messages.append(Message('clock'))
                i += 1
            elif status == 0xFA:  # Start
                messages.append(Message('start'))
                i += 1
            elif status == 0xFB:  # Continue
                messages.append(Message('continue'))
                i += 1
            elif status == 0xFC:  # Stop
                messages.append(Message('stop'))
                i += 1
            elif status == 0xFE:  # Active Sensing
                messages.append(Message('active_sensing'))
                i += 1
            else:
                i += 1
        return messages

    def translate_to_cc(self, msg):
        """Convert MCU pitchwheel to CC for Ableton InstantMapping."""
        if msg.type == 'pitchwheel' and msg.channel in FADER_CC_MAP:
            # Convert 14-bit pitch (-8192 to 8191) to 7-bit CC (0-127)
            cc_value = int((msg.pitch + 8192) / 16383 * 127)
            cc_value = max(0, min(127, cc_value))
            cc_num = FADER_CC_MAP[msg.channel]
            return Message('control_change', channel=0, control=cc_num, value=cc_value)
        return msg

    def translate_from_cc(self, msg):
        """Convert CC back to MCU pitchwheel for Nucleus feedback."""
        if msg.type == 'control_change' and msg.control in FADER_CC_MAP.values():
            # Find which channel this CC maps to
            for channel, cc in FADER_CC_MAP.items():
                if cc == msg.control:
                    # Convert 7-bit CC (0-127) to 14-bit pitch (-8192 to 8191)
                    pitch = int(msg.value / 127 * 16383) - 8192
                    return Message('pitchwheel', channel=channel, pitch=pitch)
        return msg

    def handle_from_nucleus(self, data, port_number):
        """Forward Nucleus data to DAW."""
        messages = self.parse_midi_bytes(data)
        for msg in messages:
            self.rx_count += 1

            # Check if this is an echo of something we sent to Nucleus
            if self.is_echo(msg, self.recent_to_nucleus):
                if VERBOSITY >= 2:
                    print(f"  [BLOCKED ECHO] Nucleus -> DAW: {msg}")
                continue

            # Translate if enabled
            out_msg = self.translate_to_cc(msg) if TRANSLATE_TO_CC else msg

            if self.midi_out:
                self.midi_out.send(out_msg)
                self.mark_sent(out_msg, self.recent_to_daw)

            if VERBOSITY >= 2 or (VERBOSITY >= 1 and msg.type not in ('clock', 'active_sensing')):
                if TRANSLATE_TO_CC and msg != out_msg:
                    print(f"  Nucleus -> DAW: {msg} -> {out_msg}")
                else:
                    print(f"  Nucleus -> DAW: {msg}")

    def handle_from_daw(self, msg):
        """Forward DAW data to Nucleus."""
        self.tx_count += 1

        # Check if this is an echo of something we sent to DAW
        if self.is_echo(msg, self.recent_to_daw):
            if VERBOSITY >= 2:
                print(f"  [BLOCKED ECHO] DAW -> Nucleus: {msg}")
            return

        # Translate CC back to pitchwheel for Nucleus
        out_msg = self.translate_from_cc(msg) if TRANSLATE_TO_CC else msg

        data = bytes(out_msg.bytes())
        for sender in self.senders:
            sender.send(data)
        self.mark_sent(out_msg, self.recent_to_nucleus)

        if VERBOSITY >= 2 or (VERBOSITY >= 1 and msg.type not in ('clock', 'active_sensing')):
            if TRANSLATE_TO_CC and msg != out_msg:
                print(f"  DAW -> Nucleus: {msg} -> {out_msg}")
            else:
                print(f"  DAW -> Nucleus: {msg}")

    def daw_receive_loop(self):
        while self.running:
            try:
                for msg in self.midi_in.iter_pending():
                    self.handle_from_daw(msg)
                threading.Event().wait(0.001)
            except Exception as e:
                if self.running:
                    print(f"  Error from DAW: {e}")

    def start(self):
        print("\n" + "=" * 60)
        print("SSL Nucleus 2 <-> MIDI Bridge")
        print("=" * 60)

        # Virtual MIDI ports
        print(f"\nVirtual MIDI: '{VIRTUAL_PORT_NAME}'")
        try:
            self.midi_out = mido.open_output(VIRTUAL_PORT_NAME, virtual=True)
            self.midi_in = mido.open_input(VIRTUAL_PORT_NAME, virtual=True)
            print("  Created ✓")
        except Exception as e:
            print(f"  Error: {e}")
            return

        # ipMIDI
        if not LOCAL_IP:
            print("\nNo link-local (169.254.x.x) interface found!")
            print("Make sure the Nucleus is connected via Ethernet.")
            return
        print(f"\nipMIDI on {LOCAL_IP} -> {IPMIDI_MULTICAST_GROUP}")
        for port_num in IPMIDI_PORTS:
            receiver = ipMIDIReceiver(port_num)
            try:
                receiver.setup_socket()
                self.receivers.append(receiver)
                t = threading.Thread(target=receiver.receive_loop,
                                    args=(self.handle_from_nucleus,), daemon=True)
                t.start()
                print(f"  Port {port_num} (UDP {receiver.udp_port}): listening ✓")
            except Exception as e:
                print(f"  Port {port_num}: {e}")

            sender = ipMIDISender(port_num)
            try:
                sender.setup_socket()
                self.senders.append(sender)
            except Exception as e:
                print(f"  Port {port_num} sender: {e}")

        if not self.receivers:
            print("\nNo ports opened!")
            return

        self.running = True
        threading.Thread(target=self.daw_receive_loop, daemon=True).start()

        print("\n" + "-" * 60)
        print("Bridge running! Ableton: Mackie Control -> Nucleus 2 Bridge")
        print("Press Ctrl+C to stop.")
        print("-" * 60 + "\n")

        try:
            while self.running:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self.stop()

    def stop(self):
        self.running = False
        for r in self.receivers:
            r.stop()
        for s in self.senders:
            s.stop()
        if self.midi_out:
            self.midi_out.close()
        if self.midi_in:
            self.midi_in.close()
        print(f"\n{self.rx_count} received, {self.tx_count} sent")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        print("\nOutputs:", mido.get_output_names())
        print("Inputs:", mido.get_input_names())
    else:
        NucleusBridge().start()
