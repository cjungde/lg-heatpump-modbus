# `lg-heatpump-modbus` Python library

[![CI](https://github.com/lg-heatpump/lg-heatpump-modbus/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/lg-heatpump/lg-heatpump-modbus/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/lg-heatpump-modbus.svg)](https://pypi.org/project/lg-heatpump-modbus/)
[![Python](https://img.shields.io/pypi/pyversions/lg-heatpump-modbus.svg)](https://pypi.org/project/lg-heatpump-modbus/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

`lg-heatpump-modbus` is an asynchronous, transport-independent Python library
for reading and controlling **LG heat pumps** — the Therma V family and the
siblings that share its Modbus register map — over Modbus.

> ### DISCLAIMER
> 
> This library is in early development. It is not yet feature-complete, bugs are expected! 
> 
> Anyone with a real device is encouraged to test it and report issues. See section [Contributing](#contributing) for a quick way to check your installation.

## Purpose and scope

The library is meant for operational monitoring and day-to-day control of a
commissioned installation: temperatures, pressures, pump and heater states,
setpoints, operating mode, hot water and silent mode. It is **not** a
commissioning tool and does not attempt to reproduce every installer menu.

The library:

* contains the **device-specific data model** — the valid registers and coils,
  their data types, scaling, units, ranges and enumerations, and the rules for
  safe reads and writes;

* structures the register map as **device models**, so a new variant is added
  as a `ModelDefinition` (data) rather than as new code;

* carries **neutral datapoint metadata** — unit, precision, step, limits, enum
  options and writability — next to each address, so the model *is* the
  datasheet;

* **does not create or own the Modbus transport.** Applications provide a
  [`modbus_connection.ModbusUnit`](https://github.com/home-assistant-libs/modbus-connection)
  and may use any backend `modbus-connection` supports (tmodbus, pymodbus, …).

## Prerequisites

Development or testing this library can be done **without a real heat pump**, using the in-memory mock backend that ships with `modbus-connection`. The mock backend simulates a heat pump and responds to reads and writes. 

Before you can use the library **with a real heatpump**, you must have a working Modbus connection to the heat pump. 

This takes two steps: 

1. Get and connect a compatible Modbus interface to the heat pump. At this point, the Waveshare RS485 to ETH Adapter is known to work and well-tested.
2. Configure the heat pump to enable Modbus communication.

Both steps are explained in more detail [here](https://github.com/basti242/homeassistant_lg_therma_v_modbus/wiki/2.-How-to-install), you can skip the middle *Homeassistant* section. 

## Installation

```bash
pip install lg-heatpump-modbus
```

The library itself only needs the protocol and the device-modelling framework.
To use the bundled query script, install a concrete backend as well:

```bash
pip install "lg-heatpump-modbus[cli]"
```

## Usage

```python
import asyncio

from modbus_connection import ModbusTcpParams
from modbus_connection.tmodbus import ModbusConnection 

from lg_heatpump_modbus import LgHeatPump, OperationMode


async def main() -> None:
    connection = ModbusConnection(ModbusTcpParams(host="192.168.1.50", port=502))
    try:
        pump = LgHeatPump(connection.for_unit(1))
        await pump.async_update()

        print("Outdoor:", pump.sensors.outdoor_temperature, "°C")
        print("Flow:", pump.sensors.water_outlet_temperature, "°C")
        print("Compressor running:", pump.states.compressor)
        print("Circuit 1 target:", pump.controls.target_temperature_circuit_1)

        await pump.controls.set_operation_mode(OperationMode.HEAT)
        await pump.controls.set_target_temperature(1, 42.0)
        await pump.switches.set_silent_mode(True)
    finally:
        await connection.close()


asyncio.run(main())
```

`async_update()` fans out to each sub-system, and each sub-system reads only its
own registers in as few Modbus round-trips as the map allows: the whole device
is seven block reads. Poll the two halves at different rates if you prefer:

```python
await pump.async_update_readings()  # what the heat pump measures
await pump.async_update_settings()  # what it has been configured to do
```

Every poll returns an `UpdateReport` naming the sub-systems that refreshed and
those that failed, so one unanswered block does not take the rest with it.

## Sub-systems

| Attribute | Modbus table | Contents |
| :-------- | :----------- | :------- |
| `pump.info` | input register | Product information word |
| `pump.sensors` | input registers | Temperatures, pressures, flow rate, compressor frequency, error code, energy state |
| `pump.states` | discrete inputs | Pump, compressor, heater, defrost and fault flags |
| `pump.controls` | holding registers | Operation mode, control method, water/room/hot-water setpoints, auto-mode shift |
| `pump.switches` | coils | Power, hot water, silent mode, disinfection, emergency stop |

## Register map

The address range of Modbus is very large and divided into multiple sections called Function Code (FC). Each FC has its own address space at the beginning of the address.

The FCs are either written as hexadecimal (0x01, 0x02, …) or as FC01, FC02, … in the documentation.

Modbus is defined to have four kinds of tables:

| Modbus Table   | Data Size | Reading FC | Writing FC    |
|:---------------|:----------|:-----------|---------------|
| Coil           | 1-bit     | 0x01       | 0x05 / 0x0F   |
| Discrete Input | 1-bit     | 0x02       | no, read-only |
| Holding        | 16-bit    | 0x03       | 0x06 / 0x10   |
| Input          | 16-bit    | 0x04       | no, read-only |

Addresses used in this library are the actual **protocol addresses**. 
The manuals of the manufacturer have an offset of one: Input register 1 of the manufacturer
documentation is address 0 here.

The manuals give human-friendly addresses with this scheme: `XYYYY`

- `X` is the concerning the address space, `X+1` equals the function code.
- `YYYY` is the address with an offset of `+1` to the protocol address, so the actual protocol address is `YYYY-1`.

For instance, when the manual states 10007, this means that:
- `1`: FC02, discrete input
- `0008`: Modbus address `7` (`=8-1`)

This is the Silent Mode sensor, see `states.py`:
```python
silent_mode = discrete_input(7, description="Silent mode active")
```

### Input registers (FC04, read-only)

| Address | Datapoint | Scale | Unit |
| ---: | :--- | ---: | :--- |
| 0 | `error_code` | 1 | |
| 1 | `odu_operation_cycle` | 1 | |
| 2 | `water_inlet_temperature` | 0.1 | °C |
| 3 | `water_outlet_temperature` | 0.1 | °C |
| 4 | `backup_heater_outlet_temperature` | 0.1 | °C |
| 5 | `dhw_tank_temperature` | 0.1 | °C |
| 6 | `solar_collector_temperature` | 0.1 | °C |
| 7 | `room_air_temperature_circuit_1` | 0.1 | °C |
| 8 | `water_flow_rate_raw` | 0.1 | L/min |
| 9 | `water_outlet_temperature_circuit_2` | 0.1 | °C |
| 10 | `room_air_temperature_circuit_2` | 0.1 | °C |
| 11 | `energy_state` | enum | |
| 12 | `outdoor_temperature` | 0.1 | °C |
| 16 | `liquid_pipe_temperature` | 1 | °C |
| 18 | `suction_temperature` | 1 | °C |
| 19 | `discharge_temperature` | 1 | °C |
| 20 | `evaporator_inlet_temperature` | 0.1 | °C |
| 21 | `evaporator_outlet_temperature` | 0.1 | °C |
| 22 | `refrigerant_high_pressure` | 1 | bar |
| 23 | `refrigerant_low_pressure` | 1 | bar |
| 24 | `compressor_frequency` | 1 | Hz |
| 9998 | `product_info` | raw | |

`sensors.compressor_speed` derives revolutions per minute from
`compressor_frequency`, and `sensors.water_temperature_difference` the spread
across the heat exchanger.

`solar_collector_temperature` reads as `None` when no solar collector is
fitted: the heat pump reports a fixed dummy value of 300.0 °C instead of
refusing the read, and that sentinel is masked. Set `pump.debug = True` to
see the raw 300.0 °C reading instead, e.g. while diagnosing a device.

### Holding registers (FC03 read, FC06 write)

| Address | Datapoint | Scale | Unit | Writable |
| ---: | :--- | ---: | :--- | :--- |
| 0 | `operation_mode` | enum | | ✔ |
| 1 | `control_method` | enum | | ✔ |
| 2 | `target_temperature_circuit_1` | 0.1 | °C | ✔ |
| 3 | `room_air_setpoint_circuit_1` | 0.1 | °C | ✔ |
| 4 | `shift_in_auto_mode_circuit_1` | 1 | K | ✔ |
| 5 | `target_temperature_circuit_2` | 0.1 | °C | ✔ |
| 6 | `room_air_setpoint_circuit_2` | 0.1 | °C | ✔ |
| 7 | `shift_in_auto_mode_circuit_2` | 1 | K | ✔ |
| 8 | `dhw_target_temperature` | 0.1 | °C | ✔ |
| 9 | `energy_state` | enum | | |

### Coils (FC01 read, FC05 write)

| Address | Datapoint |
| ---: | :--- |
| 0 | `heating_circuit` |
| 1 | `dhw` |
| 2 | `silent_mode` |
| 3 | `dhw_disinfection` |
| 4 | `emergency_stop` |
| 5 | `trigger_emergency_operation` |

### Discrete inputs (FC02, read-only)

| Address | Datapoint | | Address | Datapoint |
| ---: | :--- | --- | ---: | :--- |
| 0 | `water_flow` | | 9 | `solar_pump` |
| 1 | `water_pump` | | 10 | `backup_heater_step_1` |
| 2 | `external_water_pump` | | 11 | `backup_heater_step_2` |
| 3 | `compressor` | | 12 | `dhw_boost_heater` |
| 4 | `defrosting` | | 13 | `error` |
| 5 | `dhw_heating` | | 14 | `emergency_operation_space` |
| 6 | `dhw_disinfection` | | 15 | `emergency_operation_dhw` |
| 7 | `silent_mode` | | 16 | `mixing_pump` |
| 8 | `cooling` | | | |

## Device models

An installation without a second circuit, a hot water tank or a solar collector
should not be asked for registers it does not serve. Name the model and the
read plan narrows itself:

```python
from lg_heatpump_modbus import LgHeatPump, THERMA_V_SINGLE_CIRCUIT

pump = LgHeatPump(unit, model=THERMA_V_SINGLE_CIRCUIT)
pump.serves("target_temperature_circuit_2")  # False
```

| Model key | Circuits | Hot water | Solar | Mixing valve |
| :-------- | :------: | :-------: | :---: | :----------: |
| `therma_v` | 2 | ✔ | ✔ | ✔ |
| `therma_v_single_circuit` | 1 | ✔ | | |
| `therma_v_heating_only` | 2 | | ✔ | ✔ |

Individual datapoints can be excluded on top of the model, for a unit that
refuses one particular register:

```python
pump = LgHeatPump(unit, excluded_datapoints=["solar_collector_temperature"])
```

Adding a variant means adding a `ModelDefinition` to
`lg_heatpump_modbus/configurations/models.py`, never a code change.

## Datapoint metadata

Each datapoint carries neutral metadata, so an application can build its user
interface from the model instead of hard-coding it:

```python
metadata = pump.controls.require_metadata_for("dhw_target_temperature")
metadata.register_type  # 'holding'
metadata.address  # 8
metadata.writable  # True
metadata.number.unit  # '°C'
metadata.number.min_value, metadata.number.max_value  # 30.0, 80.0
metadata.number.step  # 0.1
```

The documented limits are the **protocol** limits. The range an installation
actually accepts is narrower and set by the installer; a value the heat pump
refuses comes back as a Modbus exception.

## Writing values

Writes go through the component that owns the register, either by attribute
name or through the named helpers:

```python
await pump.controls.write("dhw_target_temperature", 48.0)
await pump.controls.set_dhw_target_temperature(48.0)
await pump.switches.set_heating_circuit(True)
```

A value outside the documented range raises `LgValueValidationError` before
anything reaches the wire. Input registers and discrete inputs are read-only
and raise `AttributeError` if written.

## Contributing

Any contribution is welcome. Most valuable is testing against a real device.

#### 1. Prepare your hardware

Following the instructions in [Prerequisites](#prerequisites), connect a Modbus interface to your heat pump and enable Modbus communication.

#### 2. Set up the library (pre-PyPI stage)

Create a virtual Python environment and install `modbus-connection`, preferably with `tmodbus`:

```bash
python -m venv venv_modbus
pip install "modbus-connection[tmodbus]"
```

#### 3a. Run the query script and dump the outputs

Set up the library and run `script/query.py` against your heat pump. Parameters usually like this:

```
192.168.0.XXX --port 502 --unit 1 --json-dir C:\tmp\lg_json_dumps
```

Directly report the JSON output, as well as any unexpected values or errors.

Occasional `Response timeout`s are expected when the heat pump is still connected to another Modbus device (e.g., legacy Home Assistant integration). If you see a timeout, wait a few seconds and try again.

Add `--debug` to see raw sentinel values (e.g. `solar_collector_temperature`'s 300 °C no-sensor reading) instead of them being masked to `None` — handy when a reported value looks suspicious and you want to confirm what the heat pump actually sent.

#### 3b. Use the library from a Python interpreter

Play around with the controls, read the sensors, change controls and switches. Report any findings, unexpected values, or errors. A JSON file from the query script (see section beforehand) is also very valuable for debugging.

### Querying a real device

`script/query.py` connects to a heat pump, reads it once and prints every
value. It is the quickest way to check an installation with no application
around it. Add `--json-dir <folder>` to write a JSON snapshot for later
analysis; omit it to keep the script purely terminal-based:

```bash
python script/query.py 192.168.1.50 --unit 1
python script/query.py /dev/ttyUSB0 --transport serial --unit 1 --baudrate 9600
python script/query.py 192.168.1.50 --unit 1 --json-dir ./query-dumps
python script/query.py --help
```

After collecting a few JSON snapshots, you can convert them into a single CSV table:

```bash
python script/json_to_csv.py --input-dir ./query-dumps --output ./query-dumps.csv
```

The CSV contains one row per snapshot and one column per flattened datapoint,
with names like `components.sensors.outdoor_temperature` and
`derived.compressor_speed`.

### Development

```bash
script/run_checks.sh     # format check, lint, compile, test, build
python -m ruff check --fix .
python -m ruff format .
```

Tests run against the in-memory mock backend that ships with
`modbus-connection`, so the whole suite runs without hardware, a server or a
network.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
