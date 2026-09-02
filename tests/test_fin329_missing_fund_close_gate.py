"""Unit tests for FIN-329 missing-fund close gate."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import c9999_close_guard_error, parse_period, verify_period


def _missing_fund_check(
    count: Any = 0,
    *,
    include_count: bool = True,
    status: str | None = None,
    line_ids: list[str] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    if include_count and isinstance(count, int) and type(count) is int and count >= 0:
        resolved_status = status or ("pass" if count == 0 else "warn")
        resolved_message = message or f"Нет фонда на позициях: {count}"
        resolved_ids = [] if line_ids is None else line_ids
        details: dict[str, Any] = {"count": count, "line_ids": resolved_ids}
    else:
        resolved_status = status or "warn"
        resolved_message = message or "Нет фонда на позициях: 0"
        details = {}
        if include_count:
            details["count"] = count
            details["line_ids"] = [] if line_ids is None else line_ids
    return {
        "id": "missing_fund",
        "status": resolved_status,
        "blocking": False,
        "message": resolved_message,
        "details": details,
    }


def _readiness_payload(
    *,
    ready: bool = True,
    pending: int = 0,
    other_without_note: int = 0,
    missing_fund: Any = 0,
    include_classification_checks: bool = True,
    include_missing_fund: bool = True,
    missing_fund_check: dict[str, Any] | None = None,
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
            ]
        )
    if include_missing_fund:
        checks.append(
            missing_fund_check
            if missing_fund_check is not None
            else _missing_fund_check(missing_fund)
        )
    return {"ready": ready, "checks": checks}


def _verify_ready(
    c9999: int = 0,
    *,
    ready: bool = True,
    pending: int = 0,
    other_without_note: int = 0,
    missing_fund: Any = 0,
    include_missing_fund: bool = True,
    missing_fund_check: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "issues": [],
        "warnings": [] if warnings is None else warnings,
        "mc": {"mc_total": 10, "mc_from_17th": 1, "from_17th_samples": []},
        "classification_summary": {"expense_c9999_count": c9999, "row_count": 10},
        "readiness": _readiness_payload(
            ready=ready,
            pending=pending,
            other_without_note=other_without_note,
            missing_fund=missing_fund,
            include_missing_fund=include_missing_fund,
            missing_fund_check=missing_fund_check,
        ),
    }


def _process_month_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    import server

    content = server._handle_process_month(arguments)[0].text
    return json.loads(content)


class Fin329GuardUnitTest(unittest.TestCase):
    """T3, T7, T8, T10 — shared guard helper."""

    def test_t3_final_blocks_when_count_positive(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(missing_fund=2),
        )
        self.assertEqual(
            err, "missing fund on lines > 0 — assign fund before final close"
        )

    def test_t7_final_blocks_when_check_absent(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(include_missing_fund=False),
        )
        self.assertIsNotNone(err)
        self.assertIn("missing required classification checks", err or "")
        self.assertIn("missing_fund", err or "")

    def test_t8_final_blocks_invalid_counts(self) -> None:
        cases: list[dict[str, Any]] = [
            {"include_count": False},
            {"count": None},
            {"count": "2"},
            {"count": 1.5},
            {"count": -1},
            {"count": True},
        ]
        for case in cases:
            with self.subTest(case=case):
                err = c9999_close_guard_error(
                    expense_c9999_count=0,
                    close_phase="final",
                    keywords_effective=False,
                    c9999_acknowledged=False,
                    readiness=_readiness_payload(
                        missing_fund_check=_missing_fund_check(**case)
                    ),
                )
                self.assertIsNotNone(err)
                self.assertIn("invalid required fund check", err or "")
                self.assertIn("missing_fund", err or "")

    def test_t10_pending_wins_over_missing_fund(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="final",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(pending=1, missing_fund=2),
        )
        self.assertIsNotNone(err)
        self.assertIn("unclassified pending", err or "")
        self.assertNotIn("missing fund", err or "")

    def test_preliminary_ignores_missing_fund(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=0,
            close_phase="preliminary",
            keywords_effective=False,
            c9999_acknowledged=False,
            readiness=_readiness_payload(
                include_missing_fund=False,
            ),
        )
        self.assertIsNone(err)


class Fin329ProcessMonthTest(unittest.TestCase):
    """T3–T10 process_month orchestration."""

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

    def test_t3_final_close_refused(self) -> None:
        payload = self._run(
            {
                "period": "2026-07",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(missing_fund=2),
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"],
            "missing fund on lines > 0 — assign fund before final close",
        )
        payload["_close_mock"].assert_not_called()

    def test_t4_final_close_when_count_zero(self) -> None:
        payload = self._run(
            {
                "period": "2026-07",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(missing_fund=0),
        )
        self.assertTrue(payload["ok"])
        payload["_close_mock"].assert_called_once()

    def test_t5_preliminary_close_not_blocked(self) -> None:
        payload = self._run(
            {
                "period": "2026-07",
                "skip_import": True,
                "close": True,
                "close_phase": "preliminary",
            },
            verify_return=_verify_ready(missing_fund=2),
        )
        self.assertTrue(payload["ok"])
        payload["_close_mock"].assert_called_once()

    def test_t6_non_close_keeps_verify_ok(self) -> None:
        verify = _verify_ready(
            missing_fund=2,
            warnings=["Нет фонда на позициях: 2"],
        )
        payload = self._run(
            {"period": "2026-07", "skip_import": True},
            verify_return=verify,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["log"]["steps"]["verify"]["warnings"], verify["warnings"])
        payload["_close_mock"].assert_not_called()

    def test_t7_final_close_when_check_absent(self) -> None:
        payload = self._run(
            {
                "period": "2026-07",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(include_missing_fund=False),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("missing_fund", payload["error"])
        payload["_close_mock"].assert_not_called()

    def test_t8_final_close_when_count_invalid(self) -> None:
        cases: list[dict[str, Any]] = [
            {"include_count": False},
            {"count": None},
            {"count": "2"},
            {"count": 1.5},
            {"count": -1},
            {"count": True},
        ]
        for case in cases:
            with self.subTest(case=case):
                payload = self._run(
                    {
                        "period": "2026-07",
                        "skip_import": True,
                        "close": True,
                        "close_phase": "final",
                    },
                    verify_return=_verify_ready(
                        missing_fund_check=_missing_fund_check(**case)
                    ),
                )
                self.assertFalse(payload["ok"])
                self.assertIn("invalid required fund check", payload["error"])
                payload["_close_mock"].assert_not_called()

    def test_t9_period_before_boundary_not_blocked(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(missing_fund=0),
        )
        self.assertTrue(payload["ok"])
        payload["_close_mock"].assert_called_once()

    def test_t10_pending_blocks_before_missing_fund(self) -> None:
        payload = self._run(
            {
                "period": "2026-07",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(pending=1, missing_fund=2),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("unclassified pending", payload["error"])
        self.assertNotIn("missing fund", payload["error"])
        payload["_close_mock"].assert_not_called()


class Fin329VerifyWarningsTest(unittest.TestCase):
    """T1, T2, T9, T11 — verify_period warnings without forcing ok=false."""

    def _verify(
        self,
        *,
        period: str,
        summary: dict[str, Any],
        readiness: dict[str, Any],
    ) -> dict[str, Any]:
        with patch("monthly_close_lib.mc_verify") as mc_verify:
            mc_verify.return_value = {
                "mc_total": 5,
                "mc_from_17th": 2,
                "from_17th_samples": [],
            }
            api = MagicMock()
            api.get_json.side_effect = [summary, readiness]
            return verify_period(api, parse_period(period), "vid")

    def test_t1_no_warning_when_count_zero(self) -> None:
        result = self._verify(
            period="2026-07",
            summary={"expense_c9999_count": 0, "row_count": 10},
            readiness=_readiness_payload(missing_fund=0),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(any("фонда" in warning for warning in result["warnings"]))
        fund = next(
            check
            for check in result["readiness"]["checks"]
            if check["id"] == "missing_fund"
        )
        self.assertEqual(fund["status"], "pass")
        self.assertEqual(fund["details"]["count"], 0)

    def test_t2_warning_when_count_positive(self) -> None:
        line_ids = [
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "ffffffff-1111-2222-3333-444444444444",
        ]
        result = self._verify(
            period="2026-07",
            summary={"expense_c9999_count": 0, "row_count": 10},
            readiness=_readiness_payload(
                missing_fund_check=_missing_fund_check(2, line_ids=line_ids)
            ),
        )
        self.assertTrue(result["ok"])
        self.assertIn("Нет фонда на позициях: 2", result["warnings"])
        fund = next(
            check
            for check in result["readiness"]["checks"]
            if check["id"] == "missing_fund"
        )
        self.assertEqual(fund["details"]["line_ids"], line_ids)

    def test_t2_status_pass_with_positive_count_still_warns(self) -> None:
        result = self._verify(
            period="2026-07",
            summary={"expense_c9999_count": 0, "row_count": 10},
            readiness=_readiness_payload(
                missing_fund_check=_missing_fund_check(2, status="pass")
            ),
        )
        self.assertTrue(result["ok"])
        self.assertIn("Нет фонда на позициях: 2", result["warnings"])

    def test_t9_june_2026_count_zero_has_no_warning(self) -> None:
        result = self._verify(
            period="2026-06",
            summary={"expense_c9999_count": 0, "row_count": 10},
            readiness=_readiness_payload(missing_fund=0),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(any("фонда" in warning for warning in result["warnings"]))

    def test_t11_c9999_and_missing_fund_warnings_coexist(self) -> None:
        result = self._verify(
            period="2026-07",
            summary={"expense_c9999_count": 3, "row_count": 10},
            readiness=_readiness_payload(missing_fund=2),
        )
        self.assertTrue(result["ok"])
        self.assertIn("C9999: 3 расходов", result["warnings"])
        self.assertIn("Нет фонда на позициях: 2", result["warnings"])

    def test_invalid_count_does_not_add_warning(self) -> None:
        result = self._verify(
            period="2026-07",
            summary={"expense_c9999_count": 0, "row_count": 10},
            readiness=_readiness_payload(
                missing_fund_check=_missing_fund_check(include_count=False)
            ),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(any("фонда" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
