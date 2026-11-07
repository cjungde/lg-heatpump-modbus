"""Power and modulation limits, which only some units serve (holding 23/24, coil 6).

The manual lists three settings that limit what the heat pump draws. Units differ
in which of them they serve, and no two of three known units agree:

* a 9 kW R290 (HM093HFX UB60 / HN1639HC NK0) serves none of them, measured on
  2026-10-03 and 2026-10-17: all three stay silent while holding 22 and coil 5
  answer in the same second;
* a 12 kW unit serves holding 23;
* the manual of an HM071HF UB40 lists coil 6 and holding 24, but not holding 23.

An address a unit does not serve is **not refused, it is not answered at all**, so
reading it costs a full request timeout. That is why these settings are not part
of any regular poll: :class:`~lg_heatpump_modbus.heatpump.LgHeatPump` reads only
the ones a consumer has declared served (``set_served_limits``), and finds out
which those are with one probe per register (``async_probe_limit``). Whether to
trust a remembered answer is the consumer's policy; the library only asks.
"""

from __future__ import annotations

from typing import Any

from ..data_model import LgComponent, coil, gauge, integer
from ..exceptions import LgValueValidationError

PERCENT = "%"
KILOWATT = "kW"

#: The lowest modulation limit the manual allows; 0 switches the limit off.
MIN_MODULATION_LIMIT = 40


def _validate_modulation_limit(value: Any) -> Any:
    """Allow 0 (no limit) or 40-100 %; the device clamps 1-39 without refusing."""
    if 0 < float(value) < MIN_MODULATION_LIMIT:
        raise LgValueValidationError(f"Value {value} is not 0 (no limit) or {MIN_MODULATION_LIMIT}-100 %")
    return value


#: The datapoints of :class:`Limits`, in probing order.
LIMIT_DATAPOINTS: tuple[str, ...] = (
    "compressor_modulation_limit",
    "power_limit",
    "power_limit_enable",
)


class Limits(LgComponent):
    """Optional limits on compressor modulation and power draw.

    Each address is its own readable range: holding 23 and 24 are neighbours,
    but a block read covering both is dropped as a whole by a unit that serves
    only one of them.
    """

    register_space = "holding"
    register_ranges = ((23, 23), (24, 24))
    coil_ranges = ((6, 6),)

    # not verified on hardware by the library authors; served by a 12 kW unit
    compressor_modulation_limit = integer(
        23,
        signed=False,
        unit=PERCENT,
        min_value=0,
        max_value=100,
        writable=True,
        validator=_validate_modulation_limit,
        description="Maximum compressor modulation, 40-100 %; 0 means no limit",
    )
    """Maximum compressor modulation in percent, ``0`` when no limit is set."""

    # not verified on hardware; listed in the manual of an HM071HF UB40
    power_limit = gauge(
        24,
        0.1,
        signed=False,
        unit=KILOWATT,
        digits=1,
        min_value=0.1,
        max_value=25.0,
        writable=True,
        description="Active power limit, 0.1-25.0 kW; applies while enabled",
    )
    """Active power limit in kilowatts; only obeyed while ``power_limit_enable``."""

    # not verified on hardware; listed in the manual of an HM071HF UB40
    power_limit_enable = coil(6, writable=True, description="Limit power according to the power limit")
    """Whether the unit obeys :attr:`power_limit`; off means it runs unrestricted."""

    async def set_compressor_modulation_limit(self, value: int) -> None:
        """Set the maximum compressor modulation (0 = no limit, else 40-100 %)."""
        await self.write("compressor_modulation_limit", value)

    async def set_power_limit(self, value: float) -> None:
        """Set the active power limit in kilowatts (0.1-25.0)."""
        await self.write("power_limit", value)

    async def set_power_limit_enable(self, value: bool) -> None:
        """Make the unit obey, or ignore, the power limit."""
        await self.write("power_limit_enable", value)
