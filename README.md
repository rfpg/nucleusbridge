# SSL Nucleus 2 ipMIDI Bridge for macOS

Bidirectional MIDI bridge between SSL Nucleus 2 and DAWs on macOS using virtual MIDI ports.

The Nucleus 2 uses ipMIDI (UDP multicast) for communication. This bridge:
- Receives ipMIDI from the Nucleus 2 and forwards to a virtual MIDI port
- Receives MIDI from the DAW and sends back to the Nucleus via ipMIDI
- Supports Mackie Control protocol for motorized fader feedback and LED updates
- Sends MCU initialization on startup to wake up the connection
- Can auto-start on login via launchd

## Requirements

```bash
pip install mido python-rtmidi
```

## Usage

1. Connect the Nucleus 2 via Ethernet (it uses link-local 169.254.x.x addresses)
2. Set Nucleus to **DAW2** mode (Mackie Control)
3. Run the bridge:
   ```bash
   python3 main.py
   ```
4. In Ableton Live:
   - Go to Preferences > Link, Tempo & MIDI
   - Add "Mackie Control" as a Control Surface
   - Set Input and Output to "Nucleus 2 Bridge"

## Auto-Start on Login

Install the included launchd agent to start the bridge automatically:

```bash
# Install
cp com.nucleusbridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nucleusbridge.plist

# Uninstall
launchctl unload ~/Library/LaunchAgents/com.nucleusbridge.plist
rm ~/Library/LaunchAgents/com.nucleusbridge.plist
```

**Note:** Edit `com.nucleusbridge.plist` to update the Python path if needed. The bridge logs to `bridge.log` in this directory.

### Useful Commands

```bash
# Check if running
launchctl list | grep nucleusbridge

# View logs
tail -f ~/nucleusbridge/bridge.log

# Restart
launchctl unload ~/Library/LaunchAgents/com.nucleusbridge.plist
launchctl load ~/Library/LaunchAgents/com.nucleusbridge.plist
```

## Configuration

The bridge auto-detects the link-local network interface. Key settings in `main.py`:

- `VERBOSITY`: 0=quiet, 1=basic, 2=verbose logging
- `IPMIDI_PORTS`: Which ipMIDI ports to listen on (default: 1, 2, 3)

## How It Works

The Nucleus 2 communicates via ipMIDI (UDP multicast on 225.0.0.37, ports 21928-21930). When set to DAW2 mode, it speaks Mackie Control Universal (MCU) protocol:
- Faders send pitchwheel messages on channels 0-8
- Buttons send note on/off messages
- The bridge includes echo prevention to avoid feedback loops

On startup, the bridge sends MCU device query and fader touch messages to trigger Ableton's initial sync with the control surface.

## Nucleus Mode Settings

For best results:
- **Protocol**: DAW2 (Mackie Control)
- **Channel Mode**: 01-08 for main mixer channels
- RA/RB modes control return tracks

## License

MIT
