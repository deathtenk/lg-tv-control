#!/usr/bin/env python3

import argparse
import binascii
import fcntl
import json
import os
import selectors
import socket
import sys
import time
from pathlib import Path

from wakeonlan import wake
from pywebostv.connection import WebOSClient
from pywebostv.controls import ApplicationControl, SourceControl


TV_REACHABLE_TIMEOUT = 20
WEBOS_CONNECT_TIMEOUT = 30
WEBOS_RETRY_DELAY = 2
DEFAULT_DEBOUNCE_SECONDS = 3.0
DEFAULT_LOCK_FILE = "/tmp/lg-tv-control.lock"
DEFAULT_HID_REPORT_SIZE = 64
DEFAULT_DEBUG_MAX_REPORTS = 0


def configure_stdio():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)


def require_env(name):
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set."
        )

    return value


def get_pairing_file():
    return os.environ.get(
        "PAIRING_FILE",
        os.path.expanduser("~/.lg_webos_key.json"),
    )


def parse_hex_bytes(value):
    normalized = "".join(value.split())

    if len(normalized) % 2 != 0:
        raise ValueError("Hex strings must contain an even number of digits.")

    return binascii.unhexlify(normalized)


def read_env_float(name, default):
    value = os.environ.get(name)

    if value is None:
        return default

    return float(value)


def read_env_int(name, default):
    value = os.environ.get(name)

    if value is None:
        return default

    return int(value)


def parse_hid_id(value):
    parts = [part.strip().lower() for part in value.split(":")]

    if len(parts) == 3:
        bus, vendor, product = parts
        return (
            int(bus, 16),
            int(vendor, 16),
            int(product, 16),
        )

    if len(parts) == 2:
        vendor, product = parts
        return (None, int(vendor, 16), int(product, 16))

    raise ValueError(
        "HID device IDs must be BUS:VENDOR:PRODUCT or VENDOR:PRODUCT."
    )


def build_hid_id_candidates(value):
    bus, vendor, product = parse_hid_id(value)
    candidates = {
        f"{vendor:04x}:{product:04x}",
        f"{vendor:08x}:{product:08x}",
    }

    if bus is not None:
        candidates.add(f"{bus:04x}:{vendor:04x}:{product:04x}")
        candidates.add(f"{bus:04x}:{vendor:08x}:{product:08x}")

    return candidates


def read_sysfs_uevent(path):
    data = {}

    for line in path.read_text().splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        data[key] = value

    return data


def hid_id_candidates_from_uevent(uevent):
    hid_id = uevent.get("HID_ID")

    if not hid_id:
        return set()

    return build_hid_id_candidates(hid_id)


def resolve_button_devices():
    explicit_device = os.environ.get("BUTTON_DEVICE")

    if explicit_device:
        return [{"devnode": explicit_device, "hid_id": "", "name": ""}]

    button_device_id = require_env("BUTTON_DEVICE_ID")
    target_candidates = build_hid_id_candidates(button_device_id)
    matches = []

    for hidraw_path in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        uevent_path = hidraw_path / "device" / "uevent"

        if not uevent_path.exists():
            continue

        uevent = read_sysfs_uevent(uevent_path)
        current_candidates = hid_id_candidates_from_uevent(uevent)

        if not target_candidates.intersection(current_candidates):
            continue

        devnode = f"/dev/{hidraw_path.name}"
        matches.append(
            {
                "devnode": devnode,
                "hid_id": uevent.get("HID_ID", ""),
                "name": uevent.get("HID_NAME", ""),
            }
        )

    if not matches:
        raise RuntimeError(
            f"Could not find a hidraw device for BUTTON_DEVICE_ID="
            f"{button_device_id}."
        )

    if len(matches) > 1:
        match_list = ", ".join(
            f"{match['devnode']} ({match['hid_id']} {match['name']})"
            for match in matches
        )
        print(
            f"BUTTON_DEVICE_ID={button_device_id} matched multiple "
            f"hidraw devices. Listening on all of them: {match_list}"
        )
    else:
        match = matches[0]
        print(
            f"Resolved BUTTON_DEVICE_ID={button_device_id} to "
            f"{match['devnode']} ({match['hid_id']} {match['name']})"
        )

    return matches


def load_button_config():
    devices = resolve_button_devices()
    match = parse_hex_bytes(require_env("BUTTON_MATCH_HEX"))
    mask_hex = os.environ.get("BUTTON_MATCH_MASK_HEX")
    offset = read_env_int("BUTTON_MATCH_OFFSET", 0)
    debounce_seconds = read_env_float(
        "BUTTON_DEBOUNCE_SECONDS",
        DEFAULT_DEBOUNCE_SECONDS,
    )
    report_size = read_env_int(
        "BUTTON_REPORT_SIZE",
        DEFAULT_HID_REPORT_SIZE,
    )

    if mask_hex:
        mask = parse_hex_bytes(mask_hex)
    else:
        mask = b"\xff" * len(match)

    if len(mask) != len(match):
        raise RuntimeError(
            "BUTTON_MATCH_MASK_HEX must be the same length as "
            "BUTTON_MATCH_HEX."
        )

    return {
        "devices": devices,
        "match": match,
        "mask": mask,
        "offset": offset,
        "debounce_seconds": debounce_seconds,
        "report_size": report_size,
    }


def load_store():
    pairing_file = get_pairing_file()

    if os.path.exists(pairing_file):
        with open(pairing_file, "r") as f:
            return json.load(f)

    return {}


def save_store(store):
    pairing_file = get_pairing_file()

    os.makedirs(os.path.dirname(pairing_file), exist_ok=True)

    with open(pairing_file, "w") as f:
        json.dump(store, f)


def wait_for_tv(host, port=3001, timeout=TV_REACHABLE_TIMEOUT):
    """
    Wait until the TV's secure webOS TCP port begins accepting connections.

    This only tells us that the TV's network stack is awake. It does not
    guarantee that webOS is ready to accept commands yet.
    """
    print("Waiting for TV webOS service...")

    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                print("TV is responding.")
                return True
        except OSError:
            time.sleep(1)

    return False


def connect_webos(
    tv_ip,
    store,
    timeout=WEBOS_CONNECT_TIMEOUT,
    retry_delay=WEBOS_RETRY_DELAY,
    register_timeout=3,
):
    """
    Retry complete webOS sessions until registration succeeds.

    Each registration attempt is intentionally short so a half-awake TV
    cannot consume the entire overall retry budget.
    """
    deadline = time.time() + timeout
    attempt = 1
    last_error = None

    while time.time() < deadline:
        client = None

        try:
            print(f"Connecting to webOS (attempt {attempt})...")

            client = WebOSClient(tv_ip, secure=True)
            client.connect()

            print("WebSocket connected. Registering...")

            registered = False

            for status in client.register(
                store,
                timeout=register_timeout,
            ):
                if status == WebOSClient.PROMPTED:
                    print("Accept the connection prompt on the TV.")

                elif status == WebOSClient.REGISTERED:
                    print("Registered with TV.")
                    registered = True
                    break

            if registered:
                return client

            raise RuntimeError(
                "Registration ended without reaching REGISTERED state."
            )

        except Exception as e:
            last_error = e
            print(f"webOS not ready yet: {e}")

            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

            remaining = deadline - time.time()

            if remaining <= 0:
                break

            time.sleep(min(retry_delay, remaining))
            attempt += 1

    raise RuntimeError(
        f"Could not establish a usable webOS session within "
        f"{timeout} seconds. Last error: {last_error}"
    )


def switch_to_input(client, target_input):
    source_control = SourceControl(client)
    application_control = ApplicationControl(client)
    sources = source_control.list_sources()

    print("Available inputs:")

    for source in sources:
        source_id = source.data.get("id")
        label = source.data.get("label", "")
        print(f"  {source_id} ({label})")

    target = next(
        (
            source
            for source in sources
            if source.data.get("id") == target_input
        ),
        None,
    )

    if target is None:
        raise RuntimeError(
            f"{target_input} wasn't found in the TV's source list."
        )

    current_app_id = None

    try:
        current_app_id = application_control.get_current()
        print(f"Current foreground app: {current_app_id}")
    except Exception as e:
        print(f"Could not determine current foreground app: {e}")

    target_app_ids = set()

    for key in ("appId", "launcherAppId"):
        value = target.data.get(key)

        if value:
            target_app_ids.add(value)

    normalized_target = target_input.lower().replace("_", "")

    if normalized_target.startswith("hdmi"):
        target_app_ids.add(f"com.webos.app.{normalized_target}")

    if current_app_id and current_app_id in target_app_ids:
        print(
            f"TV is already on {target_input} "
            f"({current_app_id}). Skipping input switch."
        )
        return

    print(f"Switching to {target_input}...")
    source_control.set_source(target)

    print("Done.")


def acquire_action_lock():
    lock_file = os.environ.get("ACTION_LOCK_FILE", DEFAULT_LOCK_FILE)
    lock_handle = open(lock_file, "w")

    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        return None

    return lock_handle


def run_tv_action(target_input):
    tv_ip = require_env("TV_IP")
    tv_mac = require_env("TV_MAC")

    lock_handle = acquire_action_lock()

    if lock_handle is None:
        print("Another lg-tv-control action is already running. Skipping.")
        return 0

    try:
        print(f"Waking TV at {tv_mac}...")
        wake(tv_mac)

        if not wait_for_tv(tv_ip):
            raise RuntimeError(
                f"TV did not become reachable within "
                f"{TV_REACHABLE_TIMEOUT} seconds."
            )

        store = load_store()

        client = connect_webos(tv_ip, store)

        try:
            # Registration can update the client key in the store.
            save_store(store)

            switch_to_input(client, target_input)

        finally:
            try:
                client.close()
            except Exception:
                pass

        return 0

    finally:
        lock_handle.close()


def report_matches(report, match, mask, offset):
    end = offset + len(match)

    if len(report) < end:
        return False

    window = report[offset:end]

    for report_byte, match_byte, mask_byte in zip(window, match, mask):
        if ((report_byte ^ match_byte) & mask_byte) != 0:
            return False

    return True


def format_match_window(report, offset, length):
    end = min(len(report), offset + length)
    return report[offset:end].hex()


def open_hid_devices(devices):
    selector = selectors.DefaultSelector()
    handles = []

    for device in devices:
        fd = os.open(device["devnode"], os.O_RDONLY)
        selector.register(fd, selectors.EVENT_READ, data=device)
        handles.append(fd)

    return selector, handles


def close_hid_devices(selector, handles):
    for fd in handles:
        try:
            selector.unregister(fd)
        except Exception:
            pass

        try:
            os.close(fd)
        except Exception:
            pass


def listen_for_hid_button(target_input):
    config = load_button_config()
    devices = config["devices"]
    match = config["match"]
    mask = config["mask"]
    offset = config["offset"]
    debounce_seconds = config["debounce_seconds"]
    report_size = config["report_size"]

    device_list = ", ".join(device["devnode"] for device in devices)
    print(f"Listening for button reports on {device_list}...")

    selector, handles = open_hid_devices(devices)
    last_trigger_at = 0.0
    button_is_down = False

    try:
        while True:
            events = selector.select(timeout=0.5)

            if not events:
                continue

            for key, _ in events:
                report = os.read(key.fd, report_size)

                if not report:
                    continue

                matched = report_matches(report, match, mask, offset)

                if matched and not button_is_down:
                    now = time.time()

                    if now - last_trigger_at >= debounce_seconds:
                        print(
                            "Matched button press on "
                            f"{key.data['devnode']}. Triggering TV action."
                        )
                        run_tv_action(target_input)
                        last_trigger_at = now

                    button_is_down = True

                elif not matched:
                    button_is_down = False

    finally:
        close_hid_devices(selector, handles)


def debug_hid_button(max_reports):
    config = load_button_config()
    devices = config["devices"]
    match = config["match"]
    mask = config["mask"]
    offset = config["offset"]
    report_size = config["report_size"]

    device_list = ", ".join(device["devnode"] for device in devices)
    print(f"Debugging button reports on {device_list}...")
    print(f"report_size={report_size}")
    print(f"match_offset={offset}")
    print(f"match_hex={match.hex()}")
    print(f"match_mask={mask.hex()}")

    selector, handles = open_hid_devices(devices)
    previous_reports = {}
    seen = 0

    try:
        while max_reports <= 0 or seen < max_reports:
            events = selector.select(timeout=0.5)

            if not events:
                continue

            for key, _ in events:
                report = os.read(key.fd, report_size)

                if not report:
                    continue

                matched = report_matches(report, match, mask, offset)
                previous = previous_reports.get(key.fd)

                if previous != (report, matched):
                    status = "MATCH" if matched else "no-match"
                    window = format_match_window(report, offset, len(match))
                    print(
                        f"{status} device={key.data['devnode']} "
                        f"report={report.hex()} window={window}"
                    )
                    previous_reports[key.fd] = (report, matched)
                    seen += 1

                    if max_reports > 0 and seen >= max_reports:
                        break

    finally:
        close_hid_devices(selector, handles)


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--input",
        dest="target_input",
        default=os.environ.get("TARGET_INPUT"),
        help="TV input ID to switch to after wake.",
    )

    listen_parser = subparsers.add_parser("listen-hid-button")
    listen_parser.add_argument(
        "--input",
        dest="target_input",
        default=os.environ.get("TARGET_INPUT"),
        help="TV input ID to switch to after a matched HID report.",
    )

    debug_parser = subparsers.add_parser("debug-hid-button")
    debug_parser.add_argument(
        "--max-reports",
        type=int,
        default=read_env_int(
            "BUTTON_DEBUG_MAX_REPORTS",
            DEFAULT_DEBUG_MAX_REPORTS,
        ),
        help="Stop after printing this many changed reports. 0 means no limit.",
    )

    return parser.parse_args()


def main():
    configure_stdio()

    args = parse_args()
    command = args.command or "run"

    if command == "debug-hid-button":
        debug_hid_button(args.max_reports)
        return 0

    target_input = getattr(
        args,
        "target_input",
        os.environ.get("TARGET_INPUT"),
    )

    if not target_input:
        raise RuntimeError(
            "A target input is required via --input or TARGET_INPUT."
        )

    if command == "listen-hid-button":
        listen_for_hid_button(target_input)
        return 0

    return run_tv_action(target_input)


if __name__ == "__main__":
    sys.exit(main())
