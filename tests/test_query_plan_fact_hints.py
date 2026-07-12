"""Unit tests for FIN-122 query_plan_fact article resolve hints."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "query_plan_fact",
    _SCRIPTS / "query-plan-fact.py",
)
assert _SPEC and _SPEC.loader
_query_plan_fact = importlib.util.module_from_spec(_SPEC)
sys.modules["query_plan_fact"] = _query_plan_fact
_SPEC.loader.exec_module(_query_plan_fact)

resolve_budget_item_id = _query_plan_fact.resolve_budget_item_id
normalize_match_text = _query_plan_fact.normalize_match_text
rank_article_candidates = _query_plan_fact.rank_article_candidates

SALARY_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
SALARY_NIK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
SAVE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
SAVE_OTHER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
DUPE_NAME_A = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"
DUPE_NAME_B = "cccccccc-cccc-4ccc-8ccc-ccccccccccc2"
UUID_DIRECT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _item(
    item_id: str,
    name: str,
    *,
    status: str = "ACT",
    operation_category_id: str = "C0001",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "status": status,
        "operation_category_id": operation_category_id,
    }


class _ResolveMockApi:
    """Stub ApiClient for budget item resolve (FIN-122)."""

    def __init__(
        self,
        items: list[dict[str, Any]],
        *,
        list_error: Exception | None = None,
    ) -> None:
        self._items = items
        self._by_id = {str(row["id"]): row for row in items}
        self._list_error = list_error
        self.list_calls = 0

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/budget/items":
            self.list_calls += 1
            if self._list_error is not None:
                raise self._list_error
            return {"budget_items": self._items}
        if path.startswith("/api/v1/budget/items/"):
            item_id = path.rsplit("/", 1)[-1]
            if item_id not in self._by_id:
                raise RuntimeError(f"not found {item_id}")
            return self._by_id[item_id]
        raise AssertionError(f"unexpected path {path}")


class TestQueryPlanFactHints(unittest.TestCase):
    """FIN-122 resolve_budget_item_id enriched errors."""

    def test_t01_happy_path_single_substring(self) -> None:
        """T1: one substring match returns id and name."""
        api = _ResolveMockApi([_item(SALARY_ID, "Заработная плата", operation_category_id="P0001")])
        item_id, name = resolve_budget_item_id(api, "Заработная", None)
        self.assertEqual(item_id, SALARY_ID)
        self.assertEqual(name, "Заработная плата")

    def test_t02_budget_item_id_direct(self) -> None:
        """T2: explicit UUID resolves without list catalog."""
        api = _ResolveMockApi([_item(UUID_DIRECT, "Прямой UUID")])
        item_id, name = resolve_budget_item_id(api, None, UUID_DIRECT)
        self.assertEqual(item_id, UUID_DIRECT)
        self.assertEqual(name, "Прямой UUID")
        self.assertEqual(api.list_calls, 0)

    def test_t03_not_found_with_candidates(self) -> None:
        """T3: not-found lists ranked candidates with three fields."""
        api = _ResolveMockApi(
            [
                _item(SALARY_ID, "Заработная плата", operation_category_id="P0001"),
                _item(SAVE_ID, "Сбережения", operation_category_id="C0099"),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "P000", None)
        message = str(ctx.exception)
        self.assertIn("не найдена", message)
        self.assertIn(SALARY_ID, message)
        self.assertIn("P0001", message)
        self.assertIn("категория", message)

    def test_t04_category_alias_p001(self) -> None:
        """T4: P001 suggests P0001 salary item in top candidates."""
        api = _ResolveMockApi(
            [_item(SALARY_ID, "Заработная плата", operation_category_id="P0001")],
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "P001", None)
        message = str(ctx.exception)
        self.assertIn("P0001", message)
        self.assertIn("Заработная плата", message)

    def test_t05_ambiguous_substring_lists_all(self) -> None:
        """T5: ambiguous substring lists every match with id."""
        api = _ResolveMockApi(
            [
                _item(SAVE_ID, "Сбережения личный фонд", operation_category_id="C0001"),
                _item(SAVE_OTHER_ID, "Сбережения общие", operation_category_id="C0002"),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "Сбережения", None)
        message = str(ctx.exception)
        self.assertIn("Неоднозначно", message)
        self.assertIn(SAVE_ID, message)
        self.assertIn(SAVE_OTHER_ID, message)
        self.assertIn("категория C0001", message)

    def test_t06_ambiguous_disambiguation_hints(self) -> None:
        """T6: ambiguous error suggests budget_item_id and shared prefix."""
        api = _ResolveMockApi(
            [
                _item(SAVE_ID, "Сбережения личный фонд"),
                _item(SAVE_OTHER_ID, "Сбережения общие"),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "Сбережения", None)
        message = str(ctx.exception)
        self.assertIn("budget_item_id=", message)
        self.assertIn("сбережения", message.casefold())

    def test_t07_exact_match_priority(self) -> None:
        """T7: exact match wins over multiple substring matches."""
        api = _ResolveMockApi(
            [
                _item(SALARY_ID, "Заработная плата", operation_category_id="P0001"),
                _item(SALARY_NIK_ID, "Заработная плата Николай", operation_category_id="P0002"),
            ],
        )
        item_id, name = resolve_budget_item_id(api, "Заработная плата", None)
        self.assertEqual(item_id, SALARY_ID)
        self.assertEqual(name, "Заработная плата")

    def test_t08_whitespace_exact_match(self) -> None:
        """T8: collapsed whitespace exact match succeeds."""
        api = _ResolveMockApi([_item(SALARY_ID, "Заработная плата")])
        item_id, name = resolve_budget_item_id(api, "Заработная   плата", None)
        self.assertEqual(item_id, SALARY_ID)
        self.assertEqual(name, "Заработная плата")

    def test_t09_inactive_items_excluded(self) -> None:
        """T9: INA items are not matched or suggested."""
        api = _ResolveMockApi(
            [
                _item(SALARY_ID, "Заработная плата", status="INA"),
                _item(SAVE_ID, "Сбережения"),
            ],
        )
        item_id, name = resolve_budget_item_id(api, "Сбережения", None)
        self.assertEqual(item_id, SAVE_ID)

    def test_t10_candidate_tie_break_by_id(self) -> None:
        """T10: equal score and name tie-break by budget_item_id ASC."""
        items = [
            _item("zzzzzzzz-zzzz-4zzz-8zzz-zzzzzzzzzzz9", "Дубль", operation_category_id="C0002"),
            _item("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa0", "Дубль", operation_category_id="C0001"),
        ]
        ranked = rank_article_candidates("Дуб", items)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(str(ranked[0]["id"]), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa0")

    def test_t11_top_five_candidates_only(self) -> None:
        """T11: not-found shows at most five substring candidates."""
        items = [
            _item(f"id-{index:02d}", f"Статья prefix-{index}", operation_category_id=f"C{index:04d}")
            for index in range(8)
        ]
        ranked = rank_article_candidates("prefix", items)
        self.assertEqual(len(ranked), 5)

    def test_t12_list_catalog_failure_propagates(self) -> None:
        """T12: GET /budget/items failure is not swallowed."""
        api = _ResolveMockApi([], list_error=RuntimeError("API down"))
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "любая", None)
        self.assertEqual(str(ctx.exception), "API down")

    def test_t13_duplicate_exact_names_ambiguous(self) -> None:
        """T13: two ACT items with same normalized name → ambiguous exact branch."""
        api = _ResolveMockApi(
            [
                _item(DUPE_NAME_A, "Одинаковое имя", operation_category_id="C0001"),
                _item(DUPE_NAME_B, "Одинаковое имя", operation_category_id="C0002"),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            resolve_budget_item_id(api, "Одинаковое имя", None)
        message = str(ctx.exception)
        self.assertIn("Неоднозначно", message)
        self.assertIn(DUPE_NAME_A, message)
        self.assertIn(DUPE_NAME_B, message)

    def test_normalize_match_text_collapses_whitespace(self) -> None:
        """D-08: normalize_match_text collapses spaces and casefolds."""
        self.assertEqual(
            normalize_match_text("  Заработная   плата "),
            normalize_match_text("заработная плата"),
        )


if __name__ == "__main__":
    unittest.main()
