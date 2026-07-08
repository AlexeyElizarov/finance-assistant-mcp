"""Unit tests for FIN-106 methodology fields in period status reports."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from monthly_close_lib import (
    Period,
    compact_period_summary,
    parse_period,
    period_status_report,
)


VID = "00000000-0000-4000-8000-000000000001"


class _MockApi:
    """Minimal API client stub for reconciliation and classification-summary."""

    def __init__(
        self,
        reconciliation: dict[str, dict[str, Any]],
        *,
        row_counts: dict[str, int] | None = None,
    ) -> None:
        self._reconciliation = reconciliation
        self._row_counts = row_counts or {}

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith("/budget/reconciliation"):
            qs = parse_qs(parsed.query)
            period_start = qs.get("period", [""])[0]
            yyyy_mm = period_start[:7]
            return dict(self._reconciliation[yyyy_mm])
        if parsed.path.endswith("/transactions/classification-summary"):
            qs = parse_qs(parsed.query)
            ymmm = qs.get("period", [""])[0]
            yyyy_mm = f"{ymmm[:4]}-{ymmm[4:6]}"
            return {"row_count": self._row_counts.get(yyyy_mm, 0)}
        raise AssertionError(f"unexpected get_json path: {path}")


class PeriodStatusMethodologyTest(unittest.TestCase):
    """Methodology passthrough and aggregates (FIN-106)."""

    def test_status_only_preliminary_closed(self) -> None:
        """T1: status_only includes close_phase for preliminary_closed."""
        periods = [parse_period("2026-06")]
        api = _MockApi(
            {
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "preliminary_closed",
                    "close_phase": "preliminary",
                }
            }
        )
        report = period_status_report(
            api, VID, periods, detail="status_only", skip_empty=True
        )
        row = report["periods"][0]
        self.assertEqual(row["methodology_status"], "preliminary_closed")
        self.assertEqual(row["close_phase"], "preliminary")
        self.assertEqual(report["preliminary_closed_count"], 1)
        self.assertEqual(report["preliminary_closed_periods"], ["2026-06"])

    def test_open_after_reopen(self) -> None:
        """T2: open month after reopen — methodology open, close_phase null."""
        periods = [parse_period("2026-06")]
        api = _MockApi(
            {
                "2026-06": {
                    "status": "open",
                    "methodology_status": "open",
                    "close_phase": None,
                }
            }
        )
        row = period_status_report(
            api, VID, periods, detail="status_only", skip_empty=True
        )["periods"][0]
        self.assertEqual(row["reconciliation_status"], "open")
        self.assertEqual(row["methodology_status"], "open")
        self.assertIsNone(row["close_phase"])

    def test_legacy_final_closed(self) -> None:
        """T3: legacy closed maps to final_closed / final from API."""
        periods = [parse_period("2026-05")]
        api = _MockApi(
            {
                "2026-05": {
                    "status": "closed",
                    "methodology_status": "final_closed",
                    "close_phase": "final",
                }
            }
        )
        report = period_status_report(
            api, VID, periods, detail="status_only", skip_empty=True
        )
        row = report["periods"][0]
        self.assertEqual(row["methodology_status"], "final_closed")
        self.assertEqual(row["close_phase"], "final")
        self.assertEqual(report["final_closed_count"], 1)

    def test_aggregates_match_rows(self) -> None:
        """T4: preliminary and final aggregates align with row values."""
        periods = [parse_period("2026-05"), parse_period("2026-06")]
        api = _MockApi(
            {
                "2026-05": {
                    "status": "closed",
                    "methodology_status": "final_closed",
                    "close_phase": "final",
                },
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "preliminary_closed",
                    "close_phase": "preliminary",
                },
            }
        )
        report = period_status_report(
            api, VID, periods, detail="status_only", skip_empty=True
        )
        self.assertEqual(report["closed_count"], 2)
        self.assertEqual(report["preliminary_closed_periods"], ["2026-06"])
        self.assertEqual(report["final_closed_periods"], ["2026-05"])

    def test_skip_empty_still_includes_methodology(self) -> None:
        """T5: skip_empty with row_count=0 keeps methodology fields."""
        period = parse_period("2026-06")
        api = _MockApi(
            {
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "preliminary_closed",
                    "close_phase": "preliminary",
                }
            },
            row_counts={"2026-06": 0},
        )
        row = period_status_report(
            api, VID, [period], detail="summary", skip_empty=True
        )["periods"][0]
        self.assertEqual(row["row_count"], 0)
        self.assertEqual(row["methodology_status"], "preliminary_closed")
        self.assertEqual(row["close_phase"], "preliminary")

    def test_unknown_methodology_passthrough(self) -> None:
        """T8: unknown methodology_status is passed through; aggregates unchanged."""
        periods = [parse_period("2026-06")]
        api = _MockApi(
            {
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "migration_closed",
                    "close_phase": None,
                }
            }
        )
        report = period_status_report(
            api, VID, periods, detail="status_only", skip_empty=True
        )
        row = report["periods"][0]
        self.assertEqual(row["methodology_status"], "migration_closed")
        self.assertEqual(report["preliminary_closed_count"], 0)
        self.assertEqual(report["final_closed_count"], 0)

    def test_draft_and_open_orthogonal(self) -> None:
        """T9: draft reconciliation with open methodology — no normalization."""
        rec = {
            "status": "draft",
            "methodology_status": "open",
            "close_phase": None,
        }
        row = compact_period_summary(
            parse_period("2026-06"),
            reconciliation=rec,
            row_count=0,
        )
        self.assertEqual(row["reconciliation_status"], "draft")
        self.assertEqual(row["methodology_status"], "open")

    @patch("monthly_close_lib.verify_period")
    def test_summary_verify_fields_preserved(self, mock_verify: Any) -> None:
        """T7: summary detail still includes verify fields alongside methodology."""
        period = parse_period("2026-06")
        api = _MockApi(
            {
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "preliminary_closed",
                    "close_phase": "preliminary",
                }
            },
            row_counts={"2026-06": 10},
        )
        mock_verify.return_value = {
            "ok": True,
            "issues": [],
            "mc": {"mc_total": 1, "mc_from_17th": 0},
            "classification_summary": {"row_count": 10, "expense_c9999_count": 0},
            "readiness": {"ready": True, "checks": []},
        }
        row = period_status_report(
            api, VID, [period], detail="summary", skip_empty=True
        )["periods"][0]
        self.assertEqual(row["methodology_status"], "preliminary_closed")
        self.assertTrue(row["verify_ok"])
        self.assertEqual(row["c9999_count"], 0)


if __name__ == "__main__":
    unittest.main()
