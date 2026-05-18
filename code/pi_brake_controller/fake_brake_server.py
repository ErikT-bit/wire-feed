#!/usr/bin/env python3
"""
fake_brake_server.py — Stand-in for the real wire_brake.py daemon.

Speaks the exact same conversational protocol over TCP as the real
Pi 3B+ brake controller, but without importing moteus or moteus_pi3hat
and without actually commanding any motor.  Use it to test
brake_console.py and exercise the full operator UX from a machine that
doesn't have the brake hardware.

Run on any machine (Pi 5, your laptop, etc.):

    python3 fake_brake_server.py                # listens on 0.0.0.0:5005
    python3 fake_brake_server.py 127.0.0.1 5005 # custom bind

Then in another terminal on the same machine:

    python3 brake_console.py 127.0.0.1 5005

You'll see the greeting, the velocity prompt, the tension prompt, the
"brake engaged" line with the computed Kd, then the resume prompt after
'stop'.  No hardware required.
"""

import argparse
import asyncio
import sys


# ---------- Calibrated input ranges (must match wire_brake.py) ----------------
V_MIN, V_MAX = 5.0, 15.0
T_MIN, T_MAX = 0.2, 3.0


def calculate_kd(v: float, T: float) -> float:
    if not (V_MIN <= v <= V_MAX):
        raise ValueError(f"velocity {v} m/min is outside [{V_MIN}..{V_MAX}].")
    if not (T_MIN <= T <= T_MAX):
        raise ValueError(f"tension {T} kgf is outside [{T_MIN}..{T_MAX}].")
    kd = (
        0.560
        - 0.218 * v
        + 2.246 * T
        - 0.107 * v * T
        + 0.0126 * v ** 2
        - 0.122 * T ** 2
    )
    if kd <= 0:
        raise ValueError(f"Kd={kd:.3f} non-positive (unstable).")
    return kd


VELOCITY_PROMPT = (
    "What velocity do you want the wire to be pulled at (m/min)?\r\n"
    f"Pick a value between {V_MIN}-{V_MAX}: "
)
TENSION_PROMPT = (
    "What tension do you want the wire to be held at (kgf)?\r\n"
    f"Pick a value between {T_MIN}-{T_MAX}: "
)


# ---------- Channel wrapper around an asyncio TCP connection -----------------
class TCPChannel:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    def send(self, msg: str):
        try:
            self.writer.write((msg + "\r\n").encode("utf-8", errors="replace"))
        except Exception:
            pass

    async def get_line(self) -> str:
        try:
            data = await self.reader.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            raise ConnectionResetError("client disconnected")
        return data.decode("utf-8", errors="replace").rstrip("\r\n")


# ---------- Fake brake (no hardware) -----------------------------------------
class FakeBrake:
    """Pretends to engage and stop a motor.  Logs what it would do."""

    async def initialize(self):
        print("[fake-brake] initialized (no hardware)", flush=True)

    async def engage(self, kd: float):
        print(f"[fake-brake] engage  kd={kd:.4f}", flush=True)

    async def stop(self):
        print("[fake-brake] stop", flush=True)


# ---------- Conversation helpers (same wording as wire_brake.py) -------------
def _greet(channel):
    channel.send("")
    channel.send("==============================================")
    channel.send("  Wire EDM Brake Controller   [FAKE BRAKE]")
    channel.send("==============================================")
    channel.send("")


async def _prompt_for_value(channel, prompt, vmin, vmax):
    while True:
        channel.send(prompt)
        line = (await channel.get_line()).strip()
        if line in ("", "?"):
            continue
        try:
            x = float(line)
        except ValueError:
            channel.send(f"  '{line}' is not a number.  Please try again.")
            continue
        if not (vmin <= x <= vmax):
            channel.send(f"  {x} is out of range.  Must be {vmin} to {vmax}.")
            continue
        return x


async def _prompt_yes_no(channel, prompt):
    while True:
        channel.send(prompt)
        line = (await channel.get_line()).strip().lower()
        if line in ("y", "yes"):
            return True
        if line in ("n", "no"):
            return False
        channel.send("  Please type 'y' or 'n'.")


async def _hold_until_stop(channel):
    """Just wait for 'stop' from the operator (no fault polling needed
    in the fake version — there's no motor)."""
    while True:
        try:
            line = (await channel.get_line()).strip().lower()
        except ConnectionResetError:
            return "disconnect"
        if line == "stop":
            return "stop"
        if line in ("?", ""):
            channel.send("  Brake is engaged.  Type 'stop' to disengage.")
        else:
            channel.send(f"  '{line}' ignored — type 'stop' to disengage.")


# ---------- Main conversation -----------------------------------------------
async def session(channel, brake):
    _greet(channel)
    last_v = last_T = last_kd = None

    while True:  # outer loop: starts over after 'no' at resume prompt
        v = await _prompt_for_value(channel, VELOCITY_PROMPT, V_MIN, V_MAX)
        T = await _prompt_for_value(channel, TENSION_PROMPT, T_MIN, T_MAX)
        try:
            kd = calculate_kd(v, T)
        except ValueError as e:
            channel.send(f"  Cannot compute Kd: {e}")
            continue
        last_v, last_T, last_kd = v, T, kd

        while True:  # ENGAGED + ASK_RESUME loop
            await brake.engage(kd)
            channel.send("")
            channel.send(f"Brake engaged.  Kd = {kd:.4f}  (v={v} m/min, T={T} kgf)")
            channel.send("Type 'stop' to disengage when finished.")
            channel.send("")

            outcome = await _hold_until_stop(channel)
            await brake.stop()

            if outcome == "disconnect":
                raise ConnectionResetError("client disconnected")

            channel.send("")
            channel.send("Brake disengaged.")
            channel.send("")

            resume = await _prompt_yes_no(
                channel,
                f"Resume with previous values (v={last_v}, T={last_T}, Kd={last_kd:.4f})? (y/n): "
            )
            if resume:
                kd = last_kd
                continue
            channel.send("")
            break  # back to outer -> ASK_V


# ---------- Server runner ----------------------------------------------------
async def run(host, port):
    brake = FakeBrake()
    await brake.initialize()
    active = [None]

    async def handle_client(reader, writer):
        peer = writer.get_extra_info("peername")
        if active[0] is not None:
            try:
                writer.write(
                    f"\r\nFake brake controller is busy serving {active[0]}.\r\n"
                    .encode("utf-8")
                )
                await writer.drain()
                writer.close()
            except Exception:
                pass
            return
        active[0] = peer
        print(f"[connect] {peer}", flush=True)
        try:
            await session(TCPChannel(reader, writer), brake)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            print(f"[error] {e!r}", flush=True)
        finally:
            await brake.stop()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            active[0] = None
            print(f"[disconnect] {peer} -- waiting for next client", flush=True)

    server = await asyncio.start_server(handle_client, host=host, port=port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"[boot] fake brake server listening on {addrs}", flush=True)
    print(f"[boot] connect with:  python3 brake_console.py {host} {port}", flush=True)
    async with server:
        await server.serve_forever()


def main():
    p = argparse.ArgumentParser(description="Fake brake server (no hardware).")
    p.add_argument("host", nargs="?", default="0.0.0.0",
                   help="bind address (default 0.0.0.0)")
    p.add_argument("port", nargs="?", type=int, default=5005,
                   help="TCP port (default 5005)")
    args = p.parse_args()
    try:
        asyncio.run(run(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[exit]")
        sys.exit(0)


if __name__ == "__main__":
    main()
