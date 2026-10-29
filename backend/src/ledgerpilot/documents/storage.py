"""Private local original adapter; encrypted bytes and generated relative keys only."""

import hashlib
import os
import socket
import struct
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError


def scan(content: bytes, settings: Settings) -> str:
    """Clamd INSTREAM must positively confirm clean before content is parsed."""
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=10
        ) as conn:
            conn.sendall(b"zINSTREAM\0")
            for start in range(0, len(content), 65536):
                chunk = content[start : start + 65536]
                conn.sendall(struct.pack("!I", len(chunk)) + chunk)
            conn.sendall(struct.pack("!I", 0))
            reply = b""
            while b"\0" not in reply and len(reply) < 4096:
                part = conn.recv(1024)
                if not part:
                    break
                reply += part
        if b"FOUND" in reply:
            raise DomainError("ERR_MALWARE", "The upload was rejected by malware scanning.", 422)
        if reply.strip(b"\0\r\n ") != b"stream: OK":
            raise DomainError(
                "ERR_SCAN_UNAVAILABLE", "Upload scanning did not confirm a clean result.", 503
            )
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=5
        ) as conn:
            conn.sendall(b"zVERSION\0")
            version = conn.recv(512).rstrip(b"\0\n").decode("ascii", errors="replace")
        if not version.startswith("ClamAV"):
            raise DomainError("ERR_SCAN_UNAVAILABLE", "Scanner version could not be verified.", 503)
        return version[:160]
    except OSError:
        raise DomainError(
            "ERR_SCAN_UNAVAILABLE", "The local malware scanner is unavailable.", 503
        ) from None


def detect(content: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if content.startswith(b"%PDF-") and suffix == ".pdf":
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n") and suffix == ".png":
        return "image/png"
    if content.startswith(b"\xff\xd8\xff") and suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if (
        len(content) > 12
        and content[4:8] == b"ftyp"
        and content[8:12] in {b"heic", b"heix"}
        and suffix == ".heic"
    ):
        return "image/heic"
    if content.startswith(b"PK\x03\x04") and suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".csv" and b"\0" not in content:
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        else:
            return "text/csv"
    raise DomainError(
        "ERR_FILE_TYPE", "File extension and content do not match a supported type.", 422
    )


def cipher(settings: Settings) -> Fernet:
    if settings.evidence_encryption_key is None:
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Private evidence encryption is not configured.", 503
        )
    return Fernet(settings.evidence_encryption_key.get_secret_value().encode())


def path(settings: Settings, key: str) -> Path:
    root = settings.evidence_storage_root.resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root) or target == root:
        raise DomainError("ERR_STORAGE", "Invalid evidence object key.")
    return target


def put(settings: Settings, bid: UUID, eid: UUID, content: bytes) -> tuple[str, str]:
    key = f"{bid}/{eid}.bin"
    target = path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encrypted = cipher(settings).encrypt(content)
    try:
        with target.open("xb") as file:
            os.chmod(target, 0o600)
            file.write(encrypted)
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Private evidence storage is unavailable.", 503
        ) from None
    return key, hashlib.sha256(content).hexdigest()


def get(settings: Settings, key: str, expected_hash: str) -> bytes:
    try:
        content = cipher(settings).decrypt(path(settings, key).read_bytes())
    except (OSError, InvalidToken):
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Evidence could not be read and verified.", 503
        ) from None
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise DomainError("ERR_INTEGRITY", "Evidence integrity check failed.", 503)
    return content
