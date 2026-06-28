"""Unit tests for MC reopen neighbour horizon filter."""

from __future__ import annotations

import unittest

from monthly_close_lib import (
    Period,
    filter_periods_to_horizon,
    mc_affected_periods,
    parse_period,
)


class McReopenHorizonTest(unittest.TestCase):
    """Horizon filter for reopen_neighbors."""

    def test_jan_2026_skips_dec_2025(self) -> None:
        """First ACT month: M-1 outside horizon is skipped."""
        target = parse_period("2026-01")
        horizon = [parse_period(f"2026-{m:02d}") for m in range(1, 13)]
        affected = mc_affected_periods(target)
        filtered = filter_periods_to_horizon(affected, horizon)
        self.assertEqual(
            [p.yyyy_mm for p in filtered],
            ["2026-01", "2026-02"],
        )
        skipped_keys = {p.yyyy_mm for p in affected} - {p.yyyy_mm for p in filtered}
        self.assertEqual(skipped_keys, {"2025-12"})

    def test_feb_2026_keeps_all_neighbours(self) -> None:
        """Mid-year month: M-1, M, M+1 inside horizon."""
        target = parse_period("2026-02")
        horizon = [parse_period(f"2026-{m:02d}") for m in range(1, 13)]
        filtered = filter_periods_to_horizon(mc_affected_periods(target), horizon)
        self.assertEqual(
            [p.yyyy_mm for p in filtered],
            ["2026-01", "2026-02", "2026-03"],
        )


if __name__ == "__main__":
    unittest.main()
