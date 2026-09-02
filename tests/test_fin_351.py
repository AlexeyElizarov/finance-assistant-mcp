"""Unit tests for FIN-351 internal-transfer match MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonschema

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from internal_transfer_matches import (  # noqa: E402
    MATCHES_PATH,
    create_internal_transfer_match,
    create_internal_transfer_matches,
    delete_internal_transfer_match,
    delete_internal_transfer_matches,
    get_internal_transfer_match,
    list_internal_transfer_matches,
    resolve_base,
    resolve_profile,
)

import server  # noqa: E402

_UUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_PADDED_UUID = f"  {_UUID}  "
_WRITE_HANDLERS = (
    (
        server._handle_create_internal_transfer_match,
        {"debit_line_ids": ["d1"], "credit_line_ids": ["c1"]},
        201,
        {"id": _UUID},
    ),
    (
        server._handle_create_internal_transfer_matches,
        {
            "internal_transfer_matches": [
                {"debit_line_ids": ["d1"], "credit_line_ids": ["c1"]}
            ]
        },
        201,
        {"internal_transfer_matches": [{"id": _UUID}]},
    ),
    (
        server._handle_delete_internal_transfer_match,
        {"match_id": _UUID},
        204,
        b"",
    ),
    (
        server._handle_delete_internal_transfer_matches,
        {"ids": [_UUID]},
        204,
        b"",
    ),
)


def _sample_match(**overrides: Any) -> dict[str, Any]:
    match = {
        "id": _UUID,
        "clearing_currency": "RUB",
        "clearing_rate": "98.29048",
        "debit_line_ids": ["d1", "d2"],
        "credit_line_ids": ["c1", "c2"],
        "debit_clearing_amounts": ["10.00", "20.00"],
        "credit_clearing_amounts": ["15.00", "15.00"],
    }
    match.update(overrides)
    return match


def _tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.inputSchema


def _payload(result: list[Any]) -> dict[str, Any]:
    return json.loads(result[0].text)


class _MockApi:
    """Stub ApiClient capturing match API calls."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body: Any = {} if body is None else body
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
    def last_path(self) -> str | None:
        return self.calls[-1][1] if self.calls else None

    @property
    def last_body(self) -> dict[str, Any] | None:
        return self.calls[-1][2] if self.calls else None


def _call_handler(
    handler: Any,
    arguments: dict[str, Any],
    api: _MockApi,
    resolved_base: str = "http://test",
) -> tuple[list[Any], Any]:
    with patch.object(
        server, "get_session", return_value=(api, resolved_base)
    ) as get_session:
        result = handler(arguments)
        return result, get_session


class SchemaTests(unittest.TestCase):
    """MCP schemas reject undeclared keys and types."""

    def test_six_tools_registered(self) -> None:
        expected = {
            "list_internal_transfer_matches",
            "get_internal_transfer_match",
            "create_internal_transfer_match",
            "create_internal_transfer_matches",
            "delete_internal_transfer_match",
            "delete_internal_transfer_matches",
        }
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertTrue(expected.issubset(names))

    def test_create_rejects_missing_and_undeclared(self) -> None:
        schema = _tool_schema("create_internal_transfer_match")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"credit_line_ids": ["c1"]}, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "debit_line_ids": ["d1"],
                    "credit_line_ids": ["c1"],
                    "clearing_rate": "1",
                },
                schema,
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "debit_line_ids": ["d1"],
                    "credit_line_ids": ["c1"],
                    "id": _UUID,
                },
                schema,
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"debit_line_ids": None, "credit_line_ids": ["c1"]},
                schema,
            )

    def test_read_rejects_allow_closed(self) -> None:
        list_schema = _tool_schema("list_internal_transfer_matches")
        get_schema = _tool_schema("get_internal_transfer_match")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"allow_closed": False}, list_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"match_id": _UUID, "allow_closed": True},
                get_schema,
            )

    def test_batch_item_rejects_allow_closed(self) -> None:
        schema = _tool_schema("create_internal_transfer_matches")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "internal_transfer_matches": [
                        {
                            "debit_line_ids": ["d1"],
                            "credit_line_ids": ["c1"],
                            "allow_closed": True,
                        }
                    ]
                },
                schema,
            )

    def test_non_string_profile_rejected_by_schema(self) -> None:
        schema = _tool_schema("list_internal_transfer_matches")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"profile": 1}, schema)


class ProfileBaseTests(unittest.TestCase):
    """Effective profile and base after schema."""

    def test_resolve_helpers(self) -> None:
        self.assertEqual(resolve_profile({}), "prod")
        self.assertEqual(resolve_profile({"profile": "   "}), "prod")
        self.assertEqual(resolve_profile({"profile": " cand "}), "cand")
        self.assertIsNone(resolve_base({}))
        self.assertIsNone(resolve_base({"base": "   "}))
        self.assertEqual(
            resolve_base({"base": " http://127.0.0.1:9 "}),
            "http://127.0.0.1:9",
        )

    def test_missing_profile_requests_prod(self) -> None:
        api = _MockApi(body={"internal_transfer_matches": []})
        result, get_session = _call_handler(
            server._handle_list_internal_transfer_matches,
            {},
            api,
            resolved_base="http://prod-default",
        )
        get_session.assert_called_once_with("prod", None)
        payload = _payload(result)
        self.assertEqual(payload["profile"], "prod")
        self.assertEqual(payload["base"], "http://prod-default")

    def test_padded_profile_and_cand_default_base(self) -> None:
        api = _MockApi(body={"internal_transfer_matches": []})
        result, get_session = _call_handler(
            server._handle_list_internal_transfer_matches,
            {"profile": " cand "},
            api,
            resolved_base="http://cand-default",
        )
        get_session.assert_called_once_with("cand", None)
        payload = _payload(result)
        self.assertEqual(payload["profile"], "cand")
        self.assertEqual(payload["base"], "http://cand-default")

    def test_blank_base_uses_profile_default(self) -> None:
        api = _MockApi(body={"internal_transfer_matches": []})
        _, get_session = _call_handler(
            server._handle_list_internal_transfer_matches,
            {"profile": "cand", "base": "   "},
            api,
            resolved_base="http://cand-default",
        )
        get_session.assert_called_once_with("cand", None)

    def test_padded_base_override(self) -> None:
        api = _MockApi(body={"internal_transfer_matches": []})
        result, get_session = _call_handler(
            server._handle_list_internal_transfer_matches,
            {"profile": "cand", "base": " http://127.0.0.1:9 "},
            api,
            resolved_base="http://127.0.0.1:9",
        )
        get_session.assert_called_once_with("cand", "http://127.0.0.1:9")
        payload = _payload(result)
        self.assertEqual(payload["base"], "http://127.0.0.1:9")


class HandlerValidationTests(unittest.TestCase):
    """ValueError before session and HTTP."""

    def test_empty_match_id(self) -> None:
        api = _MockApi()
        with patch.object(server, "get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_get_internal_transfer_match({"match_id": ""})
            with self.assertRaises(ValueError):
                server._handle_get_internal_transfer_match({"match_id": "   "})
            get_session.assert_not_called()
        self.assertEqual(api.calls, [])

    def test_whitespace_line_id(self) -> None:
        with patch.object(server, "get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_list_internal_transfer_matches({"line_id": "   "})
            get_session.assert_not_called()

    def test_empty_batch_and_whitespace_ids(self) -> None:
        with patch.object(server, "get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_create_internal_transfer_matches(
                    {"internal_transfer_matches": []}
                )
            with self.assertRaises(ValueError):
                server._handle_delete_internal_transfer_matches({"ids": []})
            with self.assertRaises(ValueError):
                server._handle_delete_internal_transfer_matches({"ids": ["   "]})
            get_session.assert_not_called()


class HttpContractTests(unittest.TestCase):
    """Request composition, success codes, and error pass-through."""

    def test_create_passthrough_both_sides(self) -> None:
        api = _MockApi(status=201, body=_sample_match())
        args = {
            "debit_line_ids": ["d1", "d2"],
            "credit_line_ids": ["c1", "c2"],
        }
        result = create_internal_transfer_match(
            api, profile="cand", base="http://test", arguments=args
        )
        self.assertEqual(
            api.last_body,
            {"debit_line_ids": ["d1", "d2"], "credit_line_ids": ["c1", "c2"]},
        )
        self.assertEqual(api.calls[0][:2], ("POST", MATCHES_PATH))
        self.assertNotIn("allow_closed", api.last_path or "")
        self.assertEqual(result["internal_transfer_match"]["id"], _UUID)

    def test_allow_closed_query_on_all_write_tools(self) -> None:
        for handler, base_args, status, body in _WRITE_HANDLERS:
            with self.subTest(handler=handler.__name__, state="omit"):
                api = _MockApi(status=status, body=body)
                _call_handler(handler, dict(base_args), api)
                self.assertNotIn("allow_closed", api.last_path or "")
            with self.subTest(handler=handler.__name__, state="false"):
                api = _MockApi(status=status, body=body)
                args = dict(base_args)
                args["allow_closed"] = False
                _call_handler(handler, args, api)
                self.assertIn("allow_closed=false", api.last_path or "")
            with self.subTest(handler=handler.__name__, state="true"):
                api = _MockApi(status=status, body=body)
                args = dict(base_args)
                args["allow_closed"] = True
                _call_handler(handler, args, api)
                self.assertIn("allow_closed=true", api.last_path or "")

    def test_list_empty_and_line_id_strip(self) -> None:
        api = _MockApi(body={"internal_transfer_matches": []})
        result = list_internal_transfer_matches(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertEqual(result["internal_transfer_matches"], [])
        self.assertTrue(result["ok"])
        result = list_internal_transfer_matches(
            api,
            profile="cand",
            base="http://test",
            arguments={"line_id": "  line-a  "},
        )
        self.assertIn("line_id=line-a", api.last_path or "")
        self.assertNotIn("%20", (api.last_path or "").split("line_id=")[-1])

    def test_delete_204_has_no_entity(self) -> None:
        api = _MockApi(status=204, body=b"")
        result = delete_internal_transfer_match(
            api,
            profile="cand",
            base="http://test",
            arguments={"match_id": _UUID},
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("internal_transfer_match", result)

    def test_padded_match_id_and_ids_go_as_is(self) -> None:
        api = _MockApi(
            status=422,
            body={"error": {"code": "validation_error", "details": {}}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_internal_transfer_match(
                api,
                profile="cand",
                base="http://test",
                arguments={"match_id": _PADDED_UUID},
            )
        encoded = urllib.parse.quote(_PADDED_UUID, safe="")
        self.assertIn(encoded, api.last_path or "")
        self.assertIn("validation_error", str(ctx.exception))
        self.assertFalse(hasattr(ctx.exception, "details"))

        api.calls.clear()
        with self.assertRaises(RuntimeError) as ctx2:
            delete_internal_transfer_matches(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": [_PADDED_UUID]},
            )
        self.assertEqual(api.last_body, {"ids": [_PADDED_UUID]})
        self.assertIn("validation_error", str(ctx2.exception))

    def test_batch_error_keeps_index_in_text(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "invalid_internal_transfer_account_same",
                    "message": "same",
                    "details": {"index": 1},
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_internal_transfer_matches(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "internal_transfer_matches": [
                        {"debit_line_ids": ["d1"], "credit_line_ids": ["c1"]},
                        {"debit_line_ids": ["d2"], "credit_line_ids": ["c2"]},
                    ]
                },
            )
        text = str(ctx.exception)
        self.assertIn("invalid_internal_transfer_account_same", text)
        self.assertIn('"index": 1', text)
        self.assertFalse(hasattr(ctx.exception, "details"))

    def test_unexpected_2xx(self) -> None:
        api = _MockApi(status=201, body={"id": _UUID})
        with self.assertRaises(RuntimeError) as ctx:
            get_internal_transfer_match(
                api,
                profile="cand",
                base="http://test",
                arguments={"match_id": _UUID},
            )
        self.assertIn(_UUID, str(ctx.exception))

        api.status = 200
        api.body = _sample_match()
        with self.assertRaises(RuntimeError):
            create_internal_transfer_match(
                api,
                profile="cand",
                base="http://test",
                arguments={"debit_line_ids": ["d1"], "credit_line_ids": ["c1"]},
            )

        api.status = 200
        api.body = {"ok": True}
        with self.assertRaises(RuntimeError):
            delete_internal_transfer_match(
                api,
                profile="cand",
                base="http://test",
                arguments={"match_id": _UUID},
            )

    def test_network_errors_not_wrapped(self) -> None:
        for exc in (ConnectionError("down"), TimeoutError("timeout")):
            with self.subTest(exc=type(exc).__name__):
                api = _MockApi(raise_on_request=exc)
                with self.assertRaises(type(exc)):
                    list_internal_transfer_matches(
                        api, profile="cand", base="http://test", arguments={}
                    )

    def test_empty_create_arrays_reach_http(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "invalid_internal_transfer_sides",
                    "details": {},
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_internal_transfer_match(
                api,
                profile="cand",
                base="http://test",
                arguments={"debit_line_ids": [], "credit_line_ids": ["c1"]},
            )
        self.assertEqual(api.last_body["debit_line_ids"], [])
        self.assertIn("invalid_internal_transfer_sides", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
