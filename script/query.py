#!/usr/bin/env python3

"""Query an LG heat pump over Modbus and print every value.

Connects over Modbus TCP (a network gateway) or a serial/USB port, reads the
whole heat pump once, and dumps every sub-system's values to the terminal.
Handy for checking a real installation without any application around it.

The library itself only needs the connection protocol; this script selects a
concrete backend, so install the ``cli`` extra first::

    pip install "lg-heatpump-modbus[cli]"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from getpass import getuser
from hashlib import pbkdf2_hmac
from pathlib import Path

from modbus_connection import ModbusError
from modbus_connection.cli_helper import (
    CountingUnit,
    add_connection_args,
    connect_from_args,
    print_component,
)

from lg_heatpump_modbus import MODELS, LgHeatPump

# (label, attribute name on LgHeatPump) — the order sections are printed in.
SECTIONS: list[tuple[str, str]] = [
    ("Device", "info"),
    ("Measurements", "sensors"),
    ("Status", "states"),
    ("Settings", "controls"),
    ("Switches", "switches"),
]

DEFAULT_UNIT_ID = 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the connection arguments plus the unit id and model."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser)
    # The unit id is not part of connecting — it varies per device and per tool
    # — so it is added alongside the connection arguments rather than by them.
    parser.add_argument(
        "--unit",
        type=int,
        default=DEFAULT_UNIT_ID,
        help=f"Modbus unit/station address (default: {DEFAULT_UNIT_ID})",
    )
    parser.add_argument(
        "--model",
        choices=sorted(MODELS),
        default="therma_v",
        help="Device model deciding which datapoints are read (default: therma_v)",
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=None,
        help="Directory for one JSON snapshot per query; omit to disable dumping.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Show raw sentinel values (e.g. solar_collector_temperature's 300 °C"
            " no-sensor reading) instead of masking them to None."
        ),
    )
    return parser.parse_args(argv)


def _print(pump: LgHeatPump) -> None:
    """Print every sub-system of the heat pump."""
    for label, attribute in SECTIONS:
        print()
        print_component(getattr(pump, attribute), title=label)


def _print_derived(pump: LgHeatPump) -> None:
    """Print the values the library derives from several registers."""
    print()
    print("Derived")
    print(f"  compressor speed                    {_fmt(pump.sensors.compressor_speed)} rpm")
    print(
        "  water outlet temperature circuit 1  "
        f"{_fmt(pump.water_outlet_temperature_circuit_1)} °C"
    )
    print(
        "  water temperature spread            "
        f"{_fmt(pump.sensors.water_temperature_difference)} K"
    )
    print(
        "  temperature drop circuit 2          "
        f"{_fmt(pump.sensors.water_temperature_drop_circuit_2)} K"
    )
    print(f"  water flow rate                     {_fmt(pump.water_flow_rate)} l/min")
    print(f"  backup heater steps                 {_fmt(pump.states.backup_heater_steps)}")


def _fmt(value: object) -> str:
    """Format a possibly-absent value for the terminal."""
    return "-" if value is None else str(value)


def _component_snapshot(component: object) -> dict[str, object]:
    """Return every declared value in a component as a plain dict."""
    if hasattr(component, "values"):
        return component.values()
    return {}


def _snapshot(
    pump: LgHeatPump,
    *,
    elapsed_ms: float,
    modbus_reads: int,
    failed: dict[str, ModbusError],
    args: argparse.Namespace,
) -> dict[str, object]:
    """Return a JSON-serialisable snapshot of a single query."""
    return {
        "user_pseudonym": pbkdf2_hmac(
            "sha256", f"{getuser().lower().strip()}|{sys.platform or 'unknown'}".encode(),
            b"salt-lg-heatpump-modbus-library-asdf", 100000
        ).hex()[:32],
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "model": args.model,
        "unit_id": args.unit,
        "debug": args.debug,
        "elapsed_ms": round(elapsed_ms, 3),
        "modbus_reads": modbus_reads,
        "failed": {name: str(error) for name, error in failed.items()},
        "components": {
            attribute: _component_snapshot(getattr(pump, attribute))
            for _, attribute in SECTIONS
        },
        "derived": {
            "compressor_speed": pump.sensors.compressor_speed,
            "water_outlet_temperature_circuit_1": pump.water_outlet_temperature_circuit_1,
            "water_temperature_difference": pump.sensors.water_temperature_difference,
            "water_temperature_drop_circuit_2": pump.sensors.water_temperature_drop_circuit_2,
            "water_flow_rate": pump.water_flow_rate,
            "backup_heater_steps": pump.states.backup_heater_steps,
        },
    }


def _dump_json_snapshot(
    args: argparse.Namespace, snapshot: dict[str, object]
) -> Path | None:
    """Write a snapshot to disk and return the file path, or None if disabled."""
    if args.json_dir is None:
        return None
    target_dir = Path(args.json_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{args.model}-unit{args.unit}-{timestamp}.json"
    path = target_dir / filename
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return path


async def _run(args: argparse.Namespace) -> int:
    """Connect, read the heat pump once, print it and report the read count."""
    try:
        connection = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1

    counting = CountingUnit(connection.for_unit(args.unit))
    try:
        pump = LgHeatPump(counting, model=args.model, debug=args.debug)
        start = time.monotonic()
        report = await pump.async_update()
        elapsed = time.monotonic() - start
    except ModbusError as err:
        print(f"Error reading the heat pump: {err}", file=sys.stderr)
        return 1
    finally:
        await connection.close()

    _print(pump)
    _print_derived(pump)
    for name, error in report.failed.items():
        print(f"\n{name} did not answer: {error}", file=sys.stderr)
    print(f"\nQueried in {elapsed * 1000:.0f} ms ({counting.reads} Modbus reads)")

    snapshot = _snapshot(
        pump,
        elapsed_ms=elapsed * 1000,
        modbus_reads=counting.reads,
        failed=report.failed,
        args=args,
    )
    try:
        json_path = _dump_json_snapshot(args, snapshot)
    except OSError as err:
        print(f"Could not write JSON snapshot: {err}", file=sys.stderr)
    else:
        if json_path is not None:
            print(f"\nSaved JSON snapshot to {json_path}")

    return 0 if report.ok else 1


def main() -> int:
    """Run the query script."""
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
