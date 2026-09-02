"""Unit tests for FIN-293 banks MCP tools."""

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

from banks import (  # noqa: E402
    create_bank,
    create_banks,
    delete_bank,
    delete_banks,
    get_bank,
    list_banks,
    patch_bank,
    patch_banks,
)

_BANK_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_BANK_ID_2 = "0ed7530f-b054-42e7-babd-5fd541bea2b4"


def _sample_bank(**overrides: Any) -> dict[str, Any]:
    bank = {
        "id": _BANK_ID,
        "display_name": "Sparkasse",
        "bic": None,
        "created_at": "2026-08-09T10:00:00Z",
        "updated_at": "2026-08-09T10:00:00Z",
    }
    bank.update(overrides)
    return bank


class _BanksMockApi:
    """Stub ApiClient capturing bank API calls."""

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


class BanksLibTests(unittest.TestCase):
    """Lib helpers for eight bank tools."""

    def test_create_list_get(self) -> None:
        api = _BanksMockApi(status=201, body=_sample_bank())
        created = create_bank(
            api,
            profile="cand",
            base="http://test",
            arguments={"display_name": "Sparkasse", "bic": None},
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["bank"]["id"], _BANK_ID)
        self.assertEqual(api.last_body, {"display_name": "Sparkasse", "bic": None})

        api.status = 200
        api.body = {"banks": [_sample_bank()]}
        listed = list_banks(api, profile="cand", base="http://test")
        self.assertEqual(len(listed["banks"]), 1)

        api.body = _sample_bank()
        got = get_bank(
            api,
            profile="cand",
            base="http://test",
            arguments={"bank_id": _BANK_ID},
        )
        self.assertEqual(got["bank"]["display_name"], "Sparkasse")

    def test_get_not_found_and_validation(self) -> None:
        api = _BanksMockApi(
            status=404,
            body={"error": {"code": "bank_not_found", "message": "Банк не найден."}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_bank(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_id": _BANK_ID},
            )
        self.assertIn("bank_not_found", str(ctx.exception))

        api.status = 422
        api.body = {"error": {"code": "validation_error", "message": "bad"}}
        with self.assertRaises(RuntimeError) as ctx2:
            get_bank(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_id": "not-a-uuid"},
            )
        self.assertIn("validation_error", str(ctx2.exception))

    def test_patch_bank(self) -> None:
        api = _BanksMockApi(body=_sample_bank(display_name="Renamed", bic="TESTBIC1"))
        result = patch_bank(
            api,
            profile="cand",
            base="http://test",
            arguments={"bank_id": _BANK_ID, "display_name": "Renamed", "bic": "TESTBIC1"},
        )
        self.assertEqual(result["bank"]["display_name"], "Renamed")
        with self.assertRaises(ValueError):
            patch_bank(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_id": _BANK_ID},
            )
        self.assertEqual(len(api.calls), 1)

    def test_create_banks_and_batch_validation(self) -> None:
        api = _BanksMockApi(
            status=201,
            body={"banks": [_sample_bank(), _sample_bank(id=_BANK_ID_2, display_name="C24")]},
        )
        result = create_banks(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "banks": [
                    {"display_name": "Sparkasse", "bic": None},
                    {"display_name": "C24"},
                ]
            },
        )
        self.assertEqual(len(result["banks"]), 2)
        self.assertEqual(api.calls[0][:2], ("POST", "/api/v1/banks/batch"))

        with self.assertRaises(ValueError):
            create_banks(
                api,
                profile="cand",
                base="http://test",
                arguments={"banks": []},
            )

        api.status = 422
        api.body = {"error": {"code": "validation_error", "message": "empty name"}}
        with self.assertRaises(RuntimeError) as ctx:
            create_banks(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "banks": [
                        {"display_name": "Ok"},
                        {"display_name": ""},
                    ]
                },
            )
        self.assertIn("validation_error", str(ctx.exception))

    def test_patch_banks(self) -> None:
        api = _BanksMockApi(
            body={"banks": [_sample_bank(display_name="A"), _sample_bank(id=_BANK_ID_2)]}
        )
        result = patch_banks(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "banks": [
                    {"id": _BANK_ID, "display_name": "A"},
                    {"id": _BANK_ID_2, "bic": None},
                ]
            },
        )
        self.assertEqual(len(result["banks"]), 2)
        self.assertEqual(api.calls[0][:2], ("PATCH", "/api/v1/banks"))

    def test_delete_bank_and_in_use(self) -> None:
        api = _BanksMockApi(status=204, body=b"")
        result = delete_bank(
            api,
            profile="cand",
            base="http://test",
            arguments={"bank_id": _BANK_ID},
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("bank", result)
        self.assertEqual(api.calls[0][:2], ("DELETE", f"/api/v1/banks/{_BANK_ID}"))

        api.status = 409
        api.body = {"error": {"code": "bank_in_use", "message": "in use"}}
        with self.assertRaises(RuntimeError) as ctx:
            delete_bank(
                api,
                profile="cand",
                base="http://test",
                arguments={"bank_id": _BANK_ID},
            )
        self.assertIn("bank_in_use", str(ctx.exception))

    def test_delete_banks(self) -> None:
        api = _BanksMockApi(status=204, body=b"")
        result = delete_banks(
            api,
            profile="cand",
            base="http://test",
            arguments={"ids": [_BANK_ID, _BANK_ID_2]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_body, {"ids": [_BANK_ID, _BANK_ID_2]})

        with self.assertRaises(ValueError):
            delete_banks(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": []},
            )

    def test_network_error_not_wrapped(self) -> None:
        api = _BanksMockApi(raise_on_request=OSError("network down"))
        with self.assertRaises(OSError):
            list_banks(api, profile="cand", base="http://test")


class BanksSchemaTests(unittest.TestCase):
    """Eight bank tools registered."""

    def test_eight_tools_registered(self) -> None:
        import server

        expected = {
            "list_banks",
            "get_bank",
            "create_bank",
            "create_banks",
            "patch_bank",
            "patch_banks",
            "delete_bank",
            "delete_banks",
        }
        tools_list = asyncio.run(server.list_tools())
        names = {t.name for t in tools_list}
        self.assertTrue(expected.issubset(names))


class BanksHandlerTests(unittest.TestCase):
    """Handler path for create_bank."""

    def test_create_bank_handler(self) -> None:
        import server

        api = _BanksMockApi(status=201, body=_sample_bank())
        with patch("server.get_session", return_value=(api, "http://test")):
            out = server._handle_create_bank(
                {"display_name": "Sparkasse", "bic": None}
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["bank"]["id"], _BANK_ID)


if __name__ == "__main__":
    unittest.main()
