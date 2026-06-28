"""Minimal REST client for FinancePlanningProject ``/api/v1`` endpoints."""

from __future__ import annotations

import http.cookiejar
import json
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path

PORT_SCAN_FIRST = 8000
PORT_SCAN_LAST = 8010

BOOTSTRAP_HINT = (
    "Запустите сервер: $env:FINANCE_DATA_PROFILE = '{profile}'; "
    "$env:FINANCE_WEB_PORT = '{port}'; "
    "cd C:\\Users\\haake\\PycharmProjects\\FinancePlanningProject; "
    ".\\.venv\\Scripts\\python.exe -m web"
)


def probe_api_port(port: int, *, timeout: int = 2) -> bool:
    """
    Return whether FinancePlanning API responds on localhost port.

    :param port: TCP port to probe
    :param timeout: Request timeout in seconds
    :return: ``True`` when ``GET /api/v1/meta`` returns 200 or 401
    """
    url = f"http://127.0.0.1:{port}/api/v1/meta"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status in (200, 401)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 401)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def discover_api_base(*, profile: str | None = None) -> str | None:
    """
    Scan localhost ports 8000–8010 for a running FinancePlanning API.

    When ``profile`` is set, log in and return the base URL only if
    ``GET /api/v1/meta`` reports matching ``data_profile``.

    :param profile: Expected ``test`` / ``cand`` / ``prod``, or ``None`` for first match
    :return: Base URL such as ``http://127.0.0.1:8000``, or ``None``
    """
    candidates = [
        f"http://127.0.0.1:{port}"
        for port in range(PORT_SCAN_FIRST, PORT_SCAN_LAST + 1)
        if probe_api_port(port)
    ]
    if not candidates:
        return None
    if profile is None:
        return candidates[0]

    password = os.environ.get("FINANCE_SEED_ADMIN_PASSWORD", "playwright-admin")
    for base in candidates:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        login_body = json.dumps(
            {"username": "admin", "password": password, "data_profile": profile}
        ).encode()
        login_req = urllib.request.Request(
            f"{base}/api/v1/auth/login",
            data=login_body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(login_req, timeout=5) as resp:
                if resp.status != 200:
                    continue
            meta_req = urllib.request.Request(f"{base}/api/v1/meta", method="GET")
            with opener.open(meta_req, timeout=5) as resp:
                meta = json.loads(resp.read())
            if meta.get("data_profile") == profile:
                return base
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError):
            continue
    return None


def resolve_api_base(explicit_base: str | None, profile: str) -> str:
    """
    Resolve API base URL: explicit ``--base`` or scan ports 8000–8010.

    :param explicit_base: CLI ``--base`` or None to auto-discover
    :param profile: Expected data profile for discovery
    :return: Base URL
    :raises RuntimeError: When no server responds in the scan range
    """
    if explicit_base:
        return explicit_base
    found = discover_api_base(profile=profile)
    if found:
        return found
    hint = BOOTSTRAP_HINT.format(profile=profile, port="8000")
    raise RuntimeError(
        f"API не найден на портах {PORT_SCAN_FIRST}–{PORT_SCAN_LAST} "
        f"для profile={profile!r}.\n{hint}"
    )


class ApiClient:
    """HTTP client for FinancePlanningProject API."""

    def __init__(self, base: str, timeout: int = 180) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )

    def login(
        self,
        *,
        username: str = "admin",
        password: str | None = None,
        data_profile: str | None = None,
    ) -> None:
        """
        Authenticate and store session cookies for subsequent requests.

        :param username: Login name
        :param password: Password (defaults to ``FINANCE_SEED_ADMIN_PASSWORD``)
        :param data_profile: Optional ``test`` / ``cand`` / ``prod`` profile switch
        :raises RuntimeError: When login fails
        """
        pwd = password or os.environ.get("FINANCE_SEED_ADMIN_PASSWORD", "playwright-admin")
        payload: dict[str, str] = {"username": username, "password": pwd}
        if data_profile is not None:
            payload["data_profile"] = data_profile
        request = urllib.request.Request(
            f"{self.base}/api/v1/auth/login",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"login -> HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"login -> HTTP {exc.code}: {raw}") from exc

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        files: list[tuple[Path, str]] | None = None,
    ) -> tuple[int, dict | bytes | str]:
        """
        Call API endpoint.

        :param method: HTTP method
        :param path: Path starting with ``/api/v1/…``
        :param data: JSON body
        :param files: Multipart upload pairs ``(path, provider)``; multiple
            entries in one request are sent as repeated ``files`` / ``providers``
            parts (required for Mastercard head+tail in a single merge)
        :return: Status code and parsed body
        """
        if files:
            boundary = uuid.uuid4().hex
            body = bytearray()
            for fp, prov in files:
                content = fp.read_bytes()
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(
                    f'Content-Disposition: form-data; name="files"; '
                    f'filename="{fp.name}"\r\n'.encode()
                )
                body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
                body.extend(content)
                body.extend(b"\r\n")
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(
                    f'Content-Disposition: form-data; name="providers"\r\n\r\n{prov}\r\n'.encode()
                )
            body.extend(f"--{boundary}--\r\n".encode())
            request = urllib.request.Request(
                self.base + path,
                data=bytes(body),
                method=method,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        elif data is not None:
            request = urllib.request.Request(
                self.base + path,
                data=json.dumps(data).encode(),
                method=method,
                headers={"Content-Type": "application/json"},
            )
        else:
            request = urllib.request.Request(self.base + path, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype:
                    return resp.status, json.loads(raw)
                return resp.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed: dict | bytes | str = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw.decode("utf-8", errors="replace")
            return exc.code, parsed

    def get_json(self, path: str) -> dict:
        """
        GET JSON endpoint or raise.

        :param path: API path
        :return: Parsed JSON object
        """
        status, body = self.request("GET", path)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"GET {path} -> {status}: {body}")
        return body
