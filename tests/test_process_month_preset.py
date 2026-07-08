"""Unit tests for FIN-31 process_month preset monthly_close_prepare."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    PRESET_MONTHLY_CLOSE_PREPARE,
    prepare_process_month_orchestrator_flags,
    resolve_process_month_arguments,
    validate_process_month_close_phase,
)


def _preset_args(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "period": "2026-02",
        "preset": PRESET_MONTHLY_CLOSE_PREPARE,
    }
    base.update(overrides)
    return base


class ProcessMonthPresetResolveTest(unittest.TestCase):
    """Preset merge and validation (T1–T5, T8, T10–T11)."""

    def test_t1_preset_defaults(self) -> None:
        """T1: preset alone → monthly_close_prepare defaults."""
        effective = resolve_process_month_arguments(_preset_args())
        assert effective is not None
        self.assertTrue(effective["reopen_neighbors"])
        self.assertTrue(effective["reopen"])
        self.assertTrue(effective["reports"])
        self.assertFalse(effective["close"])
        self.assertFalse(effective["skip_import"])
        self.assertFalse(effective["verify_only"])

    def test_t2_unknown_preset(self) -> None:
        """T2: unknown preset → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            resolve_process_month_arguments(_preset_args(preset="close_month"))
        self.assertIn("unknown preset", str(ctx.exception))

    def test_t3_reports_override(self) -> None:
        """T3: explicit reports false overrides preset."""
        flags = prepare_process_month_orchestrator_flags(_preset_args(reports=False))
        self.assertFalse(flags["reports"])
        self.assertTrue(flags["reopen_neighbors"])

    def test_t4_verify_only_override(self) -> None:
        """T4: verify_only true after merge, not error."""
        flags = prepare_process_month_orchestrator_flags(_preset_args(verify_only=True))
        self.assertTrue(flags["verify_only"])

    def test_t5_preset_reports_true_without_explicit_keys(self) -> None:
        """T5: absent bool keys do not force false when preset active."""
        flags = prepare_process_month_orchestrator_flags(_preset_args())
        self.assertTrue(flags["reports"])

    def test_t7_no_preset_reports_false(self) -> None:
        """T7: without preset absent reports → false."""
        flags = prepare_process_month_orchestrator_flags({"period": "2026-02"})
        self.assertFalse(flags["reports"])

    def test_t8_multiple_overrides(self) -> None:
        """T8: multiple explicit overrides merge correctly."""
        flags = prepare_process_month_orchestrator_flags(
            _preset_args(reports=False, reopen=False, skip_import=True)
        )
        self.assertFalse(flags["reports"])
        self.assertFalse(flags["reopen"])
        self.assertTrue(flags["skip_import"])
        self.assertTrue(flags["reopen_neighbors"])

    def test_t10_close_phase_without_close(self) -> None:
        """T10: close_phase without close → validation error."""
        with self.assertRaises(ValueError) as ctx:
            prepare_process_month_orchestrator_flags(
                _preset_args(close_phase="final")
            )
        self.assertIn("close_phase requires close=true", str(ctx.exception))

    def test_t10_no_preset_close_phase_without_close(self) -> None:
        """T10: validation applies without preset too."""
        with self.assertRaises(ValueError):
            prepare_process_month_orchestrator_flags(
                {"period": "2026-02", "close_phase": "preliminary"}
            )

    def test_t11_close_with_close_phase(self) -> None:
        """T11: close + close_phase preliminary passes validation."""
        flags = prepare_process_month_orchestrator_flags(
            _preset_args(close=True, close_phase="preliminary")
        )
        self.assertTrue(flags["close"])
        self.assertEqual(flags["close_phase"], "preliminary")

    def test_resolve_returns_none_without_preset(self) -> None:
        """D-11: no preset → None from resolve helper."""
        self.assertIsNone(resolve_process_month_arguments({"period": "2026-02"}))

    def test_validate_close_phase_helper(self) -> None:
        """validate_process_month_close_phase raises when close false."""
        with self.assertRaises(ValueError):
            validate_process_month_close_phase({"close_phase": "final"}, False)


class ProcessMonthPresetHandlerTest(unittest.TestCase):
    """Handler wiring (T6, T7 integration, T9)."""

    @patch("server.WORKING")
    @patch("server.generate_reports")
    @patch("server.verify_period")
    @patch("server.run_derive")
    @patch("server.run_imports")
    @patch("server.reopen_period")
    @patch("server.mc_reopen_neighbor_periods")
    @patch("server.resolve_budget_version_id")
    @patch("server.get_session")
    def test_t6_preset_invokes_generate_reports(
        self,
        get_session: MagicMock,
        resolve_budget_version_id: MagicMock,
        mc_reopen_neighbor_periods: MagicMock,
        reopen_period: MagicMock,
        run_imports: MagicMock,
        run_derive: MagicMock,
        verify_period: MagicMock,
        generate_reports: MagicMock,
        working: MagicMock,
    ) -> None:
        """T6: preset monthly_close_prepare → generate_reports called."""
        import server

        api = MagicMock()
        get_session.return_value = (api, "http://127.0.0.1:8000")
        resolve_budget_version_id.return_value = "vid"
        mc_reopen_neighbor_periods.return_value = ([], [])
        reopen_period.return_value = (200, {})
        run_imports.return_value = [{"status": 200}]
        run_derive.return_value = {"status": 200}
        verify_period.return_value = {
            "ok": True,
            "classification_summary": {"expense_c9999_count": 0},
            "readiness": {"ready": True},
        }
        log_path = MagicMock()
        working.__truediv__.return_value = log_path

        server._handle_process_month(_preset_args())
        generate_reports.assert_called_once()

    @patch("server.WORKING")
    @patch("server.generate_reports")
    @patch("server.verify_period")
    @patch("server.run_derive")
    @patch("server.run_imports")
    @patch("server.resolve_budget_version_id")
    @patch("server.get_session")
    def test_t7_no_preset_skips_generate_reports(
        self,
        get_session: MagicMock,
        resolve_budget_version_id: MagicMock,
        run_imports: MagicMock,
        run_derive: MagicMock,
        verify_period: MagicMock,
        generate_reports: MagicMock,
        working: MagicMock,
    ) -> None:
        """T7: without preset and absent reports → no PDF generation."""
        import server

        api = MagicMock()
        get_session.return_value = (api, "http://127.0.0.1:8000")
        resolve_budget_version_id.return_value = "vid"
        run_imports.return_value = [{"status": 200}]
        run_derive.return_value = {"status": 200}
        verify_period.return_value = {
            "ok": True,
            "classification_summary": {"expense_c9999_count": 0},
            "readiness": {"ready": True},
        }
        log_path = MagicMock()
        working.__truediv__.return_value = log_path

        server._handle_process_month({"period": "2026-02", "skip_import": True})
        generate_reports.assert_not_called()

    def test_t9_schema_includes_preset_enum(self) -> None:
        """T9: process_month schema lists monthly_close_prepare preset."""
        import server

        tools = asyncio.run(server.list_tools())
        process_month = next(t for t in tools if t.name == "process_month")
        preset_schema = process_month.inputSchema["properties"]["preset"]
        self.assertEqual(preset_schema["enum"], [PRESET_MONTHLY_CLOSE_PREPARE])

    def test_unknown_preset_tool_error(self) -> None:
        """Unknown preset surfaces as tool error before HTTP."""
        import server

        with patch("server.get_session") as get_session:
            get_session.side_effect = AssertionError("must not connect")
            with self.assertRaises(ValueError):
                server._handle_process_month(_preset_args(preset="dry_verify"))


if __name__ == "__main__":
    unittest.main()
