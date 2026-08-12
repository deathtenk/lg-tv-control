#!/usr/bin/env python3

import json
import os
import socket
import time

from wakeonlan import wake
from pywebostv.connection import WebOSClient
from pywebostv.controls import SourceControl


def require_env(name):
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set."
        )

    return value


TV_IP = require_env("TV_IP")
TV_MAC = require_env("TV_MAC")
TARGET_INPUT = require_env("TARGET_INPUT")

PAIRING_FILE = os.path.expanduser("~/.lg_webos_key.json")

TV_REACHABLE_TIMEOUT = 20
WEBOS_CONNECT_TIMEOUT = 30
WEBOS_RETRY_DELAY = 2


def load_store():
    if os.path.exists(PAIRING_FILE):
        with open(PAIRING_FILE, "r") as f:
            return json.load(f)

    return {}


def save_store(store):
    with open(PAIRING_FILE, "w") as f:
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

            client = WebOSClient(TV_IP, secure=True)
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

    print(f"Switching to {target_input}...")
    source_control.set_source(target)

    print("Done.")


def main():
    print(f"Waking TV at {TV_MAC}...")
    wake(TV_MAC)

    if not wait_for_tv(TV_IP):
        raise RuntimeError(
            f"TV did not become reachable within "
            f"{TV_REACHABLE_TIMEOUT} seconds."
        )

    store = load_store()

    client = connect_webos(store)

    try:
        # Registration can update the client key in the store.
        save_store(store)

        switch_to_input(client, TARGET_INPUT)

    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
