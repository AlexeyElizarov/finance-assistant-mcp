"""Unit tests for FIN-116 household_receivables MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from household_receivables import (
    compute_is_overdue,
    load_ledger,
    run_household_receivables,
    sum_outstanding_by_lender,
    sum_outstanding_shared,
)

MAPPING = {
    "schema_version": 1,
    "profile": "test",
    "partners": [
        {"id": "aleksey", "display_name": "Алексей"},
        {"id": "nikolay", "display_name": "Николай"},
    ],
}


class HouseholdReceivablesTests(unittest.TestCase):
    """FIN-116 T1–T20."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.mapping_path = self.root / "ops" / "household-contour-mapping.test.json"
        self.mapping_path.parent.mkdir(parents=True)
        self.mapping_path.write_text(json.dumps(MAPPING), encoding="utf-8")
        self.ledger_path = self.root / "working" / "household" / "household-receivables.test.json"
        self._assistant_patch = patch(
            "household_receivables.ASSISTANT_ROOT",
            self.root,
        )
        self._adv_patch = patch(
            "household_advances.ASSISTANT_ROOT",
            self.root,
        )
        self._assistant_patch.start()
        self._adv_patch.start()

    def tearDown(self) -> None:
        self._assistant_patch.stop()
        self._adv_patch.stop()
        self._tmpdir.cleanup()

    def _run(self, action: str, **kwargs: object) -> dict:
        return run_household_receivables("test", action, kwargs)

    def _register(
        self,
        *,
        lender_id: str = "aleksey",
        borrower_label: str = "Arkady",
        amount: float = 300,
        source: str = "personal",
        issue_period: str = "2026-06",
        due_period: str = "2026-08",
        **extra: object,
    ) -> dict:
        return self._run(
            "register",
            lender_id=lender_id,
            borrower_label=borrower_label,
            amount=amount,
            source=source,
            issue_period=issue_period,
            due_period=due_period,
            **extra,
        )

    def test_t1_register_personal_happy_path(self) -> None:
        result = self._register(note="до конца августа")
        entry = result["entry"]
        self.assertTrue(result["ok"])
        self.assertEqual(entry["balance"], 300.0)
        self.assertEqual(entry["principal"], 300.0)
        self.assertEqual(entry["status"], "open")
        self.assertEqual(entry["currency"], "EUR")
        self.assertTrue(entry["registered_at"].endswith("Z"))
        self.assertTrue(self.ledger_path.is_file())

    def test_t2_register_shared_totals(self) -> None:
        self._register(amount=200, source="shared")
        result = self._run("list", status="open")
        self.assertEqual(result["totals_shared"], 200.0)
        self.assertEqual(result["totals_by_lender"], {})

    def test_t3_register_validation_errors(self) -> None:
        with self.assertRaises(ValueError):
            self._register(lender_id="unknown")
        with self.assertRaises(ValueError):
            self._register(borrower_label="  ")
        with self.assertRaises(ValueError):
            self._register(issue_period="2026-08", due_period="2026-06")

    def test_t4_list_empty_ledger(self) -> None:
        result = self._run("list")
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["totals_by_lender"], {})
        self.assertEqual(result["totals_shared"], 0.0)
        self.assertEqual(result["totals_by_due_period"], {})
        self.assertEqual(result["overdue_count"], 0)

    def test_t5_list_filters_and_overdue(self) -> None:
        self._register(due_period="2026-08")
        result_jun = self._run("list", as_of_period="2026-06")
        self.assertFalse(result_jun["entries"][0]["is_overdue"])
        result_sep = self._run("list", as_of_period="2026-09")
        self.assertTrue(result_sep["entries"][0]["is_overdue"])
        self.assertEqual(result_sep["overdue_count"], 1)
        by_lender = self._run("list", lender_id="aleksey")
        self.assertEqual(len(by_lender["entries"]), 1)
        by_borrower = self._run("list", borrower_label="ark")
        self.assertEqual(len(by_borrower["entries"]), 1)

    def test_t6_partial_repayment(self) -> None:
        reg = self._register()
        entry_id = reg["entry"]["id"]
        rep = self._run(
            "record_repayment",
            id=entry_id,
            amount=100,
            receipt_period="2026-08",
        )
        self.assertEqual(rep["entry"]["balance"], 200.0)
        self.assertEqual(rep["entry"]["status"], "open")

    def test_t7_full_repayment(self) -> None:
        reg = self._register(amount=100)
        entry_id = reg["entry"]["id"]
        rep = self._run(
            "record_repayment",
            id=entry_id,
            amount=100,
            receipt_period="2026-08",
        )
        entry = rep["entry"]
        self.assertEqual(entry["status"], "repaid")
        self.assertEqual(entry["balance"], 0.0)
        self.assertIsNotNone(entry["closed_at"])

    def test_t8_repayment_exceeds_balance(self) -> None:
        reg = self._register(amount=50)
        with self.assertRaises(ValueError):
            self._run(
                "record_repayment",
                id=reg["entry"]["id"],
                amount=51,
                receipt_period="2026-08",
            )

    def test_t9_extend(self) -> None:
        reg = self._register()
        entry_id = reg["entry"]["id"]
        ext = self._run("extend", id=entry_id, new_due_period="2026-10", note="продление")
        entry = ext["entry"]
        self.assertEqual(entry["due_period"], "2026-10")
        self.assertEqual(len(entry["extensions"]), 1)
        self.assertEqual(entry["extensions"][0]["from_due_period"], "2026-08")
        self.assertEqual(entry["extensions"][0]["to_due_period"], "2026-10")

    def test_t10_write_off_after_partial_repayment(self) -> None:
        reg = self._register(amount=500)
        entry_id = reg["entry"]["id"]
        self._run(
            "record_repayment",
            id=entry_id,
            amount=100,
            receipt_period="2026-08",
        )
        closed = self._run("write_off", id=entry_id)
        entry = closed["entry"]
        self.assertEqual(entry["principal"], 500.0)
        self.assertEqual(len(entry["repayments"]), 1)
        self.assertEqual(entry["repayments"][0]["amount"], 100.0)
        self.assertEqual(entry["balance"], 0.0)
        self.assertEqual(entry["status"], "written_off")
        self.assertIsNotNone(entry["closed_at"])

    def test_t10_mark_gift(self) -> None:
        reg = self._register(amount=80)
        entry_id = reg["entry"]["id"]
        gift = self._run("mark_gift", id=entry_id)
        self.assertEqual(gift["entry"]["status"], "gift")
        self.assertEqual(gift["entry"]["balance"], 0.0)

    def test_t11_totals_separate_personal_and_shared(self) -> None:
        self._register(amount=100, source="personal", lender_id="aleksey")
        self._register(amount=200, source="shared")
        result = self._run("list")
        self.assertEqual(result["totals_by_lender"]["aleksey"], 100.0)
        self.assertEqual(result["totals_shared"], 200.0)

    def test_t12_multiple_loans_same_lender(self) -> None:
        self._register(amount=100)
        self._register(amount=200)
        totals = self._run("list")["totals_by_lender"]
        self.assertEqual(totals["aleksey"], 300.0)

    def test_t13_corrupt_ledger_json(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            load_ledger("test")

    def test_t14_arkady_example(self) -> None:
        self._register()
        jun = self._run("list", as_of_period="2026-06")
        self.assertFalse(jun["entries"][0]["is_overdue"])
        sep = self._run("list", as_of_period="2026-09")
        self.assertTrue(sep["entries"][0]["is_overdue"])

    def test_t15_repayment_after_due_period(self) -> None:
        reg = self._register()
        entry_id = reg["entry"]["id"]
        rep = self._run(
            "record_repayment",
            id=entry_id,
            amount=50,
            receipt_period="2026-10",
        )
        self.assertEqual(rep["entry"]["balance"], 250.0)

    def test_t16_two_loans_same_borrower_label(self) -> None:
        first = self._register(amount=100, borrower_label="Arkady")
        second = self._register(amount=300, borrower_label="Arkady")
        self.assertNotEqual(first["entry"]["id"], second["entry"]["id"])
        self.assertEqual(self._run("list")["totals_by_lender"]["aleksey"], 400.0)

    def test_t17_multiple_repayments_same_receipt_period(self) -> None:
        reg = self._register(amount=100)
        entry_id = reg["entry"]["id"]
        self._run(
            "record_repayment",
            id=entry_id,
            amount=30,
            receipt_period="2026-08",
        )
        self._run(
            "record_repayment",
            id=entry_id,
            amount=20,
            receipt_period="2026-08",
        )
        entry = self._run("list")["entries"][0]
        self.assertEqual(len(entry["repayments"]), 2)
        self.assertEqual(entry["balance"], 50.0)

    def test_t18_duplicate_transaction_key(self) -> None:
        self._register(transaction_key="tx-1")
        self._register(transaction_key="tx-1")
        self.assertEqual(len(self._run("list")["entries"]), 2)

    def test_t19_balance_desync_on_load(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "profile": "test",
            "entries": [
                {
                    "id": "recv-202606-aleksey-001",
                    "lender_id": "aleksey",
                    "borrower_label": "X",
                    "principal": 100.0,
                    "balance": 50.0,
                    "currency": "EUR",
                    "source": "personal",
                    "issue_period": "2026-06",
                    "due_period": "2026-08",
                    "note": None,
                    "transaction_key": None,
                    "status": "open",
                    "repayments": [],
                    "extensions": [],
                    "registered_at": "2026-06-01T00:00:00Z",
                    "closed_at": None,
                    "close_reason": None,
                }
            ],
        }
        self.ledger_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            load_ledger("test")

    def test_t20_extend_chain_invariant(self) -> None:
        reg = self._register()
        entry_id = reg["entry"]["id"]
        self._run("extend", id=entry_id, new_due_period="2026-10")
        entry = self._run("extend", id=entry_id, new_due_period="2026-12")["entry"]
        self.assertEqual(entry["extensions"][-1]["to_due_period"], entry["due_period"])

    def test_helpers(self) -> None:
        reg = self._register(amount=150, source="personal")
        self._register(amount=75, source="shared")
        ledger = load_ledger("test")
        self.assertEqual(sum_outstanding_by_lender(ledger), {"aleksey": 150.0})
        self.assertEqual(sum_outstanding_shared(ledger), 75.0)
        self.assertTrue(
            compute_is_overdue(reg["entry"], "2026-09"),
        )

    def test_terminal_cannot_mutate(self) -> None:
        reg = self._register(amount=10)
        entry_id = reg["entry"]["id"]
        self._run("write_off", id=entry_id)
        with self.assertRaises(ValueError):
            self._run("record_repayment", id=entry_id, amount=1, receipt_period="2026-08")

    def test_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            run_household_receivables("test", "noop", {})


if __name__ == "__main__":
    unittest.main()
