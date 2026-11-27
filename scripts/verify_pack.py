#!/usr/bin/env python3
"""Offline cryptographic verifier for LedgerPilot signed auditor & quarter evidence packs.

Verifies:
1. Ed25519 signature (`manifest.sig`) over exact `manifest.json` bytes using either
   an embedded or caller-pinned Ed25519 public key hex.
2. Every file listed in `manifest.json["files"]` exists in the archive and matches
   its recorded byte size and SHA-256 digest.
3. No unverified extra files (other than `manifest.json` and `manifest.sig`) are
   injected into the archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def verify_auditor_pack(
    zip_path: Path | bytes,
    *,
    expected_public_key_hex: str | None = None,
) -> dict[str, Any]:
    """Verify a signed LedgerPilot evidence/quarter pack ZIP archive offline."""
    import io

    source: Any = io.BytesIO(zip_path) if isinstance(zip_path, bytes) else zip_path
    with ZipFile(source, "r") as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names or "manifest.sig" not in names:
            raise ValueError("Archive is missing manifest.json or manifest.sig.")

        manifest_bytes = archive.read("manifest.json")
        signature_bytes = archive.read("manifest.sig")
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        vk_info = manifest.get("verification_key") or {}
        pub_hex = expected_public_key_hex or str(vk_info.get("public_key_hex") or "")
        if not pub_hex:
            raise ValueError("No Ed25519 public key provided or present in manifest.")

        pub_bytes = bytes.fromhex(pub_hex)
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        try:
            pub_key.verify(signature_bytes, manifest_bytes)
        except InvalidSignature as exc:
            raise ValueError("Ed25519 signature verification FAILED for manifest.json.") from exc

        manifest_files: dict[str, dict[str, Any]] = manifest.get("files") or {}
        extra_files = names - set(manifest_files.keys()) - {"manifest.json", "manifest.sig"}
        if extra_files:
            raise ValueError(f"Unsigned extra files detected in archive: {sorted(extra_files)}")

        verified_count = 0
        for rel_name, meta in sorted(manifest_files.items()):
            if rel_name not in names:
                raise ValueError(f"Missing expected file in archive: {rel_name}")
            body = archive.read(rel_name)
            actual_sha = hashlib.sha256(body).hexdigest()
            if actual_sha != meta.get("sha256"):
                raise ValueError(
                    f"SHA-256 mismatch for {rel_name}: expected {meta.get('sha256')}, got {actual_sha}"
                )
            if len(body) != int(meta.get("size", -1)):
                raise ValueError(
                    f"Byte size mismatch for {rel_name}: expected {meta.get('size')}, got {len(body)}"
                )
            verified_count += 1

    return {
        "valid": True,
        "business_id": manifest.get("business_id"),
        "period": {
            "quarter": manifest.get("quarter"),
            "start": manifest.get("start"),
            "end": manifest.get("end"),
        },
        "verified_file_count": verified_count,
        "public_key_fingerprint": hashlib.sha256(pub_bytes).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a LedgerPilot signed auditor pack ZIP.")
    parser.add_argument("pack_zip", type=Path, help="Path to evidence-pack.zip")
    parser.add_argument(
        "--public-key-hex",
        default=None,
        help="Optional pinned 64-hex Ed25519 public key from /exports/verification-key",
    )
    args = parser.parse_args(argv)
    try:
        report = verify_auditor_pack(args.pack_zip, expected_public_key_hex=args.public_key_hex)
    except Exception as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
