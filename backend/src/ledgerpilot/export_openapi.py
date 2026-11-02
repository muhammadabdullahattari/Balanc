"""Reproducible, service-independent browser contract export and drift check."""

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    # Importing the application must not require workstation/provider credentials.
    os.environ.update(
        APP_ENV="test",
        APP_DB_USER="ledgerpilot_app",
        DATABASE_URL="postgresql+psycopg://ledgerpilot_app:contract-only@localhost/ledgerpilot",
        SESSION_SIGNING_KEY="contract-export-only-key-never-use-for-a-server",
        FRONTEND_ORIGINS="http://localhost:5173",
    )
    from ledgerpilot.local_setup import ROOT, FileSettings
    from ledgerpilot.main import create_app

    settings = FileSettings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        session_signing_key=os.environ["SESSION_SIGNING_KEY"],
    )
    spec = create_app(settings).openapi()
    path: Path = ROOT / "docs/contracts/openapi.json"
    if args.check:
        if json.loads(path.read_text(encoding="utf-8")) != spec:
            raise SystemExit("OpenAPI drift: regenerate and review the browser contract")
    else:
        path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OpenAPI {'checked' if args.check else 'exported'}: {len(spec['paths'])} paths")


if __name__ == "__main__":
    main()
