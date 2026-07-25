from __future__ import annotations

import unittest

from finance_extension.periods import (
    PeriodSelectionError,
    available_periods,
    resolve_period,
    resolve_period_from_payload,
)


class PeriodResolutionTests(unittest.TestCase):
    def test_month_period_resolves_bounds_label_and_day_aggregation(self) -> None:
        period = resolve_period("MONTH", year=2026, month=7)
        self.assertEqual(period["start_date"], "2026-07-01")
        self.assertEqual(period["end_date"], "2026-07-31")
        self.assertEqual(period["display_label"], "Juli 2026")
        self.assertEqual(period["aggregation"], "DAY")

    def test_month_period_handles_year_boundary(self) -> None:
        period = resolve_period("MONTH", year=2026, month=12)
        self.assertEqual(period["start_date"], "2026-12-01")
        self.assertEqual(period["end_date"], "2026-12-31")
        next_year = resolve_period("MONTH", year=2027, month=1)
        self.assertEqual(next_year["start_date"], "2027-01-01")
        self.assertEqual(next_year["end_date"], "2027-01-31")

    def test_year_period_uses_month_aggregation(self) -> None:
        period = resolve_period("YEAR", year=2026)
        self.assertEqual(period["start_date"], "2026-01-01")
        self.assertEqual(period["end_date"], "2026-12-31")
        self.assertEqual(period["aggregation"], "MONTH")

    def test_custom_range_up_to_92_days_uses_day_aggregation(self) -> None:
        period = resolve_period("CUSTOM_RANGE", start_date="2026-01-01", end_date="2026-03-01")
        self.assertEqual(period["aggregation"], "DAY")

    def test_custom_range_beyond_92_days_uses_week_or_month_aggregation(self) -> None:
        period = resolve_period("CUSTOM_RANGE", start_date="2026-01-01", end_date="2026-06-01")
        self.assertEqual(period["aggregation"], "WEEK")
        wide = resolve_period("CUSTOM_RANGE", start_date="2026-01-01", end_date="2026-12-31")
        self.assertEqual(wide["aggregation"], "MONTH")

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodSelectionError, "FINANCE_PERIOD_SELECTION_INVALID"):
            resolve_period("QUARTER", year=2026)

    def test_invalid_month_number_is_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodSelectionError, "FINANCE_PERIOD_SELECTION_INVALID"):
            resolve_period("MONTH", year=2026, month=13)

    def test_custom_range_end_before_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodSelectionError, "FINANCE_PERIOD_SELECTION_INVALID"):
            resolve_period("CUSTOM_RANGE", start_date="2026-07-10", end_date="2026-07-01")

    def test_custom_range_missing_dates_is_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodSelectionError, "FINANCE_PERIOD_SELECTION_INVALID"):
            resolve_period("CUSTOM_RANGE", start_date=None, end_date="2026-07-01")

    def test_excessively_large_custom_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodSelectionError, "FINANCE_PERIOD_RANGE_TOO_LARGE"):
            resolve_period("CUSTOM_RANGE", start_date="2000-01-01", end_date="2026-01-01")

    def test_resolve_from_payload_supports_nested_and_flat_shape(self) -> None:
        nested = resolve_period_from_payload({"period": {"mode": "YEAR", "year": 2026}})
        self.assertEqual(nested["mode"], "YEAR")
        flat = resolve_period_from_payload({"mode": "YEAR", "year": 2026})
        self.assertEqual(flat["mode"], "YEAR")

    def test_available_periods_falls_back_to_today_when_no_events(self) -> None:
        class EmptyStore:
            def events(self, event_type: str | None = None) -> list:
                return []

        result = available_periods(EmptyStore())
        self.assertEqual(result["earliest_date"], result["latest_date"])
        self.assertIn("MONTH", result["supported_modes"])
        self.assertIn("QUARTER", result["reserved_modes"])


if __name__ == "__main__":
    unittest.main()
