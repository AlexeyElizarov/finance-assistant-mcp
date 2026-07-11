"""Unit tests for FIN-115 household_advances MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from household_advances import (
    empty_ledger,
    load_ledger,
    run_household_advances,
    sum_open_by_partner,
    sum_open_for_issue_period,
)

MAPPING = {
    "schema_version": 1,
    "profile": "test",
    "partners": [
        {"id": "aleksey", "display_name": "Алексей"},
        {"id": "nikolay", "display_name": "Николай"},
    ],
}


class HouseholdAdvancesTests(unittest.TestCase):
    """FIN-115 T1–T13."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.mapping_path = self.root / "methodology" / "household-contour-mapping.test.json"
        self.mapping_path.parent.mkdir(parents=True)
        self.mapping_path.write_text(json.dumps(MAPPING), encoding="utf-8")
        self.ledger_path = self.root / "working" / "household" / "household-advances.test.json"
        self._assistant_patch = patch(
            "household_advances.ASSISTANT_ROOT",
            self.root,
        )
        self._assistant_patch.start()

    def tearDown(self) -> None:
        self._assistant_patch.stop()
        self._tmpdir.cleanup()

    def _run(self, action: str, **kwargs: object) -> dict:
        return run_household_advances("test", action, kwargs)

    def test_t1_register_happy_path(self) -> None:
        result = self._run(
            "register",
            partner_id="nikolay",
            issue_period="2026-07",
            amount=70,
            note="продукты",
        )
        entry = result["entry"]
        self.assertTrue(result["ok"])
        self.assertEqual(entry["deduct_in_period"], "2026-08")
        self.assertEqual(entry["status"], "open")
        self.assertEqual(entry["currency"], "EUR")
        self.assertTrue(entry["registered_at"].endswith("Z"))
        self.assertTrue(self.ledger_path.is_file())

    def test_t2_register_invalid_partner(self) -> None:
        with self.assertRaises(ValueError):
            self._run("register", partner_id="unknown", issue_period="2026-07", amount=10)

    def test_t3_register_invalid_amount(self) -> None:
        with self.assertRaises(ValueError):
            self._run("register", partner_id="nikolay", issue_period="2026-07", amount=0)
        with self.assertRaises(ValueError):
            self._run("register", partner_id="nikolay", issue_period="2026-07", amount=1.234)

    def test_t4_list_empty_ledger(self) -> None:
        result = self._run("list")
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["totals_by_partner"], {})
        self.assertEqual(result["totals_by_issue_period"], {})

    def test_t5_list_filters(self) -> None:
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        self._run("register", partner_id="aleksey", issue_period="2026-08", amount=40)
        by_partner = self._run("list", partner_id="nikolay")
        self.assertEqual(len(by_partner["entries"]), 1)
        self.assertEqual(by_partner["entries"][0]["partner_id"], "nikolay")
        by_period = self._run("list", issue_period="2026-08")
        self.assertEqual(len(by_period["entries"]), 1)
        by_status = self._run("list", status="open")
        self.assertEqual(len(by_status["entries"]), 2)

    def test_t6_list_totals_only_open(self) -> None:
        reg = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=30)
        self._run("void", id=reg["entry"]["id"])
        result = self._run("list", partner_id="nikolay", status="void")
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["totals_by_partner"], {"nikolay": 30.0})

    def test_t7_void_open_entry(self) -> None:
        reg = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        entry_id = reg["entry"]["id"]
        voided = self._run("void", id=entry_id, reason="mistake")
        entry = voided["entry"]
        self.assertEqual(entry["status"], "void")
        self.assertIsNotNone(entry["voided_at"])
        self.assertTrue(entry["voided_at"].endswith("Z"))
        self.assertEqual(entry["void_reason"], "mistake")

    def test_t8_void_already_void_or_deducted(self) -> None:
        reg = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        entry_id = reg["entry"]["id"]
        self._run("void", id=entry_id)
        with self.assertRaises(ValueError):
            self._run("void", id=entry_id)
        reg2 = self._run("register", partner_id="nikolay", issue_period="2026-08", amount=50)
        self._run("mark_deducted", issue_period="2026-08")
        with self.assertRaises(ValueError):
            self._run("void", id=reg2["entry"]["id"])

    def test_t9_mark_deducted_idempotent(self) -> None:
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=30)
        first = self._run("mark_deducted", issue_period="2026-07")
        self.assertEqual(first["marked_total"], 100.0)
        self.assertEqual(len(first["marked"]), 2)
        second = self._run("mark_deducted", issue_period="2026-07")
        self.assertEqual(second["marked_total"], 0.0)
        self.assertEqual(second["marked"], [])

    def test_t10_multiple_registers_sum(self) -> None:
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=30)
        totals = self._run("list", status="open")["totals_by_partner"]
        self.assertEqual(totals["nikolay"], 100.0)
        by_issue = sum_open_for_issue_period(load_ledger("test"), "2026-07")
        self.assertEqual(by_issue["nikolay"], 100.0)

    def test_t11_corrupt_ledger_json(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            load_ledger("test")

    def test_t12_id_seq_increments(self) -> None:
        first = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=10)
        second = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=20)
        self.assertEqual(first["entry"]["id"], "adv-202607-nikolay-001")
        self.assertEqual(second["entry"]["id"], "adv-202607-nikolay-002")
        self._run("void", id=first["entry"]["id"])
        third = self._run("register", partner_id="nikolay", issue_period="2026-07", amount=5)
        self.assertEqual(third["entry"]["id"], "adv-202607-nikolay-003")

    def test_t13_register_past_period_allowed(self) -> None:
        result = self._run(
            "register",
            partner_id="nikolay",
            issue_period="2026-05",
            amount=15,
        )
        self.assertEqual(result["entry"]["issue_period"], "2026-05")
        self.assertEqual(result["entry"]["deduct_in_period"], "2026-06")

    def test_helpers_sum_open_by_partner(self) -> None:
        ledger = empty_ledger("test")
        ledger["entries"] = [
            {"partner_id": "nikolay", "status": "open", "amount": 10.0, "issue_period": "2026-07"},
            {"partner_id": "aleksey", "status": "open", "amount": 20.0, "issue_period": "2026-07"},
            {"partner_id": "nikolay", "status": "void", "amount": 99.0, "issue_period": "2026-07"},
        ]
        self.assertEqual(sum_open_by_partner(ledger), {"nikolay": 10.0, "aleksey": 20.0})
        self.assertEqual(sum_open_by_partner(ledger, partner_id="nikolay"), {"nikolay": 10.0})

    def test_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            run_household_advances("test", "noop", {})

    def test_mark_deducted_partner_filter(self) -> None:
        self._run("register", partner_id="nikolay", issue_period="2026-07", amount=70)
        self._run("register", partner_id="aleksey", issue_period="2026-07", amount=40)
        result = self._run("mark_deducted", issue_period="2026-07", partner_id="nikolay")
        self.assertEqual(result["marked_total"], 70.0)
        open_totals = self._run("list", status="open")["totals_by_partner"]
        self.assertEqual(open_totals, {"aleksey": 40.0})


if __name__ == "__main__":
    unittest.main()
