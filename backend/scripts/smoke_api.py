"""Boot the real no-key API on a temporary localhost port and verify HTTP behavior."""

import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    for name in (
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "OPENROUTER_API_KEY",
        "SERPER_API_KEY",
        "MODEL_CHAT",
        "MODEL_FAST",
        "MODEL_RESEARCH",
    ):
        env[name] = ""
    env["HOST"], env["PORT"] = "127.0.0.1", str(port)
    child = subprocess.Popen(
        [sys.executable, "-c", "from app.core.runtime import api_main; api_main()"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                with urlopen(base + "/health", timeout=1) as response:
                    assert json.load(response)["status"] == "ok"
                break
            except (URLError, TimeoutError):
                if child.poll() is not None:
                    raise RuntimeError("API exited before startup") from None
                time.sleep(0.2)
        else:
            raise RuntimeError("API startup timed out")
        try:
            urlopen(base + "/ready", timeout=2)
            raise AssertionError("No-key readiness must be unavailable")
        except HTTPError as exc:
            assert exc.code == 503
        with urlopen(base + "/openapi.json", timeout=2) as response:
            schema = json.load(response)
            assert "/api/chat" in schema["paths"]
            assert "/api/posts/{post_id}/approve" in schema["paths"]
        print(
            json.dumps(
                {
                    "real_http_startup": "passed",
                    "health": 200,
                    "no_key_readiness": 503,
                    "openapi_paths": len(schema["paths"]),
                }
            )
        )
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    main()
