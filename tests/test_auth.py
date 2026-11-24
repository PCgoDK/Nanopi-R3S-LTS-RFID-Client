import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from app import __main__ as runner
from app import main


@pytest.mark.parametrize(
    ("flag", "expected_required"),
    [(None, True), ("0", True), ("1", False)],
)
def test_unauthenticated_mode_requires_explicit_opt_in(flag, expected_required):
    env = {**os.environ, "RFID_APP_TOKEN": "test-token"}
    env.pop("RFID_ALLOW_UNAUTHENTICATED", None)
    if flag is not None:
        env["RFID_ALLOW_UNAUTHENTICATED"] = flag
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import get_app_config; print(get_app_config()['auth_required'])"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == str(expected_required)


@pytest.mark.parametrize(
    ("token", "allow", "starts"),
    [("", False, False), ("", True, True), ("test-token", False, True)],
)
def test_nonlocal_binding_requires_token_or_explicit_opt_in(monkeypatch, token, allow, starts):
    monkeypatch.setenv("RFID_HOST", "192.168.1.132")
    monkeypatch.setattr(runner, "API_TOKEN", token)
    monkeypatch.setattr(runner, "ALLOW_UNAUTHENTICATED", allow)
    called = []
    monkeypatch.setattr(runner.uvicorn, "run", lambda *args, **kwargs: called.append(kwargs))
    if starts:
        runner.main()
        assert called[0]["host"] == "192.168.1.132"
    else:
        with pytest.raises(SystemExit):
            runner.main()
        assert not called


@pytest.mark.parametrize(
    ("configured", "supplied", "expected"),
    [("test-token", "", 401), ("test-token", "wrong", 401),
     ("test-token", "test-token", 204), ("", "", 204)],
)
def test_api_authentication_respects_selected_mode(monkeypatch, configured, supplied, expected):
    monkeypatch.setattr(main, "API_TOKEN", configured)
    request = SimpleNamespace(url=SimpleNamespace(path="/api/state"), headers={"x-api-token": supplied})

    async def next_response(_request):
        return Response(status_code=204)

    response = asyncio.run(main.protect_api(request, next_response))
    assert response.status_code == expected
