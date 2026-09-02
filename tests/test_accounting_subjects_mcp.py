"""Unit tests for FIN-366 accounting subjects MCP tools."""

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

from accounting_subjects import (  # noqa: E402
    ACCOUNTING_SUBJECTS_PATH,
    create_accounting_subject,
    create_accounting_subjects,
    delete_accounting_subject,
    delete_accounting_subjects,
    get_accounting_subject,
    list_accounting_subjects,
    patch_accounting_subject,
    patch_accounting_subjects,
    resolve_base,
    resolve_profile,
)

import server  # noqa: E402

_SUBJECT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_SUBJECT_ID_2 = "0ed7530f-b054-42e7-babd-5fd541bea2b4"


def _sample_subject(**overrides: Any) -> dict[str, Any]:
    subject = {
        "id": _SUBJECT_ID,
        "subject_type": "person",
        "display_name": "Arkady",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
    }
    subject.update(overrides)
    return subject


def _tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.inputSchema


class _MockApi:
    """Stub ApiClient capturing accounting-subject API calls."""

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


class AccountingSubjectsLibTests(unittest.TestCase):
    """Lib helpers for eight CRUD/batch tools."""

    def test_create_list_get_patch_delete(self) -> None:
        api = _MockApi(status=201, body=_sample_subject())
        created = create_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"subject_type": "person", "display_name": "Arkady"},
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["accounting_subject"]["id"], _SUBJECT_ID)
        self.assertEqual(
            api.last_body,
            {"subject_type": "person", "display_name": "Arkady"},
        )
        self.assertNotIn("household_id", api.last_body or {})

        api.status = 200
        api.body = {"accounting_subjects": [_sample_subject()]}
        listed = list_accounting_subjects(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertEqual(len(listed["accounting_subjects"]), 1)
        self.assertEqual(api.last_path, ACCOUNTING_SUBJECTS_PATH)

        api.body = _sample_subject()
        got = get_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"subject_id": _SUBJECT_ID},
        )
        self.assertEqual(got["accounting_subject"]["display_name"], "Arkady")

        api.body = _sample_subject(display_name="Renamed")
        patched = patch_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"subject_id": _SUBJECT_ID, "display_name": "Renamed"},
        )
        self.assertEqual(patched["accounting_subject"]["display_name"], "Renamed")

        api.status = 204
        api.body = b""
        deleted = delete_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"subject_id": _SUBJECT_ID},
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("accounting_subject", deleted)

    def test_list_subject_type_filter_omitted(self) -> None:
        api = _MockApi(body={"accounting_subjects": []})
        list_accounting_subjects(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertEqual(api.last_path, ACCOUNTING_SUBJECTS_PATH)
        self.assertNotIn("subject_type", api.last_path or "")

    def test_create_with_household_id_null(self) -> None:
        api = _MockApi(status=201, body=_sample_subject(subject_type="group"))
        with self.assertRaises(ValueError):
            create_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "subject_type": "group",
                    "display_name": "Home",
                    "household_id": None,
                },
            )
        self.assertEqual(api.calls, [])

    def test_create_without_display_name(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"subject_type": "person"},
            )
        self.assertEqual(api.calls, [])

    def test_patch_without_display_name(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            patch_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"subject_id": _SUBJECT_ID},
            )
        self.assertEqual(api.calls, [])

    def test_batch_create_patch_delete(self) -> None:
        api = _MockApi(
            status=201,
            body={
                "accounting_subjects": [
                    _sample_subject(),
                    _sample_subject(id=_SUBJECT_ID_2, display_name="B"),
                ]
            },
        )
        result = create_accounting_subjects(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "accounting_subjects": [
                    {"subject_type": "person", "display_name": "A"},
                    {"subject_type": "person", "display_name": "B"},
                ]
            },
        )
        self.assertEqual(len(result["accounting_subjects"]), 2)
        self.assertEqual(api.calls[0][:2], ("POST", f"{ACCOUNTING_SUBJECTS_PATH}/batch"))

        api.status = 200
        api.body = {
            "accounting_subjects": [
                _sample_subject(display_name="A2"),
                _sample_subject(id=_SUBJECT_ID_2, display_name="B2"),
            ]
        }
        patch_accounting_subjects(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "accounting_subjects": [
                    {"id": _SUBJECT_ID, "display_name": "A2"},
                    {"id": _SUBJECT_ID_2, "display_name": "B2"},
                ]
            },
        )
        self.assertEqual(api.calls[1][:2], ("PATCH", ACCOUNTING_SUBJECTS_PATH))

        api.status = 204
        api.body = b""
        delete_accounting_subjects(
            api,
            profile="cand",
            base="http://test",
            arguments={"ids": [_SUBJECT_ID, _SUBJECT_ID_2]},
        )
        self.assertEqual(api.last_body, {"ids": [_SUBJECT_ID, _SUBJECT_ID_2]})

    def test_empty_batch_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_accounting_subjects(
                api,
                profile="cand",
                base="http://test",
                arguments={"accounting_subjects": []},
            )
        self.assertEqual(api.calls, [])

    def test_batch_null_array_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_accounting_subjects(
                api,
                profile="cand",
                base="http://test",
                arguments={"accounting_subjects": None},
            )
        self.assertEqual(api.calls, [])

    def test_batch_domain_error_with_index(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "invalid_subject_type",
                    "message": "bad type",
                    "details": {"index": 1},
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_accounting_subjects(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "accounting_subjects": [
                        {"subject_type": "person", "display_name": "Ok"},
                        {"subject_type": "foobar", "display_name": "Bad"},
                    ]
                },
            )
        self.assertIn("invalid_subject_type", str(ctx.exception))
        self.assertIn('"index": 1', str(ctx.exception))

    def test_list_envelope_unwrapped(self) -> None:
        api = _MockApi(body={"accounting_subjects": [_sample_subject()]})
        result = list_accounting_subjects(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertIsInstance(result["accounting_subjects"], list)
        nested = result.get("accounting_subjects", {})
        if isinstance(nested, dict):
            self.fail("double envelope accounting_subjects.accounting_subjects")

    def test_errors_passthrough(self) -> None:
        api = _MockApi(
            status=404,
            body={"error": {"code": "subject_not_found", "message": "missing"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"subject_id": _SUBJECT_ID},
            )
        self.assertIn("subject_not_found", str(ctx.exception))

        api.status = 409
        api.body = {"error": {"code": "subject_in_use", "message": "in use"}}
        with self.assertRaises(RuntimeError) as ctx2:
            delete_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"subject_id": _SUBJECT_ID},
            )
        self.assertIn("subject_in_use", str(ctx2.exception))

        api.status = 503
        api.body = {
            "error": {"code": "accounting_subject_store_busy", "message": "busy"}
        }
        with self.assertRaises(RuntimeError) as ctx3:
            create_accounting_subjects(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "accounting_subjects": [
                        {"subject_type": "person", "display_name": "A"}
                    ]
                },
            )
        self.assertIn("accounting_subject_store_busy", str(ctx3.exception))

    def test_network_error_not_wrapped(self) -> None:
        api = _MockApi(raise_on_request=OSError("network down"))
        with self.assertRaises(OSError):
            list_accounting_subjects(
                api, profile="cand", base="http://test", arguments={}
            )

    def test_whitespace_display_name_passthrough(self) -> None:
        api = _MockApi(status=201, body=_sample_subject(display_name=" "))
        create_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"subject_type": "person", "display_name": " "},
        )
        self.assertEqual(api.last_body, {"subject_type": "person", "display_name": " "})


class AccountingSubjectsSchemaTests(unittest.TestCase):
    """MCP schemas per FIN-366 D-24."""

    def test_eight_crud_tools_registered(self) -> None:
        expected = {
            "list_accounting_subjects",
            "get_accounting_subject",
            "create_accounting_subject",
            "create_accounting_subjects",
            "patch_accounting_subject",
            "patch_accounting_subjects",
            "delete_accounting_subject",
            "delete_accounting_subjects",
        }
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertTrue(expected.issubset(names))

    def test_create_rejects_extra_id(self) -> None:
        schema = _tool_schema("create_accounting_subject")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "subject_type": "person",
                    "display_name": "Arkady",
                    "id": _SUBJECT_ID,
                },
                schema,
            )

    def test_batch_item_rejects_foo(self) -> None:
        schema = _tool_schema("create_accounting_subjects")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "accounting_subjects": [
                        {
                            "subject_type": "person",
                            "display_name": "Arkady",
                            "foo": "bar",
                        }
                    ]
                },
                schema,
            )

    def test_batch_rejects_string_element(self) -> None:
        schema = _tool_schema("create_accounting_subjects")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"accounting_subjects": ["abc"]}, schema)


class AccountingSubjectsHandlerTests(unittest.TestCase):
    """Handler path and session resolution."""

    def test_resolve_profile_base(self) -> None:
        self.assertEqual(resolve_profile({}), "prod")
        self.assertEqual(resolve_profile({"profile": "cand"}), "cand")
        with self.assertRaises(ValueError):
            resolve_profile({"profile": None})
        with self.assertRaises(ValueError):
            resolve_profile({"profile": "   "})
        with self.assertRaises(ValueError):
            resolve_base({"base": None})

    def test_create_handler(self) -> None:
        api = _MockApi(status=201, body=_sample_subject())
        with patch("server.get_session", return_value=(api, "http://test")):
            out = server._handle_create_accounting_subject(
                {"subject_type": "person", "display_name": "Arkady"}
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["accounting_subject"]["id"], _SUBJECT_ID)

    def test_empty_batch_handler_no_session(self) -> None:
        with patch("server.get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_create_accounting_subjects(
                    {"accounting_subjects": []}
                )
            get_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
