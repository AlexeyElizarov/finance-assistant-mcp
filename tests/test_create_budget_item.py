"""Unit tests for FIN-109 create_budget_item (rev.3 T1–T14)."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    CreateBudgetItemPlanItemError,
    CreateBudgetItemRecalculateError,
    Period,
    assert_budget_item_name_available,
    assert_period_range,
    build_reg_plan_item_body,
    create_budget_item,
    parse_period,
    period_last_day,
    projection_rows_count,
)

VID = "00000000-0000-4000-8000-000000000001"
ITEM_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
START = Period(year=2026, month=5)
END = Period(year=2026, month=7)


def _created_item(keywords: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": ITEM_ID,
        "name": "Подписка Philips Hue Sync TV",
        "flow_type": "EXP",
        "operation_category_id": "C0006",
        "planning_type": "REG",
        "keywords": keywords if keywords is not None else ["Hue"],
        "status": "ACT",
    }


def _created_plan(amount: str = "3.00", end_date: str | None = None) -> dict[str, Any]:
    return {
        "id": PLAN_ID,
        "budget_version_id": VID,
        "budget_item_id": ITEM_ID,
        "planning_type": "REG",
        "amount": amount,
        "currency": "EUR",
        "status": "ACTIVE",
        "periodicity": "M",
        "start_date": "2026-05-01",
        "end_date": end_date,
        "forecast_method": None,
    }


class _CreateBudgetItemMockApi:
    """Stub ApiClient for budget item create flows."""

    def __init__(
        self,
        *,
        version_status: str = "ACT",
        existing_names: list[str] | None = None,
        item_status: int = 201,
        plan_status: int = 201,
        recalc_status: int = 200,
        projection_count: int = 42,
    ) -> None:
        self._version_status = version_status
        self._existing_names = existing_names or []
        self._item_status = item_status
        self._plan_status = plan_status
        self._recalc_status = recalc_status
        self._projection_count = projection_count
        self.item_bodies: list[dict[str, Any]] = []
        self.plan_bodies: list[dict[str, Any]] = []
        self.recalc_calls: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/budget/versions":
            return {"budget_versions": [{"id": VID, "status": "ACT"}]}
        if path.endswith(f"/budget/versions/{VID}"):
            return {"id": VID, "status": self._version_status}
        if path == "/api/v1/budget/items":
            return {
                "budget_items": [{"id": "x", "name": n} for n in self._existing_names],
            }
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "POST" and path == "/api/v1/budget/items":
            self.item_bodies.append(dict(data or {}))
            if self._item_status != 201:
                return self._item_status, {"message": "validation error"}
            kw = (data or {}).get("keywords", ["Hue"])
            return 201, _created_item(list(kw))
        if method == "POST" and path == "/api/v1/budget/plan-items":
            self.plan_bodies.append(dict(data or {}))
            if self._plan_status != 201:
                return self._plan_status, {"message": "validation error"}
            return 201, _created_plan(
                str((data or {}).get("amount", "3.00")),
                (data or {}).get("end_date"),
            )
        if method == "POST" and path == "/api/v1/budget/projections/recalculate":
            self.recalc_calls.append(str((data or {}).get("budget_version_id")))
            if self._recalc_status != 200:
                return self._recalc_status, {"message": "recalc failed"}
            return 200, {
                "budget_projections": [{}] * self._projection_count,
                "horizon_months": [],
                "grid_nodes": [],
            }
        raise AssertionError(f"unexpected request {method} {path}")


class PeriodHelpersTest(unittest.TestCase):
    """period_last_day and assert_period_range (D-05)."""

    def test_period_last_day(self) -> None:
        self.assertEqual(period_last_day(Period(2026, 7)), "2026-07-31")
        self.assertEqual(period_last_day(Period(2024, 2)), "2024-02-29")

    def test_assert_period_range_ok(self) -> None:
        assert_period_range(START, END)

    def test_assert_period_range_rejects(self) -> None:
        with self.assertRaises(ValueError):
            assert_period_range(END, START)


class BuildRegPlanItemBodyTest(unittest.TestCase):
    """build_reg_plan_item_body (D-05)."""

    def test_start_and_no_end(self) -> None:
        body = build_reg_plan_item_body(
            budget_version_id=VID,
            budget_item_id=ITEM_ID,
            amount="3.00",
            currency="EUR",
            start_period=START,
            end_period=None,
            periodicity="M",
        )
        self.assertEqual(body["start_date"], "2026-05-01")
        self.assertIsNone(body["end_date"])
        self.assertEqual(body["status"], "ACTIVE")

    def test_end_period_last_day(self) -> None:
        body = build_reg_plan_item_body(
            budget_version_id=VID,
            budget_item_id=ITEM_ID,
            amount="3.00",
            currency="EUR",
            start_period=START,
            end_period=END,
            periodicity="M",
        )
        self.assertEqual(body["end_date"], "2026-07-31")


class CreateBudgetItemTest(unittest.TestCase):
    """create_budget_item (FIN-109 T1–T14)."""

    def _call(self, api: _CreateBudgetItemMockApi, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": "Подписка Philips Hue Sync TV",
            "flow_type": "EXP",
            "operation_category_id": "C0006",
            "amount": "3.00",
            "start_period": START,
            "keywords": ["Hue"],
        }
        params.update(kwargs)
        return create_budget_item(api, **params)

    def test_happy_path(self) -> None:
        """T1: POST items + plan-items + recalculate; dates per D-05."""
        api = _CreateBudgetItemMockApi()
        result = self._call(api, end_period=END)
        self.assertEqual(result["budget_item_id"], ITEM_ID)
        self.assertEqual(result["plan_item_id"], PLAN_ID)
        self.assertEqual(api.plan_bodies[0]["start_date"], "2026-05-01")
        self.assertEqual(api.plan_bodies[0]["end_date"], "2026-07-31")
        self.assertEqual(api.recalc_calls, [VID])
        self.assertEqual(result["recalculate"]["projection_rows"], 42)

    def test_duplicate_name_exact_case(self) -> None:
        """T2: duplicate name → error before POST."""
        api = _CreateBudgetItemMockApi(existing_names=["Подписка Philips Hue Sync TV"])
        with self.assertRaises(RuntimeError):
            self._call(api)
        self.assertEqual(api.item_bodies, [])

    def test_arc_version_blocks(self) -> None:
        """T3: ARC version blocks before POST."""
        api = _CreateBudgetItemMockApi(version_status="ARC")
        with self.assertRaises(RuntimeError):
            self._call(api)
        self.assertEqual(api.item_bodies, [])

    def test_negative_amount_raises(self) -> None:
        """T4: negative amount."""
        api = _CreateBudgetItemMockApi()
        with self.assertRaises(ValueError):
            self._call(api, amount="-1")

    def test_recalculate_false_skips_post(self) -> None:
        """T5: recalculate=false."""
        api = _CreateBudgetItemMockApi()
        result = self._call(api, recalculate=False)
        self.assertNotIn("recalculate", result)
        self.assertEqual(api.recalc_calls, [])

    def test_plan_item_failure_partial_create(self) -> None:
        """T6: items OK + plan-items fail → D-04 context."""
        api = _CreateBudgetItemMockApi(plan_status=422)
        with self.assertRaises(CreateBudgetItemPlanItemError) as ctx:
            self._call(api)
        err = ctx.exception
        self.assertEqual(err.context["budget_item_id"], ITEM_ID)
        self.assertIn("budget_item", err.context)
        self.assertEqual(api.recalc_calls, [])
        self.assertEqual(len(api.item_bodies), 1)

    def test_recalculate_failure_full_context(self) -> None:
        """T7: recalculate fail → full create context (D-09)."""
        api = _CreateBudgetItemMockApi(recalc_status=500)
        with self.assertRaises(CreateBudgetItemRecalculateError) as ctx:
            self._call(api)
        err = ctx.exception
        for key in (
            "budget_item_id",
            "plan_item_id",
            "budget_version_id",
            "amount",
            "budget_item",
            "plan_item",
        ):
            self.assertIn(key, err.context)

    def test_irr_planning_type_rejected(self) -> None:
        """T8: only REG supported."""
        api = _CreateBudgetItemMockApi()
        with self.assertRaises(ValueError):
            self._call(api, planning_type="IRR")

    def test_invalid_start_period(self) -> None:
        """T9: invalid period."""
        with self.assertRaises(ValueError):
            parse_period("2026-13")

    def test_end_before_start_raises(self) -> None:
        """T10: end_period < start_period."""
        api = _CreateBudgetItemMockApi()
        with self.assertRaises(ValueError):
            self._call(api, start_period=END, end_period=START)
        self.assertEqual(api.item_bodies, [])

    def test_duplicate_case_and_trim(self) -> None:
        """T11: duplicate via casefold + strip (D-02)."""
        api = _CreateBudgetItemMockApi(existing_names=["hue sync"])
        with self.assertRaises(RuntimeError):
            self._call(api, name="  HUE SYNC  ")
        self.assertEqual(api.item_bodies, [])

    def test_projection_rows_count(self) -> None:
        """T12: projection_rows from recalculate response."""
        api = _CreateBudgetItemMockApi(projection_count=7)
        result = self._call(api)
        self.assertEqual(result["recalculate"]["projection_rows"], 7)
        body = {"updated_count": 99, "budget_projections": [{}]}
        self.assertEqual(projection_rows_count(body), 99)

    def test_amount_zero_allowed(self) -> None:
        """T13: amount=0."""
        api = _CreateBudgetItemMockApi()
        result = self._call(api, amount=0)
        self.assertEqual(result["amount"], "0.00")
        self.assertEqual(api.plan_bodies[0]["amount"], "0.00")

    def test_empty_keywords_allowed(self) -> None:
        """T14: keywords=[] (D-15)."""
        api = _CreateBudgetItemMockApi()
        result = self._call(api, keywords=[])
        self.assertEqual(api.item_bodies[0]["keywords"], [])
        self.assertEqual(result["budget_item"]["keywords"], [])


class AssertBudgetItemNameAvailableTest(unittest.TestCase):
    """assert_budget_item_name_available (D-02)."""

    def test_strip_and_casefold(self) -> None:
        api = _CreateBudgetItemMockApi(existing_names=["  Hue Sync  "])
        with self.assertRaises(RuntimeError):
            assert_budget_item_name_available(api, "HUE SYNC")


class CreateBudgetItemHandlerTest(unittest.TestCase):
    """MCP handler (FIN-109)."""

    @patch("server.create_budget_item")
    @patch("server.get_session")
    def test_success_payload(self, mock_get_session: MagicMock, mock_create: MagicMock) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_create.return_value = {
            "budget_version_id": VID,
            "budget_item_id": ITEM_ID,
            "plan_item_id": PLAN_ID,
            "name": "Подписка Philips Hue Sync TV",
            "amount": "3.00",
            "start_period": "2026-05",
            "budget_item": _created_item(),
            "plan_item": _created_plan(),
            "recalculate": {"budget_version_id": VID, "projection_rows": 10},
        }
        out = server._handle_create_budget_item(
            {
                "name": "Подписка Philips Hue Sync TV",
                "flow_type": "EXP",
                "operation_category_id": "C0006",
                "amount": "3.00",
                "start_period": "2026-05",
            },
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs.get("item_status"), "ACT")

    @patch("server.create_budget_item")
    @patch("server.get_session")
    def test_plan_item_error_returns_items_context(
        self,
        mock_get_session: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        context = {
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "name": "Hue",
            "amount": "3.00",
            "budget_item": _created_item(),
        }
        mock_create.side_effect = CreateBudgetItemPlanItemError("plan failed", context)
        out = server._handle_create_budget_item(
            {
                "name": "Hue",
                "flow_type": "EXP",
                "operation_category_id": "C0006",
                "amount": "3.00",
                "start_period": "2026-05",
            },
        )
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["budget_item_id"], ITEM_ID)
        self.assertIn("budget_item", payload)

    @patch("server.create_budget_item")
    @patch("server.get_session")
    def test_recalculate_error_returns_context(
        self,
        mock_get_session: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        context = {
            "budget_item_id": ITEM_ID,
            "plan_item_id": PLAN_ID,
            "budget_version_id": VID,
            "amount": "3.00",
            "budget_item": _created_item(),
            "plan_item": _created_plan(),
        }
        mock_create.side_effect = CreateBudgetItemRecalculateError("recalc failed", context)
        out = server._handle_create_budget_item(
            {
                "name": "Hue",
                "flow_type": "EXP",
                "operation_category_id": "C0006",
                "amount": "3.00",
                "start_period": "2026-05",
            },
        )
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["plan_item_id"], PLAN_ID)


if __name__ == "__main__":
    unittest.main()
