"""Unit tests for FIN-16 unified apply_keywords."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    ApplyKeywordsPartialError,
    ApplyKeywordsValidationError,
    apply_keywords_payload,
    empty_keywords_changes,
    keywords_payload_effective,
    parse_keywords_payload,
)

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import server  # noqa: E402

CAT_ID = "C0001"
ITEM_ID = "11111111-1111-4111-8111-111111111111"
ITEM_NAME = "Прочие доходы"
PROJ_ID = "PR001"


def _category(keywords: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": CAT_ID,
        "type": "C",
        "description": "Test",
        "keywords": keywords or [],
        "default": False,
    }


def _budget_item(keywords: list[str] | None = None, name: str = ITEM_NAME) -> dict[str, Any]:
    return {
        "id": ITEM_ID,
        "name": name,
        "flow_type": "INC",
        "operation_category_id": "P9999",
        "planning_type": "REG",
        "keywords": keywords or [],
        "status": "ACT",
    }


def _project(keywords: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": PROJ_ID,
        "description": "Trip",
        "keywords": keywords or [],
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
    }


class _KeywordsMockApi:
    """Minimal ApiClient stub for apply_keywords tests."""

    def __init__(
        self,
        *,
        categories: list[dict[str, Any]] | None = None,
        budget_items: list[dict[str, Any]] | None = None,
        projects: list[dict[str, Any]] | None = None,
        fail_put: str | None = None,
    ) -> None:
        self.categories = categories if categories is not None else [_category()]
        self.budget_items = budget_items if budget_items is not None else [_budget_item()]
        self.projects = projects if projects is not None else [_project()]
        self.fail_put = fail_put
        self.put_paths: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/categories":
            return {"categories": [dict(c) for c in self.categories]}
        if path == "/api/v1/budget/items":
            return {"budget_items": [dict(i) for i in self.budget_items]}
        if path == "/api/v1/projects":
            return {"projects": [dict(p) for p in self.projects]}
        raise AssertionError(f"unexpected GET {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        self.put_paths.append(path)
        if self.fail_put and path.endswith(self.fail_put):
            return 500, {"error": "fail"}
        if method == "PUT" and path == "/api/v1/categories":
            self.categories = data["categories"]  # type: ignore[index]
            return 200, {"categories": self.categories}
        if method == "PUT" and path.startswith("/api/v1/budget/items/"):
            item_id = path.rsplit("/", 1)[-1]
            for index, item in enumerate(self.budget_items):
                if item["id"] == item_id:
                    self.budget_items[index] = data  # type: ignore[assignment]
                    return 200, data
            return 404, {}
        if method == "PUT" and path.startswith("/api/v1/projects/"):
            proj_id = path.rsplit("/", 1)[-1]
            for index, proj in enumerate(self.projects):
                if proj["id"] == proj_id:
                    self.projects[index] = data  # type: ignore[assignment]
                    return 200, data
            return 404, {}
        raise AssertionError(f"unexpected {method} {path}")


class ParseValidationTest(unittest.TestCase):
    """T13–T15, T19, T22 validation."""

    def test_t13_add_not_array(self) -> None:
        with self.assertRaises(ApplyKeywordsValidationError):
            parse_keywords_payload({"categories": {CAT_ID: {"add": "x"}}})

    def test_t14_invalid_root(self) -> None:
        with self.assertRaises(ApplyKeywordsValidationError):
            parse_keywords_payload({"foo": {}})

    def test_t15_mixed_root(self) -> None:
        with self.assertRaises(ApplyKeywordsValidationError):
            parse_keywords_payload({"categories": {}, CAT_ID: []})

    def test_t19_null_add(self) -> None:
        with self.assertRaises(ApplyKeywordsValidationError):
            parse_keywords_payload({"categories": {CAT_ID: {"add": None}}})

    def test_t22_extra_field(self) -> None:
        with self.assertRaises(ApplyKeywordsValidationError):
            parse_keywords_payload(
                {"categories": {CAT_ID: {"add": [], "comment": "x"}}}
            )


class ApplyKeywordsPayloadTest(unittest.TestCase):
    """T01–T12, T16–T21 core apply flows."""

    def test_t01_legacy_flat_add(self) -> None:
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(api, {CAT_ID: ["x"]})
        self.assertEqual(changes["categories_added"], [{"category": CAT_ID, "keyword": "x"}])
        self.assertIn("/api/v1/categories", api.put_paths)

    def test_t02_budget_item_add_by_name(self) -> None:
        api = _KeywordsMockApi()
        payload = {"budget_items": {ITEM_NAME: {"add": ["Zinszahlung"]}}}
        changes = apply_keywords_payload(api, payload)
        self.assertEqual(len(changes["budget_items_added"]), 1)
        self.assertEqual(api.budget_items[0]["keywords"], ["Zinszahlung"])

    def test_t03_remove_only_not_effective(self) -> None:
        self.assertFalse(
            keywords_payload_effective(
                {"budget_items": {ITEM_NAME: {"remove": ["old"]}}}
            )
        )
        api = _KeywordsMockApi(budget_items=[_budget_item(["old"])])
        changes = apply_keywords_payload(
            api, {"budget_items": {ITEM_NAME: {"remove": ["old"]}}}
        )
        self.assertEqual(len(changes["budget_items_removed"]), 1)

    def test_t04_ambiguous_budget_item_name(self) -> None:
        api = _KeywordsMockApi(
            budget_items=[
                _budget_item(name="Foo"),
                _budget_item(name=" foo "),
            ]
        )
        with self.assertRaises(ApplyKeywordsValidationError):
            apply_keywords_payload(api, {"budget_items": {"foo": {"add": ["x"]}}})

    def test_t09_idempotent_category_add(self) -> None:
        api = _KeywordsMockApi(categories=[_category(["existing"])])
        changes = apply_keywords_payload(
            api, {"categories": {CAT_ID: {"add": ["existing"]}}}
        )
        self.assertEqual(changes["categories_added"], [])
        self.assertEqual(api.put_paths, [])

    def test_t10_idempotent_remove_missing(self) -> None:
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(
            api, {"categories": {CAT_ID: {"remove": ["missing"]}}}
        )
        self.assertEqual(changes["categories_removed"], [])
        self.assertEqual(api.put_paths, [])

    def test_t11_empty_payload_no_put(self) -> None:
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(api, {})
        self.assertEqual(changes, empty_keywords_changes())
        self.assertFalse(keywords_payload_effective({}))
        self.assertEqual(api.put_paths, [])

    def test_t12_categories_before_budget_items(self) -> None:
        api = _KeywordsMockApi()
        apply_keywords_payload(
            api,
            {
                "categories": {CAT_ID: {"add": ["c"]}},
                "budget_items": {ITEM_NAME: {"add": ["b"]}},
            },
        )
        self.assertEqual(api.put_paths[0], "/api/v1/categories")
        self.assertTrue(api.put_paths[1].startswith("/api/v1/budget/items/"))

    def test_t16_duplicate_add_dedup(self) -> None:
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(api, {CAT_ID: ["A", "A"]})
        self.assertEqual(changes["categories_added"], [{"category": CAT_ID, "keyword": "A"}])

    def test_t17_add_then_remove_existing(self) -> None:
        api = _KeywordsMockApi(categories=[_category(["A"])])
        changes = apply_keywords_payload(
            api,
            {"categories": {CAT_ID: {"add": ["A"], "remove": ["A"]}}},
        )
        self.assertEqual(changes["categories_added"], [])
        self.assertEqual(changes["categories_removed"], [{"category": CAT_ID, "keyword": "A"}])

    def test_t20_blank_ignored(self) -> None:
        self.assertFalse(keywords_payload_effective({CAT_ID: [" "]}))
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(api, {CAT_ID: [" "]})
        self.assertEqual(changes["categories_added"], [])
        self.assertEqual(api.put_paths, [])

    def test_t21_unified_shorthand(self) -> None:
        api = _KeywordsMockApi()
        changes = apply_keywords_payload(api, {"categories": {CAT_ID: ["x"]}})
        self.assertEqual(changes["categories_added"], [{"category": CAT_ID, "keyword": "x"}])

    def test_t08_partial_budget_item_failure(self) -> None:
        second_id = "22222222-2222-4222-8222-222222222222"
        api = _KeywordsMockApi(
            budget_items=[
                _budget_item(name="First"),
                _budget_item(name="Second"),
            ]
        )
        api.budget_items[1]["id"] = second_id
        api.fail_put = second_id
        with self.assertRaises(ApplyKeywordsPartialError) as ctx:
            apply_keywords_payload(
                api,
                {
                    "budget_items": {
                        "First": {"add": ["a"]},
                        "Second": {"add": ["b"]},
                    }
                },
            )
        self.assertEqual(len(ctx.exception.partial_changes["budget_items_added"]), 1)


class ApplyKeywordsHandlerTest(unittest.TestCase):
    """T05, T06, T18 standalone / process_month handlers."""

    def test_t05_standalone_no_derive(self) -> None:
        with patch("server.get_session") as session, patch(
            "server.apply_keywords_payload", return_value=empty_keywords_changes()
        ) as apply_mock, patch("server.run_derive") as derive_mock:
            session.return_value = (MagicMock(), "http://127.0.0.1:8000")
            apply_mock.return_value = empty_keywords_changes()
            with patch(
                "server.keywords_payload_effective", return_value=True
            ):
                out = json.loads(
                    server._handle_apply_keywords(
                        {
                            "period": "2026-06",
                            "payload": {"categories": {CAT_ID: {"add": ["x"]}}},
                            "derive": False,
                        }
                    )[0].text
                )
        derive_mock.assert_not_called()
        self.assertTrue(out["ok"])

    def test_t18_effective_true_empty_changes_still_derives(self) -> None:
        with patch("server.get_session") as session, patch(
            "server.apply_keywords_payload", return_value=empty_keywords_changes()
        ), patch("server.run_derive", return_value={"ok": True}) as derive_mock, patch(
            "server.keywords_payload_effective", return_value=True
        ):
            session.return_value = (MagicMock(), "http://127.0.0.1:8000")
            out = json.loads(
                server._handle_apply_keywords(
                    {
                        "period": "2026-06",
                        "payload": {"categories": {CAT_ID: {"add": ["existing"]}}},
                        "derive": True,
                    }
                )[0].text
            )
        derive_mock.assert_called_once()
        self.assertTrue(out["effective"])
        self.assertTrue(out["ok"])

    def test_t06_process_month_logs_changes(self) -> None:
        changes = empty_keywords_changes()
        changes["categories_added"] = [{"category": CAT_ID, "keyword": "x"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump({"categories": {CAT_ID: {"add": ["x"]}}}, tmp)
            kw_path = tmp.name
        try:
            with patch("server.get_session") as session, patch(
                "server.resolve_budget_version_id", return_value="vid"
            ), patch("server.run_derive", return_value={}), patch(
                "server.verify_period",
                return_value={
                    "ok": True,
                    "classification_summary": {"expense_c9999_count": 0},
                },
            ), patch(
                "server.apply_keywords_file", return_value=changes
            ), patch("server.keywords_payload_effective", return_value=True):
                session.return_value = (MagicMock(), "http://127.0.0.1:8000")
                out = json.loads(
                    server._handle_process_month(
                        {
                            "period": "2026-06",
                            "skip_import": True,
                            "apply_keywords": kw_path,
                        }
                    )[0].text
                )
            self.assertTrue(out["ok"])
            self.assertEqual(out["log"]["steps"]["keywords_changes"], changes)
        finally:
            Path(kw_path).unlink(missing_ok=True)

    def test_t07_unified_effective(self) -> None:
        payload = {
            "categories": {CAT_ID: {"add": ["x"]}},
            "budget_items": {ITEM_NAME: {"add": ["y"]}},
        }
        self.assertTrue(keywords_payload_effective(payload))


if __name__ == "__main__":
    unittest.main()
