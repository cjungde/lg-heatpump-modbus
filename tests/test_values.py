"""Every seeded register decodes to the documented value."""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit

from lg_heatpump_modbus import (
    ControlMethod,
    EnergyState,
    LgHeatPump,
    OduOperationCycle,
    OperationMode,
)


async def test_update_refreshes_every_component(pump: LgHeatPump) -> None:
    report = await pump.async_update()

    assert report.ok
    assert report.updated == ["sensors", "states", "controls", "switches"]
    assert report.failed == {}


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("error_code", 0),
        ("odu_operation_cycle", 2),
        ("water_inlet_temperature", 31.5),
        ("water_outlet_temperature", 35.2),
        ("backup_heater_outlet_temperature", 35.3),
        ("dhw_tank_temperature", 48.2),
        ("solar_collector_temperature", 0.0),
        ("room_air_temperature_circuit_1", 21.4),
        ("water_flow_rate_raw", 17.2),
        ("water_outlet_temperature_circuit_2", 28.8),
        ("room_air_temperature_circuit_2", 20.9),
        ("outdoor_temperature", -4.2),
        ("liquid_pipe_temperature", 12.0),
        ("suction_temperature", 7.0),
        ("discharge_temperature", 76.0),
        ("evaporator_inlet_temperature", -5.5),
        ("evaporator_outlet_temperature", -2.1),
        ("refrigerant_high_pressure", 28),
        ("refrigerant_low_pressure", 7),
        ("compressor_frequency", 62),
    ],
)
async def test_sensor_values(pump: LgHeatPump, attribute: str, expected: float) -> None:
    await pump.sensors.async_update()

    assert getattr(pump.sensors, attribute) == expected


async def test_solar_collector_temperature_hides_the_no_sensor_sentinel(
    pump: LgHeatPump, unit: MockModbusUnit
) -> None:
    unit.input[6] = 3000  # the 300.0 °C reading reported with no solar collector

    await pump.sensors.async_update()

    assert pump.sensors.solar_collector_temperature is None

    pump.debug = True
    assert pump.sensors.solar_collector_temperature == 300.0

    pump.debug = False
    assert pump.sensors.solar_collector_temperature is None


async def test_sensor_enum_and_derived_values(pump: LgHeatPump) -> None:
    await pump.sensors.async_update()

    assert pump.sensors.odu_operation_cycle is OduOperationCycle.HEATING
    assert pump.sensors.energy_state is EnergyState.NORMAL
    assert pump.sensors.compressor_speed == 62 * 60
    assert pump.water_outlet_temperature_circuit_1 == 35.2
    assert pump.sensors.water_temperature_drop_circuit_2 == 6.4
    assert pump.sensors.water_temperature_difference == 3.7


async def test_derived_values_are_none_before_the_first_read(
    pump: LgHeatPump,
) -> None:
    assert pump.sensors.compressor_speed is None
    assert pump.water_outlet_temperature_circuit_1 is None
    assert pump.sensors.water_temperature_drop_circuit_2 is None
    assert pump.sensors.water_temperature_difference is None
    assert pump.states.backup_heater_steps is None


async def test_water_outlet_temperature_circuit_1_is_none_while_heating_dhw(
    pump: LgHeatPump, unit: MockModbusUnit
) -> None:
    unit.discrete_inputs[5] = True  # dhw_heating

    await pump.sensors.async_update()
    await pump.states.async_update()

    assert pump.water_outlet_temperature_circuit_1 is None


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("target_temperature_circuit_1", 40.0),
        ("room_air_setpoint_circuit_1", 21.0),
        ("shift_in_auto_mode_circuit_1", -2),
        ("target_temperature_circuit_2", 32.0),
        ("room_air_setpoint_circuit_2", 20.5),
        ("shift_in_auto_mode_circuit_2", 1),
        ("dhw_target_temperature", 50.0),
    ],
)
async def test_control_values(
    pump: LgHeatPump, attribute: str, expected: float
) -> None:
    await pump.controls.async_update()

    assert getattr(pump.controls, attribute) == expected


async def test_control_enums(pump: LgHeatPump) -> None:
    await pump.controls.async_update()

    assert pump.controls.operation_mode is OperationMode.HEAT
    assert pump.controls.control_method is ControlMethod.WATER_OUTLET
    assert pump.controls.energy_state is EnergyState.NORMAL


async def test_switch_values(pump: LgHeatPump) -> None:
    await pump.switches.async_update()

    assert pump.switches.heating_circuit is True
    assert pump.switches.dhw is True
    assert pump.switches.silent_mode is False
    assert pump.switches.dhw_disinfection is False


async def test_state_values(pump: LgHeatPump) -> None:
    await pump.states.async_update()

    assert pump.states.water_flow is True
    assert pump.states.compressor is True
    assert pump.states.defrosting is False
    assert pump.states.mixing_pump is False
    assert pump.states.emergency_operation_dhw is True
    assert pump.states.backup_heater_steps == 1


async def test_device_information(pump: LgHeatPump) -> None:
    await pump.info.async_update()

    assert pump.info.manufacturer == "LG"
    assert pump.info.product_group == 0x80
    assert pump.info.product_info == 0x1A2B
    assert pump.info.product_code == "0x0080"


async def test_unknown_enum_code_decodes_to_none(
    pump: LgHeatPump, unit: MockModbusUnit
) -> None:
    unit.holding[0] = 99  # not an OperationMode

    await pump.controls.async_update()

    assert pump.controls.operation_mode is None


async def test_circuit_accessors(pump: LgHeatPump) -> None:
    await pump.controls.async_update()

    assert pump.controls.target_temperature(1) == 40.0
    assert pump.controls.target_temperature(2) == 32.0
    assert pump.controls.room_air_setpoint(1) == 21.0
    assert pump.controls.shift_in_auto_mode(2) == 1


async def test_circuit_accessors_reject_an_unknown_circuit(
    pump: LgHeatPump,
) -> None:
    with pytest.raises(ValueError, match="Circuit must be 1 or 2"):
        pump.controls.target_temperature(3)
