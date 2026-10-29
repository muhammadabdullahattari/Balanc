"""Opening extensions preserve legacy serialized commands and idempotency fingerprints."""

import hashlib
import json

from ledgerpilot.shared.operations import fingerprint
from ledgerpilot.workspace.imports import ImportApply


def test_opening_optional_fields_preserve_existing_import_fingerprint() -> None:
    legacy = {
        "mode": "chart",
        "content": "code,name,kind\n100,Bank,asset\n",
        "columns": {},
        "cutover_date": "2026-10-01",
        "expected_trial": {},
        "idempotency_key": "a-legacy-import-key",
        "expected_version": 0,
        "preview_hash": "0" * 64,
    }
    command = ImportApply.model_validate(legacy)
    assert command.model_dump(mode="json") == legacy
    payload = {k: v for k, v in legacy.items() if k != "expected_version"}
    expected = hashlib.sha256(
        json.dumps(["migration.applied", payload], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fingerprint(command, "migration.applied") == expected
