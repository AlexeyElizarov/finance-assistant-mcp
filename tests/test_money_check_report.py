"""Unit tests for FIN-104 money_check_report MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from money_check_report import (
    compute_money_check_report,
    find_carryover_run,
    materialize_carryover_from_log,
)

MAPPING = {
    "schema_version": 1,
    "profile": "test",
    "partners": [
        {"id": "aleksey", "display_name": "Алексей"},
        {"id": "nikolay", "display_name": "Николай"},
    ],
    "legacy_irr_sanity": [{"article_match": "Кафе и рестораны"}],
    "personal_subscriptions_sanity": [{"article_match": "ChatGPT"}],
    "account_attribution": {
        "default_partner_by_provider": {"c24": "nikolay"},
        "description_overrides": [],
    },
}

BUDGET_ITEMS = [
    {"id": "item-cafe", "name": "Кафе и рестораны", "flow_type": "IRR"},
]


class FakeApi:
    """Minimal API stub for money check tests."""

    def __init__(
        self,
        *,
        reconciliation: dict[str, dict[str, Any]],
        transactions: list[dict[str, Any]] | None = None,
        classification: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.reconciliation = reconciliation
        self.transactions = transactions or []
        self.classification = classification or {}

    def get_json(self, path: str) -> dict[str, Any]:
        if path.startswith("/api/v1/budget/reconciliation?"):
            period_start = path.split("period=", 1)[1].split("&", 1)[0]
            yyyy_mm = period_start[:7]
            return dict(self.reconciliation[yyyy_mm])
        if path.startswith("/api/v1/transactions/classification-summary?"):
            ymmm = path.split("accounting_period=", 1)[1].split("&", 1)[0]
            yyyy_mm = f"{ymmm[:4]}-{ymmm[4:6]}"
            return dict(
                self.classification.get(
                    yyyy_mm,
                    {
                        "expense_c9999_count": 0,
                        "expense_c9999_amount_eur": "0.00",
                    },
                )
            )
        if path.startswith("/api/v1/transactions?"):
            return {"rows": self.transactions, "meta": {"filter_error": None}}
        if path == "/api/v1/budget/items":
            return {"budget_items": BUDGET_ITEMS}
        raise AssertionError(f"unexpected get_json path: {path}")


def _base_payload(base_share: float = 1000.0) -> dict[str, Any]:
    return {
        "partners": [
            {"id": "aleksey", "display_name": "Алексей", "base_share": base_share},
            {"id": "nikolay", "display_name": "Николай", "base_share": base_share},
        ]
    }


class MoneyCheckReportTests(unittest.TestCase):
    """FIN-104 core scenarios."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.mapping_path = self.root / "methodology" / "household-contour-mapping.test.json"
        self.mapping_path.parent.mkdir(parents=True)
        self.mapping_path.write_text(json.dumps(MAPPING), encoding="utf-8")
        self.advances_path = self.root / "working" / "household" / "household-advances.test.json"
        self.recv_path = self.root / "working" / "household" / "household-receivables.test.json"
        self.log_path = self.root / "working" / "household" / "personal-fund-carryover.test.json"
        self._assistant_patch = patch(
            "household_advances.ASSISTANT_ROOT",
            self.root,
        )
        self._assistant_patch.start()

    def tearDown(self) -> None:
        self._assistant_patch.stop()
        self._tmpdir.cleanup()

    def _run(
        self,
        *,
        check_period: str = "2026-07",
        prior_period: str | None = None,
        reconciliation: dict[str, dict[str, Any]] | None = None,
        transactions: list[dict[str, Any]] | None = None,
        classification: dict[str, dict[str, Any]] | None = None,
        carryover_log: dict[str, Any] | None = None,
        spend: dict[str, float] | None = None,
        dry_run_payload: dict[str, Any] | None = None,
        include_advance_breakdown: bool = True,
    ) -> dict[str, Any]:
        prior = prior_period or "2026-06"
        recon = reconciliation or {
            prior: {
                "status": "closed",
                "methodology_status": "final_closed",
                "close_phase": "final",
            },
            check_period: {
                "status": "open",
                "methodology_status": "open",
                "close_phase": None,
            },
        }
        api = FakeApi(
            reconciliation=recon,
            transactions=transactions or [],
            classification=classification,
        )
        if carryover_log is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(json.dumps(carryover_log), encoding="utf-8")

        spend_map = spend or {"aleksey": 100.0, "nikolay": 50.0}

        def spend_side_effect(*_args: Any, **_kwargs: Any) -> tuple[dict[str, float], dict[str, list], list[str]]:
            return spend_map, {}, []

        patches = [
            patch(
                "money_check_report.compute_household_base_share",
                return_value=_base_payload(),
            ),
            patch("money_check_report.compute_personal_spend", side_effect=spend_side_effect),
        ]
        if dry_run_payload is not None:
            patches.append(
                patch(
                    "money_check_report.compute_personal_fund_carryover",
                    return_value=dry_run_payload,
                )
            )

        for item in patches:
            item.start()
        try:
            return compute_money_check_report(
                api,
                profile="test",
                base="http://test",
                budget_version_id="vid",
                check_period=check_period,
                prior_period=prior,
                mapping_path=str(self.mapping_path),
                include_advance_breakdown=include_advance_breakdown,
                carryover_log_path=self.log_path,
                advances_ledger_path=self.advances_path,
                receivables_ledger_path=self.recv_path,
            )
        finally:
            for item in reversed(patches):
                item.stop()

    def test_t1_happy_path_remaining_balance(self) -> None:
        payload = self._run(
            dry_run_payload={
                "partners": [
                    {
                        "id": "aleksey",
                        "incoming_carryover": 120.0,
                        "advance_deduction": 0.0,
                        "available_personal_fund": 1120.0,
                    },
                    {
                        "id": "nikolay",
                        "incoming_carryover": 0.0,
                        "advance_deduction": 0.0,
                        "available_personal_fund": 1000.0,
                    },
                ]
            }
        )
        aleksey = next(row for row in payload["partners"] if row["id"] == "aleksey")
        self.assertEqual(aleksey["remaining_balance"], 1020.0)
        self.assertEqual(payload["carryover"]["source"], "dry_run")

    def test_t2_preliminary_flags(self) -> None:
        payload = self._run(
            reconciliation={
                "2026-06": {
                    "status": "closed",
                    "methodology_status": "preliminary_closed",
                    "close_phase": "preliminary",
                },
                "2026-07": {
                    "status": "open",
                    "methodology_status": "open",
                    "close_phase": None,
                },
            },
            dry_run_payload={"partners": []},
        )
        self.assertTrue(all(row["figures_preliminary"] for row in payload["partners"]))
        self.assertFalse(any(row["figures_incomplete"] for row in payload["partners"]))
        self.assertIn("figures_preliminary", payload["warnings"])

    def test_t3_prior_open_incomplete(self) -> None:
        payload = self._run(
            reconciliation={
                "2026-06": {
                    "status": "open",
                    "methodology_status": "open",
                    "close_phase": None,
                },
                "2026-07": {
                    "status": "open",
                    "methodology_status": "open",
                    "close_phase": None,
                },
            }
        )
        self.assertEqual(payload["carryover"]["source"], "none")
        self.assertTrue(all(row["figures_incomplete"] for row in payload["partners"]))
        self.assertIn("prior_period_not_closed:2026-06", payload["warnings"])

    def test_t4_carryover_log_hit(self) -> None:
        log = {
            "schema_version": 1,
            "profile": "test",
            "runs": [
                {
                    "closed_period": "2026-06",
                    "target_period": "2026-07",
                    "computed_at": "2026-07-01T08:00:00Z",
                    "partners": {
                        "aleksey": {
                            "carryover": 120.0,
                            "advance_deduction": 20.0,
                            "overrun_amount": 0.0,
                        },
                        "nikolay": {
                            "carryover": 0.0,
                            "advance_deduction": 0.0,
                            "overrun_amount": 0.0,
                        },
                    },
                }
            ],
        }
        payload = self._run(carryover_log=log)
        aleksey = next(row for row in payload["partners"] if row["id"] == "aleksey")
        self.assertEqual(payload["carryover"]["source"], "log")
        self.assertEqual(aleksey["starting_fund"], 1100.0)

    def test_t5b_log_target_mismatch_uses_dry_run(self) -> None:
        log = {
            "schema_version": 1,
            "profile": "test",
            "runs": [
                {
                    "closed_period": "2026-06",
                    "target_period": "2026-08",
                    "computed_at": "2026-07-01T08:00:00Z",
                    "partners": {
                        "aleksey": {
                            "carryover": 999.0,
                            "advance_deduction": 0.0,
                            "overrun_amount": 0.0,
                        }
                    },
                }
            ],
        }
        with patch(
            "money_check_report.compute_personal_fund_carryover",
            return_value={
                "partners": [
                    {
                        "id": "aleksey",
                        "incoming_carryover": 50.0,
                        "advance_deduction": 0.0,
                        "available_personal_fund": 1050.0,
                    },
                    {
                        "id": "nikolay",
                        "incoming_carryover": 0.0,
                        "advance_deduction": 0.0,
                        "available_personal_fund": 1000.0,
                    },
                ]
            },
        ) as mocked:
            payload = self._run(carryover_log=log)
            mocked.assert_called_once()
        self.assertEqual(payload["carryover"]["source"], "dry_run")
        aleksey = next(row for row in payload["partners"] if row["id"] == "aleksey")
        self.assertEqual(aleksey["starting_fund"], 1050.0)

    def test_t10_c9999_warning(self) -> None:
        payload = self._run(
            classification={
                "2026-07": {
                    "expense_c9999_count": 2,
                    "expense_c9999_amount_eur": "10.00",
                }
            },
            dry_run_payload={"partners": []},
        )
        self.assertIn("expense_c9999_open:2", payload["warnings"])

    def test_t11_unresolved_expenses(self) -> None:
        payload = self._run(
            transactions=[
                {
                    "transaction_type": "C",
                    "transaction_category": "",
                    "amount": "10",
                }
            ],
            dry_run_payload={"partners": []},
        )
        self.assertEqual(payload["classification"]["unresolved_expense_count"], 1)
        self.assertIn("unresolved_expenses:1", payload["warnings"])

    def test_t12_advance_breakdown_optional(self) -> None:
        payload = self._run(
            include_advance_breakdown=False,
            dry_run_payload={"partners": []},
        )
        self.assertEqual(payload["advances"]["totals_by_issue_period"], {})

    def test_t13_invalid_prior_period(self) -> None:
        with self.assertRaises(ValueError):
            self._run(prior_period="2026-07", check_period="2026-07")

    def test_t17_historical_check_period(self) -> None:
        payload = self._run(
            check_period="2026-04",
            prior_period="2026-03",
            reconciliation={
                "2026-03": {
                    "status": "closed",
                    "methodology_status": "final_closed",
                    "close_phase": "final",
                },
                "2026-04": {
                    "status": "closed",
                    "methodology_status": "final_closed",
                    "close_phase": "final",
                },
            },
            dry_run_payload={"partners": []},
        )
        self.assertEqual(payload["check_period_methodology"]["label"], "final")

    def test_find_carryover_run_requires_both_periods(self) -> None:
        log = {
            "runs": [
                {"closed_period": "2026-06", "target_period": "2026-08"},
                {"closed_period": "2026-06", "target_period": "2026-07"},
            ]
        }
        self.assertIsNotNone(
            find_carryover_run(log, closed_period="2026-06", target_period="2026-07")
        )
        self.assertIsNone(
            find_carryover_run(log, closed_period="2026-06", target_period="2026-09")
        )

    def test_materialize_from_log_no_recompute(self) -> None:
        block = materialize_carryover_from_log(
            {
                "computed_at": "2026-07-01T08:00:00Z",
                "partners": {
                    "aleksey": {
                        "carryover": 120.0,
                        "advance_deduction": 20.0,
                        "overrun_amount": 0.0,
                    }
                },
            },
            base_share_by_partner={"aleksey": 1000.0},
        )
        self.assertEqual(block["partners"]["aleksey"]["starting_fund"], 1100.0)


if __name__ == "__main__":
    unittest.main()
