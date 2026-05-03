#!/usr/bin/env python3
"""
wire_brake.py — Wire EDM brake controller for the Pi 3B+ + pi3hat r4.5.

System architecture
-------------------
    Pi 5 (LinuxCNC, runs brake_console.py for the operator)
        |
        | TCP/IP over ethernet (port 5005, conversation in plain text)
        |
    Pi 3B+ (this script — runs as a systemd service)
        |
        | pi3hat r4.5 -> CAN bus 1 (JC1 port)
        |
    moteus r4.11 (electric brake)

The wire is pulled by a separate motor, driven by the Mesa motion
controller on the Pi 5 / LinuxCNC side.  The moteus on this Pi acts
purely as a damping brake: velocity = 0 with kd_scale = computed Kd, so
the moteus produces a braking torque proportional to motion.

Conversation model
------------------
This script owns the UI text.  It sends prompts and status messages over
the TCP socket; the operator (via brake_console.py on the Pi 5) sees
those prompts and types replies.  No machine codes.  The state machine
walks the operator through:

    1. ASK_V     "What velocity do you want the wire to be pulled at..."
    2. ASK_T     "What tension do you want the wire to be held at..."
    3. ENGAGED   brake is held with computed Kd; type 'stop' to release.
    4. ASK_RESUME on stop or fault: "Resume with previous values? (y/n)"
       -> y : back into ENGAGED with the same Kd
       -> n : back to ASK_V (start over)

Out-of-range or unparseable answers are rejected with a friendly note
and the same prompt is re-issued.  Single line of input '?' anywhere
re-displays the current prompt (the brake_console sends '?' on connect
so the operator's screen always shows the live prompt, no matter when
the Pi 5 boots relative to the Pi 3).

If the TCP client disconnects mid-session, the brake is safed and the
service waits for the next client.  Designed to run as a systemd service,
never exits voluntarily.

Usage
-----
    # Production: TCP server on all interfaces, port 5005 (default)
    sudo /home/admin/moteus/venv/bin/python wire_brake.py

    # Custom port
    sudo /home/admin/moteus/venv/bin/python wire_brake.py --port 5010

    # Bench test with no Pi 5 — converse on stdin/stdout
    sudo /home/admin/moteus/venv/bin/python wire_brake.py --sim
"""

import argparse
import asyncio
import math
import sys

import moteus
import moteus_pi3hat


# ---------- Hardware constants ------------------------------------------------
MOTEUS_ID        = 1
MOTEUS_BUS       = 1      # JC1 on the pi3hat
MAX_TORQUE_NM    = 5.0
CONTROL_PERIOD_S = 0.05   # re-issue brake cmd every 50 ms (watchdog ~100 ms)
FAULT_POLL_EVERY = 20     # query moteus state every N control cycles (~1 s)

# ---------- TCP defaults ------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"   # listen on all interfaces
DEFAULT_PORT = 5005

# ---------- Calibrated input ranges -------------------------------------------
V_MIN, V_MAX = 5.0, 15.0   # wire velocity, m/min
T_MIN, T_MAX = 0.2, 3.0    # wire tension,  kgf

# ---------- Daemon retry on hard error ----------------------------------------
RETRY_DELAY_S = 2.0


# ---------- Kd equation -------------------------------------------------------
def calculate_kd(v_m_per_min: float, T_kgf: float) -> float:
    """Kd = 0.560 - 0.218 v + 2.246 T - 0.107 v T + 0.0126 v^2 - 0.122 T^2"""
    if not (V_MIN <= v_m_per_min <= V_MAX):
        raise ValueError(
            f"velocity {v_m_per_min} m/min is outside [{V_MIN}..{V_MAX}]."
        )
    if not (T_MIN <= T_kgf <= T_MAX):
        raise ValueError(
            f"tension {T_kgf} kgf is outside [{T_MIN}..{T_MAX}]."
        )
    kd = (
        0.560
        - 0.218 * v_m_per_min
        + 2.246 * T_kgf
        - 0.107 * v_m_per_min * T_kgf
        + 0.0126 * v_m_per_min ** 2
        - 0.122 * T_kgf ** 2
    )
    if kd <= 0:
        raise ValueError(
            f"Kd={kd:.3f} for v={v_m_per_min},T={T_kgf} is non-positive (unstable)."
        )
    return kd


# ---------- Channel abstraction (TCP vs stdin/stdout) -------------------------
class Channel:
    """Send/receive lines of plain text.  send() is synchronous; get_line()
    is async and yields the next received line, blocking until one arrives.
    On a broken connection get_line() raises ConnectionResetError."""

    def send(self, msg: str) -> None:
        raise NotImplementedError

    async def get_line(self) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class TCPChannel(Channel):
    """Wraps an asyncio TCP connection (one client) with line buffering."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
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
        except asyncio.IncompleteReadError as e:
            # peer closed the connection
            raise ConnectionResetError("client disconnected") from e
        except (asyncio.LimitOverrunError, ConnectionResetError):
            raise
        return data.decode("utf-8", errors="replace").rstrip("\r\n")

    def close(self):
        try:
            self.writer.close()
        except Exception:
            pass


class StdinChannel(Channel):
    """Stdin/stdout substitute for bench testing without the Pi 5."""

    def __init__(self):
        self._loop = asyncio.get_event_loop()

    def send(self, msg: str):
        print(msg, flush=True)

    async def get_line(self) -> str:
        line = await self._loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            raise ConnectionResetError("stdin closed")
        return line.rstrip("\r\n")


# ---------- Brake control wrapper around moteus + pi3hat ----------------------
class Brake:
    def __init__(self):
        self.transport = moteus_pi3hat.Pi3HatRouter(
            servo_bus_map={MOTEUS_BUS: [MOTEUS_ID]}
        )
        self.controller = moteus.Controller(id=MOTEUS_ID, transport=self.transport)

    async def initialize(self):
        await self.controller.set_stop()
        await asyncio.wait_for(self.controller.query(), timeout=1.0)

    async def engage(self, kd: float):
        await self.controller.set_position(
            position=math.nan,
            velocity=0.0,
            kp_scale=0.0,
            kd_scale=float(kd),
            maximum_torque=MAX_TORQUE_NM,
            query=False,
        )

    async def query(self):
        return await self.controller.query()

    async def stop(self):
        try:
            await self.controller.set_stop()
        except Exception:
            pass


# ---------- Conversation helpers ----------------------------------------------
def _greet(channel: Channel):
    channel.send("")
    channel.send("==============================================")
    channel.send("  Wire EDM Brake Controller")
    channel.send("==============================================")
    channel.send("")


VELOCITY_PROMPT = (
    "What velocity do you want the wire to be pulled at (m/min)?\r\n"
    f"Pick a value between {V_MIN}-{V_MAX}: "
)
TENSION_PROMPT = (
    "What tension do you want the wire to be held at (kgf)?\r\n"
    f"Pick a value between {T_MIN}-{T_MAX}: "
)


async def _prompt_for_value(channel: Channel, prompt: str,
                            vmin: float, vmax: float) -> float:
    """Ask the operator for a number in [vmin, vmax].  Re-prompts on bad input
    or out-of-range values.  '?' or empty input re-displays the prompt."""
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


async def _prompt_yes_no(channel: Channel, prompt: str) -> bool:
    while True:
        channel.send(prompt)
        line = (await channel.get_line()).strip().lower()
        if line in ("y", "yes"):
            return True
        if line in ("n", "no"):
            return False
        channel.send("  Please type 'y' or 'n'.")


async def _hold_until_stop_or_fault(channel: Channel, brake: Brake, kd: float):
    """Re-issues the brake command on a 50 ms cadence, polls the moteus for
    faults every ~1 s, and watches the channel for 'stop'.  Returns:
        ("stop",  None)
        ("fault", "<description>")
        ("error", "<description>")
        ("disconnect", None)         -- TCP client went away mid-engaged
    """
    stop_event = asyncio.Event()
    result = ["stop", None]

    async def hold_task():
        i = 0
        while not stop_event.is_set():
            try:
                await brake.engage(kd)
            except Exception as e:
                result[0] = "error"
                result[1] = repr(e)
                stop_event.set()
                return
            i += 1
            if i % FAULT_POLL_EVERY == 0:
                try:
                    s = await brake.query()
                    fault = int(s.values[moteus.Register.FAULT])
                    if fault != 0:
                        result[0] = "fault"
                        result[1] = f"moteus fault code {fault}"
                        stop_event.set()
                        return
                except Exception as e:
                    result[0] = "error"
                    result[1] = repr(e)
                    stop_event.set()
                    return
            await asyncio.sleep(CONTROL_PERIOD_S)

    async def input_task():
        while not stop_event.is_set():
            try:
                line = (await channel.get_line()).strip().lower()
            except ConnectionResetError:
                result[0] = "disconnect"
                result[1] = None
                stop_event.set()
                return
            if line == "stop":
                result[0] = "stop"
                result[1] = None
                stop_event.set()
                return
            if line in ("?", ""):
                channel.send("  Brake is engaged.  Type 'stop' to disengage.")
            else:
                channel.send(f"  '{line}' ignored — type 'stop' to disengage.")

    h = asyncio.create_task(hold_task())
    i = asyncio.create_task(input_task())
    try:
        await stop_event.wait()
    finally:
        h.cancel()
        i.cancel()
        for t in (h, i):
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
    return tuple(result)


# ---------- Main conversation loop --------------------------------------------
async def session(channel: Channel, brake: Brake):
    _greet(channel)
    last_v = last_T = last_kd = None

    while True:  # outer: starts over after a 'no' at resume prompt
        v = await _prompt_for_value(channel, VELOCITY_PROMPT, V_MIN, V_MAX)
        T = await _prompt_for_value(channel, TENSION_PROMPT, T_MIN, T_MAX)
        try:
            kd = calculate_kd(v, T)
        except ValueError as e:
            channel.send(f"  Cannot compute Kd: {e}")
            continue
        last_v, last_T, last_kd = v, T, kd

        while True:  # ENGAGED + ASK_RESUME
            try:
                await brake.engage(kd)
            except Exception as e:
                channel.send(f"  Failed to engage brake: {e!r}")
                break

            channel.send("")
            channel.send(f"Brake engaged.  Kd = {kd:.4f}  (v={v} m/min, T={T} kgf)")
            channel.send("Type 'stop' to disengage when finished.")
            channel.send("")

            outcome, info = await _hold_until_stop_or_fault(channel, brake, kd)
            await brake.stop()

            if outcome == "disconnect":
                # Client went away.  No point asking for resume; let outer
                # service loop accept the next connection.
                raise ConnectionResetError("client disconnected mid-engaged")

            channel.send("")
            if outcome == "stop":
                channel.send("Brake disengaged.")
            elif outcome == "fault":
                channel.send(f"Brake stopped — {info}.")
            else:
                channel.send(f"Brake stopped — communication error: {info}.")
            channel.send("")

            resume = await _prompt_yes_no(
                channel,
                f"Resume with previous values (v={last_v}, T={last_T}, Kd={last_kd:.4f})? (y/n): "
            )
            if resume:
                kd = last_kd
                continue
            else:
                channel.send("")
                break  # back to outer -> ASK_V


# ---------- TCP server runner -------------------------------------------------
async def run_tcp(host: str, port: int):
    """Listen on host:port forever, handle one client at a time."""
    brake = Brake()
    print(f"[boot] connecting to moteus on JC1...", flush=True)
    await brake.initialize()
    print(f"[boot] moteus OK", flush=True)

    active_peer = [None]   # mutable holder so the inner closure can write it

    async def handle_client(reader, writer):
        peer = writer.get_extra_info("peername")
        if active_peer[0] is not None:
            print(f"[reject] {peer} -- already serving {active_peer[0]}", flush=True)
            try:
                writer.write(
                    f"\r\nBrake controller is already in use by {active_peer[0]}.\r\n"
                    f"Disconnect that session first.\r\n".encode("utf-8")
                )
                await writer.drain()
            except Exception:
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        active_peer[0] = peer
        print(f"[connect] {peer}", flush=True)
        channel = TCPChannel(reader, writer)
        try:
            await session(channel, brake)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            print(f"[disconnect] {peer}", flush=True)
        except Exception as e:
            print(f"[error during session for {peer}] {e!r}", flush=True)
        finally:
            await brake.stop()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            active_peer[0] = None
            print(f"[idle] waiting for next client", flush=True)

    server = await asyncio.start_server(handle_client, host=host, port=port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"[boot] listening on {addrs}", flush=True)
    async with server:
        await server.serve_forever()


# ---------- Outer daemon loop -------------------------------------------------
async def daemon(host: str, port: int):
    while True:
        try:
            await run_tcp(host, port)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[fatal] {e!r} -- restarting in {RETRY_DELAY_S}s", flush=True)
            await asyncio.sleep(RETRY_DELAY_S)


# ---------- Sim mode (bench test) --------------------------------------------
async def run_sim():
    channel = StdinChannel()
    brake = Brake()
    print("[boot] connecting to moteus on JC1...", flush=True)
    await brake.initialize()
    print("[boot] moteus OK", flush=True)
    try:
        await session(channel, brake)
    finally:
        await brake.stop()


# ---------- Entry point ------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Wire EDM brake controller (Pi 3B+ + pi3hat).")
    p.add_argument("--sim", action="store_true",
                   help="Skip TCP; converse on stdin/stdout (bench test).")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help=f"TCP bind address (default {DEFAULT_HOST}, all interfaces)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"TCP port (default {DEFAULT_PORT})")
    args = p.parse_args()

    try:
        if args.sim:
            asyncio.run(run_sim())
        else:
            asyncio.run(daemon(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[exit]")
        sys.exit(130)


if __name__ == "__main__":
    main()
