"""Unit tests for FIN-321 bank-account identifier MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from bank_account_identifiers import (  # noqa: E402
    create_bank_account_identifier,
    create_bank_account_identifiers,
    delete_bank_account_identifier,
    delete_bank_account_identifiers,
    get_bank_account_identifier,
    list_bank_account_identifiers,
    patch_bank_account_identifier,
    patch_bank_account_identifiers,
)

_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_ID_2 = "0ed7530f-b054-42e7-babd-5fd541bea2b4"
_ACCOUNT = "acc-sparkasse-sepa"


def _sample_identifier(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": _ID,
        "bank_account_id": _ACCOUNT,
        "identifier_type": "iban",
        "value": "DE89370400440532013000",
        "created_at": "2026-08-20T10:00:00Z",
        "updated_at": "2026-08-20T10:00:00Z",
    }
    row.update(overrides)
    return row


class _IdentifiersMockApi:
    """Stub ApiClient capturing identifier API calls."""

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


class IdentifiersLibTests(unittest.TestCase):
    """Lib helpers for eight identifier tools."""

    def test_create_list_get(self) -> None:
        api = _IdentifiersMockApi(status=201, body=_sample_identifier())
        created = create_bank_account_identifier(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": _ACCOUNT,
                "identifier_type": "iban",
                "value": "DE89 3704 0044 0532 0130 00",
            },
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["bank_account_identifier"]["id"], _ID)
        self.assertEqual(
            api.last_body,
            {
                "bank_account_id": _ACCOUNT,
                "identifier_type": "iban",
                "value": "DE89 3704 0044 0532 0130 00",
            },
        )

        api.status = 200
        api.body = {"bank_account_identifiers": [_sample_identifier()]}
        listed = list_bank_account_identifiers(
            api,
            profile="cand",
            base="http://test",
            arguments={"bank_account_id": _ACCOUNT},
        )
        self.assertEqual(len(listed["bank_account_identifiers"]), 1)
        self.assertEqual(
            api.calls[-1][:2],
            ("GET", f"/api/v1/bank-account-identifiers?bank_account_id={_ACCOUNT}"),
        )

        api.body = _sample_identifier()
        got = get_bank_account_identifier(
            api,
            profile="cand",
            base="http://test",
            arguments={"identifier_id": _ID},
        )
        self.assertEqual(got["bank_account_identifier"]["value"], "DE89370400440532013000")

    def test_get_not_found_and_validation(self) -> None:
        api = _IdentifiersMockApi(
            status=404,
            body={
                "error": {
                    "code": "identifier_not_found",
                    "message": "Идентификатор банковского счёта не найден.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={"identifier_id": _ID},
            )
        self.assertIn("identifier_not_found", str(ctx.exception))

        api.status = 422
        api.body = {"error": {"code": "validation_error", "message": "bad"}}
        with self.assertRaises(RuntimeError) as ctx2:
            get_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={"identifier_id": "not-a-uuid"},
            )
        self.assertIn("validation_error", str(ctx2.exception))

    def test_create_unknown_account_and_duplicates(self) -> None:
        api = _IdentifiersMockApi(
            status=404,
            body={"error": {"code": "bank_account_not_found", "message": "not found"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_id": "missing",
                    "identifier_type": "iban",
                    "value": "DE89370400440532013000",
                },
            )
        self.assertIn("bank_account_not_found", str(ctx.exception))

        api.status = 409
        api.body = {"error": {"code": "duplicate_iban", "message": "dup"}}
        with self.assertRaises(RuntimeError) as ctx2:
            create_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_id": "acc-b",
                    "identifier_type": "iban",
                    "value": "DE89370400440532013000",
                },
            )
        self.assertIn("duplicate_iban", str(ctx2.exception))

        api.body = {"error": {"code": "duplicate_identifier", "message": "dup"}}
        with self.assertRaises(RuntimeError) as ctx3:
            create_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_id": _ACCOUNT,
                    "identifier_type": "iban",
                    "value": "DE89370400440532013000",
                },
            )
        self.assertIn("duplicate_identifier", str(ctx3.exception))

    def test_create_empty_fields_no_http(self) -> None:
        api = _IdentifiersMockApi()
        with self.assertRaises(ValueError):
            create_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_id": _ACCOUNT,
                    "identifier_type": "iban",
                    "value": "  ",
                },
            )
        self.assertEqual(api.calls, [])

    def test_list_filter_null_no_http(self) -> None:
        api = _IdentifiersMockApi()
        with self.assertRaises(ValueError):
            list_bank_account_identifiers(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_account_id": None},
            )
        self.assertEqual(api.calls, [])

    def test_patch_and_delete_one(self) -> None:
        api = _IdentifiersMockApi(
            body=_sample_identifier(value="DE44500105175407324931")
        )
        result = patch_bank_account_identifier(
            api,
            profile="cand",
            base="http://test",
            arguments={"identifier_id": _ID, "value": "DE44 5001 0517 5407 3249 31"},
        )
        self.assertEqual(result["bank_account_identifier"]["value"], "DE44500105175407324931")
        self.assertEqual(api.last_body, {"value": "DE44 5001 0517 5407 3249 31"})

        with self.assertRaises(ValueError):
            patch_bank_account_identifier(
                api,
                profile="cand",
                base="http://test",
                arguments={"identifier_id": _ID},
            )
        self.assertEqual(len(api.calls), 1)

        api.status = 204
        api.body = b""
        deleted = delete_bank_account_identifier(
            api,
            profile="cand",
            base="http://test",
            arguments={"identifier_id": _ID},
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("bank_account_identifier", deleted)

    def test_create_batch_passes_empty_element_value(self) -> None:
        api = _IdentifiersMockApi(
            status=422,
            body={"error": {"code": "validation_error", "message": "empty value"}},
        )
        items = [
            {
                "bank_account_id": _ACCOUNT,
                "identifier_type": "iban",
                "value": "DE89370400440532013000",
            },
            {
                "bank_account_id": _ACCOUNT,
                "identifier_type": "account_number",
                "value": "",
            },
        ]
        with self.assertRaises(RuntimeError) as ctx:
            create_bank_account_identifiers(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_account_identifiers": items},
            )
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][:2], ("POST", "/api/v1/bank-account-identifiers/batch"))
        self.assertEqual(api.last_body, {"bank_account_identifiers": items})

        with self.assertRaises(ValueError):
            create_bank_account_identifiers(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_account_identifiers": []},
            )
        self.assertEqual(len(api.calls), 1)

    def test_create_batch_success(self) -> None:
        api = _IdentifiersMockApi(
            status=201,
            body={
                "bank_account_identifiers": [
                    _sample_identifier(),
                    _sample_identifier(id=_ID_2, identifier_type="account_number", value="0532013000"),
                ]
            },
        )
        result = create_bank_account_identifiers(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_identifiers": [
                    {
                        "bank_account_id": _ACCOUNT,
                        "identifier_type": "iban",
                        "value": "DE89370400440532013000",
                    },
                    {
                        "bank_account_id": _ACCOUNT,
                        "identifier_type": "account_number",
                        "value": "0532013000",
                    },
                ]
            },
        )
        self.assertEqual(len(result["bank_account_identifiers"]), 2)

    def test_patch_batch_error_forwards_once(self) -> None:
        api = _IdentifiersMockApi(
            status=404,
            body={"error": {"code": "identifier_not_found", "message": "missing"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            patch_bank_account_identifiers(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_identifiers": [
                        {"id": _ID, "value": "DE44500105175407324931"},
                        {"id": _ID_2, "value": "XX"},
                    ]
                },
            )
        self.assertIn("identifier_not_found", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][:2], ("PATCH", "/api/v1/bank-account-identifiers"))
        self.assertIsNotNone(api.last_body)
        self.assertEqual(len(api.last_body["bank_account_identifiers"]), 2)

    def test_delete_batch_error_forwards_once(self) -> None:
        api = _IdentifiersMockApi(
            status=404,
            body={"error": {"code": "identifier_not_found", "message": "missing"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            delete_bank_account_identifiers(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": [_ID, _ID_2]},
            )
        self.assertIn("identifier_not_found", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][:2], ("DELETE", "/api/v1/bank-account-identifiers"))
        self.assertEqual(api.last_body, {"ids": [_ID, _ID_2]})

        api.status = 204
        api.body = b""
        api.calls.clear()
        result = delete_bank_account_identifiers(
            api,
            profile="cand",
            base="http://test",
            arguments={"ids": [_ID, _ID_2]},
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("bank_account_identifiers", result)

    def test_network_error_not_wrapped(self) -> None:
        api = _IdentifiersMockApi(raise_on_request=OSError("network down"))
        with self.assertRaises(OSError):
            list_bank_account_identifiers(api, profile="cand", base="http://test")


class IdentifiersSchemaTests(unittest.TestCase):
    """Eight identifier tools registered."""

    def test_eight_tools_registered(self) -> None:
        import server

        expected = {
            "list_bank_account_identifiers",
            "get_bank_account_identifier",
            "create_bank_account_identifier",
            "create_bank_account_identifiers",
            "patch_bank_account_identifier",
            "patch_bank_account_identifiers",
            "delete_bank_account_identifier",
            "delete_bank_account_identifiers",
        }
        tools_list = asyncio.run(server.list_tools())
        names = {t.name for t in tools_list}
        self.assertTrue(expected.issubset(names))
        create_batch = next(
            t for t in tools_list if t.name == "create_bank_account_identifiers"
        )
        items = create_batch.inputSchema["properties"]["bank_account_identifiers"]["items"]
        self.assertEqual(
            set(items["required"]),
            {"bank_account_id", "identifier_type", "value"},
        )
        self.assertFalse(items.get("additionalProperties", True))


class IdentifiersHandlerTests(unittest.TestCase):
    """Handler path for create_bank_account_identifier."""

    def test_create_identifier_handler(self) -> None:
        import server

        api = _IdentifiersMockApi(status=201, body=_sample_identifier())
        with patch("server.get_session", return_value=(api, "http://test")):
            out = server._handle_create_bank_account_identifier(
                {
                    "bank_account_id": _ACCOUNT,
                    "identifier_type": "iban",
                    "value": "DE89370400440532013000",
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["bank_account_identifier"]["id"], _ID)


if __name__ == "__main__":
    unittest.main()
