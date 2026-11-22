"""Fixtures: an LgHeatPump over modbus-connection's in-memory mock backend.

The mock backend and its fixtures ship with ``modbus-connection``. They are
imported explicitly below so the test suite does not depend on pytest
entry-point autoloading. There is no real server, socket or backend here — just
address-keyed stores loaded with heat-pump-shaped register, coil and discrete
input values.
"""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit
from modbus_connection.pytest_plugin import (
    mock_modbus_connection as mock_modbus_connection,
    mock_modbus_unit as mock_modbus_unit,
)

from lg_heatpump_modbus import LgHeatPump

# Raw input-register words keyed by their protocol address; decoded view inline.
INPUT_REGISTERS: dict[int, int] = {
    0: 0,  # error_code -> no error
    1: 2,  # odu_operation_cycle
    2: 315,  # water_inlet_temperature -> 31.5 °C
    3: 352,  # water_outlet_temperature -> 35.2 °C
    4: 353,  # backup_heater_outlet_temperature -> 35.3 °C
    5: 482,  # dhw_tank_temperature -> 48.2 °C
    6: 0,  # solar_collector_temperature -> 0.0 °C
    7: 214,  # room_air_temperature_circuit_1 -> 21.4 °C
    8: 172,  # water_flow_rate_raw -> 17.2 L/min
    9: 288,  # water_outlet_temperature_circuit_2 -> 28.8 °C
    10: 209,  # room_air_temperature_circuit_2 -> 20.9 °C
    11: 2,  # energy_state -> NORMAL
    12: 0x10000 - 42,  # outdoor_temperature -> -4.2 °C (signed)
    16: 120,  # liquid_pipe_temperature -> 12.0 °C (raw value scaled by 0.1)
    18: 70,  # suction_temperature -> 7.0 °C (raw value scaled by 0.1)
    19: 760,  # discharge_temperature -> 76.0 °C (raw value scaled by 0.1)
    20: 0x10000 - 55,  # evaporator_inlet_temperature -> -5.5 °C
    21: 0x10000 - 21,  # evaporator_outlet_temperature -> -2.1 °C
    22: 2800,  # refrigerant_high_pressure -> 28.0 bar (raw value scaled by 0.01)
    23: 700,  # refrigerant_low_pressure -> 7.0 bar (raw value scaled by 0.01)
    24: 62,  # compressor_frequency -> 62 Hz -> 3720 rpm
    9997: 0x0080,  # product_group -> VRF? family code / 0x8X group
    9998: 0x1A2B,  # product_info
}

# Raw holding-register words keyed by their protocol address.
HOLDING_REGISTERS: dict[int, int] = {
    0: 4,  # operation_mode -> HEAT
    1: 0,  # control_method -> WATER_OUTLET
    2: 400,  # target_temperature_circuit_1 -> 40.0 °C
    3: 210,  # room_air_setpoint_circuit_1 -> 21.0 °C
    4: 0x10000 - 2,  # shift_in_auto_mode_circuit_1 -> -2 K (signed)
    5: 320,  # target_temperature_circuit_2 -> 32.0 °C
    6: 205,  # room_air_setpoint_circuit_2 -> 20.5 °C
    7: 1,  # shift_in_auto_mode_circuit_2 -> 1 K
    8: 500,  # dhw_target_temperature -> 50.0 °C
    9: 2,  # energy_state -> NORMAL
}

COILS: dict[int, bool] = {
    0: True,  # heating_circuit
    1: True,  # dhw
    2: False,  # silent_mode
    3: False,  # dhw_disinfection
    4: False,  # emergency_stop
    5: False,  # trigger_emergency_operation
}

DISCRETE_INPUTS: dict[int, bool] = {
    0: True,  # water_flow
    1: True,  # water_pump
    2: False,  # external_water_pump
    3: True,  # compressor
    4: False,  # defrosting
    5: False,  # dhw_heating
    6: False,  # dhw_disinfection
    7: False,  # silent_mode
    8: False,  # cooling
    9: False,  # solar_pump
    10: True,  # backup_heater_step_1
    11: False,  # backup_heater_step_2
    12: False,  # dhw_boost_heater
    13: False,  # error
    14: True,  # emergency_operation_space
    15: True,  # emergency_operation_dhw
    16: False,  # mixing_pump
}


def seed(unit: MockModbusUnit) -> MockModbusUnit:
    """Load a full heat pump image into the mock unit's stores."""
    unit.input.update(INPUT_REGISTERS)
    unit.holding.update(HOLDING_REGISTERS)
    unit.coils.update(COILS)
    unit.discrete_inputs.update(DISCRETE_INPUTS)
    return unit


@pytest.fixture
def unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """Return the seeded mock unit the heat pump reads and writes through.

    Request it alongside ``pump`` to assert on the stores a write landed in,
    rather than reaching for the unit the components hold.
    """
    return seed(mock_modbus_unit)


@pytest.fixture
def pump(unit: MockModbusUnit) -> LgHeatPump:
    """Return an LgHeatPump over the seeded mock unit."""
    return LgHeatPump(unit)
