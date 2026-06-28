"""Unit tests for FIN-107 put_transaction_overrides and upsert_expense_project."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from monthly_close_lib import (
    Period,
    parse_period,
    put_transaction_overrides,
    upsert_expense_project,
)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

VID = "00000000-0000-4000-8000-000000000001"
PERIOD = Period(year=2026, month=5)


class _ReconciliationMockApi:
    """Stub ApiClient for reconciliation GET/PUT."""

    def __init__(self, reconciliation: dict[str, Any]) -> None:
        self._reconciliation = dict(reconciliation)
        self.put_payloads: list[dict[str, Any]] = []
        self.derive_calls = 0

    def get_json(self, path: str) -> dict[str, Any]:
        parsed = urlparse(path)
        if parsed.path.endswith("/budget/reconciliation"):
            return dict(self._reconciliation)
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if method == "PUT" and path == "/api/v1/budget/reconciliation":
            self.put_payloads.append(dict(data or {}))
            if self._reconciliation.get("_put_status", 200) != 200:
                return self._reconciliation["_put_status"], {"error": "period_closed"}
            overrides = (data or {}).get("transaction_overrides", {})
            body = {
                "status": "draft",
                "transaction_overrides": overrides,
            }
            self._reconciliation["transaction_overrides"] = overrides
            return 200, body
        if method == "POST" and path == "/api/v1/transactions/derive":
            self.derive_calls += 1
            return 200, {"ok": True}
        raise AssertionError(f"unexpected request {method} {path}")


class _ProjectsMockApi:
    """Stub ApiClient for projects list and upsert."""

    def __init__(self, projects: list[dict[str, Any]]) -> None:
        self._projects = list(projects)
        self.post_calls: list[dict[str, Any]] = []
        self.put_calls: list[tuple[str, dict[str, Any]]] = []

    def get_json(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/projects":
            return {"projects": list(self._projects)}
        raise AssertionError(f"unexpected get_json path: {path}")

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        payload = dict(data or {})
        if method == "POST" and path == "/api/v1/projects":
            self.post_calls.append(payload)
            if not payload.get("keywords"):
                return 422, {"message": "Список ключевых слов не может быть пустым."}
            self._projects.append(payload)
            return 201, payload
        if method == "PUT" and path.startswith("/api/v1/projects/"):
            project_id = path.rsplit("/", 1)[-1]
            self.put_calls.append((project_id, payload))
            if not payload.get("keywords"):
                return 422, {"message": "Список ключевых слов не может быть пустым."}
            for idx, row in enumerate(self._projects):
                if row["id"] == project_id:
                    self._projects[idx] = payload
                    return 200, payload
            return 404, {"message": "not found"}
        raise AssertionError(f"unexpected request {method} {path}")


def _valid_project(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "PR005",
        "description": "Canal Pride Amsterdam",
        "keywords": ["BOOKING"],
        "valid_from": "20260501",
        "valid_to": "20260531",
    }
    base.update(overrides)
    return base


class PutTransactionOverridesTest(unittest.TestCase):
    """put_transaction_overrides (FIN-107 T1–T5, T12)."""

    def test_merge_true_adds_keys(self) -> None:
        """T1: merge=true merges existing and new overrides."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {"a": "1"}, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"b": "2"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1", "b": "2"},
        )

    def test_merge_true_overwrites_key(self) -> None:
        """T2: merge=true overwrites existing key."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {"a": "1"}, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "9"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "9"},
        )

    def test_merge_false_replaces_map(self) -> None:
        """T3: merge=false sends only argument map."""
        api = _ReconciliationMockApi(
            {
                "transaction_overrides": {"a": "1", "legacy": "x"},
                "status": "open",
            }
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"b": "2"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"b": "2"},
        )

    def test_null_existing_overrides_treated_as_empty(self) -> None:
        """Null transaction_overrides from GET normalizes to {}."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": None, "status": "open"}
        )
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=True
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_missing_overrides_field_treated_as_empty(self) -> None:
        """Missing transaction_overrides from GET normalizes to {}."""
        api = _ReconciliationMockApi({"status": "open"})
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_merge_false_empty_existing(self) -> None:
        """T12: merge=false with empty existing map."""
        api = _ReconciliationMockApi({"transaction_overrides": {}, "status": "open"})
        put_transaction_overrides(
            api, VID, PERIOD, {"a": "1"}, merge=False
        )
        self.assertEqual(
            api.put_payloads[-1]["transaction_overrides"],
            {"a": "1"},
        )

    def test_period_closed_raises(self) -> None:
        """T5: PUT 422 surfaces as tool error."""
        api = _ReconciliationMockApi(
            {"transaction_overrides": {}, "status": "closed", "_put_status": 422}
        )
        with self.assertRaises(RuntimeError):
            put_transaction_overrides(
                api, VID, PERIOD, {"a": "1"}, merge=True
            )


class UpsertExpenseProjectTest(unittest.TestCase):
    """upsert_expense_project (FIN-107 T8–T10, T13)."""

    def test_create_new_project(self) -> None:
        """T8: new id uses POST."""
        api = _ProjectsMockApi([])
        result = upsert_expense_project(api, _valid_project())
        self.assertEqual(result["action"], "created")
        self.assertEqual(len(api.post_calls), 1)
        self.assertEqual(api.put_calls, [])

    def test_update_existing_project(self) -> None:
        """T9: existing id uses PUT."""
        existing = _valid_project()
        api = _ProjectsMockApi([existing])
        updated = _valid_project(keywords=["BOOKING", "HOTEL"])
        result = upsert_expense_project(api, updated)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(len(api.put_calls), 1)
        self.assertEqual(api.put_calls[0][0], "PR005")

    def test_invalid_id_raises_from_api(self) -> None:
        """T10: invalid project id rejected by API validation."""
        api = _ProjectsMockApi([])

        def failing_request(
            method: str,
            path: str,
            data: dict[str, Any] | None = None,
        ) -> tuple[int, Any]:
            return 422, {"message": "Идентификатор проекта должен быть в формате PR и три цифры."}

        api.request = failing_request  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            upsert_expense_project(
                api,
                _valid_project(id="PR1"),
            )

    def test_empty_keywords_validation_error(self) -> None:
        """T13: empty keywords list rejected."""
        api = _ProjectsMockApi([])
        with self.assertRaises(RuntimeError):
            upsert_expense_project(api, _valid_project(keywords=[]))


class ParsePeriodTest(unittest.TestCase):
    """parse_period validation (FIN-107 T11)."""

    def test_invalid_period_raises(self) -> None:
        """T11: invalid period string."""
        with self.assertRaises(ValueError):
            parse_period("2026-13")


class PutTransactionOverridesHandlerTest(unittest.TestCase):
    """MCP handler derive semantics (FIN-107 T4, T6, T7)."""

    @patch("server.run_derive")
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_derive_false_omits_field_and_skips_call(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """T6: derive=false — no derive call, field omitted."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.return_value = {"status": "draft", "transaction_overrides": {"a": "1"}}
        out = server._handle_put_transaction_overrides(
            {
                "period": "2026-05",
                "overrides": {"a": "1"},
                "derive": False,
            }
        )
        mock_derive.assert_not_called()
        payload = json.loads(out[0].text)
        self.assertNotIn("derive", payload)

    @patch("server.run_derive", return_value={"derived": 1})
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_derive_true_calls_once(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """T7: derive=true — single derive after successful PUT."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.return_value = {"status": "draft", "transaction_overrides": {"a": "1"}}
        out = server._handle_put_transaction_overrides(
            {
                "period": "2026-05",
                "overrides": {"a": "1"},
                "derive": True,
            }
        )
        mock_derive.assert_called_once()
        payload = json.loads(out[0].text)
        self.assertEqual(payload["derive"], {"derived": 1})

    @patch("server.run_derive")
    @patch("server.put_transaction_overrides")
    @patch("server.resolve_budget_version_id", return_value=VID)
    @patch("server.get_session")
    def test_put_failure_skips_derive(
        self,
        mock_get_session: MagicMock,
        _mock_vid: MagicMock,
        mock_put: MagicMock,
        mock_derive: MagicMock,
    ) -> None:
        """Derive not called when PUT raises."""
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_put.side_effect = RuntimeError("PUT reconciliation -> 422")
        with self.assertRaises(RuntimeError):
            server._handle_put_transaction_overrides(
                {
                    "period": "2026-05",
                    "overrides": {"a": "1"},
                    "derive": True,
                }
            )
        mock_derive.assert_not_called()

    def test_empty_overrides_raises_before_http(self) -> None:
        """T4: empty overrides rejected in handler."""
        import server

        with self.assertRaises(ValueError):
            server._handle_put_transaction_overrides(
                {"period": "2026-05", "overrides": {}}
            )


if __name__ == "__main__":
    unittest.main()
