"""Unit tests for FIN-110 create_plan_item (REG) and FIN-119 (IRR)."""

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
OTHER_ITEM_ID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
JUNE = Period(year=2026, month=6)
JULY = Period(year=2026, month=7)


def _created_reg_plan(amount: str = "62.90", end_date: str | None = "2026-06-30") -> dict[str, Any]:
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


def _created_irr_plan(amount: str = "0.00", forecast_method: str = "MAN") -> dict[str, Any]:
    return {
        "id": PLAN_ID,
        "budget_version_id": VID,
        "budget_item_id": ITEM_ID,
        "planning_type": "IRR",
        "amount": amount,
        "currency": "EUR",
        "status": "ACTIVE",
        "periodicity": None,
        "start_date": None,
        "end_date": None,
        "forecast_method": forecast_method,
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
        article_planning_type: str = "REG",
        budget_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self._version_status = version_status
        self._plan_status = plan_status
        self._recalc_status = recalc_status
        self._projection_count = projection_count
        self._article_name = article_name
        self._article_planning_type = article_planning_type
        self._budget_items = budget_items or [
            {
                "id": ITEM_ID,
                "name": article_name,
                "planning_type": article_planning_type,
            },
        ]
        self.plan_bodies: list[dict[str, Any]] = []
        self.recalc_calls: list[str] = []

    def _item_payload(self, item_id: str) -> dict[str, Any]:
        for item in self._budget_items:
            if str(item["id"]) == item_id:
                return dict(item)
        raise AssertionError(f"unknown item id {item_id}")

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/budget/versions":
            return {"budget_versions": [{"id": VID, "status": "ACT"}]}
        if path.endswith(f"/budget/versions/{VID}"):
            return {"id": VID, "status": self._version_status}
        if path == "/api/v1/budget/items":
            return {"budget_items": self._budget_items}
        if path.startswith("/api/v1/budget/items/"):
            item_id = path.rsplit("/", 1)[-1]
            return self._item_payload(item_id)
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
            body = data or {}
            if body.get("planning_type") == "IRR":
                return 201, _created_irr_plan(
                    str(body.get("amount", "0.00")),
                    str(body.get("forecast_method", "MAN")),
                )
            return 201, _created_reg_plan(
                str(body.get("amount", "62.90")),
                body.get("end_date"),
            )
        if method == "POST" and path == "/api/v1/budget/projections/recalculate":
            self.recalc_calls.append(str((data or {}).get("budget_version_id")))
            if self._recalc_status != 200:
                return self._recalc_status, {"message": "recalc failed"}
            return 200, {"budget_projections": [{}] * self._projection_count}
        raise AssertionError(f"unexpected request {method} {path}")


class CreatePlanItemRegTest(unittest.TestCase):
    """create_plan_item REG (FIN-110)."""

    def test_bounded_happy_path(self) -> None:
        """T8 / FIN-110 T1: start=end=2026-06 → POST + recalculate."""
        api = _CreatePlanItemMockApi()
        result = create_plan_item(
            api,
            "62.90",
            JUNE,
            budget_item_id=ITEM_ID,
            end_period=JUNE,
        )
        self.assertEqual(result["plan_item_id"], PLAN_ID)
        self.assertEqual(result["planning_type"], "REG")
        self.assertEqual(api.plan_bodies[0]["start_date"], "2026-06-01")
        self.assertEqual(api.plan_bodies[0]["end_date"], period_last_day(JUNE))
        self.assertEqual(api.recalc_calls, [VID])
        self.assertEqual(result["recalculate"]["projection_rows"], 42)

    def test_open_ended_no_end_period(self) -> None:
        """FIN-110 T2: no end_period → end_date=null."""
        api = _CreatePlanItemMockApi()
        create_plan_item(api, "10.00", JUNE, article="BahnCard")
        self.assertIsNone(api.plan_bodies[0]["end_date"])

    def test_end_before_start_raises(self) -> None:
        """FIN-110 T3: end_period < start_period → error before HTTP."""
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
        """FIN-110 T4: ARC version → error before POST."""
        api = _CreatePlanItemMockApi(version_status="ARC")
        with self.assertRaises(RuntimeError):
            create_plan_item(api, "62.90", JUNE, budget_item_id=ITEM_ID, end_period=JUNE)
        self.assertEqual(api.plan_bodies, [])

    def test_reg_forecast_method_rejected(self) -> None:
        """FIN-119 T5: REG + forecast_method → error."""
        api = _CreatePlanItemMockApi()
        with self.assertRaises(ValueError):
            create_plan_item(
                api,
                "62.90",
                JUNE,
                budget_item_id=ITEM_ID,
                end_period=JUNE,
                forecast_method="MAN",
                provided_fields=frozenset({"forecast_method"}),
            )
        self.assertEqual(api.plan_bodies, [])

    def test_recalculate_false_skips_post(self) -> None:
        """FIN-110 T6: recalculate=false → no POST recalculate."""
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
        """FIN-110 T7: recalculate fail after POST → context with plan_item_id."""
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
        """FIN-110 T8: amount=0 → POST succeeds."""
        api = _CreatePlanItemMockApi()
        create_plan_item(
            api,
            0,
            JUNE,
            budget_item_id=ITEM_ID,
            end_period=JUNE,
        )
        self.assertEqual(api.plan_bodies[0]["amount"], "0.00")


class CreatePlanItemIrrTest(unittest.TestCase):
    """create_plan_item IRR (FIN-119)."""

    def test_irr_happy_path_default_forecast(self) -> None:
        """T1: IRR amount=0, forecast_method omitted → MAN + recalculate."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        result = create_plan_item(
            api,
            "0",
            article="Прочие",
        )
        self.assertEqual(result["planning_type"], "IRR")
        self.assertEqual(result["forecast_method"], "MAN")
        self.assertNotIn("start_period", result)
        body = api.plan_bodies[0]
        self.assertEqual(body["planning_type"], "IRR")
        self.assertEqual(body["forecast_method"], "MAN")
        self.assertIsNone(body["start_date"])
        self.assertEqual(api.recalc_calls, [VID])

    def test_irr_forecast_avg(self) -> None:
        """T2: IRR forecast_method=AVG."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        create_plan_item(
            api,
            "100",
            budget_item_id=ITEM_ID,
            forecast_method="AVG",
            provided_fields=frozenset({"forecast_method"}),
        )
        self.assertEqual(api.plan_bodies[0]["forecast_method"], "AVG")

    def test_planning_type_mismatch_on_irr_article(self) -> None:
        """T3: explicit REG on IRR article → error."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        with self.assertRaises(ValueError):
            create_plan_item(
                api,
                "0",
                budget_item_id=ITEM_ID,
                planning_type="REG",
                provided_fields=frozenset({"planning_type"}),
            )
        self.assertEqual(api.plan_bodies, [])

    def test_irr_start_period_rejected(self) -> None:
        """T4: IRR + start_period → error."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        with self.assertRaises(ValueError):
            create_plan_item(
                api,
                "0",
                JUNE,
                budget_item_id=ITEM_ID,
                provided_fields=frozenset({"start_period"}),
            )
        self.assertEqual(api.plan_bodies, [])

    def test_irr_recalculate_false(self) -> None:
        """T6: IRR recalculate=false."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        result = create_plan_item(
            api,
            "0",
            budget_item_id=ITEM_ID,
            recalculate=False,
        )
        self.assertNotIn("recalculate", result)
        self.assertEqual(api.recalc_calls, [])

    def test_irr_recalculate_failure_includes_context(self) -> None:
        """T7: IRR recalculate fail → context."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
            recalc_status=500,
        )
        with self.assertRaises(CreatePlanItemRecalculateError) as ctx:
            create_plan_item(api, "0", budget_item_id=ITEM_ID)
        self.assertEqual(ctx.exception.context["plan_item_id"], PLAN_ID)
        self.assertEqual(ctx.exception.context["planning_type"], "IRR")

    def test_article_and_budget_item_id_mismatch(self) -> None:
        """T9: article + budget_item_id → different UUIDs."""
        api = _CreatePlanItemMockApi(
            budget_items=[
                {"id": ITEM_ID, "name": "BahnCard", "planning_type": "REG"},
                {"id": OTHER_ITEM_ID, "name": "Other", "planning_type": "REG"},
            ],
        )
        with self.assertRaises(RuntimeError):
            create_plan_item(
                api,
                "62.90",
                JUNE,
                article="Other",
                budget_item_id=ITEM_ID,
                end_period=JUNE,
            )
        self.assertEqual(api.plan_bodies, [])

    def test_budget_item_id_infers_irr(self) -> None:
        """T10: budget_item_id only, IRR article."""
        api = _CreatePlanItemMockApi(
            article_name="Прочие доходы",
            article_planning_type="IRR",
        )
        result = create_plan_item(api, "0", budget_item_id=ITEM_ID)
        self.assertEqual(result["planning_type"], "IRR")
        self.assertEqual(api.plan_bodies[0]["planning_type"], "IRR")

    def test_mismatch_before_irr_start_period(self) -> None:
        """T11: planning_type=IRR + start_period on REG article → mismatch first."""
        api = _CreatePlanItemMockApi(article_planning_type="REG")
        with self.assertRaises(ValueError) as ctx:
            create_plan_item(
                api,
                "62.90",
                JUNE,
                budget_item_id=ITEM_ID,
                planning_type="IRR",
                provided_fields=frozenset({"planning_type", "start_period"}),
            )
        self.assertIn("does not match article", str(ctx.exception))
        self.assertEqual(api.plan_bodies, [])


class CreatePlanItemHandlerTest(unittest.TestCase):
    """MCP handler (FIN-110 / FIN-119)."""

    @patch("server.create_plan_item")
    @patch("server.get_session")
    def test_reg_success_payload(self, mock_get_session: MagicMock, mock_create: MagicMock) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_create.return_value = {
            "plan_item_id": PLAN_ID,
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "article": "BahnCard",
            "amount": "62.90",
            "planning_type": "REG",
            "start_period": "2026-06",
            "end_period": "2026-06",
            "plan_item": _created_reg_plan(),
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
    def test_irr_omits_start_period(self, mock_get_session: MagicMock, mock_create: MagicMock) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_create.return_value = {
            "plan_item_id": PLAN_ID,
            "budget_item_id": ITEM_ID,
            "budget_version_id": VID,
            "article": "Прочие доходы",
            "amount": "0.00",
            "planning_type": "IRR",
            "forecast_method": "MAN",
            "plan_item": _created_irr_plan(),
        }
        out = server._handle_create_plan_item(
            {
                "article": "Прочие",
                "amount": "0",
            },
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        mock_create.assert_called_once()
        args, call_kwargs = mock_create.call_args
        self.assertIsNone(args[2])
        self.assertNotIn("start_period", call_kwargs["provided_fields"])

    def test_reg_missing_start_period_raises(self) -> None:
        import server

        with patch("server.get_session", return_value=(MagicMock(), "http://test")):
            with patch(
                "server.create_plan_item",
                side_effect=ValueError("start_period is required for REG plan-items"),
            ):
                with self.assertRaises(ValueError):
                    server._handle_create_plan_item(
                        {
                            "budget_item_id": ITEM_ID,
                            "amount": "62.90",
                        },
                    )

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
            "plan_item": _created_reg_plan(),
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
