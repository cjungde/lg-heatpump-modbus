"""The optional limits are probed one register at a time and polled only once served.

An address a unit lacks is not refused but left unanswered, so every read of it
costs a full request timeout. These tests pin down that nothing reads holding 23,
holding 24 or coil 6 unless a consumer asked for it, and that a probe touches
exactly one register.
"""

from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusUnit

from lg_heatpump_modbus import LIMIT_DATAPOINTS, LgHeatPump, LgValueValidationError

LIMIT_READS = {("holding", 23), ("holding", 24), ("coil", 6)}

# (datapoint, register type, address)
LIMITS = [
    ("compressor_modulation_limit", "holding", 23),
    ("power_limit", "holding", 24),
    ("power_limit_enable", "coil", 6),
]


def _reads(unit: MockModbusUnit) -> list[tuple[str, int, int]]:
    return [(e.register_type, e.address, e.count) for e in unit.read_events]


def _limit_reads(unit: MockModbusUnit) -> list[tuple[str, int, int]]:
    return [r for r in _reads(unit) if (r[0], r[1]) in LIMIT_READS]


@pytest.fixture
def limited(unit: MockModbusUnit) -> MockModbusUnit:
    """Return a unit that serves all three limits."""
    unit.holding[23] = 80  # 80 %
    unit.holding[24] = 125  # 12.5 kW
    unit.coils[6] = True
    return unit


async def test_nothing_reads_the_limits_unless_served(pump: LgHeatPump, unit: MockModbusUnit) -> None:
    await pump.async_update()
    await pump.async_update_settings()
    await pump.async_read_raw()

    assert pump.served_limits == ()
    assert _limit_reads(unit) == []
    assert "limits" not in pump.component_names


@pytest.mark.parametrize(("name", "space", "address"), LIMITS)
async def test_probe_reads_exactly_one_register(
    pump: LgHeatPump, limited: MockModbusUnit, name: str, space: str, address: int
) -> None:
    limited.read_events.clear()

    assert await pump.async_probe_limit(name) is True
    assert _reads(limited) == [(space, address, 1)]
    # a probe does not make the limit served
    assert pump.served_limits == ()


@pytest.mark.parametrize(
    "error",
    [ModbusTimeoutError("silent"), IllegalDataAddressError()],
    ids=["silent", "refused"],
)
@pytest.mark.parametrize(("name", "space", "address"), LIMITS)
async def test_probe_reports_an_unserved_register(
    pump: LgHeatPump,
    limited: MockModbusUnit,
    error: Exception,
    name: str,
    space: str,
    address: int,
) -> None:
    limited.fail_read(address, error, register_type=space)

    assert await pump.async_probe_limit(name) is False


async def test_probe_says_nothing_when_the_link_fails(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    limited.fail_read(23, ModbusConnectionError("gone"))

    assert await pump.async_probe_limit("compressor_modulation_limit") is None


async def test_unknown_names_are_rejected(pump: LgHeatPump) -> None:
    with pytest.raises(ValueError, match="unknown limit"):
        await pump.async_probe_limit("water_flow_rate")
    with pytest.raises(ValueError, match="unknown limit"):
        pump.set_served_limits(["compressor_modulation_limit", "silent_mode"])


async def test_served_limits_are_polled_with_the_settings(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    pump.set_served_limits(["power_limit_enable", "compressor_modulation_limit"])
    limited.read_events.clear()

    report = await pump.async_update_settings()

    assert pump.served_limits == ("compressor_modulation_limit", "power_limit_enable")
    assert "limits" in report.updated
    assert sorted(_limit_reads(limited)) == [("coil", 6, 1), ("holding", 23, 1)]
    assert pump.limits.compressor_modulation_limit == 80
    assert pump.limits.power_limit_enable is True
    assert pump.limits.power_limit is None  # not served, not read
    # the measurements do not touch them
    limited.read_events.clear()
    await pump.async_update_readings()
    assert _limit_reads(limited) == []


async def test_neighbouring_holdings_are_read_separately(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    """A unit serving only one of 23/24 would drop a block spanning both."""
    pump.set_served_limits(["compressor_modulation_limit", "power_limit"])
    limited.read_events.clear()

    await pump.async_update_settings()

    assert sorted(_limit_reads(limited)) == [("holding", 23, 1), ("holding", 24, 1)]
    assert pump.limits.power_limit == 12.5


async def test_a_failing_limit_does_not_take_the_settings_down(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    pump.set_served_limits(["power_limit"])
    limited.fail_read(24, ModbusTimeoutError("silent"))

    report = await pump.async_update_settings()

    assert report.updated == ["controls", "switches"]
    assert set(report.failed) == {"limits"}


async def test_served_limits_appear_in_the_raw_map(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    pump.set_served_limits(["power_limit"])

    raw = await pump.async_read_raw()

    assert raw["holding"][24] == 125
    assert 23 not in raw["holding"]


async def test_setting_the_served_limits_again_replaces_them(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    pump.set_served_limits(["power_limit"])
    pump.set_served_limits([])
    limited.read_events.clear()

    await pump.async_update_settings()

    assert pump.served_limits == ()
    assert _limit_reads(limited) == []


@pytest.mark.parametrize("value", [0, 40, 75, 100])
async def test_modulation_limit_accepts_off_and_40_to_100(
    pump: LgHeatPump, limited: MockModbusUnit, value: int
) -> None:
    pump.set_served_limits(["compressor_modulation_limit"])

    await pump.limits.set_compressor_modulation_limit(value)

    assert limited.holding[23] == value


@pytest.mark.parametrize("value", [1, 39, 101, -1])
async def test_modulation_limit_rejects_the_gap_and_the_ends(
    pump: LgHeatPump, limited: MockModbusUnit, value: int
) -> None:
    pump.set_served_limits(["compressor_modulation_limit"])
    writes: list[object] = []
    limited.on_write(writes.append)

    with pytest.raises(LgValueValidationError):
        await pump.limits.set_compressor_modulation_limit(value)
    assert writes == []
    assert limited.holding[23] == 80


async def test_power_limit_writes_tenths_of_a_kilowatt(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    pump.set_served_limits(["power_limit", "power_limit_enable"])

    await pump.limits.set_power_limit(6.7)
    await pump.limits.set_power_limit_enable(False)

    assert limited.holding[24] == 67
    assert limited.coils[6] is False
    for bad in (0.0, 25.1):
        with pytest.raises(LgValueValidationError):
            await pump.limits.set_power_limit(bad)


async def test_an_unserved_limit_cannot_be_written(pump: LgHeatPump, limited: MockModbusUnit) -> None:
    with pytest.raises(AttributeError):
        await pump.limits.set_power_limit(5.0)


def test_metadata_describes_every_limit(pump: LgHeatPump) -> None:
    full = type(pump.limits)(None)  # metadata does not need a unit
    for name in LIMIT_DATAPOINTS:
        metadata = full.require_metadata_for(name)
        assert metadata.writable
        assert metadata.description
    assert full.require_metadata_for("power_limit").number.unit == "kW"
    assert full.require_metadata_for("compressor_modulation_limit").number.max_value == 100
