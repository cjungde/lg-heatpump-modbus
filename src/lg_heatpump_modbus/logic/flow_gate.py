"""Report no water flow while the internal water pump is off.

Mechanism
---------
The flow sensor is a SIKA VVX20 vortex meter with a measuring range of
5-80 l/min. It cannot measure below the start of its range, so with the pump
stopped the heat pump reports exactly 5.0 l/min, which is the range floor and
not a flow. Anything that integrates the flow (thermal power ``flow x spread x
4186 J/(kg K)``) then books heat that was never delivered: roughly 350 W per
1 K of residual spread at standstill.

The pump bit is the gate, not a threshold on the value being corrected: it is
a signal the device itself provides.

Measured basis
--------------
Constants and claims come from one R290 installation and one R32 installation
(reported by G-nn-r in lg-heatpump/meta discussion 3):

* 4 weeks of data with the pump bit recorded, 136,583 flow samples: 5.0 never
  occurred with the pump on, and the pump bit and the flow bit agreed in every
  hour.
* The ramp samples between 5.0 and about 12 l/min that arrive while the bit is
  still off are dropped as well, about 0.1 % of the samples, all transitions.
* On the R290 installation, 2535 hours between May and October 2026 reported
  5.0 at 0 rpm; the phantom power added up to about 925 kWh.

Contract (design guideline, section 5): stateless, idempotent, never writes to
the device. An input that is ``None`` leaves the output unchanged, because
guessing would replace a real reading with a made-up one.
"""

from __future__ import annotations

from typing import Final

#: Registry key of this function.
FLOW_GATE_KEY: Final = "flow_gate"

#: Start of the flow sensor's measuring range in l/min (SIKA VVX20, 5-80 l/min).
#: The value the heat pump reports for "no flow"; documentation only, the gate
#: does not compare against it.
FLOW_SENSOR_RANGE_FLOOR: Final = 5.0


def gate_flow_rate(
    flow_rate: float | None,
    pump_running: bool | None,
) -> float | None:
    """Return the flow with the sensor's range floor removed.

    ``flow_rate`` is the value as the heat pump reports it, ``pump_running`` the
    internal water pump bit. With the pump off the flow is ``0.0``. With the pump
    on, or when either input is unknown, ``flow_rate`` is returned unchanged.
    """
    if flow_rate is None or pump_running is None:
        return flow_rate
    return flow_rate if pump_running else 0.0
