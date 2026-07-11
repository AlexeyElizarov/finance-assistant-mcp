"""Unit tests for FIN-2 C9999 warning policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    c9999_close_guard_error,
    empty_keywords_changes,
    keywords_payload_effective,
    parse_period,
    prepare_process_month_orchestrator_flags,
    validate_process_month_c9999_acknowledged,
    verify_period,
)


def _readiness_payload(*, ready: bool = True) -> dict[str, Any]:
    return {
        "ready": ready,
        "checks": [
            {"id": "account_balances_reconciliation", "status": "pass", "blocking": True},
            {"id": "t13_income_expense", "status": "pass", "blocking": True},
        ],
    }


def _verify_ready(c9999: int = 0, *, ready: bool = True, mc_from_17th: int = 1) -> dict[str, Any]:
    return {
        "ok": True,
        "issues": [],
        "warnings": [f"C9999: {c9999} расходов"] if c9999 else [],
        "mc": {"mc_total": 10, "mc_from_17th": mc_from_17th, "from_17th_samples": []},
        "classification_summary": {"expense_c9999_count": c9999, "row_count": 10},
        "readiness": _readiness_payload(ready=ready),
    }


def _process_month_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    import server

    content = server._handle_process_month(arguments)[0].text
    return json.loads(content)


class VerifyPeriodFin2Test(unittest.TestCase):
    """T1, T2: verify_period issues vs warnings."""

    @patch("monthly_close_lib.mc_verify")
    def test_t1_c9999_in_warnings_not_issues(self, mc_verify: MagicMock) -> None:
        """T1: C9999 > 0 with readiness ready → ok true, warning only."""
        mc_verify.return_value = {"mc_total": 5, "mc_from_17th": 2, "from_17th_samples": []}
        api = MagicMock()
        api.get_json.side_effect = [
            {"expense_c9999_count": 3, "row_count": 10},
            _readiness_payload(ready=True),
        ]
        result = verify_period(api, parse_period("2026-06"), "vid")
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], ["C9999: 3 расходов"])
        self.assertNotIn("C9999: 3 расходов", result["issues"])

    @patch("monthly_close_lib.mc_verify")
    def test_t2_balances_blocking_in_issues(self, mc_verify: MagicMock) -> None:
        """T2: balances incomplete → ok false, issue in issues."""
        mc_verify.return_value = {"mc_total": 5, "mc_from_17th": 2, "from_17th_samples": []}
        api = MagicMock()
        api.get_json.side_effect = [
            {"expense_c9999_count": 0, "row_count": 10},
            {
                "ready": False,
                "checks": [
                    {
                        "id": "account_balances_reconciliation",
                        "status": "incomplete",
                        "blocking": True,
                    }
                ],
            },
        ]
        result = verify_period(api, parse_period("2026-06"), "vid")
        self.assertFalse(result["ok"])
        self.assertTrue(any("balances" in issue for issue in result["issues"]))


class KeywordsEffectiveTest(unittest.TestCase):
    """D-03 keyword payload semantics."""

    def test_empty_payload_not_effective(self) -> None:
        self.assertFalse(keywords_payload_effective({}))

    def test_blank_strings_not_effective(self) -> None:
        self.assertFalse(keywords_payload_effective({"C0005": ["", " ", "\t"]}))

    def test_non_blank_keyword_effective(self) -> None:
        self.assertTrue(keywords_payload_effective({"C0005": [" Adana "] }))


class C9999GuardTest(unittest.TestCase):
    """Guard helper matrix."""

    def test_final_blocks_when_n_positive(self) -> None:
        err = c9999_close_guard_error(
            expense_c9999_count=2,
            close_phase="final",
            keywords_effective=True,
            c9999_acknowledged=False,
        )
        self.assertIsNotNone(err)
        self.assertIn("final close", err)

    def test_preliminary_allows_ack(self) -> None:
        self.assertIsNone(
            c9999_close_guard_error(
                expense_c9999_count=2,
                close_phase="preliminary",
                keywords_effective=False,
                c9999_acknowledged=True,
            )
        )


class ProcessMonthValidationTest(unittest.TestCase):
    """T8, T9: c9999_acknowledged validation."""

    def test_t8_ack_without_close(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            prepare_process_month_orchestrator_flags(
                {"period": "2026-06", "c9999_acknowledged": True}
            )
        self.assertIn("requires close=true", str(ctx.exception))

    def test_t9_ack_with_final_close(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            prepare_process_month_orchestrator_flags(
                {
                    "period": "2026-06",
                    "close": True,
                    "close_phase": "final",
                    "c9999_acknowledged": True,
                }
            )
        self.assertIn("not allowed with close_phase=final", str(ctx.exception))

    def test_validate_helper_d05_d06(self) -> None:
        with self.assertRaises(ValueError):
            validate_process_month_c9999_acknowledged(
                {"c9999_acknowledged": True}, False, "preliminary"
            )
        with self.assertRaises(ValueError):
            validate_process_month_c9999_acknowledged(
                {"c9999_acknowledged": True}, True, "final"
            )


class ProcessMonthHandlerFin2Test(unittest.TestCase):
    """T3–T7b, T10–T13: process_month handler."""

    def _run(
        self,
        arguments: dict[str, Any],
        *,
        verify_return: dict[str, Any],
        close_return: tuple[int, dict[str, Any]] = (200, {}),
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
        ), patch("server.close_period", return_value=close_return):
            working.__truediv__.return_value = MagicMock()
            return _process_month_payload(arguments)

    def test_t3_non_close_ok_with_c9999_warning(self) -> None:
        verify = _verify_ready(3)
        verify["ok"] = True
        verify["issues"] = []
        verify["warnings"] = ["C9999: 3 расходов"]
        payload = self._run(
            {"period": "2026-06", "skip_import": True},
            verify_return=verify,
        )
        self.assertTrue(payload["ok"])

    def test_t4_preliminary_close_with_ack(self) -> None:
        import server

        verify = _verify_ready(2)
        verify["ok"] = True
        verify["issues"] = []
        verify["warnings"] = ["C9999: 2 расходов"]
        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=verify), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period", return_value=(200, {})) as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(
                {
                    "period": "2026-06",
                    "skip_import": True,
                    "close": True,
                    "close_phase": "preliminary",
                    "c9999_acknowledged": True,
                }
            )
        self.assertTrue(payload["ok"])
        close_period.assert_called_once()
        self.assertTrue(payload["log"]["steps"]["c9999_acknowledged"])

    def test_t5_preliminary_blocked_without_ack(self) -> None:
        import server

        verify = _verify_ready(2)
        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=verify), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period") as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(
                {
                    "period": "2026-06",
                    "skip_import": True,
                    "close": True,
                    "close_phase": "preliminary",
                }
            )
        self.assertFalse(payload["ok"])
        self.assertIn("preliminary close", payload["error"])
        close_period.assert_not_called()

    def test_t6_final_blocked_when_n_positive(self) -> None:
        import server

        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=_verify_ready(1)), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period") as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(
                {
                    "period": "2026-06",
                    "skip_import": True,
                    "close": True,
                    "close_phase": "final",
                }
            )
        self.assertFalse(payload["ok"])
        self.assertIn("final close", payload["error"])
        close_period.assert_not_called()

    def test_t7a_final_close_after_n_zero(self) -> None:
        import server

        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=_verify_ready(0)), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.keywords_file_effective", return_value=True), patch(
            "server.apply_keywords_file",
            return_value={"categories_added": [{"category": "C0005", "keyword": "x"}], "categories_removed": [], "budget_items_added": [], "budget_items_removed": [], "projects_added": [], "projects_removed": []},
        ), patch("server.close_period", return_value=(200, {})) as close_period:
            working.__truediv__.return_value = MagicMock()
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write('{"C0005": ["x"]}')
                kw_path = fh.name
            try:
                payload = _process_month_payload(
                    {
                        "period": "2026-06",
                        "skip_import": True,
                        "close": True,
                        "close_phase": "final",
                        "apply_keywords": kw_path,
                    }
                )
            finally:
                Path(kw_path).unlink(missing_ok=True)
        self.assertTrue(payload["ok"])
        close_period.assert_called_once()

    def test_t7b_final_blocked_after_keywords_still_c9999(self) -> None:
        import server

        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=_verify_ready(2)), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.keywords_file_effective", return_value=True), patch(
            "server.apply_keywords_file", return_value=empty_keywords_changes(),
        ), patch("server.close_period") as close_period:
            working.__truediv__.return_value = MagicMock()
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write('{"C0005": ["x"]}')
                kw_path = fh.name
            try:
                payload = _process_month_payload(
                    {
                        "period": "2026-06",
                        "skip_import": True,
                        "close": True,
                        "close_phase": "final",
                        "apply_keywords": kw_path,
                    }
                )
            finally:
                Path(kw_path).unlink(missing_ok=True)
        self.assertFalse(payload["ok"])
        close_period.assert_not_called()

    def test_t10_empty_keywords_file_not_effective(self) -> None:
        import server

        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=_verify_ready(1)), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.apply_keywords_file", return_value=empty_keywords_changes()), patch(
            "server.close_period"
        ) as close_period:
            working.__truediv__.return_value = MagicMock()
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write("{}")
                kw_path = fh.name
            try:
                payload = _process_month_payload(
                    {
                        "period": "2026-06",
                        "skip_import": True,
                        "close": True,
                        "close_phase": "preliminary",
                        "apply_keywords": kw_path,
                    }
                )
            finally:
                Path(kw_path).unlink(missing_ok=True)
        self.assertFalse(payload["log"]["steps"]["keywords_effective"])
        self.assertFalse(payload["ok"])
        close_period.assert_not_called()

    def test_t11_final_close_without_c9999_guard(self) -> None:
        payload = self._run(
            {
                "period": "2026-06",
                "skip_import": True,
                "close": True,
                "close_phase": "final",
            },
            verify_return=_verify_ready(0),
        )
        self.assertTrue(payload["ok"])

    def test_t12_readiness_false_blocks_close(self) -> None:
        import server

        verify = _verify_ready(0, ready=False)
        verify["ok"] = True
        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=verify), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period") as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(
                {
                    "period": "2026-06",
                    "skip_import": True,
                    "close": True,
                    "close_phase": "final",
                }
            )
        self.assertFalse(payload["ok"])
        close_period.assert_not_called()

    def test_t13_verify_not_ok_but_readiness_ready_closes(self) -> None:
        import server

        verify = _verify_ready(0, ready=True, mc_from_17th=0)
        verify["ok"] = False
        verify["issues"] = ["MC: нет операций с 17-го — проверь tail PDF в одном batch с head"]
        with patch("server.WORKING") as working, patch(
            "server.generate_reports"
        ), patch("server.verify_period", return_value=verify), patch(
            "server.run_derive", return_value={"status": 200}
        ), patch("server.run_imports", return_value=[{"status": 200}]), patch(
            "server.resolve_budget_version_id", return_value="vid"
        ), patch(
            "server.get_session",
            return_value=(MagicMock(), "http://127.0.0.1:8000"),
        ), patch("server.close_period", return_value=(200, {})) as close_period:
            working.__truediv__.return_value = MagicMock()
            payload = _process_month_payload(
                {
                    "period": "2026-06",
                    "skip_import": True,
                    "close": True,
                    "close_phase": "final",
                }
            )
        self.assertTrue(payload["ok"])
        close_period.assert_called_once()


if __name__ == "__main__":
    unittest.main()
