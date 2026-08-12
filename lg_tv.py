#!/usr/bin/env python3

import json
import os
import socket
import time

from wakeonlan import wake
from pywebostv.connection import WebOSClient
from pywebostv.controls import SourceControl


TV_IP = "192.168.1.177"
TV_MAC = "d8:e3:5e:52:e5:24"
TARGET_INPUT = "HDMI_4"

PAIRING_FILE = os.path.expanduser("~/.lg_webos_key.json")


def load_store():
    if os.path.exists(PAIRING_FILE):
        with open(PAIRING_FILE, "r") as f:
            return json.load(f)

    return {}


def save_store(store):
    with open(PAIRING_FILE, "w") as f:
        json.dump(store, f)


def wait_for_tv(host, port=3001, timeout=20):
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


def main():
    # Wake the TV
    print(f"Waking TV at {TV_MAC}...")
    wake(TV_MAC)

    # Wait for webOS to become available
    if not wait_for_tv(TV_IP):
        raise RuntimeError("TV did not become reachable.")

    print("Waiting for webOS services to finish waking...")
    time.sleep(7)
    # Load previously saved webOS pairing credentials
    store = load_store()

    # Connect to the TV.
    # LG C3/webOS uses the secure WebSocket interface.
    print("Creating webOS client...")
    client = WebOSClient(TV_IP, secure=True)

    print("Connecting...")
    client.connect()
    print("Connected socket.")


    print("Registering...")
    for status in client.register(store):
        print(f"Registration status: {status}")

        if status == WebOSClient.PROMPTED:
            print("Accept the connection prompt on the TV.")

        elif status == WebOSClient.REGISTERED:
            print("Registered with TV.")

    print("Registration complete.")

    # Save the client key so we don't need to pair every time.
    save_store(store)

    source_control = SourceControl(client)

    # Ask the TV what inputs actually exist.
    sources = source_control.list_sources()

    print("Available inputs:")

    for source in sources:
        print(
                f"  {source.data['id']} "
                f"({source.data.get('label', '')})"
                )

    # Find HDMI 4.
    target = next(
            (
                source
                for source in sources
                if source.data.get("id") == TARGET_INPUT
                ),
            None,
            )

    if target is None:
        raise RuntimeError(
                f"{TARGET_INPUT} wasn't found in the TV's source list."
                )

    print(f"Switching to {TARGET_INPUT}...")
    source_control.set_source(target)

    print("Done.")


if __name__ == "__main__":
    main()
