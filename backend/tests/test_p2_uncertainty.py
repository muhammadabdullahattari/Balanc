"""Chronological calibration must abstain on insufficient or failed validation."""

from decimal import Decimal

from ledgerpilot.reports.uncertainty import calibrated_radius


def test_interval_abstains_on_insufficient_or_nonfinite_samples() -> None:
    assert calibrated_radius([Decimal(1)] * 29) == (None, None)
    assert calibrated_radius([Decimal("NaN")] * 30) == (None, None)


def test_validation_is_separate_and_does_not_widen_calibration() -> None:
    calibration = [Decimal(value) for value in range(1, 21)]
    radius, coverage = calibrated_radius(calibration + [Decimal(100)] * 10)
    assert radius is None and coverage == 0
    radius, coverage = calibrated_radius(calibration + [Decimal(-16)] * 8 + [Decimal(20)] * 2)
    assert radius == 17 and coverage == Decimal("0.8")
