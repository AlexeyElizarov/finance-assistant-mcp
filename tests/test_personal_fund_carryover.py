"""Unit tests for FIN-105 personal_fund_carryover MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from household_advances import run_household_advances
from personal_fund_carryover import (
    compute_personal_fund_carryover,
    load_carryover_log,
    prev_calendar_month,
    validate_carryover_log_runs,
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
        "default_partner_by_provider": {
            "c24": "aleksey",
            "sparkasse-giro": "aleksey",
        },
        "description_overrides": [],
    },
}

BUDGET_ITEMS = [
    {"id": "item-cafe", "name": "Кафе и рестораны", "flow_type": "IRR"},
    {"id": "item-chatgpt", "name": "ChatGPT", "flow_type": "IRR"},
]


class FakeApi:
    """Minimal API stub for carryover tests."""

    def __init__(
        self,
        *,
        methodology_status: str = "final_closed",
        transactions: list[dict[str, Any]] | None = None,
        carryover_status: int = 404,
        carryover_body: dict[str, Any] | None = None,
        runs_get_status: int = 404,
        runs_get_body: dict[str, Any] | None = None,
        runs_put_status: int = 404,
        runs_put_body: dict[str, Any] | None = None,
    ) -> None:
        self.methodology_status = methodology_status
        self.transactions = transactions or []
        self.carryover_status = carryover_status
        self.carryover_body = carryover_body or {}
        self.runs_get_status = runs_get_status
        self.runs_get_body = runs_get_body if runs_get_body is not None else {
            "error": {"code": "not_found", "message": "missing"},
        }
        self.runs_put_status = runs_put_status
        self.runs_put_body = runs_put_body or {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_run_calls: list[str] = []
        self.carryover_get_paths: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path.startswith("/api/v1/budget/reconciliation?"):
            return {
                "status": "closed",
                "methodology_status": self.methodology_status,
                "close_phase": "final",
            }
        if path.startswith("/api/v1/transactions?"):
            return {"rows": self.transactions, "meta": {"filter_error": None}}
        if path == "/api/v1/budget/items":
            return {"budget_items": BUDGET_ITEMS}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "GET" and path.startswith("/api/v1/household/personal-fund-carryover?"):
            self.carryover_get_paths.append(path)
            return self.carryover_status, self.carryover_body
        if method == "GET" and path.startswith("/api/v1/household/personal-fund-carryover/runs/"):
            self.get_run_calls.append(path)
            return self.runs_get_status, self.runs_get_body
        if method == "PUT" and path == "/api/v1/household/personal-fund-carryover/runs":
            self.put_calls.append(data or {})
            if self.runs_put_status == 200 and not self.runs_put_body:
                body = dict(data or {})
                body["updated_at"] = "2026-08-18T10:00:00Z"
                return 200, body
            return self.runs_put_status, self.runs_put_body
        raise AssertionError(f"unexpected request: {method} {path}")


def _base_share_payload(base_share: float = 1000.0) -> dict[str, Any]:
    return {
        "partners": [
            {"id": "aleksey", "display_name": "Алексей", "base_share": base_share},
            {"id": "nikolay", "display_name": "Николай", "base_share": base_share},
        ]
    }


class PersonalFundCarryoverTests(unittest.TestCase):
    """FIN-105 T1–T19 (subset with mocks)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.mapping_path = self.root / "ops" / "household-contour-mapping.test.json"
        self.mapping_path.parent.mkdir(parents=True)
        self.mapping_path.write_text(json.dumps(MAPPING), encoding="utf-8")
        self.ledger_path = self.root / "working" / "household" / "household-advances.test.json"
        self.log_path = self.root / "working" / "household" / "personal-fund-carryover.test.json"
        self._assistant_patch = patch(
            "household_advances.ASSISTANT_ROOT",
            self.root,
        )
        self._carryover_root_patch = patch(
            "personal_fund_carryover.default_carryover_log_path",
            return_value=self.log_path,
        )
        self._assistant_patch.start()
        self._carryover_root_patch.start()

    def tearDown(self) -> None:
        self._carryover_root_patch.stop()
        self._assistant_patch.stop()
        self._tmpdir.cleanup()

    def _run(
        self,
        *,
        closed_period: str = "2026-07",
        target_period: str | None = "2026-08",
        dry_run: bool = False,
        mark_advances_deducted: bool = True,
        allow_non_final: bool = False,
        incoming_carryover_override: dict[str, float] | None = None,
        transactions: list[dict[str, Any]] | None = None,
        methodology_status: str = "final_closed",
        base_share: float = 1000.0,
        plan_txns: list[dict[str, Any]] | None = None,
        api: FakeApi | None = None,
    ) -> dict[str, Any]:
        if api is None:
            api = FakeApi(
                methodology_status=methodology_status,
                transactions=transactions or [],
            )
        plan_rows = plan_txns or []

        def fetch_side_effect(
            api: Any, budget_version_id: str, period_start_iso: str, item_id: str
        ) -> list[dict[str, Any]]:
            if item_id == "item-cafe":
                return plan_rows
            return []

        with patch(
            "personal_fund_carryover.compute_household_base_share",
            return_value=_base_share_payload(base_share),
        ), patch(
            "personal_fund_carryover._fetch_transactions",
            side_effect=fetch_side_effect,
        ):
            return compute_personal_fund_carryover(
                api,
                profile="test",
                base="http://test",
                closed_period=closed_period,
                budget_version_id="vid-1",
                target_period=target_period,
                mapping_path=str(self.mapping_path),
                dry_run=dry_run,
                mark_advances_deducted=mark_advances_deducted,
                allow_non_final=allow_non_final,
                incoming_carryover_override=incoming_carryover_override,
                ledger_path=self.ledger_path,
                carryover_log_path=self.log_path,
            )

    def test_t1_happy_path_remainder(self) -> None:
        result = self._run(
            plan_txns=[
                {
                    "transaction_key": "tx1",
                    "amount": "100.00",
                    "description": "coffee",
                }
            ],
            transactions=[
                {
                    "transaction_key": "tx1",
                    "provider": "c24",
                    "description": "coffee",
                }
            ],
        )
        self.assertTrue(result["ok"])
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["carryover"], 900.0)
        self.assertTrue(result["log_persisted"])
        self.assertFalse(result["advances_marked"])

    def test_t3_overrun_discussion(self) -> None:
        result = self._run(
            base_share=100.0,
            plan_txns=[
                {
                    "transaction_key": "tx-big",
                    "amount": "180.00",
                    "description": "spend",
                }
            ],
            transactions=[
                {
                    "transaction_key": "tx-big",
                    "provider": "c24",
                    "description": "spend",
                }
            ],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertTrue(aleksey["overrun_requires_discussion"])
        self.assertIn("overrun_discussion_required:aleksey", result["warnings"])

    def test_t4_advance_deduction_and_mark(self) -> None:
        run_household_advances(
            "test",
            "register",
            {
                "partner_id": "nikolay",
                "issue_period": "2026-07",
                "amount": 70,
                "_ledger_path": str(self.ledger_path),
                "_mapping_path": str(self.mapping_path),
            },
        )
        result = self._run()
        nikolay = next(p for p in result["partners"] if p["id"] == "nikolay")
        self.assertEqual(nikolay["advance_deduction"], 70.0)
        self.assertTrue(result["advances_marked"])
        listed = run_household_advances(
            "test",
            "list",
            {
                "status": "open",
                "_ledger_path": str(self.ledger_path),
                "_mapping_path": str(self.mapping_path),
            },
        )
        self.assertEqual(listed["totals_by_partner"], {})

    def test_t5_failure_before_log_save(self) -> None:
        run_household_advances(
            "test",
            "register",
            {
                "partner_id": "nikolay",
                "issue_period": "2026-07",
                "amount": 70,
                "_ledger_path": str(self.ledger_path),
                "_mapping_path": str(self.mapping_path),
            },
        )
        with patch("personal_fund_carryover.save_carryover_log", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self._run()
        listed = run_household_advances(
            "test",
            "list",
            {"status": "open", "_ledger_path": str(self.ledger_path), "_mapping_path": str(self.mapping_path)},
        )
        self.assertEqual(listed["totals_by_partner"]["nikolay"], 70.0)

    def test_t6_log_saved_mark_fails(self) -> None:
        run_household_advances(
            "test",
            "register",
            {
                "partner_id": "nikolay",
                "issue_period": "2026-07",
                "amount": 70,
                "_ledger_path": str(self.ledger_path),
                "_mapping_path": str(self.mapping_path),
            },
        )
        original_save_ledger = __import__(
            "personal_fund_carryover", fromlist=["save_ledger"]
        ).save_ledger

        def fail_on_ledger(path: Path, ledger: dict[str, Any]) -> None:
            if "household-advances" in str(path):
                raise OSError("ledger write failed")
            original_save_ledger(path, ledger)

        with patch("personal_fund_carryover.save_ledger", side_effect=fail_on_ledger):
            with self.assertRaises(RuntimeError):
                self._run()
        self.assertTrue(self.log_path.is_file())
        listed = run_household_advances(
            "test",
            "list",
            {"status": "open", "_ledger_path": str(self.ledger_path), "_mapping_path": str(self.mapping_path)},
        )
        self.assertEqual(listed["totals_by_partner"]["nikolay"], 70.0)

    def test_t7_dry_run_no_mutations(self) -> None:
        with patch("personal_fund_carryover.save_carryover_log") as save_log, patch(
            "personal_fund_carryover.save_ledger"
        ) as save_ledger:
            result = self._run(dry_run=True)
        self.assertFalse(result["log_persisted"])
        self.assertFalse(result["advances_marked"])
        save_log.assert_not_called()
        save_ledger.assert_not_called()

    def test_t8_target_period_available_fund(self) -> None:
        result = self._run(
            plan_txns=[{"transaction_key": "tx1", "amount": "50.00", "description": "x"}],
            transactions=[{"transaction_key": "tx1", "provider": "c24", "description": "x"}],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        # base_share(target) 1000 + carryover 950 (1000 - 50 spend) - 0 advance
        self.assertEqual(aleksey["available_personal_fund"], 1950.0)

    def test_t10_non_final_blocked(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run(methodology_status="preliminary_closed")

    def test_t12_unattributed_spend_warning(self) -> None:
        result = self._run(
            plan_txns=[{"transaction_key": "tx-x", "amount": "10.00", "description": "x"}],
            transactions=[],
        )
        self.assertTrue(any(w.startswith("unattributed_spend:") for w in result["warnings"]))

    def test_t14_re_run_keeps_single_entry(self) -> None:
        self._run()
        self._run()
        log = load_carryover_log("test", log_path=self.log_path)
        july_runs = [
            run for run in log["runs"] if run.get("closed_period") == "2026-07"
        ]
        self.assertEqual(len(july_runs), 1)

    def test_t15_duplicate_log_error(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_carryover_log_runs(
                [
                    {"closed_period": "2026-07"},
                    {"closed_period": "2026-07"},
                ]
            )

    def test_t17_omit_target_period(self) -> None:
        result = self._run(target_period=None)
        self.assertIsNone(result["target_period"])
        for row in result["partners"]:
            self.assertNotIn("available_personal_fund", row)
            self.assertNotIn("base_share_target", row)

    def test_t18_june_without_override_error(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run(closed_period="2026-06", target_period=None)

    def test_t19_june_with_override_success(self) -> None:
        result = self._run(
            closed_period="2026-06",
            target_period="2026-08",
            incoming_carryover_override={"aleksey": 50.0, "nikolay": 0.0},
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["incoming_carryover"], 50.0)
        self.assertEqual(aleksey["starting_fund"], 1050.0)

    def test_prev_calendar_month(self) -> None:
        self.assertEqual(prev_calendar_month("2026-07"), "2026-06")
        self.assertEqual(prev_calendar_month("2026-01"), "2025-12")

    def test_t12_api_put_success_no_json_required(self) -> None:
        api = FakeApi(runs_put_status=200)
        with patch("personal_fund_carryover.save_carryover_log") as save_log:
            result = self._run(api=api)
        self.assertTrue(result["log_persisted"])
        self.assertEqual(result["persist_target"], "api")
        self.assertGreaterEqual(len(api.put_calls), 1)
        save_log.assert_not_called()

    def test_t12_api_put_unavailable_json_fallback(self) -> None:
        api = FakeApi(runs_put_status=404)
        result = self._run(api=api)
        self.assertTrue(result["log_persisted"])
        self.assertEqual(result["persist_target"], "json")
        self.assertTrue(self.log_path.is_file())

    def test_t12b_detail_not_found_uses_json(self) -> None:
        from personal_fund_carryover import (
            resolve_incoming_carryover_cutover,
            save_carryover_log,
            upsert_carryover_run,
            empty_carryover_log,
        )

        log = empty_carryover_log("test")
        upsert_carryover_run(
            log,
            closed_period="2026-06",
            target_period="2026-07",
            source="manual_runbook",
            partners=[
                {
                    "id": "aleksey",
                    "carryover": 42.0,
                    "advance_deduction": 0.0,
                    "overrun_amount": 0.0,
                },
                {
                    "id": "nikolay",
                    "carryover": 0.0,
                    "advance_deduction": 0.0,
                    "overrun_amount": 0.0,
                },
            ],
            advances_marked=False,
            computed_at="2026-07-01T00:00:00Z",
        )
        save_carryover_log(self.log_path, log)
        api = FakeApi(
            runs_get_status=404,
            runs_get_body={"error": {"code": "not_found", "message": "missing"}},
        )
        incoming = resolve_incoming_carryover_cutover(
            api,
            log,
            "2026-07",
            frozenset({"aleksey", "nikolay"}),
        )
        self.assertEqual(incoming["aleksey"], 42.0)

    def test_t12c_detail_not_found_and_json_absent_zero(self) -> None:
        from personal_fund_carryover import (
            empty_carryover_log,
            resolve_incoming_carryover_cutover,
        )

        api = FakeApi(
            runs_get_status=404,
            runs_get_body={"error": {"code": "not_found", "message": "missing"}},
        )
        incoming = resolve_incoming_carryover_cutover(
            api,
            empty_carryover_log("test"),
            "2026-07",
            frozenset({"aleksey", "nikolay"}),
        )
        self.assertEqual(incoming["aleksey"], 0.0)
        self.assertEqual(incoming["nikolay"], 0.0)

    def test_t11_api_path_omits_synthesized_incoming(self) -> None:
        """FIN-230: without override, API probe must not pass incoming_carryover."""
        api = FakeApi(
            carryover_status=200,
            carryover_body={
                "closed_period": "2026-07",
                "target_period": "2026-08",
                "budget_version_id": "vid-1",
                "methodology_status": "final_closed",
                "formula": "x",
                "partners": [
                    {
                        "id": "aleksey",
                        "display_name": "Алексей",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 42.0,
                        "starting_fund": 1042.0,
                        "actual_spend": 0.0,
                        "balance": 1042.0,
                        "carryover": 1042.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 2042.0,
                    },
                    {
                        "id": "nikolay",
                        "display_name": "Николай",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 0.0,
                        "balance": 1000.0,
                        "carryover": 1000.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 2000.0,
                    },
                ],
                "warnings": [],
            },
            runs_put_status=200,
        )
        result = self._run(api=api, dry_run=True)
        self.assertEqual(len(api.carryover_get_paths), 1)
        self.assertNotIn("incoming_carryover", api.carryover_get_paths[0])
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["incoming_carryover"], 42.0)
        self.assertEqual(api.get_run_calls, [])


if __name__ == "__main__":
    unittest.main()
