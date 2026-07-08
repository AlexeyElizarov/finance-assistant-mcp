"""Unit tests for FIN-110 create_plan_item (T1–T8)."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    CreatePlanItemRecalculateError,
    Period,
    create_plan_item,
    period_last_day,
)

VID = "00000000-0000-4000-8000-000000000001"
ITEM_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
JUNE = Period(year=2026, month=6)
JULY = Period(year=2026, month=7)


def _created_plan(amount: str = "62.90", end_date: str | None = "2026-06-30") -> dict[str, Any]:
    return {
        "id": PLAN_ID,
        "budget_version_id": VID,
        "budget_item_id": ITEM_ID,
        "planning_type": "REG",
        "amount": amount,
        "currency": "EUR",
        "status": "ACTIVE",
        "periodicity": "M",
        "start_date": "2026-06-01",
        "end_date": end_date,
        "forecast_method": None,
    }


class _CreatePlanItemMockApi:
    """Stub ApiClient for create_plan_item flows."""

    def __init__(
        self,
        *,
        version_status: str = "ACT",
        plan_status: int = 201,
        recalc_status: int = 200,
        projection_count: int = 42,
        article_name: str = "BahnCard 25 (office week)",
    ) -> None:
        self._version_status = version_status
        self._plan_status = plan_status
        self._recalc_status = recalc_status
        self._projection_count = projection_count
        self._article_name = article_name
        self.plan_bodies: list[dict[str, Any]] = []
        self.recalc_calls: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/budget/versions":
            return {"budget_versions": [{"id": VID, "status": "ACT"}]}
        if path.endswith(f"/budget/versions/{VID}"):
            return {"id": VID, "status": self._version_status}
        if path == "/api/v1/budget/items":
            return {"budget_items": [{"id": ITEM_ID, "name": self._article_name}]}
        if path.endswith(f"/budget/items/{ITEM_ID}"):
            return {"id": ITEM_ID, "name": self._article_name}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "POST" and path == "/api/v1/budget/plan-items":
            self.plan_bodies.append(dict(data or {}))
            if self._plan_status != 201:
                return self._plan_status, {"message": "validation error"}
            return 201, _created_plan(
                str((data or {}).get("amount", "62.90")),
                (data or {}).get("end_date"),
            )
        if method == "POST" and path == "/api/v1/budget/projections/recalculate":
            self.recalc_calls.append(str((data or {}).get("budget_version_id")))
            if self._recalc_status != 200:
                return self._recalc_status, {"message": "recalc failed"}
            return 200, {"budget_projections": [{}] * self._projection_count}
        raise AssertionError(f"unexpected request {method} {path}")


class CreatePlanItemTest(unittest.TestCase):
    """create_plan_item (FIN-110)."""

    def test_bounded_happy_path(self) -> None:
        """T1: start=end=2026-06 → POST + recalculate."""
        api = _CreatePlanItemMockApi()
        result = create_plan_item(
            api,
            "62.90",
            JUNE,
            budget_item_id=ITEM_ID,
            end_period=JUNE,
        )
        self.assertEqual(result["plan_item_id"], PLAN_ID)
        self.assertEqual(api.plan_bodies[0]["start_date"], "2026-06-01")
        self.assertEqual(api.plan_bodies[0]["end_date"], period_last_day(JUNE))
        self.assertEqual(api.recalc_calls, [VID])
        self.assertEqual(result["recalculate"]["projection_rows"], 42)

    def test_open_ended_no_end_period(self) -> None:
        """T2: no end_period → end_date=null."""
        api = _CreatePlanItemMockApi()
        create_plan_item(api, "10.00", JUNE, article="BahnCard")
        self.assertIsNone(api.plan_bodies[0]["end_date"])

    def test_end_before_start_raises(self) -> None:
        """T3: end_period < start_period → error before HTTP."""
        api = _CreatePlanItemMockApi()
        with self.assertRaises(ValueError):
            create_plan_item(
                api,
                "62.90",
                JULY,
                budget_item_id=ITEM_ID,
                end_period=JUNE,
            )
        self.assertEqual(api.plan_bodies, [])

    def test_arc_blocks_before_post(self) -> None:
        """T4: ARC version → error before POST."""
        api = _CreatePlanItemMockApi(version_status="ARC")
        with self.assertRaises(RuntimeError):
            create_plan_item(api, "62.90", JUNE, budget_item_id=ITEM_ID, end_period=JUNE)
        self.assertEqual(api.plan_bodies, [])

    def test_irr_rejected(self) -> None:
        """T5: planning_type=IRR → error."""
        api = _CreatePlanItemMockApi()
        with self.assertRaises(ValueError):
            create_plan_item(
                api,
                "62.90",
                JUNE,
                budget_item_id=ITEM_ID,
                planning_type="IRR",
            )

    def test_recalculate_false_skips_post(self) -> None:
        """T6: recalculate=false → no POST recalculate."""
        api = _CreatePlanItemMockApi()
        result = create_plan_item(
            api,
            "62.90",
            JUNE,
            budget_item_id=ITEM_ID,
            end_period=JUNE,
            recalculate=False,
        )
        self.assertNotIn("recalculate", result)
        self.assertEqual(api.recalc_calls, [])

    def test_recalculate_failure_includes_create_context(self) -> None:
        """T7: recalculate fail after POST → context with plan_item_id."""
        api = _CreatePlanItemMockApi(recalc_status=500)
        with self.assertRaises(CreatePlanItemRecalculateError) as ctx:
            create_plan_item(
                api,
                "62.90",
                JUNE,
                budget_item_id=ITEM_ID,
                end_period=JUNE,
            )
        err = ctx.exception
        self.assertEqual(err.context["plan_item_id"], PLAN_ID)
        self.assertEqual(err.context["budget_item_id"], ITEM_ID)
        self.assertEqual(err.context["budget_version_id"], VID)
        self.assertIn("plan_item", err.context)

    def test_zero_amount_allowed(self) -> None:
        """T8: amount=0 → POST succeeds."""
        api = _CreatePlanItemMockApi()
        create_plan_item(
            api,
            0,
            JUNE,
            budget_item_id=ITEM_ID,
            end_period=JUNE,
        )
        self.assertEqual(api.plan_bodies[0]["amount"], "0.00")


class CreatePlanItemHandlerTest(unittest.TestCase):
    """MCP handler (FIN-110)."""

    @patch("server.create_plan_item")
    @patch("server.get_session")
    def test_success_payload(self, mock_get_session: MagicMock, mock_create: MagicMock) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_create.return_value = {
            "plan_item_id": PLAN_ID,
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "article": "BahnCard",
            "amount": "62.90",
            "start_period": "2026-06",
            "end_period": "2026-06",
            "plan_item": _created_plan(),
        }
        out = server._handle_create_plan_item(
            {
                "budget_item_id": ITEM_ID,
                "amount": "62.90",
                "start_period": "2026-06",
                "end_period": "2026-06",
            },
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan_item_id"], PLAN_ID)

    @patch("server.create_plan_item")
    @patch("server.get_session")
    def test_recalculate_error_returns_context(
        self,
        mock_get_session: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        context = {
            "plan_item_id": PLAN_ID,
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "amount": "62.90",
            "plan_item": _created_plan(),
        }
        mock_create.side_effect = CreatePlanItemRecalculateError("recalc failed", context)
        out = server._handle_create_plan_item(
            {
                "budget_item_id": ITEM_ID,
                "amount": "62.90",
                "start_period": "2026-06",
            },
        )
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["plan_item_id"], PLAN_ID)


if __name__ == "__main__":
    unittest.main()
