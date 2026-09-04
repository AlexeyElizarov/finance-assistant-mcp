"""Unit tests for FIN-355 clearing-documents MCP wrapping."""

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

from clearing_documents import (  # noqa: E402
    DOCUMENTS_PATH,
    create_clearing_document,
    create_clearing_document_item,
    create_clearing_documents,
    delete_clearing_document,
    delete_clearing_document_item,
    delete_clearing_documents,
    get_clearing_document,
    get_clearing_document_item,
    list_clearing_document_items,
    list_clearing_documents,
    patch_clearing_document,
    patch_clearing_document_item,
)

import server  # noqa: E402

_DOCUMENT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_ITEM_ID = "1c9e6679-7425-40de-944b-e07fc1f90ae7"
_CREDITOR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_DEBTOR = "0ed7530f-b054-42e7-babd-5fd541bea2b4"
_LINE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_LINE_F_DEBIT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_LINE_F_CREDIT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_SPACE = "\u0020"
_PADDED_PROFILE = f"{_SPACE}cand{_SPACE}"
_SESSION_EMPTY_BASE = _SPACE * 3
_TOOL_NAMES = (
    "list_clearing_documents",
    "get_clearing_document",
    "create_clearing_document",
    "create_clearing_documents",
    "patch_clearing_document",
    "delete_clearing_document",
    "delete_clearing_documents",
    "create_clearing_document_item",
    "list_clearing_document_items",
    "get_clearing_document_item",
    "patch_clearing_document_item",
    "delete_clearing_document_item",
)


class _MockApi:
    """Stub ApiClient capturing clearing-document HTTP calls."""

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

    @property
    def last_method(self) -> str | None:
        return self.calls[-1][0] if self.calls else None


def _tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.inputSchema


def _payload(result: list[Any]) -> dict[str, Any]:
    return json.loads(result[0].text)


def _sample_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "profile_id": "cand",
        "id": _ITEM_ID,
        "document_id": _DOCUMENT_ID,
        "line_id": None,
        "debit_credit_indicator": "debit",
        "clearing_amount": "10.00",
        "clearing_date": "2026-08-01",
    }
    item.update(overrides)
    return item


def _sample_document(**overrides: Any) -> dict[str, Any]:
    document = {
        "profile_id": "cand",
        "id": _DOCUMENT_ID,
        "document_type": "monetary_claim",
        "clearing_currency": "EUR",
        "clearing_rate": "1.00000",
        "creditor_subject_id": _CREDITOR,
        "debtor_subject_id": _DEBTOR,
        "status": "open",
        "status_date": "2026-08-01",
        "comment": None,
        "items": [_sample_item()],
    }
    document.update(overrides)
    return document


def _claim_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "debit_credit_indicator": "debit",
        "clearing_amount": "10.00",
        "clearing_date": "2026-08-01",
    }
    item.update(overrides)
    return item


def _claim_create_args(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "document_type": "monetary_claim",
        "creditor_subject_id": _CREDITOR,
        "debtor_subject_id": _DEBTOR,
        "clearing_currency": "EUR",
        "items": [_claim_item()],
    }
    arguments.update(overrides)
    return arguments


def _transfer_items() -> list[dict[str, str]]:
    return [
        {"debit_credit_indicator": "debit", "line_id": _LINE_F_DEBIT},
        {"debit_credit_indicator": "credit", "line_id": _LINE_F_CREDIT},
    ]


def _transfer_create_args(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "document_type": "internal_transfer",
        "items": _transfer_items(),
    }
    arguments.update(overrides)
    return arguments


def _create_ok(api: _MockApi, arguments: dict[str, Any]) -> dict[str, Any]:
    api.status = 201
    api.body = _sample_document()
    return create_clearing_document(
        api,
        profile="cand",
        base="http://test",
        arguments=arguments,
    )


class Fin355LibTests(unittest.TestCase):
    """Lib wrapping: body, query, bypass checks, D-08/D-09."""

    def test_create_forwards_document_type_without_injection(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(),
        )
        self.assertEqual(api.last_method, "POST")
        self.assertEqual(api.last_path, DOCUMENTS_PATH)
        self.assertIsNotNone(api.last_body)
        self.assertEqual(api.last_body["document_type"], "monetary_claim")
        self.assertNotIn("id", api.last_body)
        self.assertNotIn("profile", api.last_body)
        self.assertNotIn("allow_closed", api.last_body)

    def test_create_internal_transfer_items_without_amount_calls_http(self) -> None:
        api = _MockApi(
            status=201,
            body=_sample_document(document_type="internal_transfer"),
        )
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_transfer_create_args(),
        )
        items = (api.last_body or {})["items"]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertNotIn("clearing_amount", item)
            self.assertNotIn("clearing_date", item)
            self.assertIn("debit_credit_indicator", item)
            self.assertIn("line_id", item)

    def test_create_internal_transfer_with_creditor_calls_http(self) -> None:
        api = _MockApi(
            status=201,
            body=_sample_document(document_type="internal_transfer"),
        )
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_transfer_create_args(creditor_subject_id=_CREDITOR),
        )
        self.assertEqual((api.last_body or {})["creditor_subject_id"], _CREDITOR)

    def test_create_unknown_document_type_calls_http(self) -> None:
        api = _MockApi(status=201, body=_sample_document(document_type="not-a-type"))
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(document_type="not-a-type"),
        )
        self.assertEqual((api.last_body or {})["document_type"], "not-a-type")

    def test_batch_mixed_types_forwards_both(self) -> None:
        api = _MockApi(
            status=201,
            body={
                "clearing_documents": [
                    _sample_document(),
                    _sample_document(document_type="internal_transfer"),
                ]
            },
        )
        create_clearing_documents(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "clearing_documents": [
                    _claim_create_args(),
                    _transfer_create_args(),
                ]
            },
        )
        documents = (api.last_body or {})["clearing_documents"]
        self.assertEqual(documents[0]["document_type"], "monetary_claim")
        self.assertEqual(documents[1]["document_type"], "internal_transfer")
        self.assertEqual(api.last_path, f"{DOCUMENTS_PATH}/batch")

    def test_batch_element_without_document_type_raises(self) -> None:
        api = _MockApi()
        element = _claim_create_args()
        del element["document_type"]
        with self.assertRaises(ValueError):
            create_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={"clearing_documents": [element]},
            )
        self.assertEqual(api.calls, [])

    def test_batch_null_array_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={"clearing_documents": None},
            )
        self.assertEqual(api.calls, [])

    def test_list_omits_document_type_query(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        list_clearing_documents(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertEqual(api.last_path, DOCUMENTS_PATH)
        self.assertNotIn("document_type", api.last_path or "")

    def test_list_document_type_internal_transfer_query(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        list_clearing_documents(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_type": "internal_transfer"},
        )
        self.assertEqual(
            api.last_path,
            f"{DOCUMENTS_PATH}?document_type=internal_transfer",
        )

    def test_create_omits_line_id_when_absent(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(),
        )
        self.assertNotIn("line_id", (api.last_body or {})["items"][0])

    def test_create_line_id_null_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(items=[_claim_item(line_id=None)]),
            )
        self.assertEqual(api.calls, [])

    def test_create_document_type_null_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(document_type=None),
            )
        self.assertEqual(api.calls, [])

    def test_batch_element_document_type_null_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "clearing_documents": [_claim_create_args(document_type=None)]
                },
            )
        self.assertEqual(api.calls, [])

    def test_create_items_null_element_bypass_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(items=[None]),
            )
        self.assertEqual(api.calls, [])

    def test_batch_non_object_element_bypass_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={"clearing_documents": [42]},
            )
        self.assertEqual(api.calls, [])

    def test_create_claim_without_clearing_amount_calls_http(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        item = _claim_item()
        del item["clearing_amount"]
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(items=[item]),
        )
        self.assertNotIn("clearing_amount", (api.last_body or {})["items"][0])

    def test_empty_batch_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={"clearing_documents": []},
            )
        self.assertEqual(api.calls, [])

    def test_empty_ids_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            delete_clearing_documents(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": []},
            )
        self.assertEqual(api.calls, [])

    def test_unknown_root_id_bypass_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(id=_DOCUMENT_ID),
            )
        self.assertIn("id", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_unknown_nested_item_key_bypass_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(items=[_claim_item(foo="bar")]),
            )
        self.assertEqual(api.calls, [])

    def test_create_omits_allow_closed_query(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(),
        )
        self.assertNotIn("allow_closed", api.last_path or "")

    def test_create_allow_closed_true_query(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(allow_closed=True),
        )
        self.assertEqual(api.last_path, f"{DOCUMENTS_PATH}?allow_closed=true")
        self.assertNotIn("allow_closed", api.last_body or {})

    def test_create_empty_items_calls_http(self) -> None:
        api = _MockApi(status=201, body=_sample_document(items=[]))
        create_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments=_claim_create_args(items=[]),
        )
        self.assertEqual((api.last_body or {})["items"], [])

    def test_list_status_spaces_not_stripped(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        padded = f"{_SPACE}open{_SPACE}"
        list_clearing_documents(
            api,
            profile="cand",
            base="http://test",
            arguments={"status": padded},
        )
        self.assertIn("status=", api.last_path or "")
        self.assertIn("open", api.last_path or "")
        self.assertNotEqual(api.last_path, f"{DOCUMENTS_PATH}?status=open")

    def test_get_document_id_three_spaces_calls_http(self) -> None:
        api = _MockApi(body=_sample_document())
        get_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _SPACE * 3},
        )
        self.assertEqual(len(api.calls), 1)
        self.assertTrue((api.last_path or "").startswith(f"{DOCUMENTS_PATH}/"))

    def test_clearing_amount_number_bypass_raises(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(items=[_claim_item(clearing_amount=10.0)]),
            )
        self.assertEqual(api.calls, [])

    def test_create_http_200_is_unexpected(self) -> None:
        api = _MockApi(status=200, body=_sample_document())
        with self.assertRaises(RuntimeError) as ctx:
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(),
            )
        message = str(ctx.exception)
        self.assertIn("200", message)
        self.assertIn("POST", message)

    def test_create_http_201_non_object_is_unexpected(self) -> None:
        api = _MockApi(status=201, body=["not-an-object"])
        with self.assertRaises(RuntimeError) as ctx:
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(),
            )
        message = str(ctx.exception)
        self.assertIn("201", message)
        self.assertIn("POST", message)

    def test_list_missing_collection_is_unexpected(self) -> None:
        api = _MockApi(body={"foo": []})
        with self.assertRaises(RuntimeError) as ctx:
            list_clearing_documents(
                api, profile="cand", base="http://test", arguments={}
            )
        message = str(ctx.exception)
        self.assertIn("200", message)
        self.assertIn("GET", message)

    def test_list_non_array_collection_is_unexpected(self) -> None:
        api = _MockApi(body={"clearing_documents": "not-an-array"})
        with self.assertRaises(RuntimeError) as ctx:
            list_clearing_documents(
                api, profile="cand", base="http://test", arguments={}
            )
        message = str(ctx.exception)
        self.assertIn("200", message)
        self.assertIn("GET", message)

    def test_list_drops_meta(self) -> None:
        api = _MockApi(body={"clearing_documents": [], "meta": {"x": 1}})
        result = list_clearing_documents(
            api, profile="cand", base="http://test", arguments={}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["clearing_documents"], [])
        self.assertNotIn("meta", result)

    def test_delete_204_ignores_http_body(self) -> None:
        api = _MockApi(status=204, body={"id": "x"})
        result = delete_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _DOCUMENT_ID},
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("clearing_document", result)
        self.assertEqual(result["profile"], "cand")
        self.assertEqual(result["base"], "http://test")

    def test_http_422_preserves_error_code_and_details(self) -> None:
        details = [{"loc": ["body", "document_type"], "msg": "Field required"}]
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "validation_error",
                    "message": "Ошибка валидации входных данных.",
                    "details": details,
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(),
            )
        message = str(ctx.exception)
        self.assertIn("validation_error", message)
        self.assertIn("details=", message)
        self.assertIn("document_type", message)

    def test_http_404_preserves_error_code(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "clearing_document_not_found",
                    "message": "missing",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments={"document_id": _DOCUMENT_ID},
            )
        self.assertIn("clearing_document_not_found", str(ctx.exception))

    def test_http_500_without_error_code_is_unexpected(self) -> None:
        api = _MockApi(status=500, body={"oops": True})
        with self.assertRaises(RuntimeError) as ctx:
            create_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments=_claim_create_args(),
            )
        message = str(ctx.exception)
        self.assertIn("500", message)
        self.assertIn("POST", message)
        self.assertNotIn("oops", message.split("HTTP", 1)[0])

    def test_get_wraps_single_document(self) -> None:
        api = _MockApi(body=_sample_document())
        result = get_clearing_document(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _DOCUMENT_ID},
        )
        self.assertEqual(result["clearing_document"]["id"], _DOCUMENT_ID)
        self.assertNotIn("remainder", result["clearing_document"])

    def test_item_crud_paths(self) -> None:
        api = _MockApi(status=201, body=_sample_item())
        created = create_clearing_document_item(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "document_id": _DOCUMENT_ID,
                "debit_credit_indicator": "credit",
                "clearing_amount": "40.00",
                "clearing_date": "2026-08-02",
            },
        )
        self.assertEqual(created["clearing_document_item"]["id"], _ITEM_ID)
        self.assertEqual(
            api.last_path,
            f"{DOCUMENTS_PATH}/{_DOCUMENT_ID}/items",
        )
        self.assertNotIn("line_id", api.last_body or {})

        api.status = 200
        api.body = {"items": [_sample_item()]}
        listed = list_clearing_document_items(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _DOCUMENT_ID},
        )
        self.assertEqual(listed["items"][0]["id"], _ITEM_ID)

        api.body = _sample_item()
        got = get_clearing_document_item(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _DOCUMENT_ID, "item_id": _ITEM_ID},
        )
        self.assertEqual(got["clearing_document_item"]["id"], _ITEM_ID)

        api.body = _sample_item(line_id=_LINE_A)
        patched = patch_clearing_document_item(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "document_id": _DOCUMENT_ID,
                "item_id": _ITEM_ID,
                "line_id": _LINE_A,
            },
        )
        self.assertEqual(patched["clearing_document_item"]["line_id"], _LINE_A)
        self.assertEqual(api.last_body, {"line_id": _LINE_A})

        api.status = 204
        api.body = b""
        deleted = delete_clearing_document_item(
            api,
            profile="cand",
            base="http://test",
            arguments={"document_id": _DOCUMENT_ID, "item_id": _ITEM_ID},
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("clearing_document_item", deleted)

    def test_patch_document_status_required(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            patch_clearing_document(
                api,
                profile="cand",
                base="http://test",
                arguments={"document_id": _DOCUMENT_ID},
            )
        self.assertEqual(api.calls, [])


class Fin355SchemaTests(unittest.TestCase):
    """JSON Schema MCP (D-05)."""

    def test_twelve_tools_registered(self) -> None:
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertTrue(set(_TOOL_NAMES).issubset(names))
        self.assertIn("list_internal_transfer_matches", names)

    def test_create_schema_allows_both_branches_without_enum(self) -> None:
        schema = _tool_schema("create_clearing_document")
        self.assertNotIn("required", schema)
        self.assertNotIn("oneOf", schema)
        self.assertNotIn("anyOf", schema)
        jsonschema.validate(_claim_create_args(), schema)
        jsonschema.validate(_transfer_create_args(), schema)
        jsonschema.validate(_claim_create_args(document_type="not-a-type"), schema)
        jsonschema.validate(_claim_create_args(document_type=None), schema)
        jsonschema.validate(_claim_create_args(items=[]), schema)

    def test_create_items_null_element_schema_error(self) -> None:
        schema = _tool_schema("create_clearing_document")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(_claim_create_args(items=[None]), schema)

    def test_create_rejects_remainder(self) -> None:
        schema = _tool_schema("create_clearing_document")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(_claim_create_args(remainder="1.00"), schema)

    def test_list_rejects_as_of(self) -> None:
        schema = _tool_schema("list_clearing_documents")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"as_of": "2026-08-01"}, schema)

    def test_list_rejects_allow_closed(self) -> None:
        schema = _tool_schema("list_clearing_documents")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"allow_closed": True}, schema)

    def test_batch_rejects_null_element(self) -> None:
        schema = _tool_schema("create_clearing_documents")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"clearing_documents": [None]}, schema)

    def test_ids_reject_null_element(self) -> None:
        schema = _tool_schema("delete_clearing_documents")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"ids": [None]}, schema)

    def test_allow_closed_rejects_null(self) -> None:
        schema = _tool_schema("create_clearing_document")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(_claim_create_args(allow_closed=None), schema)


class Fin355HandlerTests(unittest.TestCase):
    """Handler session resolution (D-04)."""

    def setUp(self) -> None:
        server._active_profile = None
        server._active_base = None

    def tearDown(self) -> None:
        server._active_profile = None
        server._active_base = None

    def test_session_profile_trim(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        server._active_profile = _PADDED_PROFILE
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            out = server._handle_list_clearing_documents({})
        payload = _payload(out)
        self.assertEqual(payload["profile"], "cand")
        self.assertTrue(payload["ok"])

    def test_session_base_only_u0020_raises_without_http(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        with patch.object(
            server, "get_session", return_value=(api, _SESSION_EMPTY_BASE)
        ):
            with self.assertRaises(ValueError):
                server._handle_list_clearing_documents({"profile": "cand"})
        self.assertEqual(api.calls, [])

    def test_profile_null_bypass_raises_without_http(self) -> None:
        api = _MockApi(status=201, body=_sample_document())
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            with self.assertRaises(ValueError):
                server._handle_create_clearing_document(
                    _claim_create_args(profile=None, base="http://test")
                )
        self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
