"""Unit tests for FIN-332 household budget currency MCP tools."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from household_budget_currencies import (  # noqa: E402
    create_household_budget_currency,
    list_household_budget_currencies,
)

_HH = "fin332-acceptance-unit"
_PATH = f"/api/v1/households/{_HH}/budget-currencies"


def _sample_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "household_id": _HH,
        "valid_from": "2025-01-01",
        "currency": "EUR",
        "created_at": "2026-08-23T09:00:00Z",
    }
    row.update(overrides)
    return row


class _MockApi:
    """Stub ApiClient capturing budget-currency API calls."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.raise_on_request = raise_on_request
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if self.raise_on_request is not None:
            raise self.raise_on_request
        self.calls.append((method, path, dict(data) if data is not None else None))
        return self.status, self.body

    @property
    def last_body(self) -> dict[str, Any] | None:
        if not self.calls:
            return None
        return self.calls[-1][2]


class BudgetCurrencyLibTests(unittest.TestCase):
    """Lib helpers for list/create budget currency tools."""

    def test_list_happy_path_empty(self) -> None:
        api = _MockApi(status=200, body={"budget_currencies": []})
        result = list_household_budget_currencies(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": _HH},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "cand")
        self.assertEqual(result["base"], "http://test")
        self.assertEqual(result["budget_currencies"], [])
        self.assertEqual(api.calls[0][:2], ("GET", _PATH))

    def test_create_happy_path(self) -> None:
        api = _MockApi(status=201, body=_sample_row())
        result = create_household_budget_currency(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": _HH,
                "valid_from": "2025-01",
                "currency": "eur",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["budget_currency"]["currency"], "EUR")
        self.assertEqual(
            api.last_body,
            {"valid_from": "2025-01", "currency": "eur"},
        )
        self.assertEqual(api.calls[0][:2], ("POST", _PATH))

    def test_create_preserves_whitespace_in_body(self) -> None:
        api = _MockApi(status=201, body=_sample_row())
        create_household_budget_currency(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": _HH,
                "valid_from": "2025-01",
                "currency": " EUR ",
            },
        )
        self.assertEqual(api.last_body, {"valid_from": "2025-01", "currency": " EUR "})

    def test_list_404(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "household_not_found",
                    "message": "Household not found.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            list_household_budget_currencies(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": "missing"},
            )
        self.assertIn("household_not_found", str(ctx.exception))

    def test_create_409_duplicate(self) -> None:
        api = _MockApi(
            status=409,
            body={
                "error": {
                    "code": "budget_currency_duplicate_valid_from",
                    "message": "duplicate",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_household_budget_currency(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": _HH,
                    "valid_from": "2025-01-01",
                    "currency": "GBP",
                },
            )
        self.assertIn("budget_currency_duplicate_valid_from", str(ctx.exception))

    def test_create_422_valid_from_invalid(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "budget_currency_valid_from_invalid",
                    "message": "day must be 01",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_household_budget_currency(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": _HH,
                    "valid_from": "2025-01-15",
                    "currency": "GBP",
                },
            )
        self.assertIn("budget_currency_valid_from_invalid", str(ctx.exception))

    def test_whitespace_household_id_raises_before_http(self) -> None:
        api = _MockApi(status=200, body={"budget_currencies": []})
        with self.assertRaises(ValueError):
            list_household_budget_currencies(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": "   "},
            )
        self.assertEqual(api.calls, [])

    def test_whitespace_currency_raises_before_http(self) -> None:
        api = _MockApi(status=201, body=_sample_row())
        with self.assertRaises(ValueError):
            create_household_budget_currency(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": _HH,
                    "valid_from": "2025-01",
                    "currency": "  ",
                },
            )
        self.assertEqual(api.calls, [])

    def test_list_malformed_success_body(self) -> None:
        api = _MockApi(status=200, body=["not", "an", "object"])
        with self.assertRaises(RuntimeError):
            list_household_budget_currencies(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": _HH},
            )

    def test_create_malformed_success_body(self) -> None:
        api = _MockApi(status=201, body="not-an-object")
        with self.assertRaises(RuntimeError):
            create_household_budget_currency(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": _HH,
                    "valid_from": "2025-01",
                    "currency": "EUR",
                },
            )

    def test_create_unexpected_200_status(self) -> None:
        api = _MockApi(status=200, body=_sample_row())
        with self.assertRaises(RuntimeError):
            create_household_budget_currency(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": _HH,
                    "valid_from": "2025-01",
                    "currency": "EUR",
                },
            )

    def test_network_error_not_wrapped(self) -> None:
        api = _MockApi(raise_on_request=OSError("network down"))
        with self.assertRaises(OSError):
            list_household_budget_currencies(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": _HH},
            )


class BudgetCurrencySchemaTests(unittest.TestCase):
    """Tool registration and required schema fields."""

    def test_tools_registered_with_required_fields(self) -> None:
        import server

        tools_list = asyncio.run(server.list_tools())
        by_name = {t.name: t for t in tools_list}
        self.assertIn("list_household_budget_currencies", by_name)
        self.assertIn("create_household_budget_currency", by_name)

        list_schema = by_name["list_household_budget_currencies"].inputSchema
        self.assertEqual(list_schema.get("required"), ["household_id"])
        self.assertIs(list_schema.get("additionalProperties"), False)

        create_schema = by_name["create_household_budget_currency"].inputSchema
        self.assertEqual(
            set(create_schema.get("required") or []),
            {"household_id", "valid_from", "currency"},
        )
        self.assertIs(create_schema.get("additionalProperties"), False)

    def test_handlers_dispatch(self) -> None:
        import server

        api = _MockApi(status=200, body={"budget_currencies": []})
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            payload = server._handle_list_household_budget_currencies(
                {"profile": "cand", "household_id": _HH}
            )
        self.assertEqual(len(payload), 1)
        self.assertIn("budget_currencies", payload[0].text)

        api.status = 201
        api.body = _sample_row()
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            created = server._handle_create_household_budget_currency(
                {
                    "profile": "cand",
                    "household_id": _HH,
                    "valid_from": "2025-01",
                    "currency": "EUR",
                }
            )
        self.assertIn("budget_currency", created[0].text)


if __name__ == "__main__":
    unittest.main()
