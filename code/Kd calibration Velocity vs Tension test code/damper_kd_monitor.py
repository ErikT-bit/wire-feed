#!/usr/bin/env python3
import asyncio
import math
import argparse
import time
import threading
from collections import deque

import moteus


def _stdin_stop_listener(stop_flag: threading.Event):
    """
    Wait for user input in a background thread.
    Type 's' then Enter to stop.
    """
    try:
        while not stop_flag.is_set():
            s = input().strip().lower()
            if s in ("s", "stop", "q", "quit", "exit"):
                stop_flag.set()
                return
    except EOFError:
        stop_flag.set()


def _start_plot_thread(hist: deque, stop_flag: threading.Event, title: str):
    """
    Simple torque-vs-time plot in a separate thread.
    Requires: matplotlib
    """
    def _plotter():
        try:
            import matplotlib.pyplot as plt
        except Exception as e:
            print("\n[plot] matplotlib not available.")
            print("Install with:  py -m pip install matplotlib")
            print(f"[plot] Error: {e}\n")
            return

        plt.ion()
        fig, ax = plt.subplots()
        try:
            fig.canvas.manager.set_window_title(title)
        except Exception:
            pass

        (line,) = ax.plot([], [])
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Torque (Nm)")
        ax.set_title(title)
        ax.grid(True)

        t0 = None

        while not stop_flag.is_set():
            if len(hist) >= 2:
                xs = [p[0] for p in hist]
                ys = [p[1] for p in hist]
                if t0 is None:
                    t0 = xs[0]
                xs = [x - t0 for x in xs]

                line.set_data(xs, ys)
                ax.relim()
                ax.autoscale_view()

                fig.canvas.draw()
                fig.canvas.flush_events()

            time.sleep(0.05)

        try:
            plt.ioff()
            plt.close(fig)
        except Exception:
            pass

    th = threading.Thread(target=_plotter, daemon=True)
    th.start()
    return th


async def hard_stop(c: moteus.Controller):
    """
    Your proven stop method, baked into the script.
    We try multiple times to survive bus collisions.
    """
    for _ in range(3):
        try:
            await c.set_stop()
        except Exception:
            pass
        await asyncio.sleep(0.05)


def _extract_torque_nm(res):
    """
    Try to pull a measured torque value from a moteus query response.
    Different versions expose this differently, so we try a few patterns.
    Returns: (tau_nm or None, keys_seen or None)
    """
    keys_seen = None
    tau = None

    try:
        if hasattr(res, "values") and isinstance(res.values, dict):
            keys_seen = list(res.values.keys())

            # Try common register enums (depending on moteus python version)
            for reg_name in ("TORQUE", "TORQUE_NM"):
                reg = getattr(moteus.Register, reg_name, None)
                if reg is not None and reg in res.values:
                    tau = res.values[reg]
                    return float(tau), keys_seen

            # Fallback string keys (some builds)
            for sk in ("torque", "torque_nm", "measured_torque", "q_torque"):
                if sk in res.values:
                    tau = res.values[sk]
                    return float(tau), keys_seen
    except Exception:
        pass

    return None, keys_seen


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--rate", type=float, default=200.0, help="Hz command rate")
    p.add_argument("--kd", type=float, default=0.20, help="damping gain scale (unitless)")
    p.add_argument("--tau_max", type=float, default=0.60, help="max torque (Nm)")
    p.add_argument("--watchdog", type=float, default=0.10, help="watchdog timeout (s) (default 0.10)")

    p.add_argument("--interactive", action="store_true", help="prompt to start; type 's' to stop")
    p.add_argument("--plot", action="store_true", help="show torque vs time plot")
    p.add_argument("--history", type=float, default=10.0, help="seconds of plot history")
    p.add_argument("--print_hz", type=float, default=5.0, help="print torque readout rate (Hz)")

    args = p.parse_args()

    dt = 1.0 / max(args.rate, 1.0)
    print_dt = 1.0 / max(args.print_hz, 0.1)

    c = moteus.Controller(id=args.id)

    # History buffer for plot
    maxlen = max(10, int(args.history * max(args.rate, 1.0)))
    torque_hist = deque(maxlen=maxlen)

    stop_flag = threading.Event()

    if args.interactive:
        print("\n--- moteus damper monitor ---")
        print(f"Controller ID: {args.id}")
        print("Press Enter to START damping...")
        input()
        print("\nRunning.")
        print("Type 's' then Enter to STOP (or Ctrl+C).")
        threading.Thread(target=_stdin_stop_listener, args=(stop_flag,), daemon=True).start()

    if args.plot:
        _start_plot_thread(torque_hist, stop_flag, f"moteus id={args.id} torque vs time")

    # Always clear old state first
    await hard_stop(c)

    warned_unavailable = False
    printed_keys = False
    last_print = time.monotonic()

    try:
        while True:
            if stop_flag.is_set():
                # Immediate hard stop if user requested stop
                await hard_stop(c)
                break

            # Send damping command + request a query so we can read torque
            res = await c.set_position(
                position=math.nan,
                velocity=0.0,
                kp_scale=0.0,
                kd_scale=args.kd,
                maximum_torque=abs(args.tau_max),
                watchdog_timeout=args.watchdog,
                query=True,
            )

            now = time.monotonic()
            tau, keys_seen = _extract_torque_nm(res)

            if tau is not None:
                torque_hist.append((now, tau))

            # Print at a slower rate
            if now - last_print >= print_dt:
                last_print = now
                if tau is None:
                    if not warned_unavailable:
                        warned_unavailable = True
                        print("[warn] Torque not found in query response on this setup/version.")
                        if (keys_seen is not None) and (not printed_keys):
                            printed_keys = True
                            print("[debug] Keys seen in response.values (for mapping torque):")
                            print(keys_seen)
                    print("torque: (unavailable)")
                else:
                    print(f"torque: {tau:+.3f} Nm")

            await asyncio.sleep(dt)

    except KeyboardInterrupt:
        # Ctrl+C -> hard stop
        await hard_stop(c)

    except Exception as e:
        # Any crash -> hard stop
        print(f"\n[error] {e}")
        await hard_stop(c)

    finally:
        # Always attempt multiple hard-stops on exit
        await hard_stop(c)
        stop_flag.set()
        print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())