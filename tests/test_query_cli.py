"""The bundled query script parses its arguments and prints every sub-system."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from modbus_connection.mock import MockModbusUnit

from lg_heatpump_modbus import LgHeatPump

SCRIPT = Path(__file__).resolve().parents[1] / "script" / "query.py"


def _load() -> ModuleType:
    """Import script/query.py as a module, without installing it."""
    spec = importlib.util.spec_from_file_location("lg_query", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def query() -> ModuleType:
    """Return the query script, imported once."""
    return _load()


def test_the_script_exists() -> None:
    assert SCRIPT.is_file()


def test_defaults(query: ModuleType) -> None:
    args = query._parse_args(["192.168.1.50"])

    assert args.unit == 1
    assert args.model == "therma_v"
    assert args.json_dir is None
    assert args.debug is False


def test_debug_flag_can_be_set(query: ModuleType) -> None:
    args = query._parse_args(["192.168.1.50", "--debug"])

    assert args.debug is True


def test_unit_and_model_can_be_chosen(query: ModuleType) -> None:
    args = query._parse_args(
        ["192.168.1.50", "--unit", "3", "--model", "therma_v_heating_only"]
    )

    assert args.unit == 3
    assert args.model == "therma_v_heating_only"
    assert args.json_dir is None


def test_json_dir_can_be_chosen(query: ModuleType, tmp_path: Path) -> None:
    args = query._parse_args(["192.168.1.50", "--json-dir", str(tmp_path)])

    assert args.json_dir == tmp_path


def test_an_unknown_model_is_rejected(query: ModuleType) -> None:
    with pytest.raises(SystemExit):
        query._parse_args(["192.168.1.50", "--model", "therma_x"])


def test_sections_cover_every_sub_system(query: ModuleType, pump: LgHeatPump) -> None:
    printed = {attribute for _, attribute in query.SECTIONS}

    assert printed == set(pump.component_names)


async def test_printing_a_read_heat_pump(
    query: ModuleType,
    pump: LgHeatPump,
    unit: MockModbusUnit,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await pump.async_update()

    query._print(pump)
    query._print_derived(pump)

    out = capsys.readouterr().out
    assert "Measurements" in out
    assert "outdoor_temperature" in out
    assert "-4.2" in out
    assert "3720" in out  # derived compressor speed
    assert "water flow rate                     17.2 l/min" in out


async def test_json_dump_snapshot(
    query: ModuleType,
    pump: LgHeatPump,
    unit: MockModbusUnit,
    tmp_path: Path,
) -> None:
    await pump.async_update()

    args = query._parse_args(["192.168.1.50", "--json-dir", str(tmp_path)])
    snapshot = query._snapshot(
        pump,
        elapsed_ms=123.4,
        modbus_reads=7,
        failed={},
        args=args,
    )
    path = query._dump_json_snapshot(args, snapshot)

    assert path is not None and path.parent == tmp_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["model"] == "therma_v"
    assert payload["debug"] is False
    assert payload["components"]["sensors"]["outdoor_temperature"] == -4.2
    assert payload["derived"]["compressor_speed"] == 3720
    assert payload["derived"]["water_flow_rate"] == 17.2
    assert payload["components"]["sensors"]["water_flow_rate_raw"] == 17.2


async def test_snapshot_lists_the_gated_flow_under_derived(
    query: ModuleType,
    pump: LgHeatPump,
    unit: MockModbusUnit,
) -> None:
    """With the pump off the snapshot keeps the reported 5.0 and derives 0.0."""
    unit.discrete_inputs[1] = False  # water pump
    unit.input[8] = 50  # water_flow_rate_raw -> 5.0 l/min, the range floor
    await pump.async_update()

    args = query._parse_args(["192.168.1.50"])
    snapshot = query._snapshot(pump, elapsed_ms=1.0, modbus_reads=7, failed={}, args=args)

    assert snapshot["components"]["sensors"]["water_flow_rate_raw"] == 5.0
    assert "water_flow_rate" not in snapshot["components"]["sensors"]
    assert snapshot["derived"]["water_flow_rate"] == 0.0


def test_absent_values_print_as_a_dash(query: ModuleType) -> None:
    assert query._fmt(None) == "-"
    assert query._fmt(42) == "42"
