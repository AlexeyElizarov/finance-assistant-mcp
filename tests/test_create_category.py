"""Unit tests for FIN-217 create_category (rev.4 T1–T12)."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from monthly_close_lib import create_category


class _CreateCategoryMockApi:
    """Stub ApiClient for category create flows."""

    def __init__(
        self,
        *,
        status: int = 201,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._raise_on_request = raise_on_request
        self.bodies: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if self._raise_on_request is not None:
            raise self._raise_on_request
        if method != "POST" or path != "/api/v1/categories":
            raise AssertionError(f"unexpected request: {method} {path}")
        self.bodies.append(dict(data or {}))
        if self._body is not None:
            return self._status, self._body
        payload = dict(data or {})
        return self._status, {
            "id": payload.get("id"),
            "type": payload.get("type"),
            "description": payload.get("description"),
            "keywords": list(payload.get("keywords") or []),
            "default": bool(payload.get("default", False)),
        }


class CreateCategoryLibTests(unittest.TestCase):
    """create_category lib (FIN-217 T1–T5, T7–T12)."""

    def test_t1_happy_path_returns_category_id(self) -> None:
        api = _CreateCategoryMockApi()
        created = create_category(
            api,
            id="C8901",
            type="C",
            description="FIN-217 smoke",
            keywords=[],
        )
        self.assertEqual(created["id"], "C8901")
        self.assertEqual(len(api.bodies), 1)

    def test_t2_empty_description_no_http(self) -> None:
        api = _CreateCategoryMockApi()
        with self.assertRaises(ValueError) as ctx:
            create_category(api, id="C8901", type="C", description="  ")
        self.assertIn("description", str(ctx.exception))
        self.assertEqual(api.bodies, [])

    def test_t3_duplicate_422_runtime_error(self) -> None:
        err_body = {"detail": "duplicate", "message": "категория уже существует"}
        api = _CreateCategoryMockApi(status=422, body=err_body)
        with self.assertRaises(RuntimeError) as ctx:
            create_category(api, id="C8901", type="C", description="dup")
        msg = str(ctx.exception)
        self.assertIn("POST /api/v1/categories -> 422", msg)
        self.assertIn(str(err_body), msg)

    def test_t4_keywords_omit_sends_empty_list(self) -> None:
        api = _CreateCategoryMockApi()
        create_category(api, id="C8901", type="C", description="x")
        self.assertEqual(api.bodies[0]["keywords"], [])

    def test_t5_default_omit_sends_false(self) -> None:
        api = _CreateCategoryMockApi()
        create_category(api, id="C8901", type="C", description="x")
        self.assertIs(api.bodies[0]["default"], False)

    def test_t7_default_true_pass_through_422(self) -> None:
        err_body = {"message": "default true not allowed"}
        api = _CreateCategoryMockApi(status=422, body=err_body)
        with self.assertRaises(RuntimeError) as ctx:
            create_category(
                api,
                id="C8901",
                type="C",
                description="x",
                default=True,
            )
        self.assertIn("POST /api/v1/categories -> 422", str(ctx.exception))
        self.assertIs(api.bodies[0]["default"], True)

    def test_t8_default_string_value_error(self) -> None:
        api = _CreateCategoryMockApi()
        with self.assertRaises(ValueError) as ctx:
            create_category(
                api,
                id="C8901",
                type="C",
                description="x",
                default="true",
            )
        self.assertIn("default", str(ctx.exception))
        self.assertEqual(api.bodies, [])

    def test_t9_strip_required_strings(self) -> None:
        api = _CreateCategoryMockApi()
        create_category(
            api,
            id=" C8901 ",
            type=" C ",
            description=" Test ",
        )
        self.assertEqual(
            api.bodies[0],
            {
                "id": "C8901",
                "type": "C",
                "description": "Test",
                "keywords": [],
                "default": False,
            },
        )

    def test_t10_keywords_non_list_value_error(self) -> None:
        api = _CreateCategoryMockApi()
        with self.assertRaises(ValueError) as ctx:
            create_category(
                api,
                id="C8901",
                type="C",
                description="x",
                keywords="foo",
            )
        self.assertIn("keywords", str(ctx.exception))
        self.assertEqual(api.bodies, [])

    def test_t11_transport_error_propagates(self) -> None:
        err = URLError("connection refused")
        api = _CreateCategoryMockApi(raise_on_request=err)
        with self.assertRaises(URLError) as ctx:
            create_category(api, id="C8901", type="C", description="x")
        self.assertIs(ctx.exception, err)

    def test_t12_http_200_is_error(self) -> None:
        api = _CreateCategoryMockApi(
            status=200,
            body={"id": "C8901", "type": "C", "description": "x", "keywords": [], "default": False},
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_category(api, id="C8901", type="C", description="x")
        self.assertIn("POST /api/v1/categories -> 200", str(ctx.exception))


class CreateCategoryHandlerTests(unittest.TestCase):
    """MCP handler + schema (FIN-217 T1 envelope, T6)."""

    @patch("server.create_category")
    @patch("server.get_session")
    def test_t1_handler_ok_envelope(
        self,
        mock_get_session: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        import server

        mock_get_session.return_value = (MagicMock(), "http://test")
        mock_create.return_value = {
            "id": "C8901",
            "type": "C",
            "description": "FIN-217 smoke",
            "keywords": [],
            "default": False,
        }
        out = server._handle_create_category(
            {
                "id": "C8901",
                "type": "C",
                "description": "FIN-217 smoke",
            },
        )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["category"]["id"], "C8901")
        self.assertEqual(payload["profile"], server.DEFAULT_PROFILE)
        self.assertEqual(payload["base"], "http://test")
        mock_create.assert_called_once()
        self.assertNotIn("keywords", mock_create.call_args.kwargs)
        self.assertNotIn("default", mock_create.call_args.kwargs)

    def test_t6_tool_registered(self) -> None:
        import server

        tools_list = asyncio.run(server.list_tools())
        tool = next(t for t in tools_list if t.name == "create_category")
        required = tool.inputSchema.get("required") or []
        self.assertEqual(set(required), {"id", "type", "description"})
        props = tool.inputSchema["properties"]
        self.assertEqual(props["keywords"]["type"], "array")
        self.assertEqual(props["default"]["type"], "boolean")


if __name__ == "__main__":
    unittest.main()
