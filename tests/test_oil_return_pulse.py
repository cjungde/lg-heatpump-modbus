"""The ORP tracker, against the behaviour measured on the unit.

Every threshold in ``logic/oil_return_pulse.py`` came out of 29 logged pulses over seven
days, and three of them were only found by getting them wrong first: the inclusive
comparison at 1500 rpm, a stop resetting the clock instead of pausing it, and the
ramp belonging to the pulse rather than to the run-up. Those three are what these
tests are mostly about. The replay at the end runs the tracker over 48 hours that
were actually recorded.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import lg_heatpump_modbus.logic.oil_return_pulse as fp

DATA = Path(__file__).parent / "data"

T0 = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)
#: The unit is polled every 10 s on this installation.
POLL = timedelta(seconds=10)


def _tracker(**kwargs: int | float) -> fp.OilReturnPulseTracker:
    return fp.OilReturnPulseTracker(config=fp.OilReturnPulseConfig(**kwargs))


def _run(tracker, samples, start=T0, step=POLL):
    """Feed (rpm, kwargs) samples one poll apart; return (time, events) pairs."""
    seen = []
    now = start
    for sample in samples:
        rpm, extra = sample if isinstance(sample, tuple) else (sample, {})
        events = tracker.update(now, rpm, **extra)
        if events:
            seen.append((now, events))
        now += step
    return seen


def _hold(rpm, minutes, step=POLL):
    return [rpm] * int(minutes * 60 / step.total_seconds())


# --- the three details that cost time -----------------------------------


def test_exactly_1500_rpm_counts() -> None:
    """The unit sits at exactly 1500 for long stretches.

    With a `<` comparison those minutes never count and the pulse arrives as a
    surprise. This is the single most expensive mistake available here.
    """
    t = _tracker()
    _run(t, _hold(1500, 30))
    assert t.runtime_minutes is not None
    assert round(t.runtime_minutes) == 30


def test_a_stop_resets_the_clock_rather_than_pausing_it() -> None:
    """Measured intervals between pulses ran from 61 minutes to 23 hours.

    What counts is continuous running time in the band, so a stop must discard
    what was accumulated -- not resume from it.
    """
    t = _tracker()
    _run(t, _hold(1400, 40))
    assert round(t.runtime_minutes) == 40

    _run(t, [0], start=T0 + timedelta(minutes=40))
    assert t.runtime_minutes is None
    assert t.low_load_since is None

    _run(t, _hold(1400, 5), start=T0 + timedelta(minutes=41))
    assert round(t.runtime_minutes) == 5


def test_leaving_the_band_also_resets_the_clock() -> None:
    """Above 25 Hz the unit's own timer is not counting either."""
    t = _tracker()
    _run(t, _hold(1500, 20) + [1800] + _hold(1500, 3))
    assert round(t.runtime_minutes) == 3


def test_the_ramp_is_not_counted_as_run_up() -> None:
    """1140, 1680, 2280, 2880, 3600 -- the ramp belongs to the pulse.

    Attributing it to the low-load phase is what put the trigger at 60.0
    instead of 60.5 in the first analysis.
    """
    t = _tracker()
    events = _run(t, _hold(1500, 60) + [1140, 1680, 2280, 2880, 3600])
    kinds = [e for _, evs in events for e in evs]
    assert fp.EVENT_STARTED in kinds
    # 1680 already leaves the band, so the clock stopped before the pulse was
    # recognised; the runtime recorded with the pulse is the one at that point.
    assert t.pulse_active


# --- the trigger --------------------------------------------------------


def test_the_pulse_is_announced_before_it_arrives() -> None:
    """A warning that lands during the ramp is useless.

    The ramp is about 50 s, and anything that has to write to the machine first
    does not fit in that. Default lead is three minutes.
    """
    t = _tracker()
    events = _run(t, _hold(1500, 59))
    kinds = [e for _, evs in events for e in evs]
    assert kinds == [fp.EVENT_IMMINENT]

    ((fired_at, _),) = events
    assert round((fired_at - T0).total_seconds() / 60, 1) == 57.5


def test_the_announcement_is_made_once_not_every_poll() -> None:
    t = _tracker()
    events = _run(t, _hold(1500, 60))
    assert [e for _, evs in events for e in evs].count(fp.EVENT_IMMINENT) == 1


def test_a_reset_rearms_the_announcement() -> None:
    t = _tracker()
    _run(t, _hold(1500, 59) + [0])
    events = _run(t, _hold(1500, 59), start=T0 + timedelta(minutes=70))
    assert [e for _, evs in events for e in evs] == [fp.EVENT_IMMINENT]


def test_expected_time_is_the_measured_sixty_and_a_half_minutes() -> None:
    t = _tracker()
    _run(t, _hold(1500, 10))
    assert t.expected_at() == T0 + timedelta(minutes=60.5)
    # The last of 60 ten-second samples lands at 9:50, not 10:00, so the
    # remainder is one poll short of 50.5.
    assert round(t.minutes_remaining(), 1) == 50.7


# --- a whole pulse ------------------------------------------------------


def test_a_complete_pulse_is_recorded() -> None:
    t = _tracker()
    samples = _hold(1500, 60) + _hold(3600, 1) + [1500]
    events = _run(t, samples)
    kinds = [e for _, evs in events for e in evs]
    assert kinds == [fp.EVENT_IMMINENT, fp.EVENT_STARTED, fp.EVENT_FINISHED]

    record = t.last_pulse
    assert record is not None
    assert record.peak_rpm == 3600
    assert 40 <= record.duration_seconds <= 70
    assert not t.pulse_active


def test_the_runtime_before_the_pulse_is_kept_with_it() -> None:
    """The one number that says whether the mechanism still holds."""
    t = _tracker()
    _run(t, _hold(1500, 60) + _hold(3600, 1) + [1500])
    assert t.last_pulse.runtime_minutes is not None
    assert 55 <= t.last_pulse.runtime_minutes <= 65


RAMP = [1560, 2100, 2700, 3300, 3600]  # as recorded: 10 s per step, 30 s above the band


def test_the_runtime_survives_the_real_ramp() -> None:
    """The ramp leaves the band two polls before it is recognised as a pulse.

    The clock is cleared by then, so the runtime that belongs to the pulse has to
    be the one at the moment the compressor left the band. Found by replaying
    recorded data: an earlier version recorded none for every real pulse.
    """
    t = _tracker()
    # as recorded on 2026-11-02 01:38 UTC: 30 s at the top, then down
    _run(t, _hold(1500, 60) + [960] + RAMP + [3600, 3600, 3300, 1620, 900])
    assert t.last_pulse is not None
    assert t.last_pulse.peak_rpm == 3600
    assert 59 <= t.last_pulse.runtime_minutes <= 61


def test_a_climb_that_did_not_start_in_the_band_is_not_a_pulse() -> None:
    """Running at 1800 and then going to 3000 is a load change, not oil return.

    The unit's timer only counts at or below 25 Hz, so a ramp that does not begin
    there cannot be the ORP.
    """
    t = _tracker()
    events = _run(t, _hold(1800, 30) + [2400, 3000, 3300] + _hold(3300, 5) + [1800])
    assert [e for _, evs in events for e in evs] == []
    assert t.last_pulse is None
    assert not t.pulse_active


def test_a_slow_climb_after_a_defrost_is_not_a_pulse() -> None:
    """Recorded 2026-11-02 04:00 UTC: 118 s from 2400 to above 2900 rpm.

    An ORP climbs from the band to 2900 rpm in 30 s. This one left the band just
    after a defrost and took four times as long.
    """
    t = _tracker()
    samples = [1500, 2100, 2400] + [2400] * 10 + [2640, 3120, 3420]
    events = _run(t, samples)
    assert fp.EVENT_STARTED not in [e for _, evs in events for e in evs]
    assert not t.pulse_active


def test_reset_forgets_the_clock_and_the_last_pulse() -> None:
    t = _tracker()
    _run(t, _hold(1500, 60) + [960] + RAMP + [1500])
    assert t.last_pulse is not None
    t.reset()
    assert t.last_pulse is None
    assert t.runtime_minutes is None
    assert not t.pulse_active


# --- ORP against defrost ------------------------------------------------


def test_a_defrost_is_not_reported_as_a_pulse() -> None:
    """Both run the compressor up; the unit's own flag settles it.

    For two days in October 2026 the two were mistaken for each other.
    """
    t = _tracker()
    samples = _hold(1500, 60) + [(4020, {"defrosting": True})] * 9 + [(1500, {"defrosting": False})]
    events = _run(t, samples)
    kinds = [e for _, evs in events for e in evs]
    assert fp.EVENT_STARTED not in kinds
    assert fp.EVENT_FINISHED not in kinds
    assert t.last_pulse is None


def test_a_late_defrost_flag_still_does_not_produce_a_pulse() -> None:
    """The flag can arrive a poll after the ramp.

    4020 rpm alone is enough: an ORP peaks at exactly 3600, a defrost at 4020-4080.
    """
    t = _tracker()
    samples = _hold(1500, 60) + [3600, 4020, (4080, {"defrosting": True}), 1500]
    _run(t, samples)
    assert t.last_pulse is None
    assert not t.pulse_active


def test_the_clock_restarts_after_a_defrost() -> None:
    t = _tracker()
    _run(t, _hold(1500, 60) + [(4020, {"defrosting": True})])
    _run(t, _hold(1500, 4), start=T0 + timedelta(minutes=62))
    assert round(t.runtime_minutes) == 4


# --- readings that are not observations ---------------------------------


def test_an_unreadable_poll_is_skipped_not_taken_as_a_stop() -> None:
    """A dropped Modbus read is not a compressor stop.

    Treating it as one resets a clock that is in fact still running, and the
    next pulse then arrives unannounced.
    """
    t = _tracker()
    _run(t, _hold(1500, 20) + [None, None] + _hold(1500, 1))
    assert round(t.runtime_minutes) == 21


def test_a_tank_charge_keeps_the_clock_running() -> None:
    """The unit's timer does not care what the heat is used for.

    Only the pulse's comparison against the heating curve becomes meaningless
    during a charge, which is a matter for whoever reads the record.
    """
    t = _tracker()
    _run(t, [(1500, {"dhw_heating": True})] * 120)
    assert round(t.runtime_minutes) == 20


def test_nothing_is_claimed_before_the_first_observation() -> None:
    t = _tracker()
    assert t.runtime_minutes is None
    assert t.expected_at() is None
    assert t.minutes_remaining() is None
    assert not t.pulse_active
    assert not t.low_load


# --- configurability ----------------------------------------------------


def test_the_lead_time_is_configurable() -> None:
    t = _tracker(lead_seconds=600)
    events = _run(t, _hold(1500, 55))
    ((fired_at, _),) = events
    assert round((fired_at - T0).total_seconds() / 60, 1) == 50.5


# --- a warning that no pulse follows ----------------------------------


def _kinds(events: list) -> list[str]:
    return [e for _, evs in events for e in evs]


def test_a_stop_after_the_warning_cancels_it() -> None:
    """Whoever acted on the warning must hear that no pulse is coming."""
    t = _tracker()
    events = _run(t, _hold(1500, 59) + [0, 0])
    assert _kinds(events) == [fp.EVENT_IMMINENT, fp.EVENT_CANCELLED]


def test_leaving_the_band_without_a_ramp_cancels_the_warning() -> None:
    """Up to 1800 rpm and staying there: the 90 s for a ramp run out."""
    t = _tracker()
    events = _run(t, _hold(1500, 59) + _hold(1800, 3))
    assert _kinds(events) == [fp.EVENT_IMMINENT, fp.EVENT_CANCELLED]


def test_a_defrost_after_the_warning_cancels_it() -> None:
    t = _tracker()
    events = _run(t, _hold(1500, 59) + [(4020, {"defrosting": True})] * 3)
    assert _kinds(events) == [fp.EVENT_IMMINENT, fp.EVENT_CANCELLED]


def test_a_real_pulse_is_never_cancelled() -> None:
    t = _tracker()
    events = _run(t, _hold(1500, 60) + [960] + RAMP + [3600, 3600, 3300, 1620, 900])
    assert _kinds(events) == [fp.EVENT_IMMINENT, fp.EVENT_STARTED, fp.EVENT_FINISHED]


def test_nothing_is_cancelled_without_a_warning() -> None:
    t = _tracker()
    assert _kinds(_run(t, _hold(1500, 30) + [0])) == []


def test_a_climb_far_longer_than_an_orp_is_dropped() -> None:
    """A tank charge that climbs out of the band runs for minutes.

    On 27 October 2026 the earlier tracker reported one as a 500 s 'pulse'. An
    ORP lasts 40-60 s.
    """
    t = _tracker()
    events = _run(t, _hold(1500, 59) + [960] + RAMP + _hold(3300, 8) + [1500])
    assert fp.EVENT_FINISHED not in _kinds(events)
    assert _kinds(events)[-1] == fp.EVENT_CANCELLED
    assert t.last_pulse is None


# --- a slower poll ------------------------------------------------------


def test_an_earlier_exit_does_not_lend_its_runtime_to_a_later_pulse() -> None:
    """At 30 s a pulse can jump from 1500 to 3600 rpm between two polls.

    The tracker then has no exit of its own for this run and must not use the
    one from an earlier excursion: that reported 20 minutes for a 61 minute run.
    """
    slow = timedelta(seconds=30)
    t = _tracker()
    samples = _hold(1500, 20, slow) + [1800, 1800] + _hold(1500, 61, slow)
    samples += [3600, 3600, 1500]
    _run(t, samples, step=slow)
    assert t.last_pulse is not None
    assert 60 <= t.last_pulse.runtime_minutes <= 62


# --- replay over recorded data ------------------------------------------


def _utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _data_rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open() as handle:
        return list(csv.DictReader(line for line in handle if not line.startswith("#")))


@pytest.mark.parametrize("poll_seconds", [10, 30])
def test_replay_over_48_recorded_hours_finds_every_logged_pulse(poll_seconds: int) -> None:
    """The installation's own log recorded 30 pulses in this window.

    The recording is what the integration read, one row per change, polled every
    10 s; replayed at 10 s and, sampled down, at 30 s. Every logged pulse has exactly
    one started event within the 40 s the log lags behind, each preceded by one
    warning and followed by one finish, and the runtime recorded with the pulse is
    the 60 minutes measured on this unit. The one extra pulse is the one at 23:28 UTC, logged after this window ends.
    """
    changes = [(_utc(r["timestamp_utc"]), r) for r in _data_rows("orp_recording.csv")]
    logged = [_utc(r["pulse_logged_utc"]) for r in _data_rows("orp_logged.csv")]
    assert len(logged) == 30

    tracker = fp.OilReturnPulseTracker()
    events: list[tuple[str, datetime]] = []
    runtimes: list[float | None] = []
    now, end = changes[0][0], _utc("2026-11-04T00:00:00Z")
    index, current = 0, changes[0][1]
    while now < end:
        while index + 1 < len(changes) and changes[index + 1][0] <= now:
            index += 1
            current = changes[index][1]
        rpm = None if current["rpm"] == "" else int(current["rpm"])
        for event in tracker.update(
            now,
            rpm,
            defrosting=current["defrosting"] == "1",
            dhw_heating=current["dhw_heating"] == "1",
        ):
            events.append((event, now))
            if event == fp.EVENT_STARTED:
                runtimes.append(tracker.pulse_runtime_minutes)
        now += timedelta(seconds=poll_seconds)

    started = [t for e, t in events if e == fp.EVENT_STARTED]
    assert len(started) == len(logged) + 1
    for pulse in logged:
        assert min(abs((s - pulse).total_seconds()) for s in started) <= 60
    kinds = [e for e, _ in events]
    assert kinds.count(fp.EVENT_IMMINENT) == kinds.count(fp.EVENT_FINISHED) == len(started)
    assert fp.EVENT_CANCELLED not in kinds
    assert all(r is not None and 59 <= r <= 61 for r in runtimes)
