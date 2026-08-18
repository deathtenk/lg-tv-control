# lg-tv-control

`lg-tv-control` is a small utility for waking an LG webOS TV and switching it to a target input. This repository also includes systemd units for two automation flows:

- Wake the TV and switch input after the Steam Deck resumes from sleep
- Listen for the Steam button on a Valve controller and switch the TV to a target HDMI input

The current Steam button listener targets the 2026 controller HID stream and uses the short raw Steam-button event packets observed on that device.

## What It Does

- Sends Wake-on-LAN to the TV if it is not already reachable
- Connects to the TV over webOS and switches to the configured input
- Skips the input switch if the TV is already on the target input
- Uses a shared lock file so the resume service and Steam button listener do not stomp on each other

## Requirements

- An LG TV running webOS on the same network as the Steam Deck
- Wake-on-LAN enabled on the TV
- A saved webOS pairing key, or the ability to accept the TV pairing prompt on first run
- `systemd` and `sudo` on the target machine

## Installation

### 1. Create a local config file

Copy the example file and fill in your own values:

```bash
cp lg-tv-control.env.example lg-tv-control.env
```

At minimum you will usually want:

```env
TV_IP=192.168.1.177
TV_MAC=d8:e3:5e:52:e5:24
TARGET_INPUT=HDMI_4
BUTTON_DEVICE_ID=28de:1304
```

### 2. Run the installer

```bash
./install.sh
```

What the installer does:

- Downloads the latest GitHub release binary
- Verifies its SHA-256 checksum
- Installs the binary to `/home/deck/.local/bin/lg-tv-control`
- Copies `./lg-tv-control.env` to `/home/deck/.config/lg-tv-control/env` if the file exists
- Stops and disables any previously installed `lg-tv-control` services before reinstalling
- Installs the systemd unit files into `/etc/systemd/system`
- Enables `lg-tv-control-resume.service`
- Enables and starts `lg-tv-control-steam-button.service`

If you want the debug service installed too:

```bash
LG_TV_DEBUG=true ./install.sh
```

That additionally installs and starts `lg-tv-control-debug.service`.

## Services

### `lg-tv-control-resume.service`

Runs once after resume and calls the main `lg-tv-control` action. It waits for the network route to the TV IP before starting.

### `lg-tv-control-steam-button.service`

Long-running listener that watches the controller HID stream and triggers the TV action when the configured button event is seen.

### `lg-tv-control-debug.service`

Optional debugging service that prints raw HID reports and whether they matched.

## Configuration

The installer reads `/home/deck/.config/lg-tv-control/env` via `EnvironmentFile=` in the systemd units.

### TV settings

- `TV_IP`: IP address of the LG TV
- `TV_MAC`: MAC address used for Wake-on-LAN
- `TARGET_INPUT`: Input ID to switch to, for example `HDMI_4`
- `PAIRING_FILE`: Optional override for the saved webOS pairing key path. By default the Steam button service uses `/home/deck/.lg_webos_key.json`

### Shared action control

- `ACTION_LOCK_FILE`: Lock file used to prevent the resume service and button listener from running the TV action at the same time

### Controller selection

- `BUTTON_DEVICE_ID`: Preferred controller selector. Accepts either `BUS:VENDOR:PRODUCT` or `VENDOR:PRODUCT`
- `BUTTON_DEVICE`: Optional explicit hidraw override if you want to pin one device node manually

Use `BUTTON_DEVICE_ID` unless you have a reason to hardcode a hidraw node.

### Button listener settings

- `BUTTON_NAME`: Button to monitor. For the current setup this should usually stay `STEAM`
- `BUTTON_REPORT_SIZE`: Number of bytes read from the hidraw device per report. `64` is the intended default
- `BUTTON_DEBOUNCE_SECONDS`: Minimum time between accepted button presses
- `BUTTON_MATCH_STREAK`: Number of consecutive matches required before triggering
- `BUTTON_RELEASE_STREAK`: Number of consecutive non-matches required before the button is considered released again

For the 2026 controller, `STEAM` uses exact short event reports:

- `BUTTON_STEAM_PRESS_HEX=440402000000`
- `BUTTON_STEAM_RELEASE_HEX=440302000000`

### Optional fallback matcher

These are not required for the 2026 Steam button path. They exist as a fallback for other controller/report situations:

- `BUTTON_MATCH_HEX`
- `BUTTON_MATCH_MASK_HEX`
- `BUTTON_MATCH_OFFSET`

If unset, the Steam listener operates only on the short Steam-button event packets.

### Debug tracing

- `BUTTON_TRACE_MATCHES=true`: Emit listener trace logs
- `BUTTON_TRACE_LIMIT=200`: Cap the number of trace lines
- `BUTTON_TRACE_WINDOW_ONLY=true|false`: Reduce repeated trace noise by only logging changes in the match window state
- `BUTTON_DEBUG_MAX_REPORTS=0`: Maximum number of debug reports to emit when running the debug command directly; `0` means unlimited

## Logs and Verification

Follow the main listener:

```bash
journalctl -u lg-tv-control-steam-button.service -f
```

Follow the debug service:

```bash
journalctl -u lg-tv-control-debug.service -f
```

Check the resume service:

```bash
journalctl -u lg-tv-control-resume.service -f
```

Useful status commands:

```bash
systemctl status lg-tv-control-steam-button.service
systemctl status lg-tv-control-resume.service
systemctl status lg-tv-control-debug.service
```

## Development

### Python environment

This project uses Python 3.12 and `uv`.

Install dependencies locally:

```bash
uv sync
```

Run the script directly:

```bash
uv run python lg_tv.py --help
```

Run the debug command locally:

```bash
uv run python lg_tv.py debug-hid-button
```

Run the listener locally:

```bash
uv run python lg_tv.py listen-hid-button --input HDMI_4
```

### Build the release binary

The repo builds a single-file Linux binary with PyInstaller inside Docker:

```bash
./compile-for-linux.sh
```

That script:

- Builds the Docker image defined in `Dockerfile`
- Runs PyInstaller inside the container
- Copies the result to `./dist/lg-tv-control-linux-x86_64`

### Pairing behavior

On first successful webOS connection, the tool may require confirmation on the TV. The client key is stored in the pairing file so later runs can reconnect without prompting again.

### Design notes

- The Steam button listener is raw HID based, not `evdev` based
- The 2026 controller path uses the documented controller/report layout from the `decktation` `steam-controller-2026` branch as a basis for device/interface handling
- The Steam button itself is currently matched through observed short event reports rather than a published named bit mapping

## Troubleshooting

If the TV does not wake:

- Confirm `TV_IP` and `TV_MAC`
- Confirm Wake-on-LAN is enabled on the TV
- Check that the TV is reachable on the local network after wake

If the listener does not react:

- Confirm `BUTTON_DEVICE_ID` matches the controller
- Run the debug service or `debug-hid-button` directly
- Check the journal for the resolved hidraw node and loaded config

If the service triggers but no TV action happens:

- Check `BUTTON_MATCH_STREAK` and `BUTTON_DEBOUNCE_SECONDS`
- Check for `Another lg-tv-control action is already running. Skipping.`
- Check whether the TV was already on the target input
