"""Unit tests for FIN-286 / FIN-313 payment means MCP tools."""

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

from payment_means import (  # noqa: E402
    create_payment_instrument,
    create_payment_instruments,
    create_payment_means_fund_assignment,
    create_payment_means_fund_assignments,
    delete_payment_instrument,
    delete_payment_instruments,
    delete_payment_means_fund_assignment,
    delete_payment_means_fund_assignments,
    get_payment_instrument,
    get_payment_means_fund_assignment,
    list_payment_instruments,
    list_payment_means_fund_assignments,
    patch_payment_instrument,
    patch_payment_instruments,
    patch_payment_means_fund_assignment,
    patch_payment_means_fund_assignments,
)

_INSTRUMENT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_INSTRUMENT_ID_2 = "0ed7530f-b054-42e7-babd-5fd541bea2b4"
_ASSIGNMENT_ID = "a1111111-1111-4111-8111-111111111111"
_ASSIGNMENT_ID_2 = "b2222222-2222-4222-8222-222222222222"


def _sample_instrument(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": _INSTRUMENT_ID,
        "bank_account_id": "acc-c24",
        "display_name": "C24 Mastercard",
        "instrument_type": "card",
        "payment_network": "mastercard",
        "settlement_class": None,
        "pan_last4": None,
        "holder_id": "aleksey",
        "valid_from": "2026-01",
        "valid_to": None,
        "issuer_expiry": None,
        "created_at": "2026-08-09T10:00:00Z",
        "updated_at": "2026-08-09T10:00:00Z",
    }
    item.update(overrides)
    return item


def _sample_assignment(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": _ASSIGNMENT_ID,
        "means_type": "payment_instrument",
        "means_id": _INSTRUMENT_ID,
        "fund_id": "fund-joint",
        "valid_from": "2026-07-08",
        "valid_to": None,
        "created_at": "2026-08-09T10:00:00Z",
        "updated_at": "2026-08-09T10:00:00Z",
    }
    item.update(overrides)
    return item


class _MockApi:
    """Stub ApiClient capturing payment-means API calls."""

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


def _reject_undeclared_properties(
    schema: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    """Raise ValueError when payload has keys outside a closed schema."""
    if schema.get("additionalProperties") is not False:
        raise AssertionError("schema must set additionalProperties false")
    extra = sorted(set(arguments) - set(schema.get("properties", {})))
    if extra:
        raise ValueError(f"undeclared properties: {extra}")


class InstrumentsLibTests(unittest.TestCase):
    """Lib helpers for eight payment-instrument tools."""

    def test_create_list_get(self) -> None:
        api = _MockApi(status=201, body=_sample_instrument())
        created = create_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": "acc-c24",
                "display_name": "C24 Mastercard",
                "instrument_type": "card",
                "payment_network": "mastercard",
                "holder_id": "aleksey",
                "valid_from": "2026-01",
                "valid_to": None,
            },
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["payment_instrument"]["id"], _INSTRUMENT_ID)
        self.assertEqual(api.last_body["valid_to"], None)

        api.status = 200
        api.body = {"payment_instruments": [_sample_instrument()]}
        listed = list_payment_instruments(api, profile="cand", base="http://test")
        self.assertEqual(len(listed["payment_instruments"]), 1)

        listed_f = list_payment_instruments(
            api,
            profile="cand",
            base="http://test",
            arguments={"bank_account_id": "acc-c24"},
        )
        self.assertEqual(len(listed_f["payment_instruments"]), 1)
        self.assertIn("bank_account_id=acc-c24", api.calls[-1][1])

        api.body = _sample_instrument()
        got = get_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={"instrument_id": _INSTRUMENT_ID},
        )
        self.assertEqual(got["payment_instrument"]["display_name"], "C24 Mastercard")

    def test_get_not_found_and_validation(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "payment_instrument_not_found",
                    "message": "not found",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": _INSTRUMENT_ID},
            )
        self.assertIn("payment_instrument_not_found", str(ctx.exception))

        api.status = 422
        api.body = {"error": {"code": "validation_error", "message": "bad"}}
        with self.assertRaises(RuntimeError) as ctx2:
            get_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": "not-a-uuid"},
            )
        self.assertIn("validation_error", str(ctx2.exception))

    def test_patch_and_pre_http(self) -> None:
        api = _MockApi(body=_sample_instrument(display_name="Renamed", payment_network=None))
        result = patch_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "instrument_id": _INSTRUMENT_ID,
                "display_name": "Renamed",
                "payment_network": None,
            },
        )
        self.assertEqual(result["payment_instrument"]["display_name"], "Renamed")
        self.assertEqual(api.last_body, {"display_name": "Renamed", "payment_network": None})

        with self.assertRaises(ValueError):
            patch_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": _INSTRUMENT_ID},
            )
        self.assertEqual(len(api.calls), 1)

    def test_batch_create_patch_delete(self) -> None:
        api = _MockApi(
            status=201,
            body={
                "payment_instruments": [
                    _sample_instrument(),
                    _sample_instrument(id=_INSTRUMENT_ID_2, display_name="Other"),
                ]
            },
        )
        created = create_payment_instruments(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "payment_instruments": [
                    {
                        "bank_account_id": "acc-c24",
                        "display_name": "A",
                        "instrument_type": "card",
                        "valid_from": "2026-01",
                    },
                    {
                        "bank_account_id": "acc-c24",
                        "display_name": "B",
                        "instrument_type": "card",
                        "valid_from": "2026-01",
                    },
                ]
            },
        )
        self.assertEqual(len(created["payment_instruments"]), 2)
        self.assertEqual(api.calls[0][:2], ("POST", "/api/v1/payment-instruments/batch"))

        with self.assertRaises(ValueError):
            create_payment_instruments(
                api,
                profile="cand",
                base="http://test",
                arguments={"payment_instruments": []},
            )

        api.status = 200
        api.body = {
            "payment_instruments": [
                _sample_instrument(display_name="A-REN"),
                _sample_instrument(id=_INSTRUMENT_ID_2),
            ]
        }
        patched = patch_payment_instruments(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "payment_instruments": [
                    {"id": _INSTRUMENT_ID, "display_name": "A-REN"},
                    {"id": _INSTRUMENT_ID_2, "payment_network": None},
                ]
            },
        )
        self.assertEqual(len(patched["payment_instruments"]), 2)

        with self.assertRaises(ValueError):
            patch_payment_instruments(
                api,
                profile="cand",
                base="http://test",
                arguments={"payment_instruments": [{"display_name": "x"}]},
            )

        api.status = 204
        api.body = b""
        deleted = delete_payment_instruments(
            api,
            profile="cand",
            base="http://test",
            arguments={"ids": [_INSTRUMENT_ID, _INSTRUMENT_ID_2]},
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("payment_instruments", deleted)

        with self.assertRaises(ValueError):
            delete_payment_instruments(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": []},
            )

    def test_delete_in_use(self) -> None:
        api = _MockApi(
            status=409,
            body={
                "error": {
                    "code": "payment_means_fund_assignment_in_use",
                    "message": "in use",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            delete_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": _INSTRUMENT_ID},
            )
        self.assertIn("payment_means_fund_assignment_in_use", str(ctx.exception))

    def test_empty_optional_body_string_not_null(self) -> None:
        api = _MockApi(status=201, body=_sample_instrument(payment_network=""))
        create_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": "acc-c24",
                "display_name": "X",
                "instrument_type": "card",
                "valid_from": "2026-01",
                "payment_network": "",
            },
        )
        self.assertEqual(api.last_body["payment_network"], "")

    def test_create_omits_valid_from_when_absent(self) -> None:
        api = _MockApi(status=201, body=_sample_instrument(valid_from=None))
        create_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": "acc-c24",
                "display_name": "X",
                "instrument_type": "card",
            },
        )
        self.assertNotIn("valid_from", api.last_body)
        self.assertNotIn("settlement_class", api.last_body)
        self.assertNotIn("pan_last4", api.last_body)
        self.assertNotIn("issuer_expiry", api.last_body)

    def test_create_explicit_null_catalogue_keys(self) -> None:
        api = _MockApi(status=201, body=_sample_instrument())
        create_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": "acc-c24",
                "display_name": "X",
                "instrument_type": "card",
                "settlement_class": None,
                "pan_last4": None,
                "issuer_expiry": None,
                "valid_from": None,
            },
        )
        self.assertEqual(api.last_body["settlement_class"], None)
        self.assertEqual(api.last_body["pan_last4"], None)
        self.assertEqual(api.last_body["issuer_expiry"], None)
        self.assertEqual(api.last_body["valid_from"], None)

    def test_create_catalogue_fields_round_trip(self) -> None:
        body = _sample_instrument(
            settlement_class="debit",
            pan_last4="4242",
            issuer_expiry="2029-08",
            valid_from="2026-07",
        )
        api = _MockApi(status=201, body=body)
        created = create_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "bank_account_id": "acc-c24",
                "display_name": "C24 Mastercard Алексей",
                "instrument_type": "card",
                "payment_network": "mastercard",
                "settlement_class": "debit",
                "pan_last4": "4242",
                "holder_id": "aleksey",
                "valid_from": "2026-07",
                "valid_to": None,
                "issuer_expiry": "2029-08",
            },
        )
        self.assertEqual(api.last_body["settlement_class"], "debit")
        self.assertEqual(api.last_body["pan_last4"], "4242")
        self.assertEqual(api.last_body["issuer_expiry"], "2029-08")
        self.assertEqual(created["payment_instrument"]["pan_last4"], "4242")

    def test_pan_last4_sixteen_digits_sent_to_http(self) -> None:
        pan = "1234567890123456"
        api = _MockApi(
            status=422,
            body={"error": {"code": "validation_error", "message": "bad"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "bank_account_id": "acc-c24",
                    "display_name": "X",
                    "instrument_type": "card",
                    "pan_last4": pan,
                },
            )
        self.assertEqual(api.last_body["pan_last4"], pan)
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)

    def test_patch_blank_catalogue_sent_as_string(self) -> None:
        api = _MockApi(body=_sample_instrument(settlement_class=None))
        patch_payment_instrument(
            api,
            profile="cand",
            base="http://test",
            arguments={"instrument_id": _INSTRUMENT_ID, "settlement_class": "   "},
        )
        self.assertEqual(api.last_body["settlement_class"], "")

    def test_patch_empty_display_name_pre_http(self) -> None:
        api = _MockApi(body=_sample_instrument())
        with self.assertRaises(ValueError):
            patch_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": _INSTRUMENT_ID, "display_name": ""},
            )
        with self.assertRaises(ValueError):
            patch_payment_instrument(
                api,
                profile="cand",
                base="http://test",
                arguments={"instrument_id": _INSTRUMENT_ID, "display_name": "   "},
            )
        self.assertEqual(api.calls, [])

    def test_batch_patch_id_only_pre_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            patch_payment_instruments(
                api,
                profile="cand",
                base="http://test",
                arguments={"payment_instruments": [{"id": _INSTRUMENT_ID}]},
            )
        self.assertEqual(api.calls, [])


class AssignmentsLibTests(unittest.TestCase):
    """Lib helpers for eight assignment tools."""

    def test_create_list_get_overlap(self) -> None:
        api = _MockApi(status=201, body=_sample_assignment())
        created = create_payment_means_fund_assignment(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "means_type": "payment_instrument",
                "means_id": _INSTRUMENT_ID,
                "fund_id": "fund-joint",
                "valid_from": "2026-07-08",
                "valid_to": None,
            },
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["payment_means_fund_assignment"]["id"], _ASSIGNMENT_ID)

        api.status = 200
        api.body = {"payment_means_fund_assignments": [_sample_assignment()]}
        listed = list_payment_means_fund_assignments(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "means_type": "payment_instrument",
                "means_id": _INSTRUMENT_ID,
            },
        )
        self.assertEqual(len(listed["payment_means_fund_assignments"]), 1)
        self.assertIn("means_type=payment_instrument", api.calls[-1][1])

        api.body = _sample_assignment()
        got = get_payment_means_fund_assignment(
            api,
            profile="cand",
            base="http://test",
            arguments={"assignment_id": _ASSIGNMENT_ID},
        )
        self.assertEqual(got["payment_means_fund_assignment"]["fund_id"], "fund-joint")

        api.status = 409
        api.body = {"error": {"code": "link_interval_overlap", "message": "overlap"}}
        with self.assertRaises(RuntimeError) as ctx:
            create_payment_means_fund_assignment(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "means_type": "payment_instrument",
                    "means_id": _INSTRUMENT_ID,
                    "fund_id": "fund-joint",
                    "valid_from": "2026-07-08",
                },
            )
        self.assertIn("link_interval_overlap", str(ctx.exception))

    def test_patch_delete_batch_negatives(self) -> None:
        api = _MockApi(body=_sample_assignment(valid_to="2026-12-31"))
        patched = patch_payment_means_fund_assignment(
            api,
            profile="cand",
            base="http://test",
            arguments={"assignment_id": _ASSIGNMENT_ID, "valid_to": "2026-12-31"},
        )
        self.assertEqual(patched["payment_means_fund_assignment"]["valid_to"], "2026-12-31")

        with self.assertRaises(ValueError):
            patch_payment_means_fund_assignment(
                api,
                profile="cand",
                base="http://test",
                arguments={"assignment_id": _ASSIGNMENT_ID},
            )

        api.status = 201
        api.body = {
            "payment_means_fund_assignments": [
                _sample_assignment(),
                _sample_assignment(id=_ASSIGNMENT_ID_2, fund_id="fund-other"),
            ]
        }
        batch = create_payment_means_fund_assignments(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "payment_means_fund_assignments": [
                    {
                        "means_type": "bank_account",
                        "means_id": "acc-c24",
                        "fund_id": "fund-joint",
                        "valid_from": "2026-01-01",
                    },
                    {
                        "means_type": "bank_account",
                        "means_id": "acc-c24",
                        "fund_id": "fund-other",
                        "valid_from": "2026-01-01",
                    },
                ]
            },
        )
        self.assertEqual(len(batch["payment_means_fund_assignments"]), 2)

        api.status = 200
        api.body = {
            "payment_means_fund_assignments": [
                _sample_assignment(),
                _sample_assignment(id=_ASSIGNMENT_ID_2),
            ]
        }
        patch_payment_means_fund_assignments(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "payment_means_fund_assignments": [
                    {"id": _ASSIGNMENT_ID, "valid_to": None},
                    {"id": _ASSIGNMENT_ID_2, "valid_from": "2026-02-01"},
                ]
            },
        )
        self.assertEqual(api.calls[-1][:2], ("PATCH", "/api/v1/payment-means-fund-assignments"))

        with self.assertRaises(ValueError):
            patch_payment_means_fund_assignments(
                api,
                profile="cand",
                base="http://test",
                arguments={"payment_means_fund_assignments": [{"valid_to": None}]},
            )

        api.status = 204
        api.body = b""
        deleted = delete_payment_means_fund_assignment(
            api,
            profile="cand",
            base="http://test",
            arguments={"assignment_id": _ASSIGNMENT_ID},
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("payment_means_fund_assignment", deleted)

        delete_payment_means_fund_assignments(
            api,
            profile="cand",
            base="http://test",
            arguments={"ids": [_ASSIGNMENT_ID, _ASSIGNMENT_ID_2]},
        )
        with self.assertRaises(ValueError):
            delete_payment_means_fund_assignments(
                api,
                profile="cand",
                base="http://test",
                arguments={"ids": []},
            )

    def test_network_error_not_wrapped(self) -> None:
        api = _MockApi(raise_on_request=OSError("network down"))
        with self.assertRaises(OSError):
            list_payment_instruments(api, profile="cand", base="http://test")


class PaymentMeansSchemaTests(unittest.TestCase):
    """Sixteen payment-means tools registered."""

    def test_sixteen_tools_registered(self) -> None:
        import server

        expected = {
            "list_payment_instruments",
            "get_payment_instrument",
            "create_payment_instrument",
            "create_payment_instruments",
            "patch_payment_instrument",
            "patch_payment_instruments",
            "delete_payment_instrument",
            "delete_payment_instruments",
            "list_payment_means_fund_assignments",
            "get_payment_means_fund_assignment",
            "create_payment_means_fund_assignment",
            "create_payment_means_fund_assignments",
            "patch_payment_means_fund_assignment",
            "patch_payment_means_fund_assignments",
            "delete_payment_means_fund_assignment",
            "delete_payment_means_fund_assignments",
        }
        tools_list = asyncio.run(server.list_tools())
        names = {t.name for t in tools_list}
        self.assertTrue(expected.issubset(names))

    def test_list_schema_rejects_settlement_class(self) -> None:
        import server

        tools_list = asyncio.run(server.list_tools())
        schema = next(
            tool.inputSchema
            for tool in tools_list
            if tool.name == "list_payment_instruments"
        )
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertNotIn("settlement_class", schema["properties"])
        payload = {"settlement_class": "debit"}
        extra = set(payload) - set(schema["properties"])
        self.assertEqual(extra, {"settlement_class"})
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            _reject_undeclared_properties(schema, payload)
        message = str(ctx.exception)
        self.assertNotIn("validation_error", message)
        self.assertNotIn("error.code", message)
        self.assertEqual(api.calls, [])

    def test_create_schema_catalogue_fields_and_optional_valid_from(self) -> None:
        import server

        tools_list = asyncio.run(server.list_tools())
        schema = next(
            tool.inputSchema
            for tool in tools_list
            if tool.name == "create_payment_instrument"
        )
        properties = schema["properties"]
        self.assertIn("settlement_class", properties)
        self.assertIn("pan_last4", properties)
        self.assertIn("issuer_expiry", properties)
        self.assertNotIn("valid_from", schema["required"])
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertNotIn("product_class", properties)
        self.assertNotIn("pan", properties)


class PaymentMeansHandlerTests(unittest.TestCase):
    """Handler path for create_payment_instrument."""

    def test_create_payment_instrument_handler(self) -> None:
        import server

        api = _MockApi(status=201, body=_sample_instrument())
        with patch("server.get_session", return_value=(api, "http://test")):
            out = server._handle_create_payment_instrument(
                {
                    "bank_account_id": "acc-c24",
                    "display_name": "C24 Mastercard",
                    "instrument_type": "card",
                    "valid_from": "2026-01",
                    "valid_to": None,
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payment_instrument"]["id"], _INSTRUMENT_ID)


if __name__ == "__main__":
    unittest.main()
