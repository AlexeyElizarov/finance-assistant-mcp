"""Unit tests for FIN-366 household accounting-subject MCP paths."""

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

from households import (  # noqa: E402
    HOUSEHOLD_MEMBERS_PATH,
    HOUSEHOLDS_PATH,
    get_household_accounting_subject,
    get_household_member_accounting_subject,
    link_household_member_accounting_subject,
    unlink_household_member_accounting_subject,
)

import server  # noqa: E402

_SUBJECT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_HOUSEHOLD_ID = "11111111-1111-4111-8111-111111111111"
_MEMBER_ID = "22222222-2222-4222-8222-222222222222"


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


class _MockApi:
    """Stub ApiClient capturing household accounting-subject calls."""

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
    def last_path(self) -> str | None:
        return self.calls[-1][1] if self.calls else None

    @property
    def last_body(self) -> dict[str, Any] | None:
        return self.calls[-1][2] if self.calls else None


class HouseholdAccountingSubjectTests(unittest.TestCase):
    """Four household/member accounting-subject tools."""

    def test_four_tools_registered(self) -> None:
        expected = {
            "get_household_accounting_subject",
            "get_household_member_accounting_subject",
            "link_household_member_accounting_subject",
            "unlink_household_member_accounting_subject",
        }
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertTrue(expected.issubset(names))

    def test_get_by_household_and_member(self) -> None:
        api = _MockApi(body=_sample_subject(subject_type="group"))
        household = get_household_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": _HOUSEHOLD_ID},
        )
        self.assertEqual(household["accounting_subject"]["subject_type"], "group")
        self.assertEqual(
            api.last_path,
            f"{HOUSEHOLDS_PATH}/{_HOUSEHOLD_ID}/accounting-subject",
        )

        api.body = _sample_subject()
        member = get_household_member_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_member_id": _MEMBER_ID},
        )
        self.assertEqual(member["accounting_subject"]["id"], _SUBJECT_ID)
        self.assertEqual(
            api.last_path,
            f"{HOUSEHOLD_MEMBERS_PATH}/{_MEMBER_ID}/accounting-subject",
        )

    def test_link_and_unlink(self) -> None:
        api = _MockApi(status=204, body=b"")
        linked = link_household_member_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_member_id": _MEMBER_ID, "subject_id": _SUBJECT_ID},
        )
        self.assertTrue(linked["ok"])
        self.assertNotIn("accounting_subject", linked)
        self.assertEqual(api.last_body, {"subject_id": _SUBJECT_ID})

        unlinked = unlink_household_member_accounting_subject(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_member_id": _MEMBER_ID},
        )
        self.assertTrue(unlinked["ok"])
        self.assertEqual(
            api.calls[1][:2],
            (
                "DELETE",
                f"{HOUSEHOLD_MEMBERS_PATH}/{_MEMBER_ID}/accounting-subject-link",
            ),
        )

    def test_link_empty_subject_id_passes_to_http(self) -> None:
        api = _MockApi(
            status=422,
            body={"error": {"code": "validation_error", "message": "bad uuid"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            link_household_member_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_member_id": _MEMBER_ID, "subject_id": ""},
            )
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(api.last_body, {"subject_id": ""})

    def test_conflict_codes_passthrough(self) -> None:
        api = _MockApi(
            status=409,
            body={
                "error": {
                    "code": "household_person_link_exists",
                    "message": "exists",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            link_household_member_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_member_id": _MEMBER_ID, "subject_id": _SUBJECT_ID},
            )
        self.assertIn("household_person_link_exists", str(ctx.exception))

        api.status = 404
        api.body = {
            "error": {
                "code": "member_subject_link_not_found",
                "message": "no link",
            }
        }
        with self.assertRaises(RuntimeError) as ctx2:
            unlink_household_member_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_member_id": _MEMBER_ID},
            )
        self.assertIn("member_subject_link_not_found", str(ctx2.exception))

    def test_household_not_found_before_subject(self) -> None:
        api = _MockApi(
            status=404,
            body={"error": {"code": "household_not_found", "message": "missing"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_household_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": _HOUSEHOLD_ID},
            )
        self.assertIn("household_not_found", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)

    def test_member_not_found_before_link(self) -> None:
        api = _MockApi(
            status=404,
            body={"error": {"code": "household_member_not_found", "message": "missing"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            link_household_member_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_member_id": _MEMBER_ID, "subject_id": _SUBJECT_ID},
            )
        self.assertIn("household_member_not_found", str(ctx.exception))

    def test_link_null_subject_id_no_http(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            link_household_member_accounting_subject(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_member_id": _MEMBER_ID, "subject_id": None},
            )
        self.assertEqual(api.calls, [])

    def test_handler_link_no_session_on_validation(self) -> None:
        with patch("server.get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_link_household_member_accounting_subject(
                    {"household_member_id": _MEMBER_ID, "subject_id": None}
                )
            get_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
