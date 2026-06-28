"""Unit tests for FIN-103 household_base_share MCP tool."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from household_base_share import (
    compute_from_mapping,
    compute_household_base_share,
    finalize_api_payload,
    load_mapping_file,
    normalize_period,
    probe_household_api,
    validate_mapping_structure,
)

VID = "00000000-0000-4000-8000-000000000001"

SALARY_ID = "inc-salary"
RUSSIA_ID = "inc-russia"
TRAVEL_ID = "exp-travel"
RENT_ID = "exp-rent"
OTHER_INC_ID = "inc-other"

BASE_ITEMS: list[dict[str, Any]] = [
    {"id": SALARY_ID, "name": "Заработная плата", "flow_type": "INC"},
    {"id": RUSSIA_ID, "name": "Переводы в Россию на оплату услуг", "flow_type": "INC"},
    {"id": OTHER_INC_ID, "name": "Налоговый возврат", "flow_type": "INC"},
    {"id": TRAVEL_ID, "name": "Командировки (отель и проезд)", "flow_type": "EXP"},
    {"id": RENT_ID, "name": "Арендная плата (Ulf Veit, Kirchhoffstraße)", "flow_type": "EXP"},
    {"id": "exp-internet", "name": "Интернет (NetCologne)", "flow_type": "EXP"},
    {"id": "exp-mobile", "name": "Мобильная связь (Telefónica Germany)", "flow_type": "EXP"},
    {"id": "exp-heat", "name": "Отопление (RheinEnergie)", "flow_type": "EXP"},
    {"id": "exp-power", "name": "Электроэнергия (RheinEnergie)", "flow_type": "EXP"},
    {"id": "exp-barmenia", "name": "Дополнительное медицинское страхование (Barmenia)", "flow_type": "EXP"},
    {"id": "exp-arag", "name": "Страхование правовой защиты (ARAG)", "flow_type": "EXP"},
    {"id": "exp-youtube", "name": "Подписка YouTube Premium", "flow_type": "EXP"},
    {"id": "exp-db1", "name": "Абонемент Deutsche Bahn (DB Vertrieb)", "flow_type": "EXP"},
    {"id": "exp-db2", "name": "Абонемент Deutsche Bahn (Abo 259857844)", "flow_type": "EXP"},
    {"id": "exp-bank", "name": "Банковское обслуживание (комиссии и счета)", "flow_type": "EXP"},
    {"id": "exp-save", "name": "Сбережения", "flow_type": "EXP"},
    {"id": "exp-save2", "name": "Прочие сбережения", "flow_type": "EXP"},
    {"id": "irr-food", "name": "Продукты питания и хозтовары", "flow_type": "EXP"},
    {"id": "sub-cursor", "name": "Подписка Cursor", "flow_type": "EXP"},
]

JULY_PLANS: dict[str, float] = {
    SALARY_ID: 4740.18,
    RUSSIA_ID: 250.0,
    OTHER_INC_ID: 100.0,
    TRAVEL_ID: 300.0,
    RENT_ID: 1100.0,
    "exp-internet": 26.95,
    "exp-mobile": 9.99,
    "exp-heat": 115.0,
    "exp-power": 77.0,
    "exp-barmenia": 61.60,
    "exp-arag": 42.46,
    "exp-youtube": 23.99,
    "exp-db1": 63.0,
    "exp-db2": 63.0,
    "exp-bank": 9.90,
    "exp-save": 0.0,
    "exp-save2": 0.0,
    "irr-food": 1300.0,
    "sub-cursor": 55.0,
}


def _grid_nodes(plans: dict[str, float]) -> list[dict[str, Any]]:
    items = {item["id"]: item["name"] for item in BASE_ITEMS}
    return [
        {
            "kind": "row",
            "budget_item_id": item_id,
            "plan_amount": amount,
            "article_label": items.get(item_id, item_id),
        }
        for item_id, amount in plans.items()
    ]


def _base_mapping(*, partners: list[dict[str, str]] | None = None) -> dict[str, Any]:
    partner_list = (
        [
            {"id": "aleksey", "display_name": "Алексей"},
            {"id": "nikolay", "display_name": "Николай"},
        ]
        if partners is None
        else partners
    )
    return {
        "schema_version": 1,
        "profile": "prod",
        "partners": partner_list,
        "household_income": {
            "include": [{"article_match": "Заработная плата"}],
            "exclude": [
                {
                    "article_match": "Переводы в Россию",
                    "reason": "nikolay_parent_support",
                }
            ],
        },
        "professional": {
            "aleksey": [{"article_match": "Командировки"}],
            "nikolay": [],
        },
        "shared_fund": [
            {"article_match": "Ulf Veit"},
            {"article_match": "NetCologne"},
            {"article_match": "Мобильная связь"},
            {"article_match": "Отопление"},
            {"article_match": "Электроэнергия"},
            {"article_match": "Barmenia"},
            {"article_match": "ARAG"},
            {"article_match": "YouTube Premium"},
            {"article_match": "DB Vertrieb"},
            {"article_match": "Abo 259857844"},
            {"article_match": "Банковское обслуживание"},
        ],
        "savings": [
            {"article_match": "Сбережения"},
            {"article_match": "Прочие сбережения"},
        ],
        "legacy_irr_sanity": [{"article_match": "Продукты питания"}],
        "personal_subscriptions_sanity": [{"article_match": "Cursor"}],
    }


class _MockApi:
    """API stub for budget items, plan-actual, household probe."""

    def __init__(
        self,
        *,
        plans: dict[str, float] | None = None,
        household_status: int = 404,
        household_body: dict[str, Any] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.plans = plans if plans is not None else JULY_PLANS
        self.household_status = household_status
        self.household_body = household_body
        self.items = items if items is not None else BASE_ITEMS
        self.plan_actual_calls = 0

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith("/budget/items"):
            return {"budget_items": self.items}
        if parsed.path.endswith("/budget/plan-actual"):
            self.plan_actual_calls += 1
            return {"grid_nodes": _grid_nodes(self.plans)}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(self, method: str, path: str, data: dict | None = None) -> tuple[int, Any]:
        assert method == "GET"
        if path.startswith("/api/v1/household/base-share"):
            return self.household_status, self.household_body or {}
        raise AssertionError(f"unexpected request path: {path}")


class HouseholdBaseShareTest(unittest.TestCase):
    """FIN-103 mapping path and API probe."""

    def _run_mapping(
        self,
        mapping: dict[str, Any],
        *,
        plans: dict[str, float] | None = None,
        api: _MockApi | None = None,
    ) -> dict[str, Any]:
        api = api or _MockApi(plans=plans or JULY_PLANS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text(json.dumps(mapping), encoding="utf-8")
            return compute_from_mapping(
                api,
                mapping,
                profile="prod",
                base="http://127.0.0.1:8000",
                yyyy_mm="2026-07",
                budget_version_id=VID,
                mapping_path=path,
            )

    def test_t1_july_2026_fixture(self) -> None:
        """T1: July 2026 totals and rounding_delta."""
        result = self._run_mapping(_base_mapping())
        self.assertEqual(result["free_remainder"], 2847.29)
        self.assertEqual(result["partners"][0]["base_share"], 1423.65)
        self.assertEqual(result["sanity_check"]["rounding_delta"], 0.01)

    def test_t2_excluded_parent_support(self) -> None:
        """T2: excluded income not in household_income.total."""
        result = self._run_mapping(_base_mapping())
        self.assertEqual(result["household_income"]["total"], 4740.18)
        excluded = result["household_income"]["excluded_income"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["reason"], "nikolay_parent_support")
        self.assertEqual(excluded[0]["plan"], 250.0)

    def test_t3_professional_nikolay_zero(self) -> None:
        """T3: Nikolay professional total is zero."""
        result = self._run_mapping(_base_mapping())
        self.assertEqual(result["professional"]["by_partner"]["nikolay"]["total"], 0.0)

    def test_t4_missing_required_article(self) -> None:
        """T4: missing shared_fund article fails."""
        mapping = _base_mapping()
        mapping["shared_fund"] = [{"article_match": "NO_SUCH_ARTICLE_XYZ"}]
        with self.assertRaises(RuntimeError):
            self._run_mapping(mapping)

    def test_t5_ambiguous_article_match(self) -> None:
        """T5: ambiguous match fails."""
        items = list(BASE_ITEMS) + [
            {"id": "dup-a", "name": "Deutsche Bahn Alpha", "flow_type": "EXP"},
            {"id": "dup-b", "name": "Deutsche Bahn Beta", "flow_type": "EXP"},
        ]
        mapping = _base_mapping()
        mapping["shared_fund"] = [{"article_match": "Deutsche Bahn"}]
        api = _MockApi(items=items)
        with self.assertRaises(RuntimeError):
            self._run_mapping(mapping, api=api)

    def test_t6_unmapped_inc_warning(self) -> None:
        """T6: unmapped INC with plan > 0 yields warning."""
        result = self._run_mapping(_base_mapping())
        self.assertTrue(
            any(w.startswith("unmapped_income:") for w in result["warnings"])
        )

    def test_t7_negative_free_remainder_warning(self) -> None:
        """T7: negative free remainder warns but succeeds."""
        plans = dict(JULY_PLANS)
        plans[SALARY_ID] = 100.0
        result = self._run_mapping(_base_mapping(), plans=plans)
        self.assertTrue(result["ok"])
        self.assertIn("negative_free_remainder", result["warnings"])

    def test_t8_sanity_legacy_total(self) -> None:
        """T8: legacy_irr_total sums sanity lines only."""
        result = self._run_mapping(_base_mapping())
        self.assertEqual(result["sanity_check"]["legacy_irr_total"], 1300.0)

    def test_t9_duplicate_contour_assignment(self) -> None:
        """T9: same item in shared_fund and professional fails."""
        mapping = _base_mapping()
        mapping["professional"]["aleksey"] = [{"article_match": "Barmenia"}]
        with self.assertRaises(RuntimeError) as ctx:
            self._run_mapping(mapping)
        self.assertIn("duplicate contour assignment", str(ctx.exception))

    def test_t10_include_exclude_overlap(self) -> None:
        """T10: same item in include and exclude fails."""
        mapping = _base_mapping()
        mapping["household_income"]["exclude"] = [
            {"article_match": "Заработная плата", "reason": "x"}
        ]
        with self.assertRaises(RuntimeError) as ctx:
            self._run_mapping(mapping)
        self.assertIn("include/exclude overlap", str(ctx.exception))

    def test_t11_empty_partners(self) -> None:
        """T11: empty partners fails validation."""
        mapping = _base_mapping(partners=[])
        with self.assertRaises(RuntimeError) as ctx:
            validate_mapping_structure(mapping, "prod")
        self.assertIn("empty partners", str(ctx.exception))

    def test_t12_profile_mismatch(self) -> None:
        """T12: mapping profile mismatch fails."""
        mapping = _base_mapping()
        mapping["profile"] = "test"
        with self.assertRaises(RuntimeError):
            validate_mapping_structure(mapping, "prod")

    def test_t13_three_partners(self) -> None:
        """T13: base_share uses len(partners)."""
        mapping = _base_mapping(
            partners=[
                {"id": "a", "display_name": "A"},
                {"id": "b", "display_name": "B"},
                {"id": "c", "display_name": "C"},
            ]
        )
        mapping["professional"] = {"a": [], "b": [], "c": []}
        result = self._run_mapping(mapping)
        expected = round(result["free_remainder"] / 3, 2)
        for partner in result["partners"]:
            self.assertEqual(partner["base_share"], expected)

    def test_t14_single_plan_actual_call(self) -> None:
        """T14: grouped plan-actual fetched once per compute."""
        api = _MockApi()
        self._run_mapping(_base_mapping(), api=api)
        self.assertEqual(api.plan_actual_calls, 1)

    def test_t15_api_404_uses_mapping(self) -> None:
        """T15: missing household API falls back to mapping."""
        api = _MockApi(household_status=404)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            mapping = _base_mapping()
            path.write_text(json.dumps(mapping), encoding="utf-8")
            result = compute_household_base_share(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="2026-07",
                budget_version_id=VID,
                mapping_path=str(path),
            )
        self.assertEqual(result["source"], "mapping")
        self.assertTrue(result["ok"])

    def test_t16_api_200_skips_mapping_file(self) -> None:
        """T16: household API 200 ignores mapping file."""
        api_body = {
            "free_remainder": 1000.0,
            "household_income": {"total": 1000.0, "lines": [], "excluded_income": []},
            "professional": {"total": 0.0, "by_partner": {}},
            "shared_fund": {"total": 0.0, "lines": []},
            "savings": {"total": 0.0, "lines": []},
            "partners": [
                {"id": "aleksey", "display_name": "Алексей", "base_share": 500.0},
                {"id": "nikolay", "display_name": "Николай", "base_share": 500.0},
            ],
        }
        api = _MockApi(household_status=200, household_body=api_body)
        with patch("household_base_share.load_mapping_file") as load_mock:
            result = compute_household_base_share(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="2026-07",
                budget_version_id=VID,
                mapping_path="/should/not/be/read.json",
            )
        load_mock.assert_not_called()
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["partner_count"], 2)

    def test_probe_household_api_5xx_raises(self) -> None:
        """Household API 5xx raises without mapping fallback."""
        api = _MockApi(household_status=503, household_body={"error": "down"})
        with self.assertRaises(RuntimeError):
            probe_household_api(api, "2026-07")

    def test_normalize_period(self) -> None:
        """Period normalization accepts YYYYMM."""
        self.assertEqual(normalize_period("202607"), "2026-07")

    def test_finalize_api_partner_count(self) -> None:
        """API path sets partner_count from partners length."""
        body = finalize_api_payload(
            {
                "free_remainder": 10.0,
                "partner_count": 99,
                "partners": [{"id": "a", "base_share": 5.0}, {"id": "b", "base_share": 5.0}],
                "household_income": {"lines": [], "excluded_income": []},
                "professional": {"by_partner": {}},
                "shared_fund": {"lines": []},
                "savings": {"lines": []},
            },
            profile="prod",
            base="http://x",
            period="2026-07",
            budget_version_id=VID,
        )
        self.assertEqual(body["partner_count"], 2)


if __name__ == "__main__":
    unittest.main()
