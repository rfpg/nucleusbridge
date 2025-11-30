# SSL Nucleus 2 ipMIDI Bridge for macOS

Bidirectional MIDI bridge between SSL Nucleus 2 and DAWs on macOS using virtual MIDI ports.

The Nucleus 2 uses ipMIDI (UDP multicast) for communication. This bridge:
- Receives ipMIDI from the Nucleus 2 and forwards to a virtual MIDI port
- Receives MIDI from the DAW and sends back to the Nucleus via ipMIDI
- Supports Mackie Control protocol for motorized fader feedback and LED updates

## Requirements

```bash
pip install mido python-rtmidi
```

## Usage

1. Connect the Nucleus 2 via Ethernet (it uses link-local 169.254.x.x addresses)
2. Run the bridge:
   ```bash
   python3 main.py
   ```
3. In Ableton Live:
   - Add "Mackie Control" as a Control Surface
   - Set Input and Output to "Nucleus 2 Bridge"

## Configuration

The bridge auto-detects the link-local network interface. Key settings in `main.py`:

- `VERBOSITY`: 0=quiet, 1=basic, 2=verbose logging
- `IPMIDI_PORTS`: Which ipMIDI ports to listen on (default: 1, 2, 3)

## How It Works

The Nucleus 2 communicates via ipMIDI (UDP multicast on 225.0.0.37, ports 21928-21930). When set to DAW2 mode, it speaks Mackie Control Universal (MCU) protocol:
- Faders send pitchwheel messages on channels 0-8
- Buttons send note on/off messages
- The bridge includes echo prevention to avoid feedback loops

## License

MIT
