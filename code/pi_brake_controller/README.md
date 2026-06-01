# Wire EDM Brake Controller

Electronic braking system for a Wire EDM machine. The brake torque is
delivered by a moteus r4.11 motor controller acting as a velocity-zero
damper on the wire spool. A Raspberry Pi 3B+ commands the moteus over
CAN-FD via a pi3hat r4.5; a Raspberry Pi 5 (LinuxCNC main controller)
provides the operator interface over an ethernet TCP link.

The operator never has to log into the Pi 3 or know any "machine codes" —
they sit in front of the Pi 5, see plain-English prompts on screen, and
type a velocity and tension. The Pi 3 computes the moteus damping value
and engages the brake.

## System architecture

```
                +-----------------------------+
                |  Raspberry Pi 5 (LinuxCNC)  |
                |  10.10.10.1                 |
                |  - Mesa motion via hm2_eth  |
                |  - brake_console.py (xterm) |
                +--------------+--------------+
                               |
                               | ethernet
                               v
                +--------------+--------------+
                |    Unmanaged ethernet switch    |
                +--+-------------+-------------+--+
                   |             |             |
                   |             |             |
        +----------+--+   +------+------+   +--+----------+
        | Mesa 7i76EU |   | Raspberry  |   |             |
        | 10.10.10.10 |   | Pi 3B+     |   | (laptop, when
        | (motion)    |   | 10.10.10.20|   |  pushing code)
        +-------------+   +-----+------+   +-------------+
                                |
                                | pi3hat r4.5 -> CAN bus 1 (JC1)
                                v
                        +-------+-------+
                        | moteus r4.11  |
                        | (electric     |
                        |  brake)       |
                        +---------------+
                                |
                                | 3-phase
                                v
                        +-------+-------+
                        | BLDC on wire  |
                        | spool         |
                        +---------------+
```

## Hardware

| Item | Role | Notes |
|---|---|---|
| Raspberry Pi 5 | Machine main / operator UI | Runs LinuxCNC + Mesa driver + brake console |
| Raspberry Pi 3B+ | Brake controller | Headless, runs `wire_brake.py` as a systemd service |
| pi3hat r4.5 | CAN-FD interface | Sits on the Pi 3B+'s 40-pin GPIO header |
| moteus r4.11 | Motor controller / brake | Connected to pi3hat JC1 (CAN bus 1), CAN ID 1 |
| 24 V bench supply | moteus high-voltage power | ~12–44 V is acceptable |
| Mesa 7i76EU | Motion controller for the wire-puller | Static IP 10.10.10.10 (factory default) |
| Unmanaged ethernet switch | Connects Pi 5, Pi 3B+, Mesa | No DHCP, all devices use static IPs |

## Network configuration

| Device | IP / subnet | Notes |
|---|---|---|
| Pi 5 (`eth0`) | `10.10.10.1/24`  | NetworkManager static profile |
| Mesa 7i76EU | `10.10.10.10/24` | Factory default, do not change |
| Pi 3B+ (`eth0`) | `10.10.10.20/24` | NetworkManager static profile |

The Pi 5 also has WiFi (`wlan0`) for development/file-transfer; it is not
used by the brake system. The Pi 3B+ is intended to be wired-only at the
machine.

## Files

| File | Where it runs | Purpose |
|---|---|---|
| `wire_brake.py` | Pi 3B+ | TCP server daemon; talks to moteus, runs the brake state machine |
| `brake_console.py` | Pi 5 | Operator-facing TCP client; relays text to/from `wire_brake.py` |
| `detect_moteus.py` | Pi 3B+ | One-shot moteus communication test, no motion |
| `fake_brake_server.py` | Anywhere | Stand-in for `wire_brake.py` for testing without hardware |
| `wire-brake.service` | Pi 3B+ (`/etc/systemd/system/`) | systemd unit for auto-start at boot |
| `brake-console.desktop` | Pi 5 (`~/.config/autostart/`) | XFCE autostart entry for the operator console |

## The brake control law

The moteus runs in `position` mode with `position = NaN`, `velocity = 0`,
`kp_scale = 0`, and `kd_scale = Kd`. With `kp_scale = 0` the position term
is disabled, so the controller only resists *motion* — exactly what a
brake should do. The braking torque is proportional to the velocity the
puller forces on the spool.

The damping coefficient `Kd` is computed from the operator's requested
wire speed and tension using a fitted equation:

```
Kd = 0.560 - 0.218 v + 2.246 T - 0.107 v T + 0.0126 v^2 - 0.122 T^2
```

where `v` is wire velocity in m/min and `T` is wire tension in kgf.

The equation is calibrated for the envelope `v ∈ [5, 15] m/min`,
`T ∈ [0.2, 3] kgf`. Inputs outside that envelope are rejected before any
moteus command is issued.

## Pi 3B+ setup

One-time install steps. Assumes Raspberry Pi OS 64-bit (Lite or Desktop)
and a user named `admin` with a home directory at `/home/admin`.

### 1. System packages

```bash
sudo apt update
sudo apt install -y tmux python3-pip python3-venv python3-dev build-essential git
```

### 2. Enable SPI (required by pi3hat)

```bash
sudo raspi-config nonint do_spi 0
```

### 3. Bump swap to 2 GB (recommended on the 1 GB Pi 3B+ for the moteus install)

```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

### 4. Install moteus + moteus-pi3hat in a venv

```bash
mkdir -p ~/moteus && cd ~/moteus
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install moteus moteus-pi3hat
```

### 5. Drop the brake controller files into `~/wirebrake/`

Copy `wire_brake.py`, `detect_moteus.py`, and `wire-brake.service` to
`/home/admin/wirebrake/`. Either via scp from a laptop or by cloning this
repo and copying out of `WireTension/`.

### 6. Install the systemd service

```bash
sudo cp ~/wirebrake/wire-brake.service /etc/systemd/system/wire-brake.service
sudo systemctl daemon-reload
sudo systemctl enable --now wire-brake.service
sudo systemctl status wire-brake
```

You should see `active (running)` and `[boot] listening on ('0.0.0.0', 5005)`
in the journal output.

### 7. Static ethernet IP (run at the Pi 3B+ console)

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 10.10.10.20/24 ipv6.method disabled
sudo nmcli con down "Wired connection 1"; sudo nmcli con up "Wired connection 1"
ip a show eth0
```

Confirm `inet 10.10.10.20/24` is shown on `eth0`.

## Pi 5 setup

Assumes the LinuxCNC bundled Debian image with NetworkManager and XFCE
desktop, user `admin`.

### 1. Static ethernet IP

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 10.10.10.1/24 ipv6.method disabled
sudo nmcli con down "Wired connection 1"; sudo nmcli con up "Wired connection 1"
```

### 2. Install xterm

```bash
sudo apt install -y xterm
```

### 3. Drop the console files in place

- `brake_console.py` → `/home/admin/wirebrake/brake_console.py`
- `brake-console.desktop` → `/home/admin/.config/autostart/brake-console.desktop`

### 4. Enable desktop auto-login (optional but recommended)

```bash
sudo raspi-config nonint do_boot_behaviour B4
```

`B4` = Desktop Autologin. After this, the Pi 5 boots straight into the
admin desktop session and the brake console window opens automatically.

### 5. Reboot

```bash
sudo reboot
```

When the desktop comes up, an xterm titled "Wire EDM Brake Console" should
auto-open and immediately show the velocity prompt from the Pi 3.

## Operator workflow

1. Power on the cabinet (Pi 5, Pi 3, Mesa, 24 V supply for the moteus).
2. Wait ~30 seconds. The Pi 5 boots into desktop, the brake console window
   opens, and the velocity prompt appears.
3. Operator types the desired wire velocity in m/min, presses Enter.
4. Tension prompt appears. Operator types tension in kgf, presses Enter.
5. Brake engages. The console shows `Brake engaged. Kd = ...`.
6. When done with the cut, operator types `stop`. Brake disengages.
7. `Resume with previous values? (y/n)` — `y` re-engages with the same
   v/T, `n` returns to the velocity prompt for new values.

If the connection drops, the brake daemon makes the motor safe and the
console attempts to reconnect every two seconds. If the moteus reports a
fault while engaged, the brake stops and the console moves to the resume
prompt.

## Development & testing without hardware

You can exercise the full operator UX (every prompt, validation message,
state transition) without a real Pi 3B+ or moteus by running the fake
server. On any machine with Python 3:

```bash
# Terminal A — fake server (acts like wire_brake.py over TCP)
python3 fake_brake_server.py

# Terminal B — operator console
python3 brake_console.py 127.0.0.1 5005
```

The fake server logs `[fake-brake] engage  kd=X.XXXX` instead of actually
commanding a motor. Useful for testing edge cases (out-of-range numbers,
invalid input, mid-engaged disconnect) without risking the hardware.

The real `wire_brake.py` also has a `--sim` mode that converses on
stdin/stdout instead of TCP, useful for one-off debugging at a Pi 3B+
console:

```bash
sudo /home/admin/moteus/venv/bin/python /home/admin/wirebrake/wire_brake.py --sim
```

## Wire protocol

The TCP link between Pi 5 and Pi 3B+ carries plain ASCII text — no machine
codes. The Pi 3B+ owns all UI text; the Pi 5 just relays bytes.

Lines from Pi 3 → Pi 5 are prompts, status, and error messages, e.g.

```
What velocity do you want the wire to be pulled at (m/min)?
Pick a value between 5.0-15.0:
Brake engaged.  Kd = 2.1030  (v=10.0 m/min, T=1.5 kgf)
  3.0 is out of range.  Must be 0.2 to 3.0.
Brake stopped — moteus fault code 32.
```

Lines from Pi 5 → Pi 3 are operator input, e.g.

```
10
1.5
stop
y
?            (refresh: re-display the current prompt)
```

The protocol is intentionally human-readable so a developer can `nc
10.10.10.20 5005` from anywhere on the LAN to drive the brake by hand.

## Troubleshooting

### Pi 3B+ won't boot / boot loop

Most often **under-voltage**. Pi 3B+ with pi3hat needs a steady 5 V at
≥2.5 A. Check that the red PWR LED is solid (not blinking or off) during
boot. Use the official Raspberry Pi 5V supply or equivalent.

If the LEDs are fine but it still loops, try removing the pi3hat — if it
boots without the HAT, the HAT is either drawing too much or not seated
flat on the GPIO header.

### moteus does not respond

Run `python3 detect_moteus.py` from `~/wirebrake/` (with the venv active or
via `sudo /home/admin/moteus/venv/bin/python3 detect_moteus.py`). Errors
narrow down to:

- pi3hat not seated on the GPIO header
- moteus 24 V power off
- CAN_H/CAN_L wiring swapped on JC1
- moteus CAN ID isn't 1 (use `moteus_tool` to check/change)

### "Realtime scheduler" error from moteus_pi3hat

The pi3hat library needs `CAP_SYS_NICE`. Either run with `sudo`, or
permanently grant the capability to your venv's Python:

```bash
sudo setcap cap_sys_nice+ep /home/admin/moteus/venv/bin/python3
```

The systemd service runs as root and avoids this.

### Pi 5 console window doesn't auto-open

- Confirm desktop auto-login is enabled (Section "Pi 5 setup" step 4).
- Confirm `~/.config/autostart/brake-console.desktop` exists and the path
  in its `Exec=` line points at your real `brake_console.py`.
- Check the XFCE session log: `~/.xsession-errors`.

### `brake_console.py` says "Waiting for brake controller..."

That means the Pi 5 reaches the network but the Pi 3 isn't responding on
`10.10.10.20:5005`. Either the Pi 3 isn't powered, the static IP didn't
take, or the `wire-brake.service` is down. From the Pi 5:

```bash
ping -c 3 10.10.10.20
ssh admin@10.10.10.20 sudo systemctl status wire-brake
```

## Future work

- **Closed-loop tension control.** Today the brake runs open-loop on the
  operator-entered `v`. The original spec calls for the brake to retune
  Kd in real time based on the puller motor's encoder velocity. The
  protocol already supports live `PARAMS,v,T` updates while engaged —
  the Pi 5 needs to emit them based on Mesa encoder data.

- **Graceful shutdown button.** A "Power Down" button in the operator
  UI that disengages the brake, SSHs `sudo poweroff` to the Pi 3, then
  shuts down the Pi 5. Avoids SD-card corruption from yanking power.

- **LinuxCNC integration.** Replace the standalone xterm console with a
  HAL component or a glade panel inside the LinuxCNC UI, so the brake
  parameters live in the same screen as the rest of the machine controls.

## License

TBD — pick one and add a LICENSE file if you intend to publish.

## Authorship and development credit

The Raspberry Pi brake-controller software and supporting configuration files in this directory were created by **Nathan Taylor** with development assistance from **Claude Code**.

Files authored and assembled for this brake-controller system:

- `brake_console.py`
- `detect_moteus.py`
- `fake_brake_server.py`
- `wire_brake.py`
- `brake-console.desktop`
- `wire-brake.service`

This `README.md` documentation was also prepared for the brake-controller system by **Nathan Taylor** with assistance from **Claude Code**.
