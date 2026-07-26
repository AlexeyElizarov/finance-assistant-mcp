"""Unit tests for FIN-240 household master data MCP tools (T1–T13)."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import patch

from households import (
    list_bank_accounts,
    list_household_members,
    list_households,
    upsert_bank_account,
    upsert_household,
    upsert_household_member,
)


class _HouseholdsMockApi:
    """Stub ApiClient capturing GET/PUT for households API."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path, dict(data) if data is not None else None))
        return self.status, self.body

    @property
    def last_body(self) -> dict[str, Any] | None:
        if not self.calls:
            return None
        return self.calls[-1][2]


class HouseholdsLibTests(unittest.TestCase):
    """Lib helpers (happy path + errors)."""

    def test_t1_upsert_household(self) -> None:
        api = _HouseholdsMockApi(
            body={
                "id": "hh1",
                "name": "Home",
                "is_active": True,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        result = upsert_household(
            api,
            profile="cand",
            base="http://test",
            arguments={"id": "hh1", "name": "Home", "is_active": True},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["household"]["id"], "hh1")
        self.assertEqual(api.calls[0][0], "PUT")
        self.assertEqual(api.calls[0][1], "/api/v1/households/hh1")
        self.assertEqual(api.last_body, {"name": "Home", "is_active": True})

    def test_t2_list_households(self) -> None:
        api = _HouseholdsMockApi(body={"households": [{"id": "hh1", "name": "Home"}]})
        result = list_households(api, profile="cand", base="http://test")
        self.assertEqual(result["households"][0]["id"], "hh1")
        self.assertEqual(api.calls[0][:2], ("GET", "/api/v1/households"))

    def test_t3_member_upsert_and_list(self) -> None:
        api = _HouseholdsMockApi(
            body={
                "id": "aleksey",
                "household_id": "hh1",
                "display_name": "Aleksey",
                "is_active": True,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        upserted = upsert_household_member(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "member_id": "aleksey",
                "display_name": "Aleksey",
                "is_active": True,
            },
        )
        self.assertEqual(upserted["member"]["id"], "aleksey")
        api.status = 200
        api.body = {"members": [upserted["member"]]}
        listed = list_household_members(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": "hh1"},
        )
        self.assertEqual(len(listed["members"]), 1)

    def test_t4_upsert_bank_account(self) -> None:
        api = _HouseholdsMockApi(
            body={
                "id": "sk",
                "household_id": "hh1",
                "provider": "sparkasse",
                "display_name": "SK",
                "holder_member_id": "aleksey",
                "statement_expected": True,
                "final_close_only": False,
                "valid_from": "2026-01",
                "valid_to": None,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        result = upsert_bank_account(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "account_id": "sk",
                "provider": "sparkasse",
                "display_name": "SK",
                "valid_from": "2026-01",
                "holder_member_id": "aleksey",
                "statement_expected": True,
                "final_close_only": False,
            },
        )
        self.assertEqual(result["bank_account"]["provider"], "sparkasse")
        self.assertEqual(
            api.calls[0][1],
            "/api/v1/households/hh1/bank-accounts/sk",
        )

    def test_t5_list_bank_accounts(self) -> None:
        api = _HouseholdsMockApi(body={"bank_accounts": [{"id": "sk"}]})
        result = list_bank_accounts(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": "hh1"},
        )
        self.assertEqual(result["bank_accounts"][0]["id"], "sk")

    def test_t6_put_422_runtime_error(self) -> None:
        err = {
            "error": {
                "code": "validation_error",
                "message": "second active household",
            }
        }
        api = _HouseholdsMockApi(status=422, body=err)
        with self.assertRaises(RuntimeError) as ctx:
            upsert_household(
                api,
                profile="cand",
                base="http://test",
                arguments={"id": "hh2", "name": "Other", "is_active": True},
            )
        msg = str(ctx.exception)
        self.assertIn("422", msg)
        self.assertIn("validation_error", msg)

    def test_t7_put_404_runtime_error(self) -> None:
        err = {"error": {"code": "not_found", "message": "member not under household"}}
        api = _HouseholdsMockApi(status=404, body=err)
        with self.assertRaises(RuntimeError) as ctx:
            upsert_household_member(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": "hh1",
                    "member_id": "aleksey",
                    "display_name": "Aleksey",
                },
            )
        self.assertIn("404", str(ctx.exception))
        self.assertIn("not_found", str(ctx.exception))

    def test_t10_empty_id_no_http(self) -> None:
        api = _HouseholdsMockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_household(
                api,
                profile="cand",
                base="http://test",
                arguments={"id": "  ", "name": "Home"},
            )
        self.assertIn("id", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_t11_statement_expected_not_bool(self) -> None:
        api = _HouseholdsMockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_bank_account(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": "hh1",
                    "account_id": "sk",
                    "provider": "sparkasse",
                    "display_name": "SK",
                    "valid_from": "2026-01",
                    "statement_expected": "true",
                },
            )
        self.assertIn("statement_expected", str(ctx.exception))
        self.assertEqual(api.calls, [])


class HouseholdsHandlerPresenceTests(unittest.TestCase):
    """D-09: omit vs null via MCP handler path (T8–T9b, T13)."""

    def _patch_session(self, api: _HouseholdsMockApi) -> Any:
        return patch("server.get_session", return_value=(api, "http://test"))

    def test_t8_omit_valid_to_handler(self) -> None:
        import server

        api = _HouseholdsMockApi(
            body={
                "id": "sk",
                "household_id": "hh1",
                "provider": "sparkasse",
                "display_name": "SK",
                "valid_from": "2026-01",
                "statement_expected": True,
                "final_close_only": False,
                "holder_member_id": None,
                "valid_to": None,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        with self._patch_session(api):
            out = server._handle_upsert_bank_account(
                {
                    "household_id": "hh1",
                    "account_id": "sk",
                    "provider": "sparkasse",
                    "display_name": "SK",
                    "valid_from": "2026-01",
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(api.last_body)
        self.assertNotIn("valid_to", api.last_body)

    def test_t9_explicit_null_valid_to_handler(self) -> None:
        import server

        api = _HouseholdsMockApi(
            body={
                "id": "sk",
                "household_id": "hh1",
                "provider": "sparkasse",
                "display_name": "SK",
                "valid_from": "2026-01",
                "statement_expected": True,
                "final_close_only": False,
                "holder_member_id": None,
                "valid_to": None,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        with self._patch_session(api):
            out = server._handle_upsert_bank_account(
                {
                    "household_id": "hh1",
                    "account_id": "sk",
                    "provider": "sparkasse",
                    "display_name": "SK",
                    "valid_from": "2026-01",
                    "valid_to": None,
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(api.last_body)
        self.assertIn("valid_to", api.last_body)
        self.assertIsNone(api.last_body["valid_to"])

    def test_t9a_omit_holder_member_id_handler(self) -> None:
        import server

        api = _HouseholdsMockApi(
            body={
                "id": "sk",
                "household_id": "hh1",
                "provider": "sparkasse",
                "display_name": "SK",
                "valid_from": "2026-01",
                "statement_expected": True,
                "final_close_only": False,
                "holder_member_id": "aleksey",
                "valid_to": None,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        with self._patch_session(api):
            server._handle_upsert_bank_account(
                {
                    "household_id": "hh1",
                    "account_id": "sk",
                    "provider": "sparkasse",
                    "display_name": "SK",
                    "valid_from": "2026-01",
                }
            )
        self.assertIsNotNone(api.last_body)
        self.assertNotIn("holder_member_id", api.last_body)

    def test_t9b_explicit_null_holder_member_id_handler(self) -> None:
        import server

        api = _HouseholdsMockApi(
            body={
                "id": "sk",
                "household_id": "hh1",
                "provider": "sparkasse",
                "display_name": "SK",
                "valid_from": "2026-01",
                "statement_expected": True,
                "final_close_only": False,
                "holder_member_id": None,
                "valid_to": None,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        with self._patch_session(api):
            server._handle_upsert_bank_account(
                {
                    "household_id": "hh1",
                    "account_id": "sk",
                    "provider": "sparkasse",
                    "display_name": "SK",
                    "valid_from": "2026-01",
                    "holder_member_id": None,
                }
            )
        self.assertIsNotNone(api.last_body)
        self.assertIn("holder_member_id", api.last_body)
        self.assertIsNone(api.last_body["holder_member_id"])

    def test_t13_omit_is_active_household_handler(self) -> None:
        import server

        api = _HouseholdsMockApi(
            body={
                "id": "hh1",
                "name": "Home",
                "is_active": False,
                "created_at": "t",
                "updated_at": "t",
            }
        )
        with self._patch_session(api):
            out = server._handle_upsert_household(
                {"id": "hh1", "name": "Home"}
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(api.last_body)
        self.assertEqual(api.last_body, {"name": "Home"})
        self.assertNotIn("is_active", api.last_body)


class HouseholdsSchemaTests(unittest.TestCase):
    """T12: six tools registered."""

    def test_t12_six_tools_registered(self) -> None:
        import server

        expected = {
            "list_households",
            "upsert_household",
            "list_household_members",
            "upsert_household_member",
            "list_bank_accounts",
            "upsert_bank_account",
        }
        tools_list = asyncio.run(server.list_tools())
        names = {t.name for t in tools_list}
        self.assertTrue(expected.issubset(names))
        upsert_ba = next(t for t in tools_list if t.name == "upsert_bank_account")
        props = upsert_ba.inputSchema["properties"]
        self.assertEqual(props["valid_to"]["type"], ["string", "null"])
        self.assertEqual(props["holder_member_id"]["type"], ["string", "null"])
        upsert_hh = next(t for t in tools_list if t.name == "upsert_household")
        self.assertEqual(
            set(upsert_hh.inputSchema.get("required") or []),
            {"id", "name"},
        )


if __name__ == "__main__":
    unittest.main()
