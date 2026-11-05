"""Disposable-container checks; only synthetic secrets and uniquely named volumes."""

import json
import subprocess
import sys
import time
from uuid import uuid4

image = sys.argv[1]
volume = "ledgerpilot-audit-" + uuid4().hex
container = volume + "-api"


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True
    )


program = r"""
import base64, hashlib, sys
from pathlib import Path
from uuid import UUID
from ledgerpilot.config import Settings
from ledgerpilot.documents.storage import put, get
settings=Settings(_env_file=None,database_url="postgresql+psycopg://ledgerpilot_app:synthetic@127.0.0.1:1/test",session_signing_key="synthetic-signing-key-12345678901234567890",evidence_storage_root=Path("/evidence"),evidence_encryption_key=base64.urlsafe_b64encode(b"a"*32).decode())
content=b"synthetic durable receipt"
bid,eid=UUID(int=1),UUID(int=2)
key=f"{bid}/{eid}.bin"
if sys.argv[1]=="write":
    put(settings,bid,eid,content)
    assert content not in (Path('/evidence')/key).read_bytes()
else:
    assert get(settings,key,hashlib.sha256(content).hexdigest())==content
print("encrypted original verified")
"""
try:
    docker("volume", "create", volume)
    docker(
        "run",
        "--rm",
        "--user",
        "0",
        "--mount",
        f"type=volume,source={volume},target=/evidence",
        "--entrypoint",
        "chown",
        image,
        "10001:10001",
        "/evidence",
    )
    for mode in ["write", "read"]:
        result = docker(
            "run",
            "--rm",
            "--mount",
            f"type=volume,source={volume},target=/evidence",
            "--entrypoint",
            "/app/.venv/bin/python",
            image,
            "-c",
            program,
            mode,
        )
        assert "verified" in result.stdout
    docker(
        "run",
        "--detach",
        "--name",
        container,
        "--health-interval=1s",
        "--health-start-period=0s",
        "--health-retries=1",
        "--env",
        "DATABASE_URL=postgresql+psycopg://ledgerpilot_app:synthetic@127.0.0.1:1/test",
        "--env",
        "SESSION_SIGNING_KEY=synthetic-signing-key-12345678901234567890",
        image,
    )
    live_probe = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status==200"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (
            docker(
                "exec",
                container,
                "/app/.venv/bin/python",
                "-c",
                live_probe,
                check=False,
            ).returncode
            == 0
        ):
            break
        time.sleep(1)
    else:
        raise AssertionError("API never became live")
    # Test a running API, not an unhealthy state caused by cold startup.
    docker(
        "exec",
        container,
        "/app/.venv/bin/python",
        "-c",
        "import urllib.request, urllib.error;\ntry: urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3); raise AssertionError('Unexpected readiness')\nexcept urllib.error.HTTPError as error: assert error.code==503",
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = json.loads(docker("inspect", container).stdout)[0]["State"]
        if state.get("Health", {}).get("Status") == "unhealthy":
            break
        time.sleep(1)
    else:
        raise AssertionError("Invalid database was not rejected by readiness")
    print(
        "PASS: encrypted originals survive replacement; live API with invalid DB is unhealthy"
    )
finally:
    docker("rm", "-f", container, check=False)
    docker("volume", "rm", volume, check=False)
