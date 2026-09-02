"""Unit tests for FIN-69 final classification close guard."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import c9999_close_guard_error, parse_period, verify_period


def _readiness_payload(
    *,
    ready: bool = True,
    pending: int = 0,
    other_without_note: int = 0,
    include_classification_checks: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {"id": "account_balances_reconciliation", "status": "pass", "blocking": True},
        {"id": "t13_income_expense", "status": "pass", "blocking": True},
    ]
    if include_classification_checks:
        checks.extend(
            [
                {
                    "id": "unclassified_pending",
                    "status": "pass" if pending == 0 else "warn",
                    "blocking": False,
                    "details": {"unclassified_pending_count": pending},
                },
                {
                    "id": "other_without_note",
                    "status": "pass" if other_without_note == 0 else "warn",
                    "blocking": False,
                    "details": {"count": other_without_note},
                },
                {
                    "id": "missing_fund",
                    "status": "pass",
                    "blocking": False,
                    "message": "Нет фонда на позициях: 0",
                    "details": {"count": 0, "line_ids": []},
                },
            ]
        )
    return {"ready": ready, "checks": checks}


def _verify_ready(
    c9999: int = 0,
    *,
    ready: bool = True,
    pending: int = 0,
    other_without_note: int = 0,
) -> dict[str, Any]:
    return {
        "ok": True,
        "issues": [],
        "warnings": [],
        "mc": {"mc_total": 10, "mc_from_17th": 1, "from_17th_samples": []},
        "classification_summary": {"expense_c9999_count": c9999, "row_count": 10},
        "readiness": _readiness_payload(
            ready=ready,
            pending=pending,
            other_without_note=other_without_note,
        ),
    }


def _process_month_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    import server

    content = server._handle_process_month(arguments)[0].text
    return json.loads(content)


class Fin69GuardUnitTest(unittest.TestCase):
    """T13, T16, T16b — shared guard helper."""

    def test_t13_final_c9999_ok_when_counts_zero(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=3,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(pending=0, other_without_note=0),
        )
        self.assertIsNone(err)

    def test_t13_final_pending_blocks(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(pending=2, other_without_note=0),
        )
        self.assertIsNotNone(err)
        self.assertIn("unclassified pending", err or "")

    def test_t16_final_missing_checks_config_error(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(include_classification_checks=False),
        )
        self.assertIsNotNone(err)
        self.assertIn("missing required classification checks", err or "")

    def test_t16b_preliminary_without_new_checks_uses_fin2(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=2,
            close_phase="preliminary",
            keywords_effective=False,
            c9999_acknowledged=True,
            readiness=_readiness_payload(include_classification_checks=False),
        )
        self.assertIsNone(err)
        err_block = c9999_close_guard_error(
            expense_c9999_count=2,
            close_phase="preliminary",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(include_classification_checks=False),
        )
        self.assertIsNotNone(err_block)
        self.assertIn("preliminary close", err_block or "")


class Fin69ProcessMonthTest(unittest.TestCase):
    """T8–T12 process_month orchestration."""

    def _run(
        self,
        arguments: dict[str, Any],
        *,
        verify_return: dict[str, Any],
    ) -> dict[str, Any]:
        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=verify_return), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period", return_value=(200, {})) as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(arguments)
            payload["_close_mock"] = close_period
            return payload

    def test_t8_final_with_intentional_other_notes(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(2, pending=0, other_without_note=0),
        )
        self.assertTrue(payload["ok"])
        payload["_close_mock"].assert_called_once()

    def test_t9_final_pending_blocks(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(0, pending=1, other_without_note=0),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("unclassified pending", payload["error"])
        payload["_close_mock"].assert_not_called()

    def test_t10_final_other_without_note_blocks(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(0, pending=0, other_without_note=1),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("reconciliation_note", payload["error"])
        payload["_close_mock"].assert_not_called()

    def test_t10b_first_match_pending_over_note(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(pending=1, other_without_note=2),
        )
        self.assertIsNotNone(err)
        self.assertIn("unclassified pending", err or "")
        self.assertNotIn("reconciliation_note", err or "")

    def test_t12_preliminary_ack_still_works(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "preliminary",
                "c9999_acknowledged": True,
            },
            verify_return=_verify_ready(2, pending=5, other_without_note=3),
        )
        self.assertTrue(payload["ok"])
        payload["_close_mock"].assert_called_once()


class Fin69VerifyWarningsTest(unittest.TestCase):
    """T14, T15 — verify_period warnings without forcing ok=false."""

    @patch("monthly_close_lib.mc_verify")
    def test_t14_pending_warning(self, mc_verify: MagicMock) -> None:
        mc_verify.return_value = {
            "mc_total": 5,
            "mc_from_17th": 2,
            "from_17th_samples": [],
        }
        api = MagicMock()
        api.get_json.side_effect = [
            {"expense_c9999_count": 0, "row_count": 10, "unclassified_pending_count": 2},
            _readiness_payload(pending=2, other_without_note=0),
        ]
        result = verify_period(api, parse_period("2026-06"), "vid")
        self.assertTrue(result["ok"])
        self.assertTrue(any("pending" in w for w in result["warnings"]))

    @patch("monthly_close_lib.mc_verify")
    def test_t15_other_without_note_warning(self, mc_verify: MagicMock) -> None:
        mc_verify.return_value = {
            "mc_total": 5,
            "mc_from_17th": 2,
            "from_17th_samples": [],
        }
        api = MagicMock()
        api.get_json.side_effect = [
            {"expense_c9999_count": 0, "row_count": 10, "other_without_note_count": 1},
            _readiness_payload(pending=0, other_without_note=1),
        ]
        result = verify_period(api, parse_period("2026-06"), "vid")
        self.assertTrue(result["ok"])
        self.assertTrue(any("Other без note" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
