"""FIN-2 smoke on live test profile API (run manually; not part of unittest suite)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for path in (_SCRIPTS, _ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_spec = importlib.util.spec_from_file_location("finance_assistant_server", _ROOT / "server.py")
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot load finance-assistant server.py")
_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_server)
_handle_process_month = _server._handle_process_month
_handle_verify_month = _server._handle_verify_month

BASE = "http://127.0.0.1:8000"
PROFILE = "test"
PERIOD = "2026-03"


def _load(handler, args: dict) -> dict:
    return json.loads(handler(args)[0].text)


def main() -> int:
    print(f"=== FIN-2 smoke profile={PROFILE} period={PERIOD} @ {BASE} ===")

    verify_payload = _load(
        _handle_verify_month,
        {"period": PERIOD, "profile": PROFILE, "base": BASE},
    )
    verify = verify_payload["verify"]
    assert "warnings" in verify, "missing warnings field"
    assert any("C9999" in w for w in verify["warnings"]), verify.get("warnings")
    assert not any("C9999" in i for i in verify["issues"]), verify.get("issues")
    print("PASS verify_month: C9999 in warnings only")

    non_close = _load(
        _handle_process_month,
        {"period": PERIOD, "profile": PROFILE, "base": BASE, "skip_import": True},
    )
    assert not any(
        "C9999" in i for i in non_close["log"]["steps"]["verify"]["issues"]
    )
    print(f"PASS process_month non-close: ok={non_close['ok']}")

    prelim = _load(
        _handle_process_month,
        {
            "period": PERIOD,
            "profile": PROFILE,
            "base": BASE,
            "skip_import": True,
            "close": True,
            "close_phase": "preliminary",
        },
    )
    assert prelim["ok"] is False
    assert "preliminary close" in prelim.get("error", "")
    print(f"PASS guard preliminary without ack: {prelim['error']}")

    final_block = _load(
        _handle_process_month,
        {
            "period": PERIOD,
            "profile": PROFILE,
            "base": BASE,
            "skip_import": True,
            "close": True,
            "close_phase": "final",
        },
    )
    assert final_block["ok"] is False
    assert "final close" in final_block.get("error", "")
    print(f"PASS guard final with C9999: {final_block['error']}")

    try:
        _handle_process_month(
            {
                "period": PERIOD,
                "profile": PROFILE,
                "base": BASE,
                "skip_import": True,
                "close": True,
                "close_phase": "final",
                "c9999_acknowledged": True,
            }
        )
        raise AssertionError("expected ValueError for ack+final")
    except ValueError as exc:
        assert "not allowed with close_phase=final" in str(exc)
    print("PASS validation ack+final rejected")

    print("=== ALL FIN-2 smoke checks PASSED on test ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
