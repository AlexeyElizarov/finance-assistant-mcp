"""Unit tests for FIN-108 update_plan_item."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from monthly_close_lib import (
    Period,
    UpdatePlanItemRecalculateError,
    normalize_plan_amount,
    parse_period,
    projection_rows_count,
    resolve_plan_item_for_update,
    update_plan_item,
)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

VID = "00000000-0000-4000-8000-000000000001"
ITEM_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
PERIOD = Period(year=2026, month=7)


def _plan_item(amount: str = "300.00") -> dict[str, Any]:
    return {
        "id": PLAN_ID,
        "budget_version_id": VID,
        "budget_item_id": ITEM_ID,
        "planning_type": "REG",
        "amount": amount,
        "currency": "EUR",
        "status": "ACTIVE",
        "periodicity": "MON",
        "start_date": "2026-01-01",
        "end_date": "2026-12-01",
        "forecast_method": None,
    }


class _UpdatePlanItemMockApi:
    """Stub ApiClient for plan-item update flows."""

    def __init__(
        self,
        *,
        plan_item: dict[str, Any] | None = None,
        version_status: str = "ACT",
        can_mutate: bool = True,
        version_missing: bool = False,
        put_status: int = 200,
        recalc_status: int = 200,
        projection_count: int = 42,
    ) -> None:
        self._plan_item = dict(plan_item or _plan_item())
        self._version_status = version_status
        self._can_mutate = can_mutate
        self._version_missing = version_missing
        self._put_status = put_status
        self._recalc_status = recalc_status
        self._projection_count = projection_count
        self.put_bodies: list[dict[str, Any]] = []
        self.recalc_calls: list[str] = []
        self.projection_page_calls = 0

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith(f"/budget/plan-items/{PLAN_ID}"):
            return dict(self._plan_item)
        if parsed.path.endswith(f"/budget/versions/{VID}"):
            if self._version_missing:
                raise RuntimeError(f"GET {path} -> 404: not found")
            return {"id": VID, "status": self._version_status}
        if parsed.path.endswith(f"/budget/items/{ITEM_ID}"):
            return {"id": ITEM_ID, "name": "Командировки (отель и проезд)"}
        if parsed.path == "/api/v1/budget/versions":
            return {"budget_versions": [{"id": VID, "status": "ACT"}]}
        if parsed.path == "/api/v1/budget/items":
            return {
                "budget_items": [
                    {"id": ITEM_ID, "name": "Командировки (отель и проезд)"},
                ],
            }
        if parsed.path == "/api/v1/budget/projection-period-page":
            self.projection_page_calls += 1
            qs = parse_qs(parsed.query)
            return {
                "can_mutate": self._can_mutate,
                "period": qs.get("period", ["2026-07-01"])[0],
                "plan_items": [
                    {**self._plan_item, "item_name": "Командировки (отель и проезд)"},
                ],
            }
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "PUT" and path == f"/api/v1/budget/plan-items/{PLAN_ID}":
            self.put_bodies.append(dict(data or {}))
            if self._put_status != 200:
                return self._put_status, {"message": "validation error"}
            updated = dict(data or {})
            self._plan_item = updated
            return 200, updated
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


class NormalizePlanAmountTest(unittest.TestCase):
    """Amount normalization (T6, T7)."""

    def test_negative_raises(self) -> None:
        """T6: negative amount rejected."""
        with self.assertRaises(ValueError):
            normalize_plan_amount(-1)

    def test_zero_allowed(self) -> None:
        """T7: zero is valid."""
        self.assertEqual(normalize_plan_amount(0), "0.00")
        self.assertEqual(normalize_plan_amount("0"), "0.00")


class UpdatePlanItemTest(unittest.TestCase):
    """update_plan_item (FIN-108 T1–T5, T8–T12, T15–T17)."""

    def test_plan_item_id_puts_new_amount(self) -> None:
        """T1: direct plan_item_id updates amount."""
        api = _UpdatePlanItemMockApi()
        result = update_plan_item(api, "335.00", plan_item_id=PLAN_ID)
        self.assertEqual(result["amount_after"], "335.00")
        self.assertEqual(api.put_bodies[0]["amount"], "335.00")
        self.assertEqual(result["recalculate"]["projection_rows"], 42)

    def test_article_period_resolve(self) -> None:
        """T2: article + period resolves plan item."""
        api = _UpdatePlanItemMockApi()
        result = update_plan_item(
            api,
            "335.00",
            period=PERIOD,
            article="Командировки",
        )
        self.assertEqual(result["plan_item_id"], PLAN_ID)
        self.assertEqual(api.put_bodies[0]["amount"], "335.00")
        self.assertNotIn("item_name", api.put_bodies[0])
        self.assertNotIn("item_flow_type", api.put_bodies[0])

    def test_budget_item_id_period_resolve(self) -> None:
        """T3: budget_item_id + period resolves plan item."""
        api = _UpdatePlanItemMockApi()
        result = update_plan_item(
            api,
            "335.00",
            period=PERIOD,
            budget_item_id=ITEM_ID,
        )
        self.assertEqual(result["budget_item_id"], ITEM_ID)

    def test_ambiguous_plan_items_raises(self) -> None:
        """T4: multiple plan items for article in month."""

        class _AmbiguousApi(_UpdatePlanItemMockApi):
            def get_json(self, path: str) -> dict[str, Any]:
                if urlparse(path).path == "/api/v1/budget/projection-period-page":
                    return {
                        "can_mutate": True,
                        "plan_items": [
                            {"id": "a", "budget_item_id": ITEM_ID},
                            {"id": "b", "budget_item_id": ITEM_ID},
                        ],
                    }
                return super().get_json(path)

        api = _AmbiguousApi()
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", period=PERIOD, article="Командировки")

    def test_no_plan_items_raises(self) -> None:
        """T5: no matching plan item."""

        class _EmptyApi(_UpdatePlanItemMockApi):
            def get_json(self, path: str) -> dict[str, Any]:
                if urlparse(path).path == "/api/v1/budget/projection-period-page":
                    return {"can_mutate": True, "plan_items": []}
                return super().get_json(path)

        api = _EmptyApi()
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", period=PERIOD, article="Командировки")

    def test_recalculate_false_skips_post(self) -> None:
        """T8: recalculate=false skips POST."""
        api = _UpdatePlanItemMockApi()
        result = update_plan_item(
            api,
            "335.00",
            plan_item_id=PLAN_ID,
            recalculate=False,
        )
        self.assertNotIn("recalculate", result)
        self.assertEqual(api.recalc_calls, [])

    def test_recalculate_true_posts_once(self) -> None:
        """T9: recalculate=true posts with version id."""
        api = _UpdatePlanItemMockApi(projection_count=5)
        result = update_plan_item(api, "335.00", plan_item_id=PLAN_ID, recalculate=True)
        self.assertEqual(api.recalc_calls, [VID])
        self.assertEqual(result["recalculate"]["projection_rows"], 5)

    def test_put_failure_skips_recalculate(self) -> None:
        """T10: PUT error skips recalculate."""
        api = _UpdatePlanItemMockApi(put_status=422)
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", plan_item_id=PLAN_ID)
        self.assertEqual(api.recalc_calls, [])

    def test_can_mutate_false_blocks_before_put(self) -> None:
        """T11: can_mutate=false on resolve path."""
        api = _UpdatePlanItemMockApi(can_mutate=False)
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", period=PERIOD, article="Командировки")

    def test_arc_version_blocks_before_put(self) -> None:
        """T12: ARC version blocks on plan_item_id path."""
        api = _UpdatePlanItemMockApi(version_status="ARC")
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", plan_item_id=PLAN_ID)

    def test_invalid_period_in_handler_path(self) -> None:
        """T13: invalid period string."""
        with self.assertRaises(ValueError):
            parse_period("2026-13")

    def test_missing_resolve_args(self) -> None:
        """T14: missing resolve parameters."""
        api = _UpdatePlanItemMockApi()
        with self.assertRaises(ValueError):
            update_plan_item(api, "335.00")

    def test_plan_item_id_ignores_resolve_fields(self) -> None:
        """T15: plan_item_id precedence — no projection-page."""
        api = _UpdatePlanItemMockApi()
        update_plan_item(
            api,
            "335.00",
            plan_item_id=PLAN_ID,
            period=PERIOD,
            article="Other",
        )
        self.assertEqual(api.projection_page_calls, 0)

    def test_recalculate_failure_includes_put_context(self) -> None:
        """T16: D-13 partial success context."""
        api = _UpdatePlanItemMockApi(recalc_status=500)
        with self.assertRaises(UpdatePlanItemRecalculateError) as ctx:
            update_plan_item(api, "335.00", plan_item_id=PLAN_ID)
        err = ctx.exception
        self.assertEqual(err.context["plan_item_id"], PLAN_ID)
        self.assertEqual(err.context["amount_after"], "335.00")
        self.assertEqual(err.context["budget_version_id"], VID)
        self.assertIn("plan_item", err.context)

    def test_missing_version_blocks_before_put(self) -> None:
        """T17: plan item exists but version GET 404."""
        api = _UpdatePlanItemMockApi(version_missing=True)
        with self.assertRaises(RuntimeError):
            update_plan_item(api, "335.00", plan_item_id=PLAN_ID)


class ProjectionRowsCountTest(unittest.TestCase):
    """projection_rows_count (D-15)."""

    def test_budget_projections_length(self) -> None:
        body = {"budget_projections": [{}, {}, {}]}
        self.assertEqual(projection_rows_count(body), 3)

    def test_updated_count_preferred(self) -> None:
        body = {"updated_count": 99, "budget_projections": [{}]}
        self.assertEqual(projection_rows_count(body), 99)


class ResolvePlanItemForUpdateTest(unittest.TestCase):
    """resolve_plan_item_for_update edge cases."""

    def test_plan_item_id_path(self) -> None:
        api = _UpdatePlanItemMockApi()
        row, name = resolve_plan_item_for_update(
            api,
            plan_item_id=PLAN_ID,
            period=PERIOD,
            article="ignored",
            budget_item_id=None,
        )
        self.assertEqual(row["id"], PLAN_ID)
        self.assertIn("Командировки", name)


class UpdatePlanItemHandlerTest(unittest.TestCase):
    """MCP handler (FIN-108)."""

    @patch("server.update_plan_item")
    @patch("server.get_session")
    def test_success_payload(self, mock_get_session: MagicMock, mock_update: MagicMock) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_update.return_value = {
            "plan_item_id": PLAN_ID,
            "budget_version_id": VID,
            "budget_item_id": ITEM_ID,
            "article": "Командировки",
            "amount_before": "300.00",
            "amount_after": "335.00",
            "plan_item": _plan_item("335.00"),
            "recalculate": {"budget_version_id": VID, "projection_rows": 10},
        }
        out = server._handle_update_plan_item(
            {"plan_item_id": PLAN_ID, "amount": "335.00"},
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["amount_after"], "335.00")

    @patch("server.update_plan_item")
    @patch("server.get_session")
    def test_recalculate_error_returns_context(
        self,
        mock_get_session: MagicMock,
        mock_update: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        context = {
            "plan_item_id": PLAN_ID,
            "budget_version_id": VID,
            "amount_after": "335.00",
            "plan_item": _plan_item("335.00"),
        }
        mock_update.side_effect = UpdatePlanItemRecalculateError("recalc failed", context)
        out = server._handle_update_plan_item(
            {"plan_item_id": PLAN_ID, "amount": "335.00"},
        )
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["plan_item_id"], PLAN_ID)
        self.assertEqual(payload["amount_after"], "335.00")


if __name__ == "__main__":
    unittest.main()
