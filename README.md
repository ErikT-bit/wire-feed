# Wire EDM Wire-Feed System

This repository contains the software, supporting libraries, and mechanical CAD files for a Wire EDM wire-feed and wire-tension system.

The system uses a motor-driven wire-pull mechanism and an electronically controlled wire brake. The brake is implemented with a moteus motor controller and is intended to maintain controlled resistance on the wire spool while the wire is pulled through the machine.

## Repository Layout

```text
wire-feed/
|-- code/
|   |-- pi_brake_controller/
|   `-- WireTension code V1/
|-- libraries/
`-- mechanical/
```

## `code/`

This directory contains the project-specific control software.

### `code/pi_brake_controller/`

This is the current Raspberry Pi brake-controller implementation.

The software supports communication between the machine controller and the moteus-based wire brake. The folder includes the Raspberry Pi brake daemon, operator-facing console, service configuration, autostart configuration, hardware-detection utility, and a fake brake server for testing without hardware.

The Raspberry Pi brake-controller code in this folder was written by Nathan Taylor with development assistance from Claude Code.

See [`code/pi_brake_controller/README.md`](code/pi_brake_controller/README.md) for the system architecture, setup instructions, operator workflow, troubleshooting notes, and file-by-file authorship credit.

### `code/WireTension code V1/`

This folder contains an earlier version of the wire-tension control software. It is retained as a reference for the development history of the project.

## `libraries/`

This directory contains supporting libraries retained with the project for development and reference.

Included libraries:

- `ACAN2517`
- `ACAN2517FD`
- `AccelStepper`
- `HX711_Arduino_Library`
- `Moteus`

These libraries are third-party dependencies or reference libraries. Their original licenses, README files, and attribution information are preserved inside their respective folders.

## `mechanical/`

This directory contains the mechanical CAD files for the Wire EDM wire-feed and wire-tension hardware.

The folder includes:

- Native SolidWorks assembly files
- Native SolidWorks part files
- Exported `.STEP`, `.STL`, and `.3mf` files
- Files organized by subsystem, including pulleys, nozzles, custom tools, the wire-collection system, the wire-pull axle assembly, and the wire-tension housing

See [`mechanical/README.md`](mechanical/README.md) for the detailed CAD-file inventory and mechanical-design credit.

## Calibration And Tuning Data

Calibration data and the tuning workflow used during development are kept in a separate repository:

[Wire-EDM-Tension-Calibration](https://github.com/ErikT-bit/Wire-EDM-Tension-Calibration)

The `data/` folder is not part of this repository layout for the GitHub push.

## Important Note About Moteus Brake Tuning

The moteus controller must be tuned for the specific motor and mechanical setup being used.

The `Kd` values used in this project were developed for this particular wire-brake assembly, motor, spool geometry, pulley arrangement, and operating range. Those values should **not** be assumed to work correctly or safely in another system.

Anyone adapting this project should calibrate and test their own wire brake before operating the machine. A different motor, spool diameter, gear ratio, pulley layout, wire path, or target tension range may require different moteus settings and different `Kd` values.

## Project Documentation

Additional documentation is available inside the relevant folders:

- [`code/pi_brake_controller/README.md`](code/pi_brake_controller/README.md) - brake-controller architecture, setup, operation, troubleshooting, and software credit
- [`mechanical/README.md`](mechanical/README.md) - CAD-file inventory and mechanical-design credit
