"""Unit tests for FIN-105 personal_fund_carryover MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
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

DEFAULT_HOUSEHOLDS = [{"id": "hh1"}]
DEFAULT_FUNDS = [
    {
        "id": "personal-elizarov",
        "allocation_rule": "equal_share",
        "member_id": "aleksey",
    },
    {
        "id": "personal-dubrovskii",
        "allocation_rule": "equal_share",
        "member_id": "nikolay",
    },
    {
        "id": "shared",
        "allocation_rule": "before_split",
        "member_id": None,
    },
    {
        "id": "office-week",
        "allocation_rule": "before_split",
        "member_id": "aleksey",
    },
]


def _op_line(
    line_id: str,
    amount: str,
    fund_id: str | None,
    *,
    line_type: str = "C",
    category: str | None = "C0001",
) -> dict[str, Any]:
    return {
        "id": line_id,
        "amount": amount,
        "assignment": {
            "type": line_type,
            "category": category,
            "fund_id": fund_id,
        },
    }


def _operation(tx_id: str, *lines: dict[str, Any]) -> dict[str, Any]:
    return {"id": tx_id, "lines": list(lines)}


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
        households: list[dict[str, Any]] | None = None,
        funds: list[dict[str, Any]] | None = None,
        operations: list[dict[str, Any]] | None = None,
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
        self.households = households if households is not None else list(DEFAULT_HOUSEHOLDS)
        self.funds = funds if funds is not None else list(DEFAULT_FUNDS)
        self.operations = operations or []
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
            rows = list(self.transactions)
            known = {str(row.get("id")) for row in rows if isinstance(row, dict)}
            for operation in self.operations:
                tx_id = str(operation.get("id") or "")
                if tx_id and tx_id not in known:
                    rows.append({"id": tx_id, "transaction_key": tx_id})
            return {"rows": rows, "meta": {"filter_error": None}}
        if path == "/api/v1/budget/items":
            return {"budget_items": BUDGET_ITEMS}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "GET" and path == "/api/v1/households":
            return 200, {"households": self.households}
        if (
            method == "GET"
            and "/households/" in path
            and path.endswith("/funds")
        ):
            return 200, {"funds": self.funds}
        if (
            method == "GET"
            and "/transactions/" in path
            and path.endswith("/lines")
        ):
            tx_id = urllib.parse.unquote(
                path.split("/transactions/", 1)[1].split("/", 1)[0]
            )
            for operation in self.operations:
                if str(operation.get("id")) == tx_id:
                    return 200, {"lines": operation.get("lines") or []}
            return 200, {"lines": []}
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
        operations: list[dict[str, Any]] | None = None,
        methodology_status: str = "final_closed",
        base_share: float = 1000.0,
        api: FakeApi | None = None,
    ) -> dict[str, Any]:
        if api is None:
            api = FakeApi(
                methodology_status=methodology_status,
                transactions=transactions or [],
                operations=operations or [],
            )

        with patch(
            "personal_fund_carryover.compute_household_base_share",
            return_value=_base_share_payload(base_share),
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
            operations=[
                _operation(
                    "tx1",
                    _op_line("line-cafe", "100.00", "personal-elizarov"),
                )
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
            operations=[
                _operation(
                    "tx-big",
                    _op_line("line-big", "180.00", "personal-elizarov"),
                )
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
            operations=[
                _operation(
                    "tx1",
                    _op_line("line-50", "50.00", "personal-elizarov"),
                )
            ],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        # base_share(target) 1000 + carryover 950 (1000 - 50 spend) - 0 advance
        self.assertEqual(aleksey["available_personal_fund"], 1950.0)

    def test_t10_non_final_blocked(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run(methodology_status="preliminary_closed")

    def test_t12_unattributed_spend_warning(self) -> None:
        result = self._run(
            operations=[
                _operation(
                    "tx-x",
                    _op_line("line-x", "10.00", None),
                )
            ],
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

    def test_fin280_api_passthrough_financing(self) -> None:
        """FIN-280 T7.1: API body financing fields reach MCP response."""
        financing_block = {
            "accounting_period": "202607",
            "projections": [
                {
                    "settlement_id": "s1",
                    "expense_line_id": "e1",
                    "financing_fund_id": "personal-elizarov",
                    "financed_fund_id": "shared",
                    "amount": "140.00",
                    "accounting_period": "202607",
                    "type": "C",
                    "category": "C0001",
                    "project": None,
                }
            ],
            "outgoing_by_fund": {"personal-elizarov": 140.0},
            "incoming_by_fund": {"shared": 140.0},
            "outgoing_by_member": {"aleksey": 140.0},
            "outgoing_by_analytics": [
                {"type": "C", "category": "C0001", "project": None, "amount": 140.0}
            ],
            "warnings": [],
        }
        api_formula = (
            "carryover = starting_fund - actual_spend - outgoing_financing; "
            "available_personal_fund = base_share(target) + carryover"
        )
        api = FakeApi(
            carryover_status=200,
            carryover_body={
                "closed_period": "2026-07",
                "target_period": "2026-08",
                "formula": api_formula,
                "partners": [
                    {
                        "id": "aleksey",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 50.0,
                        "outgoing_financing": 140.0,
                        "balance": 810.0,
                        "carryover": 810.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 1810.0,
                    },
                    {
                        "id": "nikolay",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 0.0,
                        "outgoing_financing": 0.0,
                        "balance": 1000.0,
                        "carryover": 1000.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 2000.0,
                    },
                ],
                "fund_financing": financing_block,
                "warnings": [],
            },
        )
        result = self._run(api=api, dry_run=True)
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["formula"], api_formula)
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["outgoing_financing"], 140.0)
        self.assertEqual(result["fund_financing"], financing_block)

    def test_fin280_api_missing_financing_keys(self) -> None:
        """FIN-280 T7.2: missing HTTP financing keys → zero / empty block."""
        api = FakeApi(
            carryover_status=200,
            carryover_body={
                "closed_period": "2026-07",
                "formula": "legacy",
                "partners": [
                    {
                        "id": "aleksey",
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
                    {
                        "id": "nikolay",
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
        )
        result = self._run(api=api, dry_run=True)
        for row in result["partners"]:
            self.assertEqual(row["outgoing_financing"], 0.0)
        block = result["fund_financing"]
        self.assertEqual(block["accounting_period"], "202607")
        self.assertEqual(block["projections"], [])
        self.assertEqual(block["outgoing_by_fund"], {})
        self.assertEqual(block["incoming_by_fund"], {})
        self.assertEqual(block["outgoing_by_member"], {})
        self.assertEqual(block["outgoing_by_analytics"], [])
        self.assertEqual(block["warnings"], [])

    def test_fin280_api_financing_warnings_passthrough(self) -> None:
        """FIN-280 T7.3: root financing warnings match HTTP texts."""
        warning = "financing_skipped_missing_fund:settlement-1"
        api = FakeApi(
            carryover_status=200,
            carryover_body={
                "closed_period": "2026-07",
                "formula": "x",
                "partners": [
                    {
                        "id": "aleksey",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 0.0,
                        "outgoing_financing": 0.0,
                        "balance": 1000.0,
                        "carryover": 1000.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 2000.0,
                    },
                    {
                        "id": "nikolay",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 0.0,
                        "outgoing_financing": 0.0,
                        "balance": 1000.0,
                        "carryover": 1000.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 2000.0,
                    },
                ],
                "fund_financing": {
                    "accounting_period": "202607",
                    "projections": [],
                    "outgoing_by_fund": {},
                    "incoming_by_fund": {},
                    "outgoing_by_member": {},
                    "outgoing_by_analytics": [],
                    "warnings": [warning],
                },
                "warnings": [warning],
            },
        )
        result = self._run(api=api, dry_run=True)
        self.assertIn(warning, result["warnings"])
        self.assertEqual(result["fund_financing"]["warnings"], [warning])

    def test_fin280_mapping_path_empty_financing(self) -> None:
        """FIN-280 T7.5: mapping fallback uses zeros and empty block."""
        api = FakeApi(carryover_status=404)
        result = self._run(api=api, dry_run=True)
        self.assertEqual(result["source"], "mapping")
        for row in result["partners"]:
            self.assertEqual(row["outgoing_financing"], 0.0)
        block = result["fund_financing"]
        self.assertEqual(block["accounting_period"], "202607")
        self.assertEqual(block["projections"], [])
        self.assertEqual(block["outgoing_by_fund"], {})


class Fin324PersonalSpendTests(PersonalFundCarryoverTests):
    """FIN-324 T1, T2, T4–T8: HTTP 404 local fallback and HTTP 200 passthrough."""

    def test_fin324_t1_null_owner_personal_fund(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "tx-nik",
                    _op_line("line-nik-40", "40.00", "personal-dubrovskii"),
                )
            ],
        )
        self.assertEqual(result["source"], "mapping")
        nikolay = next(p for p in result["partners"] if p["id"] == "nikolay")
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(nikolay["actual_spend"], 40.0)
        self.assertEqual(aleksey["actual_spend"], 0.0)
        self.assertFalse(
            any(w.startswith("unattributed_spend:") for w in result["warnings"])
        )

    def test_fin324_t2_http_200_passthrough(self) -> None:
        warning = "unattributed_spend:line-http-1"
        api = FakeApi(
            carryover_status=200,
            carryover_body={
                "closed_period": "2026-07",
                "target_period": "2026-08",
                "partners": [
                    {
                        "id": "aleksey",
                        "base_share_closed": 1000.0,
                        "incoming_carryover": 0.0,
                        "starting_fund": 1000.0,
                        "actual_spend": 55.0,
                        "balance": 945.0,
                        "carryover": 945.0,
                        "overrun_amount": 0.0,
                        "overrun_requires_discussion": False,
                        "base_share_target": 1000.0,
                        "available_personal_fund": 1945.0,
                    },
                    {
                        "id": "nikolay",
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
                "warnings": [warning],
            },
        )
        with patch(
            "personal_fund_carryover.compute_personal_spend"
        ) as mocked_spend:
            result = self._run(api=api, dry_run=True)
            mocked_spend.assert_not_called()
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        nikolay = next(p for p in result["partners"] if p["id"] == "nikolay")
        self.assertEqual(aleksey["actual_spend"], 55.0)
        self.assertEqual(nikolay["actual_spend"], 0.0)
        self.assertIn(warning, result["warnings"])

    def test_fin324_t4_shared_fund_excluded(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "tx-shared",
                    _op_line("line-shared-15", "15.00", "shared"),
                )
            ],
        )
        for row in result["partners"]:
            self.assertEqual(row["actual_spend"], 0.0)
        self.assertNotIn("unattributed_spend:line-shared-15", result["warnings"])

    def test_fin324_t5_before_split_excluded(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "tx-office",
                    _op_line("line-office-20", "20.00", "office-week"),
                )
            ],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["actual_spend"], 0.0)
        self.assertNotIn("unattributed_spend:line-office-20", result["warnings"])

    def test_fin324_t6_missing_fund_warning(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "tx-none",
                    _op_line("line-none-10", "10.00", None),
                )
            ],
        )
        for row in result["partners"]:
            self.assertEqual(row["actual_spend"], 0.0)
        self.assertIn("unattributed_spend:line-none-10", result["warnings"])

    def test_fin324_t7_classification_key_not_required(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "stored-key",
                    _op_line("line-alek-25", "25.00", "personal-elizarov"),
                )
            ],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["actual_spend"], 25.0)
        self.assertFalse(
            any(w.startswith("unattributed_spend:") for w in result["warnings"])
        )

    def test_fin324_t8_spend_lines_contract(self) -> None:
        result = self._run(
            dry_run=True,
            operations=[
                _operation(
                    "tx-a",
                    _op_line(
                        "line-b",
                        "25.00",
                        "personal-elizarov",
                        category="  C0001  ",
                    ),
                    _op_line(
                        "line-a",
                        "30.00",
                        "personal-elizarov",
                        category="   ",
                    ),
                )
            ],
        )
        aleksey = next(p for p in result["partners"] if p["id"] == "aleksey")
        self.assertEqual(aleksey["actual_spend"], 55.0)
        lines = aleksey["spend_lines"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [row["line_id"] for row in lines],
            ["line-a", "line-b"],
        )
        for row in lines:
            self.assertEqual(
                set(row.keys()),
                {"line_id", "amount", "fund_id", "category"},
            )
        by_id = {row["line_id"]: row for row in lines}
        self.assertEqual(by_id["line-b"]["category"], "C0001")
        self.assertIsNone(by_id["line-a"]["category"])

    def test_fin324_t8_no_spend_lines_when_empty(self) -> None:
        result = self._run(dry_run=True, closed_period="2026-05", operations=[])
        self.assertEqual(result["source"], "mapping")
        for row in result["partners"]:
            self.assertEqual(row["actual_spend"], 0.0)
            self.assertNotIn("spend_lines", row)


if __name__ == "__main__":
    unittest.main()
