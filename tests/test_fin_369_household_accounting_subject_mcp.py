"""Unit tests for FIN-369 household and member accounting-subject MCP wrapping."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonschema

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from households import (  # noqa: E402
    list_household_members,
    list_households,
    upsert_household,
    upsert_household_member,
)

import server  # noqa: E402

_ASSIGNMENT_ID = "00000000-0000-0000-0000-000000000001"
_SPACE = "\u0020"
_PADDED_PROFILE = f"{_SPACE}cand{_SPACE}"
_PADDED_BASE = f"{_SPACE}http://127.0.0.1:8000{_SPACE}"
_TRIMMED_BASE = "http://127.0.0.1:8000"
_SESSION_EMPTY_BASE = _SPACE * 3


class _MockApi:
    """Stub ApiClient capturing GET/PUT for household tools."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
    ) -> None:
        self.status = status
        self.body: Any = {} if body is None else body
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
        return self.calls[-1][2] if self.calls else None


def _tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.inputSchema


def _payload(result: list[Any]) -> dict[str, Any]:
    return json.loads(result[0].text)


def _hh_upsert_args(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {"id": "hh1", "name": "Home"}
    arguments.update(overrides)
    return arguments


def _member_upsert_args(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "household_id": "hh1",
        "member_id": "m1",
    }
    arguments.update(overrides)
    return arguments


def _hh_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "hh1",
        "name": "Home",
        "is_active": False,
        "created_at": "t",
        "updated_at": "t",
        "accounting_subject": None,
    }
    body.update(overrides)
    return body


def _member_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "m1",
        "household_id": "hh1",
        "is_active": True,
        "created_at": "t",
        "updated_at": "t",
        "accounting_subject": None,
    }
    body.update(overrides)
    return body


class Fin369HouseholdAccountingSubjectTests(unittest.TestCase):
    """FIN-369 wrapper, schema, and HTTP envelope tests."""

    def setUp(self) -> None:
        server._active_profile = None
        server._active_base = None

    def tearDown(self) -> None:
        server._active_profile = None
        server._active_base = None

    def test_omit_display_name_omits_http_key(self) -> None:
        api = _MockApi(body=_member_body())
        upsert_household_member(
            api,
            profile="cand",
            base="http://test",
            arguments=_member_upsert_args(is_active=True),
        )
        self.assertIsNotNone(api.last_body)
        self.assertNotIn("display_name", api.last_body)
        self.assertEqual(api.last_body["is_active"], True)

    def test_display_name_only_u0020_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            upsert_household_member(
                api,
                profile="cand",
                base="http://test",
                arguments=_member_upsert_args(display_name=_SPACE * 3),
            )
        self.assertEqual(api.calls, [])

    def test_display_name_null_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            upsert_household_member(
                api,
                profile="cand",
                base="http://test",
                arguments=_member_upsert_args(display_name=None),
            )
        self.assertEqual(api.calls, [])

    def test_household_assignment_id_bypass_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_household(
                api,
                profile="cand",
                base="http://test",
                arguments=_hh_upsert_args(accounting_subject_id=_ASSIGNMENT_ID),
            )
        self.assertIn("accounting_subject_id", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_household_assignment_null_bypass_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_household(
                api,
                profile="cand",
                base="http://test",
                arguments=_hh_upsert_args(accounting_subject=None),
            )
        self.assertIn("accounting_subject", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_member_assignment_id_bypass_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_household_member(
                api,
                profile="cand",
                base="http://test",
                arguments=_member_upsert_args(accounting_subject_id=_ASSIGNMENT_ID),
            )
        self.assertIn("accounting_subject_id", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_member_assignment_null_bypass_raises_without_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            upsert_household_member(
                api,
                profile="cand",
                base="http://test",
                arguments=_member_upsert_args(accounting_subject=None),
            )
        self.assertIn("accounting_subject", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_schema_rejects_undeclared_keys(self) -> None:
        household_schema = _tool_schema("upsert_household")
        member_schema = _tool_schema("upsert_household_member")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                _hh_upsert_args(accounting_subject_id=_ASSIGNMENT_ID),
                household_schema,
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                _member_upsert_args(accounting_subject=None),
                member_schema,
            )
        jsonschema.validate(_member_upsert_args(), member_schema)
        self.assertNotIn(
            "display_name",
            member_schema.get("required") or [],
        )
        self.assertIs(household_schema.get("additionalProperties"), False)
        self.assertIs(member_schema.get("additionalProperties"), False)

    def test_missing_accounting_subject_key_not_added(self) -> None:
        row = {"id": "hh1", "name": "Home"}
        api = _MockApi(body=row)
        result = upsert_household(
            api,
            profile="cand",
            base="http://test",
            arguments=_hh_upsert_args(),
        )
        self.assertNotIn("accounting_subject", result["household"])

    def test_member_display_name_in_http_not_removed(self) -> None:
        api = _MockApi(body=_member_body(display_name="Kept"))
        result = upsert_household_member(
            api,
            profile="cand",
            base="http://test",
            arguments=_member_upsert_args(display_name="Kept"),
        )
        self.assertEqual(result["member"]["display_name"], "Kept")

    def test_upsert_http_201_is_unexpected(self) -> None:
        api = _MockApi(status=201, body=_hh_body())
        with self.assertRaises(RuntimeError) as ctx:
            upsert_household(
                api,
                profile="cand",
                base="http://test",
                arguments=_hh_upsert_args(),
            )
        message = str(ctx.exception)
        self.assertIn("201", message)
        self.assertIn("PUT", message)

    def test_malformed_list_responses(self) -> None:
        cases = (
            (
                list_households,
                {},
                {"foo": "bar"},
            ),
            (
                list_households,
                {},
                {"households": "not-an-array"},
            ),
            (
                list_household_members,
                {"household_id": "hh1"},
                {"foo": "bar"},
            ),
            (
                list_household_members,
                {"household_id": "hh1"},
                {"members": "not-an-array"},
            ),
        )
        for func, arguments, body in cases:
            with self.subTest(func=func.__name__, body=body):
                api = _MockApi(body=body)
                with self.assertRaises(RuntimeError) as ctx:
                    if func is list_households:
                        func(api, profile="cand", base="http://test")
                    else:
                        func(
                            api,
                            profile="cand",
                            base="http://test",
                            arguments=arguments,
                        )
                message = str(ctx.exception)
                self.assertIn("200", message)
                self.assertIn("GET", message)

    def test_malformed_list_call_tool_ok_not_true(self) -> None:
        api = _MockApi(body={"foo": "bar"})
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool(
                    "list_households",
                    {"profile": "cand", "base": "http://test"},
                )
            )
        payload = _payload(result)
        self.assertIsNot(payload.get("ok"), True)

    def test_list_meta_dropped_for_both_collections(self) -> None:
        cases = (
            (
                list_households,
                {},
                {"households": [], "meta": {"x": 1}},
                "households",
            ),
            (
                list_household_members,
                {"household_id": "hh1"},
                {"members": [], "meta": {"x": 1}},
                "members",
            ),
        )
        for func, arguments, body, key in cases:
            with self.subTest(key=key):
                api = _MockApi(body=body)
                if func is list_households:
                    result = func(api, profile="cand", base="http://test")
                else:
                    result = func(
                        api,
                        profile="cand",
                        base="http://test",
                        arguments=arguments,
                    )
                self.assertTrue(result["ok"])
                self.assertEqual(result[key], [])
                self.assertNotIn("meta", result)

    def test_argument_profile_trim(self) -> None:
        api = _MockApi(body={"households": []})
        with patch.object(
            server, "get_session", return_value=(api, "http://test")
        ) as get_session:
            out = server._handle_list_households({"profile": _PADDED_PROFILE})
        get_session.assert_called_with("cand", None)
        payload = _payload(out)
        self.assertEqual(payload["profile"], "cand")

    def test_argument_base_trim(self) -> None:
        api = _MockApi(body={"households": []})
        with patch.object(
            server, "get_session", return_value=(api, _TRIMMED_BASE)
        ) as get_session:
            out = server._handle_list_households(
                {"profile": "cand", "base": _PADDED_BASE}
            )
        get_session.assert_called_with("cand", _TRIMMED_BASE)
        payload = _payload(out)
        self.assertEqual(payload["base"], _TRIMMED_BASE)

    def test_session_profile_trim(self) -> None:
        api = _MockApi(body={"households": []})
        server._active_profile = _PADDED_PROFILE
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            out = server._handle_list_households({})
        payload = _payload(out)
        self.assertEqual(payload["profile"], "cand")

    def test_session_base_only_u0020_raises_without_http(self) -> None:
        api = _MockApi(body={"households": []})
        with patch.object(
            server, "get_session", return_value=(api, _SESSION_EMPTY_BASE)
        ):
            with self.assertRaises(ValueError):
                server._handle_list_households({"profile": "cand"})
        self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
