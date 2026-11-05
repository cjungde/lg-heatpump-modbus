"""L1 — logic over the register values.

Interpretation that needs more than one register, history or time lives here and
not in the register model. Everything in this package is pure Python: no I/O,
no Home Assistant, no wall-clock reads, and it never writes to the heat pump.
"""

from .flow_gate import (
    FLOW_GATE_KEY,
    FLOW_SENSOR_RANGE_FLOOR,
    gate_flow_rate,
)
from .oil_return_pulse import (
    EVENT_CANCELLED,
    EVENT_FINISHED,
    EVENT_IMMINENT,
    EVENT_STARTED,
    OilReturnPulseConfig,
    OilReturnPulseTracker,
    PulseRecord,
)

__all__ = [
    "EVENT_CANCELLED",
    "EVENT_FINISHED",
    "EVENT_IMMINENT",
    "EVENT_STARTED",
    "FLOW_GATE_KEY",
    "FLOW_SENSOR_RANGE_FLOOR",
    "OilReturnPulseConfig",
    "OilReturnPulseTracker",
    "PulseRecord",
    "gate_flow_rate",
]
