"""Unit tests for FIN-107 put_transaction_overrides and upsert_expense_project."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from monthly_close_lib import (
    Period,
    is_budget_item_validation_failure,
    parse_period,
    put_transaction_overrides,
    upsert_expense_project,
)

VID = "00000000-0000-4000-8000-000000000001"
PERIOD = Period(year=2026, month=5)
IRR_ITEM_ID = "11111111-1111-4111-8111-111111111111"
REG_ITEM_ID = "22222222-2222-4222-8222-222222222222"
INACTIVE_ITEM_ID = "33333333-3333-4333-8333-333333333333"
UNKNOWN_ITEM_ID = "99999999-9999-4999-8999-999999999999"
ONCE_ITEM_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BUDGET_ITEM_422_BODY = {
    "error": {
        "code": "validation_error",
        "message": "Укажите существующую статью бюджета.",
    },
}


class _ReconciliationMockApi:
    """Stub ApiClient for reconciliation GET/PUT."""

    def __init__(self, reconciliation: dict[str, Any]) -> None:
        self._reconciliation = dict(reconciliation)
        self.put_payloads: list[dict[str, Any]] = []
        self.derive_calls = 0

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith("/budget/reconciliation"):
            return dict(self._reconciliation)
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "PUT" and path == "/api/v1/budget/reconciliation":
            self.put_payloads.append(dict(data or {}))
            if self._reconciliation.get("_put_status", 200) != 200:
                return self._reconciliation["_put_status"], {"error": "period_closed"}
            overrides = (data or {}).get("transaction_overrides", {})
            body = {
                "status": "draft",
                "transaction_overrides": overrides,
            }
            self._reconciliation["transaction_overrides"] = overrides
            return 200, body
        if method == "POST" and path == "/api/v1/transactions/derive":
            self.derive_calls += 1
            return 200, {"ok": True}
        raise AssertionError(f"unexpected request {method} {path}")


class _Fin120MockApi:
    """Stub ApiClient for FIN-120 override enrichment tests."""

    def __init__(
        self,
        *,
        budget_items: dict[str, dict[str, Any]],
        plan_item_budget_ids: frozenset[str] | set[str],
        put_body: dict[str, Any] | None = None,
        item_get_status: dict[str, int] | None = None,
        plan_items_status: int = 200,
    ) -> None:
        self._reconciliation: dict[str, Any] = {
            "transaction_overrides": {},
            "status": "open",
        }
        self.budget_items = budget_items
        self.plan_item_budget_ids = set(plan_item_budget_ids)
        self.put_body = put_body if put_body is not None else BUDGET_ITEM_422_BODY
        self.item_get_status = item_get_status or {}
        self.plan_items_status = plan_items_status
        self.plan_items_get_count = 0
        self.item_get_paths: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith("/budget/reconciliation"):
            return dict(self._reconciliation)
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "PUT" and path == "/api/v1/budget/reconciliation":
            return 422, self.put_body
        if method == "GET" and "/budget/plan-items" in path:
            self.plan_items_get_count += 1
            if self.plan_items_status != 200:
                return self.plan_items_status, {"error": "upstream"}
            return 200, {
                "budget_plan_items": [
                    {"budget_item_id": item_id}
                    for item_id in sorted(self.plan_item_budget_ids)
                ],
            }
        if method == "GET" and path.startswith("/api/v1/budget/items/"):
            item_id = path.rsplit("/", 1)[-1]
            self.item_get_paths.append(item_id)
            status = self.item_get_status.get(item_id, 200)
            if status != 200:
                return status, {"error": "upstream"}
            item = self.budget_items.get(item_id)
            if item is None:
                return 404, {"error": {"code": "not_found", "message": "missing"}}
            return 200, item
        raise AssertionError(f"unexpected request {method} {path}")


def _fin120_items() -> dict[str, dict[str, Any]]:
    return {
        IRR_ITEM_ID: {
            "id": IRR_ITEM_ID,
            "name": "Прочие доходы",
            "status": "ACT",
            "planning_type": "IRR",
        },
        REG_ITEM_ID: {
            "id": REG_ITEM_ID,
            "name": "Регулярный расход",
            "status": "ACT",
            "planning_type": "REG",
        },
        INACTIVE_ITEM_ID: {
            "id": INACTIVE_ITEM_ID,
            "name": "Архивная статья",
            "status": "ARC",
            "planning_type": "REG",
        },
        ONCE_ITEM_ID: {
            "id": ONCE_ITEM_ID,
            "name": "Разовая",
            "status": "ACT",
            "planning_type": "ONCE",
        },
    }


class Fin120PutTransactionOverridesTest(unittest.TestCase):
    """FIN-120 enriched errors for put_transaction_overrides (T1–T11)."""

    def test_t1_missing_irr_plan_item_hint(self) -> None:
        """T1: IRR without plan-item → create_plan_item hint without start_period."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {"tx1": IRR_ITEM_ID},
            )
        msg = str(ctx.exception)
        self.assertIn("Прочие доходы", msg)
        self.assertIn(IRR_ITEM_ID, msg)
        self.assertIn("planning_type=IRR", msg)
        self.assertIn("create_plan_item", msg)
        self.assertIn('planning_type="IRR"', msg)
        self.assertNotIn("start_period", msg)

    def test_t2_missing_reg_plan_item_hint(self) -> None:
        """T2: REG without plan-item → hint contains start_period."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {"tx1": REG_ITEM_ID},
            )
        self.assertIn("start_period", str(ctx.exception))
        self.assertIn('"2026-05"', str(ctx.exception))

    def test_t3_unknown_budget_item_no_hint(self) -> None:
        """T3: unknown budget_item_id → no create_plan_item hint."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset({REG_ITEM_ID}),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {"tx1": UNKNOWN_ITEM_ID},
            )
        msg = str(ctx.exception)
        self.assertIn("не найден", msg)
        self.assertNotIn("create_plan_item", msg)

    def test_t5_period_closed_no_enrichment(self) -> None:
        """T5: period_closed → raw PUT error, no enrichment."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
            put_body={
                "error": {
                    "code": "period_closed",
                    "message": "Период закрыт.",
                },
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(api, VID, PERIOD, {"tx1": IRR_ITEM_ID})
        self.assertIn("PUT reconciliation -> 422", str(ctx.exception))
        self.assertEqual(api.plan_items_get_count, 0)

    def test_t7_unknown_beats_missing(self) -> None:
        """T7: missing then unknown → unknown wins (D-06)."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {"tx1": IRR_ITEM_ID, "tx2": UNKNOWN_ITEM_ID},
            )
        msg = str(ctx.exception)
        self.assertIn("не найден", msg)
        self.assertIn(UNKNOWN_ITEM_ID, msg)

    def test_t7b_inactive_beats_missing(self) -> None:
        """T7b: inactive then missing → inactive wins (D-06)."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {"tx1": INACTIVE_ITEM_ID, "tx2": IRR_ITEM_ID},
            )
        msg = str(ctx.exception)
        self.assertIn("не ACTIVE", msg)
        self.assertIn(INACTIVE_ITEM_ID, msg)

    def test_t8_diagnostic_get_failure_fallback(self) -> None:
        """T8: diagnostic GET items 500 → fallback PUT error (D-07)."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
            item_get_status={IRR_ITEM_ID: 500},
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(api, VID, PERIOD, {"tx1": IRR_ITEM_ID})
        self.assertIn("PUT reconciliation -> 422", str(ctx.exception))
        self.assertIn("Укажите существующую статью бюджета", str(ctx.exception))

    def test_t9_early_exit_on_first_unknown(self) -> None:
        """T9: three unknowns → one plan-items GET, one items GET (D-08)."""
        unknown_ids = [
            "aaaaaaaa-0001-4000-8000-000000000001",
            "aaaaaaaa-0002-4000-8000-000000000002",
            "aaaaaaaa-0003-4000-8000-000000000003",
        ]
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError):
            put_transaction_overrides(
                api,
                VID,
                PERIOD,
                {f"tx{i}": item_id for i, item_id in enumerate(unknown_ids, start=1)},
            )
        self.assertEqual(api.plan_items_get_count, 1)
        self.assertEqual(len(api.item_get_paths), 1)

    def test_t10_non_reg_irr_planning_type_no_example(self) -> None:
        """T10: planning_type=ONCE → hint without example block (D-09)."""
        api = _Fin120MockApi(
            budget_items=_fin120_items(),
            plan_item_budget_ids=frozenset(),
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_overrides(api, VID, PERIOD, {"tx1": ONCE_ITEM_ID})
        msg = str(ctx.exception)
        self.assertIn("create_plan_item", msg)
        self.assertNotIn("Пример (", msg)

    def test_t11_is_budget_item_validation_failure(self) -> None:
        """T11: helper matches prod message, rejects flow mismatch."""
        self.assertTrue(is_budget_item_validation_failure(BUDGET_ITEM_422_BODY))
        self.assertTrue(
            is_budget_item_validation_failure(
                {
                    "error": {
                        "code": "budget_item_not_in_version",
                        "message": "any",
                    },
                },
            ),
        )
        self.assertFalse(
            is_budget_item_validation_failure(
                {
                    "error": {
                        "code": "validation_error",
                        "message": "Статья не подходит к типу операции.",
                    },
                },
            ),
        )


class _ProjectsMockApi:
    """Stub ApiClient for projects list and upsert."""

    def __init__(self, projects: list[dict[str, Any]]) -> None:
        self._projects = list(projects)
        self.post_calls: list[dict[str, Any]] = []
        self.put_calls: list[tuple[str, dict[str, Any]]] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/projects":
            return {"projects": list(self._projects)}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        payload = dict(data or {})
        if method == "POST" and path == "/api/v1/projects":
            self.post_calls.append(payload)
            if not payload.get("keywords"):
                return 422, {"message": "Список ключевых слов не может быть пустым."}
            self._projects.append(payload)
            return 201, payload
        if method == "PUT" and path.startswith("/api/v1/projects/"):
            project_id = path.rsplit("/", 1)[-1]
            self.put_calls.append((project_id, payload))
            if not payload.get("keywords"):
                return 422, {"message": "Список ключевых слов не может быть пустым."}
            for idx, row in enumerate(self._projects):
                if row["id"] == project_id:
                    self._projects[idx] = payload
                    return 200, payload
            return 404, {"message": "not found"}
        raise AssertionError(f"unexpected request {method} {path}")


def _valid_project(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "PR005",
        "description": "Canal Pride Amsterdam",
        "keywords": ["BOOKING"],
        "valid_from": "20260501",
        "valid_to": "20260531",
    }
    base.update(overrides)
    return base


class PutTransactionOverridesTest(unittest.TestCase):
    """put_transaction_overrides (FIN-107 T1–T5, T12)."""

    def test_merge_true_adds_keys(self) -> None:
        """T1: merge=true merges existing and new overrides."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {"a": "1"}, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"b": "2"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1", "b": "2"},
        )

    def test_merge_true_overwrites_key(self) -> None:
        """T2: merge=true overwrites existing key."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {"a": "1"}, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "9"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "9"},
        )

    def test_merge_false_replaces_map(self) -> None:
        """T3: merge=false sends only argument map."""
        api = _ReconciliationMockApi(
            {
                "transaction_overrides": {"a": "1", "legacy": "x"},
                "status": "open",
            }
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"b": "2"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"b": "2"},
        )

    def test_null_existing_overrides_treated_as_empty(self) -> None:
        """Null transaction_overrides from GET normalizes to {}."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": None, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_missing_overrides_field_treated_as_empty(self) -> None:
        """Missing transaction_overrides from GET normalizes to {}."""
        api = _ReconciliationMockApi({"status": "open"})
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_merge_false_empty_existing(self) -> None:
        """T12: merge=false with empty existing map."""
        api = _ReconciliationMockApi({"transaction_overrides": {}, "status": "open"})
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_period_closed_raises(self) -> None:
        """T5: PUT 422 surfaces as tool error."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {}, "status": "closed", "_put_status": 422}
        )
        with self.assertRaises(RuntimeError):
            put_transaction_overrides(
                api, VID, PERIOD, {"a": "1"}, merge=True
            )


class UpsertExpenseProjectTest(unittest.TestCase):
    """upsert_expense_project (FIN-107 T8–T10, T13)."""

    def test_create_new_project(self) -> None:
        """T8: new id uses POST."""
        api = _ProjectsMockApi([])
        result = upsert_expense_project(api, _valid_project())
        self.assertEqual(result["action"], "created")
        self.assertEqual(len(api.post_calls), 1)
        self.assertEqual(api.put_calls, [])

    def test_update_existing_project(self) -> None:
        """T9: existing id uses PUT."""
        existing = _valid_project()
        api = _ProjectsMockApi([existing])
        updated = _valid_project(keywords=["BOOKING", "HOTEL"])
        result = upsert_expense_project(api, updated)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(len(api.put_calls), 1)
        self.assertEqual(api.put_calls[0][0], "PR005")

    def test_invalid_id_raises_from_api(self) -> None:
        """T10: invalid project id rejected by API validation."""
        api = _ProjectsMockApi([])

        def failing_request(
            method: str,
            path: str,
            data: dict[str, Any] | None = None,
        ) -> tuple[int, Any]:
            return 422, {"message": "Идентификатор проекта должен быть в формате PR и три цифры."}

        api.request = failing_request  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            upsert_expense_project(
                api,
                _valid_project(id="PR1"),
            )

    def test_empty_keywords_validation_error(self) -> None:
        """T13: empty keywords list rejected."""
        api = _ProjectsMockApi([])
        with self.assertRaises(RuntimeError):
            upsert_expense_project(api, _valid_project(keywords=[]))


class ParsePeriodTest(unittest.TestCase):
    """parse_period validation (FIN-107 T11)."""

    def test_invalid_period_raises(self) -> None:
        """T11: invalid period string."""
        with self.assertRaises(ValueError):
            parse_period("2026-13")


class PutTransactionOverridesHandlerTest(unittest.TestCase):
    """MCP handler derive semantics (FIN-107 T4, T6, T7)."""

    @patch("server.run_derive")
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_derive_false_omits_field_and_skips_call(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """T6: derive=false — no derive call, field omitted."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.return_value = {"status": "draft", "transaction_overrides": {"a": "1"}}
        out = server._handle_put_transaction_overrides(
            {
                "period": "2026-05",
                "overrides": {"a": "1"},
                "derive": False,
            }
        )
        mock_derive.assert_not_called()
        payload = json.loads(out[0].text)
        self.assertNotIn("derive", payload)

    @patch("server.run_derive", return_value={"derived": 1})
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_derive_true_calls_once(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """T7: derive=true — single derive after successful PUT."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.return_value = {"status": "draft", "transaction_overrides": {"a": "1"}}
        out = server._handle_put_transaction_overrides(
            {
                "period": "2026-05",
                "overrides": {"a": "1"},
                "derive": True,
            }
        )
        mock_derive.assert_called_once()
        payload = json.loads(out[0].text)
        self.assertEqual(payload["derive"], {"derived": 1})

    @patch("server.run_derive")
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_put_failure_skips_derive(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """Derive not called when PUT raises."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.side_effect = RuntimeError("PUT reconciliation -> 422")
        with self.assertRaises(RuntimeError):
            server._handle_put_transaction_overrides(
                {
                    "period": "2026-05",
                    "overrides": {"a": "1"},
                    "derive": True,
                }
            )
        mock_derive.assert_not_called()

    def test_empty_overrides_raises_before_http(self) -> None:
        """T4: empty overrides rejected in handler."""
        import server

        with patch("server.get_session", return_value=(MagicMock(), "http://test")):
            with patch("server.resolve_budget_version_id", return_value=VID):
                with self.assertRaises(ValueError):
                    server._handle_put_transaction_overrides(
                        {"period": "2026-05", "overrides": {}}
                    )


if __name__ == "__main__":
    unittest.main()
