"""Unit tests for FIN-227 update_budget_item (rev.3 T1–T14)."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    Period,
    UpdateBudgetItemConvertError,
    UpdateBudgetItemCriticalError,
    update_budget_item,
)

VID = "00000000-0000-4000-8000-000000000001"
ITEM_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ITEM_ID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
PLAN_ID_2 = "44444444-4444-4444-8444-444444444444"
START = Period(year=2026, month=1)
END = Period(year=2026, month=6)


def _item(
    *,
    planning_type: str = "IRR",
    name: str = "Перевод карманных денег супругу",
    keywords: list[str] | None = None,
    status: str = "ACT",
) -> dict[str, Any]:
    return {
        "id": ITEM_ID,
        "name": name,
        "flow_type": "EXP",
        "operation_category_id": "C0006",
        "planning_type": planning_type,
        "keywords": keywords if keywords is not None else ["pocket"],
        "status": status,
    }


def _plan(
    *,
    planning_type: str = "IRR",
    amount: str = "250.00",
    plan_id: str = PLAN_ID,
) -> dict[str, Any]:
    if planning_type == "REG":
        return {
            "id": plan_id,
            "budget_version_id": VID,
            "budget_item_id": ITEM_ID,
            "planning_type": "REG",
            "amount": amount,
            "currency": "EUR",
            "status": "ACTIVE",
            "periodicity": "M",
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
            "forecast_method": None,
        }
    return {
        "id": plan_id,
        "budget_version_id": VID,
        "budget_item_id": ITEM_ID,
        "planning_type": "IRR",
        "amount": amount,
        "currency": "EUR",
        "status": "ACTIVE",
        "periodicity": None,
        "start_date": None,
        "end_date": None,
        "forecast_method": "MAN",
    }


class _UpdateBudgetItemMockApi:
    """Stub ApiClient for update_budget_item flows."""

    def __init__(
        self,
        *,
        version_status: str = "ACT",
        item: dict[str, Any] | None = None,
        act_plans: list[dict[str, Any]] | None = None,
        catalog: list[dict[str, Any]] | None = None,
        item_put_status: int = 200,
        plan_put_status: int = 200,
        rollback_status: int = 200,
        recalc_status: int = 200,
        projection_count: int = 42,
    ) -> None:
        self._version_status = version_status
        self._item = item or _item()
        self._act_plans = list(act_plans if act_plans is not None else [])
        self._catalog = catalog or [self._item]
        self._item_put_status = item_put_status
        self._plan_put_status = plan_put_status
        self._rollback_status = rollback_status
        self._recalc_status = recalc_status
        self._projection_count = projection_count
        self.item_puts: list[dict[str, Any]] = []
        self.plan_puts: list[dict[str, Any]] = []
        self.recalc_calls: list[str] = []
        self._item_put_count = 0

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/budget/versions":
            return {"budget_versions": [{"id": VID, "status": "ACT"}]}
        if path.endswith(f"/budget/versions/{VID}"):
            return {"id": VID, "status": self._version_status}
        if path == "/api/v1/budget/items":
            return {"budget_items": self._catalog}
        if path.startswith("/api/v1/budget/items/"):
            item_id = path.rsplit("/", 1)[-1]
            if item_id == ITEM_ID:
                return dict(self._item)
            for row in self._catalog:
                if str(row["id"]) == item_id:
                    return dict(row)
            raise AssertionError(f"unknown item {item_id}")
        if path.startswith("/api/v1/budget/plan-items?"):
            return {"budget_plan_items": list(self._act_plans)}
        if path.startswith("/api/v1/budget/plan-items/"):
            plan_id = path.rsplit("/", 1)[-1]
            for row in self._act_plans:
                if str(row["id"]) == plan_id:
                    return dict(row)
            raise AssertionError(f"unknown plan {plan_id}")
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "PUT" and path.startswith("/api/v1/budget/items/"):
            body = dict(data or {})
            self.item_puts.append(body)
            self._item_put_count += 1
            if self._item_put_count == 1:
                if self._item_put_status != 200:
                    return self._item_put_status, {"message": "item put failed"}
                self._item = dict(body)
                return 200, dict(body)
            if self._rollback_status != 200:
                return self._rollback_status, {"message": "rollback failed"}
            self._item = dict(body)
            return 200, dict(body)
        if method == "PUT" and path.startswith("/api/v1/budget/plan-items/"):
            body = dict(data or {})
            self.plan_puts.append(body)
            if self._plan_put_status != 200:
                return self._plan_put_status, {"message": "plan put failed"}
            return 200, dict(body)
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


class UpdateBudgetItemTest(unittest.TestCase):
    """update_budget_item (FIN-227 T1–T13)."""

    def test_t1_type_change_no_plan_items(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[])
        result = update_budget_item(
            api,
            budget_item_id=ITEM_ID,
            planning_type="REG",
            provided_fields=frozenset({"budget_item_id", "planning_type"}),
        )
        self.assertFalse(result["converted"])
        self.assertEqual(result["planning_type_after"], "REG")
        self.assertEqual(len(api.item_puts), 1)
        self.assertEqual(api.plan_puts, [])
        self.assertEqual(api.recalc_calls, [])
        self.assertNotIn("recalculate", result)

    def test_t2_conflict_without_convert(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[_plan()])
        with self.assertRaises(RuntimeError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                planning_type="REG",
                provided_fields=frozenset({"budget_item_id", "planning_type"}),
            )
        self.assertIn("conflicting_plan_item_ids", str(ctx.exception))
        self.assertIn(PLAN_ID, str(ctx.exception))
        self.assertEqual(api.item_puts, [])

    def test_t3_irr_to_reg_convert_recalculate(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[_plan()])
        result = update_budget_item(
            api,
            budget_item_id=ITEM_ID,
            planning_type="REG",
            convert_plan_item=True,
            amount="250.00",
            start_period=START,
            end_period=END,
            provided_fields=frozenset(
                {
                    "budget_item_id",
                    "planning_type",
                    "convert_plan_item",
                    "amount",
                    "start_period",
                    "end_period",
                },
            ),
        )
        self.assertTrue(result["converted"])
        self.assertEqual(result["planning_type_after"], "REG")
        self.assertEqual(api.plan_puts[0]["planning_type"], "REG")
        self.assertEqual(api.plan_puts[0]["start_date"], "2026-01-01")
        self.assertEqual(api.plan_puts[0]["end_date"], "2026-06-30")
        self.assertEqual(api.recalc_calls, [VID])
        self.assertEqual(result["recalculate"]["projection_rows"], 42)

    def test_t4_reg_to_irr_amount_from_current(self) -> None:
        api = _UpdateBudgetItemMockApi(
            item=_item(planning_type="REG"),
            act_plans=[_plan(planning_type="REG", amount="80.00")],
        )
        result = update_budget_item(
            api,
            budget_item_id=ITEM_ID,
            planning_type="IRR",
            convert_plan_item=True,
            forecast_method="MAN",
            provided_fields=frozenset(
                {
                    "budget_item_id",
                    "planning_type",
                    "convert_plan_item",
                    "forecast_method",
                },
            ),
        )
        self.assertTrue(result["converted"])
        self.assertEqual(api.plan_puts[0]["planning_type"], "IRR")
        self.assertEqual(api.plan_puts[0]["amount"], "80.00")
        self.assertEqual(api.plan_puts[0]["forecast_method"], "MAN")

        api2 = _UpdateBudgetItemMockApi(
            item=_item(planning_type="REG"),
            act_plans=[_plan(planning_type="REG")],
        )
        with self.assertRaises(ValueError):
            update_budget_item(
                api2,
                budget_item_id=ITEM_ID,
                planning_type="IRR",
                convert_plan_item=True,
                start_period=START,
                provided_fields=frozenset(
                    {
                        "budget_item_id",
                        "planning_type",
                        "convert_plan_item",
                        "start_period",
                    },
                ),
            )

    def test_t5_arc_version(self) -> None:
        api = _UpdateBudgetItemMockApi(version_status="ARC", act_plans=[])
        with self.assertRaises(RuntimeError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                planning_type="REG",
                provided_fields=frozenset({"budget_item_id", "planning_type"}),
            )
        self.assertIn("ARC", str(ctx.exception))
        self.assertEqual(api.item_puts, [])

    def test_t6_rename_duplicate(self) -> None:
        other = {
            "id": OTHER_ITEM_ID,
            "name": "Other Name",
            "flow_type": "EXP",
            "operation_category_id": "C0006",
            "planning_type": "REG",
            "keywords": [],
            "status": "ACT",
        }
        api = _UpdateBudgetItemMockApi(
            act_plans=[],
            catalog=[_item(), other],
        )
        with self.assertRaises(RuntimeError):
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                name="  other name  ",
                provided_fields=frozenset({"budget_item_id", "name"}),
            )
        self.assertEqual(api.item_puts, [])

    def test_t7_ambiguous_article(self) -> None:
        api = _UpdateBudgetItemMockApi(
            catalog=[
                _item(name="Transfer A"),
                {
                    "id": OTHER_ITEM_ID,
                    "name": "Transfer B",
                    "flow_type": "EXP",
                    "operation_category_id": "C0006",
                    "planning_type": "REG",
                    "keywords": [],
                    "status": "ACT",
                },
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            update_budget_item(
                api,
                article="Transfer",
                planning_type="REG",
                provided_fields=frozenset({"article", "planning_type"}),
            )
        self.assertIn("ambiguous", str(ctx.exception).lower())

    def test_t8_two_act_plans_ambiguous(self) -> None:
        api = _UpdateBudgetItemMockApi(
            act_plans=[_plan(), _plan(plan_id=PLAN_ID_2)],
        )
        with self.assertRaises(RuntimeError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                planning_type="REG",
                convert_plan_item=True,
                amount="1",
                start_period=START,
                provided_fields=frozenset(
                    {
                        "budget_item_id",
                        "planning_type",
                        "convert_plan_item",
                        "amount",
                        "start_period",
                    },
                ),
            )
        self.assertIn("ambiguous", str(ctx.exception).lower())
        self.assertEqual(api.item_puts, [])

    def test_t9a_convert_fail_rollback_ok(self) -> None:
        api = _UpdateBudgetItemMockApi(
            act_plans=[_plan()],
            plan_put_status=422,
            rollback_status=200,
        )
        with self.assertRaises(UpdateBudgetItemConvertError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                planning_type="REG",
                convert_plan_item=True,
                amount="250.00",
                start_period=START,
                provided_fields=frozenset(
                    {
                        "budget_item_id",
                        "planning_type",
                        "convert_plan_item",
                        "amount",
                        "start_period",
                    },
                ),
            )
        self.assertIn("rolled back", str(ctx.exception).lower())
        self.assertEqual(len(api.item_puts), 2)
        self.assertEqual(api.item_puts[1]["planning_type"], "IRR")
        self.assertEqual(api.recalc_calls, [])
        self.assertEqual(api._item["planning_type"], "IRR")

    def test_t9b_convert_fail_rollback_fail(self) -> None:
        api = _UpdateBudgetItemMockApi(
            act_plans=[_plan()],
            plan_put_status=422,
            rollback_status=500,
        )
        with self.assertRaises(UpdateBudgetItemCriticalError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                planning_type="REG",
                convert_plan_item=True,
                amount="250.00",
                start_period=START,
                provided_fields=frozenset(
                    {
                        "budget_item_id",
                        "planning_type",
                        "convert_plan_item",
                        "amount",
                        "start_period",
                    },
                ),
            )
        self.assertIn("rollback_error", ctx.exception.context)
        self.assertIn("article_after", ctx.exception.context)
        self.assertEqual(api.recalc_calls, [])

    def test_t10_keywords_no_recalculate(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[])
        result = update_budget_item(
            api,
            budget_item_id=ITEM_ID,
            keywords=["a", "b"],
            provided_fields=frozenset({"budget_item_id", "keywords"}),
        )
        self.assertFalse(result["converted"])
        self.assertEqual(api.item_puts[0]["keywords"], ["a", "b"])
        self.assertEqual(api.recalc_calls, [])

    def test_t11_convert_without_type_change(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[_plan()])
        with self.assertRaises(ValueError) as ctx:
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                name="New name",
                convert_plan_item=True,
                provided_fields=frozenset(
                    {"budget_item_id", "name", "convert_plan_item"},
                ),
            )
        self.assertIn("planning_type", str(ctx.exception))
        self.assertEqual(api.item_puts, [])

    def test_t12_arc_plans_ignored(self) -> None:
        """ACT list empty → type change without convert OK (ARC rows not in ACT GET)."""
        api = _UpdateBudgetItemMockApi(act_plans=[])
        result = update_budget_item(
            api,
            budget_item_id=ITEM_ID,
            planning_type="REG",
            provided_fields=frozenset({"budget_item_id", "planning_type"}),
        )
        self.assertEqual(result["planning_type_after"], "REG")
        self.assertFalse(result["converted"])
        self.assertEqual(len(api.item_puts), 1)

    def test_t13_empty_operation_category_id(self) -> None:
        api = _UpdateBudgetItemMockApi(act_plans=[])
        with self.assertRaises(ValueError):
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                operation_category_id="",
                provided_fields=frozenset(
                    {"budget_item_id", "operation_category_id"},
                ),
            )
        with self.assertRaises(ValueError):
            update_budget_item(
                api,
                budget_item_id=ITEM_ID,
                operation_category_id=None,
                provided_fields=frozenset(
                    {"budget_item_id", "operation_category_id"},
                ),
            )
        self.assertEqual(api.item_puts, [])


class UpdateBudgetItemHandlerTest(unittest.TestCase):
    """MCP handler registration (FIN-227 T14)."""

    def test_t14_tool_registered(self) -> None:
        import server

        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("update_budget_item", names)
        self.assertTrue(hasattr(server, "_handle_update_budget_item"))

    @patch("server.update_budget_item")
    @patch("server.get_session")
    def test_handler_success(
        self,
        mock_get_session: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_update.return_value = {
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "article": "X",
            "planning_type_before": "IRR",
            "planning_type_after": "REG",
            "budget_item": _item(planning_type="REG"),
            "converted": False,
        }
        out = server._handle_update_budget_item(
            {"budget_item_id": ITEM_ID, "planning_type": "REG"},
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        mock_update.assert_called_once()
        kwargs = mock_update.call_args.kwargs
        self.assertIn("planning_type", kwargs["provided_fields"])
        self.assertIsNone(kwargs["recalculate"])

    @patch("server.update_budget_item")
    @patch("server.get_session")
    def test_handler_convert_error_context(
        self,
        mock_get_session: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_update.side_effect = UpdateBudgetItemConvertError(
            "conversion failed, changes rolled back",
            {"budget_item_id": ITEM_ID, "article_before": _item()},
        )
        out = server._handle_update_budget_item(
            {
                "budget_item_id": ITEM_ID,
                "planning_type": "REG",
                "convert_plan_item": True,
                "amount": "1",
                "start_period": "2026-01",
            },
        )
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["budget_item_id"], ITEM_ID)


if __name__ == "__main__":
    unittest.main()
