#!/usr/bin/env python3
"""
detect_moteus.py — quick hardware-check script.

Verifies that the Pi 3B+ can talk to a moteus r4.11 over the pi3hat r4.5
on CAN bus 1 (the JC1 port).  No motion commands are issued — this only
queries state, so it is safe to run with the motor connected.

Usage (on the Pi, with the moteus venv active):
    cd ~/wirebrake
    python3 detect_moteus.py
"""

import asyncio
import sys

import moteus
import moteus_pi3hat

MOTEUS_ID = 1
MOTEUS_BUS = 1   # JC1 on pi3hat r4.5


async def main():
    print(f"Connecting to pi3hat (CAN bus {MOTEUS_BUS}, moteus ID {MOTEUS_ID})...")
    transport = moteus_pi3hat.Pi3HatRouter(
        servo_bus_map={MOTEUS_BUS: [MOTEUS_ID]}
    )
    controller = moteus.Controller(id=MOTEUS_ID, transport=transport)

    print("Sending stop (ensures the motor is disengaged before we query)...")
    await controller.set_stop()

    print("Querying moteus state...")
    try:
        result = await asyncio.wait_for(controller.query(), timeout=1.0)
    except asyncio.TimeoutError:
        print("\n*** ERROR: no response from moteus within 1 second.")
        print("    Check:")
        print("    - pi3hat is firmly seated on the Pi's 40-pin GPIO header")
        print("    - moteus 24 V power supply is on (check the LED on the moteus)")
        print("    - CAN_H / CAN_L wiring on JC1 isn't swapped or loose")
        print("    - moteus CAN ID really is 1 (use moteus_tool to confirm)")
        sys.exit(1)

    print("\n*** Moteus responded ***")
    v = result.values
    print(f"  Mode:      {v[moteus.Register.MODE]}")
    print(f"  Position:  {v[moteus.Register.POSITION]:.4f} rev")
    print(f"  Velocity:  {v[moteus.Register.VELOCITY]:.4f} rev/s")
    print(f"  Torque:    {v[moteus.Register.TORQUE]:.4f} Nm")
    print(f"  Voltage:   {v[moteus.Register.VOLTAGE]:.2f} V")
    print(f"  Temp:      {v[moteus.Register.TEMPERATURE]:.1f} C")
    print(f"  Fault:     {v[moteus.Register.FAULT]}")
    print("\nDetection successful — moteus is reachable on JC1.")


if __name__ == "__main__":
    asyncio.run(main())
