"""The oil-return flush pulse: when it comes, when it is running, when it is over.

Every so often the unit runs the compressor up to 60 Hz for about a minute. It
is returning oil from the refrigerant circuit to the compressor, and it is
normal -- but it ends a heating run, so it looks like short cycling to anyone
watching the compressor, and it is the one event an outside controller wants to
know about in advance.

THE TRIGGER, measured rather than assumed. On a HN1639HC NK0 over seven days,
29 pulses: the unit flushes after **60.5 minutes of uninterrupted running at or
below 25 Hz** (1500 rpm). All 29 fell in the 55-65 minute band, median 60.3, and
24 of 24 long low-load phases ended in a pulse with none skipped. Three details
cost time to find:

* The threshold is **inclusive**. The unit sits at exactly 1500 rpm for long
  stretches, and a `<` comparison never sees those minutes.
* **A compressor stop resets the clock**, it does not pause it. The time is
  continuous running time in the band, not time of day: measured intervals
  between pulses ranged from 61 minutes to 23 hours, median 124.
* The ramp up (1140, 1680, 2280, 2880, 3600 rpm, about 10 s per step) belongs
  to the pulse. Counting it as part of the run-up puts the trigger at 60.0.

A second account agrees on the threshold from a different direction: a forum
post gives "below 25 Hz for longer than 60 minutes", unsourced. 25 Hz is 1500
rpm, so the two readings are the same rule.

TELLING A FLUSH FROM A DEFROST. Both run the compressor up, and for two days in
October 2026 that cost us two misreadings. They separate cleanly on two
independent signals:

| | flush pulse | defrost |
|---|---|---|
| peak | **exactly 3600 rpm** (60.0 Hz) | 4020-4080 rpm (67-68 Hz) |
| length | 40-60 s | about 90 s |

The peak being exactly 3600 on every day a pulse occurs, never 3580 or 3620,
says 60 Hz is a setpoint of the routine rather than an incidental maximum. The
unit's own defrost flag settles it regardless, and is what this module uses;
the numbers above are the fallback when the flag is late, which it can be by
one poll.

Contract (design guideline, section 5): a stateful L1 function. All state sits
in one object that can be reset and replayed; the caller passes `now` and the
observed values, nothing here reads a clock, performs I/O or writes to the
device. The one reaction the unit's owners want -- raising the flow temperature
before a pulse -- is deliberately not here: that is a controller (L3) built on
the "imminent" and "finished" events.

EVIDENCE. One unit (R290, HN1639HC NK0), seven days, 29 pulses. Other owners
confirm that pulses occur on other units, but the trigger of 60.5 minutes in the
band is not yet confirmed there. The function is therefore labelled single-unit
and the integration ships it off by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: Event names returned by `FlushPulseTracker.update`. An integration that fires
#: one bus event and distinguishes by `type` can build device triggers on them.
EVENT_IMMINENT = "flush_pulse_imminent"
EVENT_STARTED = "flush_pulse_started"
EVENT_FINISHED = "flush_pulse_finished"
#: A warning went out, but no pulse followed: the compressor stopped, left the
#: band without ramping, defrosted, or the climb was not a flush. Lets a consumer
#: undo whatever it did on the warning.
EVENT_CANCELLED = "flush_pulse_cancelled"


@dataclass(frozen=True)
class FlushPulseConfig:
    """Thresholds, all measured. Changing one invalidates the figures above."""

    #: Upper bound of the low-load band, inclusive. 1500 rpm = 25 Hz.
    low_load_rpm: int = 1500
    #: Uninterrupted minutes in that band before the unit flushes.
    trigger_minutes: float = 60.5
    #: How early the imminent event fires. The ramp alone is about 50 s, which
    #: is too late for anything that has to talk to the machine first: a Modbus
    #: write plus the unit's reaction does not fit in it.
    lead_seconds: int = 180
    #: Above this the compressor is ramping, not modulating.
    pulse_rpm: int = 2900
    #: Above this it is not a flush. Only used when the defrost flag is late.
    defrost_rpm: int = 3700
    #: A ramp counts as a flush only if the compressor left the low-load band no
    #: longer than this ago. Measured on 30 pulses: 30 s from the last sample in
    #: the band to the first above 2900 rpm. A slower climb, such as the 118 s one
    #: after a defrost, is a load change.
    ramp_window_seconds: int = 90
    #: Longest a flush runs from the first sample above 2900 rpm to the last.
    #: Measured 40-60 s; a tank charge that ramps out of the band runs for many
    #: minutes and is dropped once it passes this.
    max_pulse_seconds: int = 120


@dataclass(frozen=True)
class PulseRecord:
    """What one completed pulse looked like."""

    started: datetime
    ended: datetime
    peak_rpm: int
    #: Running time in the low-load band before it, in minutes. `None` when the
    #: clock was not running long enough to be meaningful -- after a restart,
    #: for instance, where the unit's own timer kept running and ours did not.
    runtime_minutes: float | None

    @property
    def duration_seconds(self) -> float:
        """40-60 s for a flush. A defrost runs about 90 s and is not recorded here."""
        return (self.ended - self.started).total_seconds()


@dataclass
class FlushPulseTracker:
    """Follows the compressor and says where in the cycle the unit is.

    Fed once per poll. Returns the events that the poll caused, so the caller
    does not have to diff states to find them.

    After a Home Assistant restart the clock starts from zero, because nothing
    in the unit reports how long it has already been in the band. The device's
    own timer keeps running, so the first pulse after a restart can arrive
    earlier than predicted. That is reported as `runtime_minutes = None` rather
    than as a wrong number -- the same choice the YAML version made when it
    left its forecast columns empty.
    """

    config: FlushPulseConfig = field(default_factory=FlushPulseConfig)

    low_load_since: datetime | None = None
    #: Set when a pulse is running. Also the marker for "we are in a pulse".
    pulse_started: datetime | None = None
    pulse_peak_rpm: int = 0
    #: Running time in the band at the moment the pulse began.
    pulse_runtime_minutes: float | None = None
    last_pulse: PulseRecord | None = None
    #: Guards against re-announcing the same approach on every poll.
    _imminent_fired: bool = False
    #: True while a ramp is attributed to defrosting rather than flushing.
    _in_defrost: bool = False
    #: Set when the compressor leaves the low-load band upwards: the running time
    #: in the band at that moment, and when it left. A flush always starts from
    #: there; a ramp that does not is an ordinary load change.
    _band_exit_runtime: float | None = None
    _band_exit_at: datetime | None = None
    #: A warning went out and neither a finish nor a cancellation has followed.
    _announced: bool = False

    # -- what the entities read ------------------------------------------

    @property
    def runtime_minutes(self) -> float | None:
        """Uninterrupted running time in the low-load band."""
        return self._runtime_at(self._now) if self.low_load_since else None

    @property
    def pulse_active(self) -> bool:
        """Whether a pulse is running right now."""
        return self.pulse_started is not None

    @property
    def low_load(self) -> bool:
        """Whether the compressor is in the low-load band and the clock is running."""
        return self.low_load_since is not None

    _now: datetime | None = None

    def _runtime_at(self, now: datetime | None) -> float | None:
        if self.low_load_since is None or now is None:
            return None
        return max(0.0, (now - self.low_load_since).total_seconds() / 60)

    def expected_at(self) -> datetime | None:
        """When the unit is expected to flush, or None if the clock is not running."""
        if self.low_load_since is None:
            return None
        return self.low_load_since + timedelta(minutes=self.config.trigger_minutes)

    def minutes_remaining(self) -> float | None:
        """Minutes until the unit is expected to flush, or None if the clock is not running."""
        runtime = self.runtime_minutes
        if runtime is None:
            return None
        return max(0.0, self.config.trigger_minutes - runtime)

    # -- being fed -------------------------------------------------------

    def update(
        self,
        now: datetime,
        rpm: int | float | None,
        *,
        defrosting: bool = False,
        dhw_heating: bool = False,
    ) -> tuple[str, ...]:
        """Take one observation and return the events it caused.

        `rpm` of None means the value was not readable this poll. That is not a
        compressor stop: treating it as one would reset a clock that is in fact
        still running, so the observation is skipped entirely.
        """
        self._now = now
        if rpm is None:
            return ()

        rpm = int(rpm)
        events: list[str] = []

        if self.pulse_started is not None:
            self.pulse_peak_rpm = max(self.pulse_peak_rpm, rpm)
            if defrosting or self.pulse_peak_rpm > self.config.defrost_rpm:
                # Not a flush after all. The unit's flag can arrive a poll late,
                # so this can happen mid-ramp; drop the pulse without reporting
                # one, and let the clock restart from the end of the ramp.
                self._in_defrost = True
                self._abandon_pulse()
                return self._cancel()
            if (now - self.pulse_started).total_seconds() > self.config.max_pulse_seconds:
                # Far longer than a flush: a load change or a tank charge that
                # happened to climb out of the band.
                self._abandon_pulse()
                return self._cancel()
            if rpm <= self.config.pulse_rpm:
                self._announced = False
                events.append(EVENT_FINISHED)
                self.last_pulse = PulseRecord(
                    started=self.pulse_started,
                    ended=now,
                    peak_rpm=self.pulse_peak_rpm,
                    runtime_minutes=self.pulse_runtime_minutes,
                )
                self._clear_pulse()
                # The clock restarts only once the compressor is back in the
                # band, which the next poll decides.
            return tuple(events)

        if rpm > self.config.pulse_rpm:
            if defrosting or self._in_defrost or rpm > self.config.defrost_rpm:
                self._in_defrost = True
                self.low_load_since = None
                self._imminent_fired = False
                return self._cancel()
            if not self._ramp_started_in_band(now):
                # Running above 2900 rpm, but not out of the low-load band: a
                # high-load phase, not a flush.
                self.low_load_since = None
                self._imminent_fired = False
                return self._cancel()
            self.pulse_started = now
            self.pulse_peak_rpm = rpm
            # The ramp left the band a few polls ago, so the clock was already
            # cleared; the running time that counts is the one at that moment.
            self.pulse_runtime_minutes = (
                self._band_exit_runtime if self._band_exit_at is not None else self._runtime_at(now)
            )
            self.low_load_since = None
            self._imminent_fired = False
            self._band_exit_runtime = None
            self._band_exit_at = None
            events.append(EVENT_STARTED)
            return tuple(events)

        self._in_defrost = False

        if rpm == 0:
            # A stop resets the clock rather than pausing it.
            self.low_load_since = None
            self._imminent_fired = False
            self._band_exit_runtime = None
            self._band_exit_at = None
            return self._cancel()

        if rpm > self.config.low_load_rpm:
            # Running, but out of the band: the unit's timer is not counting.
            if self.low_load_since is not None:
                self._band_exit_runtime = self._runtime_at(now)
                self._band_exit_at = now
            self.low_load_since = None
            self._imminent_fired = False
            if self._band_exit_at is None or not self._ramp_started_in_band(now):
                # Out of the band for longer than any ramp: no pulse is coming.
                return self._cancel()
            return ()

        # In the band. Note the inclusive comparison above: the unit sits at
        # exactly 1500 for long stretches.
        if dhw_heating:
            # A tank charge drives the flow above 50 C and the pulse is not
            # counted against the heating curve, but the compressor is still
            # running in the band and the unit's timer does not care. Keep
            # counting; only the classification of the pulse differs.
            pass

        if self.low_load_since is None:
            # Back in the band: the clock starts again, and an earlier exit
            # no longer belongs to the run that is now being timed.
            self.low_load_since = now
            self._imminent_fired = False
            self._band_exit_runtime = None
            self._band_exit_at = None
            return self._cancel()

        remaining = self.minutes_remaining()
        if not self._imminent_fired and remaining is not None and remaining * 60 <= self.config.lead_seconds:
            self._imminent_fired = True
            self._announced = True
            events.append(EVENT_IMMINENT)
        return tuple(events)

    def reset(self) -> None:
        """Forget everything, as after a restart: the clock starts from zero."""
        self.low_load_since = None
        self.last_pulse = None
        self._clear_pulse()
        self._imminent_fired = False
        self._in_defrost = False
        self._band_exit_runtime = None
        self._band_exit_at = None
        self._announced = False
        self._now = None

    # -- internals -------------------------------------------------------

    def _cancel(self) -> tuple[str, ...]:
        """Withdraw an outstanding warning, if there is one."""
        if not self._announced:
            return ()
        self._announced = False
        return (EVENT_CANCELLED,)

    def _ramp_started_in_band(self, now: datetime) -> bool:
        """Whether a ramp above the pulse threshold began in the low-load band."""
        if self.low_load_since is not None:
            return True  # jumped straight out of the band within one poll
        if self._band_exit_at is None:
            return False
        return (now - self._band_exit_at).total_seconds() <= self.config.ramp_window_seconds

    def _clear_pulse(self) -> None:
        self.pulse_started = None
        self.pulse_peak_rpm = 0
        self.pulse_runtime_minutes = None

    def _abandon_pulse(self) -> None:
        self._clear_pulse()
        self.low_load_since = None
        self._imminent_fired = False
