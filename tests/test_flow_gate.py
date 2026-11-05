"""Tests for the flow gate: no flow is reported while the water pump is off."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from modbus_connection.mock import MockModbusUnit

from lg_heatpump_modbus import LgHeatPump
from lg_heatpump_modbus.logic import FLOW_SENSOR_RANGE_FLOOR, gate_flow_rate

DATA = Path(__file__).parent / "data"

# Discrete input 1 is the water pump bit; input register 8 is the flow in 0.1 l/min.
PUMP_BIT = 1
FLOW_REGISTER = 8


# --- the pure function ---------------------------------------------------------------


def test_pump_off_reports_no_flow_for_the_range_floor() -> None:
    """The reported 5.0 l/min is the sensor's range floor, not a flow."""
    assert gate_flow_rate(FLOW_SENSOR_RANGE_FLOOR, False) == 0.0


@pytest.mark.parametrize("flow", [5.0, 5.1, 10.4, 12.0, 29.5])
def test_pump_off_reports_no_flow_for_every_value(flow: float) -> None:
    """Ramp samples between the floor and about 12 l/min arrive with the bit still off."""
    assert gate_flow_rate(flow, False) == 0.0


@pytest.mark.parametrize("flow", [5.0, 16.7, 29.5, 80.0])
def test_pump_on_leaves_the_flow_alone(flow: float) -> None:
    """With the pump running the value is real, even at the floor of the range."""
    assert gate_flow_rate(flow, True) == flow


@pytest.mark.parametrize(("flow", "pump"), [(None, True), (None, False), (None, None), (5.0, None)])
def test_unknown_input_leaves_the_output_unchanged(flow: float | None, pump: bool | None) -> None:
    """Guessing would replace a real reading with a made-up one."""
    assert gate_flow_rate(flow, pump) == flow


@pytest.mark.parametrize("pump", [True, False])
def test_the_gate_is_idempotent(pump: bool) -> None:
    once = gate_flow_rate(5.0, pump)
    assert gate_flow_rate(once, pump) == once


# --- on the device object ------------------------------------------------------------


async def test_device_reports_the_gated_value(pump: LgHeatPump, unit: MockModbusUnit) -> None:
    """The unit reports 5.0 with the pump off; the device object reports 0.0."""
    unit.discrete_inputs[PUMP_BIT] = False
    unit.input[FLOW_REGISTER] = 50
    await pump.async_update()

    assert pump.sensors.water_flow_rate == 5.0
    assert pump.water_flow_rate_raw == 5.0
    assert pump.water_flow_rate == 0.0


async def test_device_reports_the_real_flow_while_the_pump_runs(
    pump: LgHeatPump,
) -> None:
    await pump.async_update()

    assert pump.water_flow_rate_raw == 17.2
    assert pump.water_flow_rate == 17.2


async def test_debug_mode_returns_the_raw_value(pump: LgHeatPump, unit: MockModbusUnit) -> None:
    """``debug`` switches every correction off, as it does for the sentinels."""
    unit.discrete_inputs[PUMP_BIT] = False
    unit.input[FLOW_REGISTER] = 50
    pump.debug = True
    await pump.async_update()

    assert pump.water_flow_rate == 5.0


async def test_value_is_none_before_the_first_read(pump: LgHeatPump) -> None:
    assert pump.water_flow_rate_raw is None
    assert pump.water_flow_rate is None


async def test_register_model_still_returns_the_device_value(pump: LgHeatPump, unit: MockModbusUnit) -> None:
    """The register layer (L0) is untouched: it reports what the device said."""
    unit.discrete_inputs[PUMP_BIT] = False
    unit.input[FLOW_REGISTER] = 50
    await pump.async_update()

    assert pump.sensors.water_flow_rate == 5.0


# --- replay over recorded data -------------------------------------------------------


def _rows(name: str) -> list[tuple[bool, float]]:
    with (DATA / name).open() as handle:
        lines = (line for line in handle if not line.startswith("#"))
        return [(row["water_pump"] == "1", float(row["water_flow_rate"])) for row in csv.DictReader(lines)]


def test_replay_over_a_running_installation_changes_nothing() -> None:
    """Recorded on one R290 installation whose pump ran continuously.

    This is real data, but it has no pump-off rows, so it proves only that the
    gate leaves a running installation untouched. The pump-off behaviour rests on
    the documented measurements (see ``logic/flow_gate.py``) and the cases above.
    """
    rows = _rows("flow_replay_pump_on.csv")
    assert len(rows) > 50
    assert all(pump for pump, _ in rows)
    for pump, flow in rows:
        assert gate_flow_rate(flow, pump) == flow


def test_replay_over_a_pump_cycle() -> None:
    """Synthetic: a pump stop, the sensor sitting at its floor, and a restart."""
    cycle = [
        (True, 28.1),
        (True, 27.9),
        (False, 5.0),
        (False, 5.0),
        (False, 5.0),
        (False, 10.4),  # ramp sample while the bit is still off
        (True, 12.3),
        (True, 27.8),
    ]
    assert [gate_flow_rate(flow, pump) for pump, flow in cycle] == [
        28.1,
        27.9,
        0.0,
        0.0,
        0.0,
        0.0,
        12.3,
        27.8,
    ]
