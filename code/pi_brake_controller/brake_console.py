#!/usr/bin/env python3
"""
brake_console.py — Operator-facing terminal for the Wire EDM brake.

Runs on the Raspberry Pi 5 (LinuxCNC machine).  Opens a TCP connection
to the brake controller running on the Pi 3B+, then relays text in both
directions.  Anything the Pi 3B+ sends comes out on the operator's
screen; anything the operator types goes back over the socket to the
Pi 3B+.

That is the entire UI.  All prompts, validation, error messages, and
the state machine live on the Pi 3B+, so a non-technical operator never
has to understand any "machine codes" — they just see questions and
type answers.

Auto-launches at desktop login via brake-console.desktop.

Usage
-----
    python3 brake_console.py                          # default host/port
    python3 brake_console.py wirebrakepi.local        # custom host
    python3 brake_console.py 10.0.0.42 5005           # host + port

Press Ctrl-C to send 'stop' to the brake controller and exit.
The script auto-reconnects if the connection drops.
"""

import socket
import sys
import threading
import time


DEFAULT_HOST = "10.10.10.20"   # RP3 brake controller, static IP on the switch
DEFAULT_PORT = 5005
RECONNECT_DELAY_S = 2.0


def reader_loop(sock: socket.socket, stop_flag: threading.Event):
    """Continuously read from the socket and write to stdout."""
    while not stop_flag.is_set():
        try:
            chunk = sock.recv(4096)
        except OSError:
            stop_flag.set()
            return
        if not chunk:
            stop_flag.set()
            return
        try:
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        except Exception:
            stop_flag.set()
            return


def connect_with_retries(host: str, port: int) -> socket.socket:
    """Block until a TCP connection succeeds.  Prints status while waiting."""
    notified = False
    while True:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.settimeout(None)
            return s
        except (socket.gaierror, OSError) as e:
            if not notified:
                print(f"\nWaiting for brake controller at {host}:{port} ...")
                print(f"  ({e})")
                notified = True
            time.sleep(RECONNECT_DELAY_S)


def session(host: str, port: int):
    """One TCP session.  Returns when the connection drops."""
    print(f"\nConnecting to {host}:{port} ...")
    sock = connect_with_retries(host, port)
    print(f"Connected.\n")

    # Nudge the brake controller to refresh its current prompt, in case
    # it was already running before we connected.
    try:
        sock.sendall(b"?\r\n")
    except Exception:
        pass

    stop_flag = threading.Event()
    t = threading.Thread(target=reader_loop, args=(sock, stop_flag), daemon=True)
    t.start()

    try:
        while not stop_flag.is_set():
            try:
                line = sys.stdin.readline()
            except KeyboardInterrupt:
                # Ctrl-C: tell the brake to stop, then exit.
                try:
                    sock.sendall(b"stop\r\n")
                    time.sleep(0.2)
                except Exception:
                    pass
                raise
            if not line:
                # EOF on stdin (terminal closed)
                break
            try:
                sock.sendall(line.encode("utf-8", errors="replace"))
            except OSError:
                break
    finally:
        stop_flag.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def main(argv):
    host = argv[1] if len(argv) >= 2 else DEFAULT_HOST
    port = int(argv[2]) if len(argv) >= 3 else DEFAULT_PORT

    print("==============================================")
    print("  Wire EDM Brake Console")
    print("==============================================")

    try:
        while True:
            try:
                session(host, port)
            except KeyboardInterrupt:
                print("\n[interrupted, sent stop]")
                return 0
            print(f"\n*** Connection lost.  Reconnecting in {RECONNECT_DELAY_S}s.")
            time.sleep(RECONNECT_DELAY_S)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
