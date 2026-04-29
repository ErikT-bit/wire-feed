#!/usr/bin/env python3
import asyncio
import math
import argparse
import moteus


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, default=1, help="moteus ID")
    p.add_argument("--rate", type=float, default=200.0, help="command rate in Hz")
    p.add_argument("--rpm", type=float, default=5.0, help="slow target speed in rev/min")
    p.add_argument("--tau", type=float, default=0.6, help="feedforward torque in Nm")
    p.add_argument("--tau_max", type=float, default=0.6, help="maximum torque in Nm")
    p.add_argument("--watchdog", type=float, default=0.1, help="watchdog timeout in seconds")
    args = p.parse_args()

    dt = 1.0 / max(args.rate, 1.0)
    target_rps = args.rpm / 60.0

    c = moteus.Controller(id=args.id)

    try:
        await c.set_stop()
        await asyncio.sleep(0.05)

        print("Starting constant_torque.py")
        print(f"  ID           : {args.id}")
        print(f"  Target speed : {args.rpm:.3f} rev/min")
        print(f"  Feedforward  : {args.tau:.3f} Nm")
        print(f"  Torque limit : {args.tau_max:.3f} Nm")
        print("  kp_scale     : 0.0")
        print("  kd_scale     : 0.0")
        print("Press Ctrl+C to stop.")

        while True:
            await c.set_position(
                position=math.nan,
                velocity=target_rps,
                feedforward_torque=args.tau,
                kp_scale=0.0,
                kd_scale=0.0,
                maximum_torque=abs(args.tau_max),
                watchdog_timeout=args.watchdog,
                query=False,
            )
            await asyncio.sleep(dt)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            await c.set_stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())