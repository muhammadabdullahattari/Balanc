"""Private streaming evidence storage adapter.

Supports chunked encryption, S3 shared object storage, and replica-safe operations.
"""

import hashlib
import logging
import os
import socket
import struct
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError

SCAN_DEADLINE_SECONDS = 10.0
CHUNK_SIZE = 65536
CHUNKED_MAGIC = b"LPENC1"

logger = logging.getLogger(__name__)


def scan_stream(stream: Iterable[bytes], settings: Settings) -> str:
    """Stream chunks to clamd zINSTREAM without buffering the full file in RAM."""
    deadline = time.monotonic() + SCAN_DEADLINE_SECONDS

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Scanner deadline exceeded")
        return value

    def send(conn: socket.socket, value: bytes) -> None:
        conn.settimeout(remaining())
        conn.sendall(value)

    def reply(conn: socket.socket, command_id: int) -> bytes:
        value = bytearray()
        while len(value) < 4096:
            conn.settimeout(remaining())
            part = conn.recv(min(1024, 4096 - len(value)))
            if not part:
                break
            value.extend(part)
            if b"\0" in value:
                prefix = f"{command_id}: ".encode()
                if value.endswith(b"\0") and value.count(0) == 1 and value.startswith(prefix):
                    return bytes(value[len(prefix) : -1])
                break
        raise DomainError("ERR_SCAN_UNAVAILABLE", "Scanner response could not be verified.", 503)

    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=remaining()
        ) as conn:
            send(conn, b"zIDSESSION\0zVERSION\0")
            version = reply(conn, 1).decode("ascii", errors="replace")
            if not version.startswith("ClamAV ") or len(version) > 160:
                raise DomainError(
                    "ERR_SCAN_UNAVAILABLE", "Scanner version could not be verified.", 503
                )
            send(conn, b"zINSTREAM\0")
            for chunk in stream:
                if chunk:
                    send(conn, struct.pack("!I", len(chunk)) + chunk)
            send(conn, struct.pack("!I", 0))
            verdict = reply(conn, 2)
            send(conn, b"zEND\0")
        if verdict.startswith(b"stream: ") and verdict.endswith(b" FOUND"):
            raise DomainError("ERR_MALWARE", "The upload was rejected by malware scanning.", 422)
        if verdict != b"stream: OK":
            raise DomainError(
                "ERR_SCAN_UNAVAILABLE", "Upload scanning did not confirm a clean result.", 503
            )
        return version
    except OSError:
        raise DomainError(
            "ERR_SCAN_UNAVAILABLE", "The local malware scanner is unavailable.", 503
        ) from None


def scan(content: bytes, settings: Settings) -> str:
    """Obtain version and positive INSTREAM verdict in one clamd IDSESSION."""
    def chunk_iter() -> Iterator[bytes]:
        for start in range(0, len(content), CHUNK_SIZE):
            yield content[start : start + CHUNK_SIZE]

    return scan_stream(chunk_iter(), settings)


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


def get_s3_client(settings: Settings):  # type: ignore[no-untyped-def]
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]
    except ImportError:
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "S3 object storage driver is not installed.", 503
        ) from None

    ak = settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None
    sk = settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None
    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        use_ssl=settings.s3_use_ssl,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_to_s3(settings: Settings, key: str, local_path: Path) -> None:
    try:
        client = get_s3_client(settings)
        client.upload_file(str(local_path), settings.s3_bucket, key)
    except Exception as exc:
        logger.error("Failed to upload object to S3: %s", exc)
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Failed to persist evidence to shared object storage.", 503
        ) from exc


def fetch_from_s3(settings: Settings, key: str, dest_path: Path) -> bool:
    try:
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]
    except ImportError:
        return False

    try:
        client = get_s3_client(settings)
        dest_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_dest = dest_path.with_name(f"{dest_path.name}.fetch.{uuid4().hex}")
        client.download_file(settings.s3_bucket, key, str(temp_dest))
        os.chmod(temp_dest, 0o600)
        temp_dest.replace(dest_path)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey"}:
            return False
        logger.error("Failed to fetch object from S3: %s", exc)
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Shared object storage read error.", 503
        ) from exc
    except Exception as exc:
        logger.error("Failed to fetch object from S3: %s", exc)
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Shared object storage read error.", 503
        ) from exc


def delete_from_s3(settings: Settings, key: str) -> None:
    try:
        client = get_s3_client(settings)
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception as exc:
        logger.warning("Failed to delete object from S3: %s", exc)


def exists_in_s3(settings: Settings, key: str) -> bool:
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return False

    try:
        client = get_s3_client(settings)
        client.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError:
        return False
    except Exception:
        return False


def put_stream_sync(
    settings: Settings,
    bid: UUID,
    eid: UUID,
    stream: Iterable[bytes],
    max_bytes: int | None = None,
) -> tuple[str, str, int]:
    """Stream chunks to storage with rolling SHA-256 without buffering entire file in RAM."""
    key = f"{bid}/{eid}.bin"
    target = path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_target = target.with_name(f"{target.name}.tmp.{uuid4().hex}")

    f_cipher = cipher(settings)
    hasher = hashlib.sha256()
    total_size = 0

    try:
        with temp_target.open("xb") as file:
            os.chmod(temp_target, 0o600)
            file.write(CHUNKED_MAGIC)
            for chunk in stream:
                if not chunk:
                    continue
                total_size += len(chunk)
                if max_bytes is not None and total_size > max_bytes:
                    raise DomainError("ERR_FILE_SIZE", "Upload exceeds the configured cap.", 413)
                hasher.update(chunk)
                encrypted_chunk = f_cipher.encrypt(chunk)
                file.write(struct.pack("!I", len(encrypted_chunk)) + encrypted_chunk)
            file.flush()
            os.fsync(file.fileno())

        temp_target.replace(target)
    except DomainError:
        temp_target.unlink(missing_ok=True)
        raise
    except OSError:
        temp_target.unlink(missing_ok=True)
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Private evidence storage is unavailable.", 503
        ) from None

    if settings.evidence_storage_backend == "s3":
        upload_to_s3(settings, key, target)

    return key, hasher.hexdigest(), total_size


async def put_stream_async(
    settings: Settings,
    bid: UUID,
    eid: UUID,
    stream: AsyncIterator[bytes],
    max_bytes: int | None = None,
) -> tuple[str, str, int]:
    """Async variant: streams incoming HTTP body chunks to storage with rolling SHA-256."""
    key = f"{bid}/{eid}.bin"
    target = path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_target = target.with_name(f"{target.name}.tmp.{uuid4().hex}")

    f_cipher = cipher(settings)
    hasher = hashlib.sha256()
    total_size = 0

    try:
        with temp_target.open("xb") as file:
            os.chmod(temp_target, 0o600)
            file.write(CHUNKED_MAGIC)
            async for chunk in stream:
                if not chunk:
                    continue
                total_size += len(chunk)
                if max_bytes is not None and total_size > max_bytes:
                    raise DomainError("ERR_FILE_SIZE", "Upload exceeds the configured cap.", 413)
                hasher.update(chunk)
                encrypted_chunk = f_cipher.encrypt(chunk)
                file.write(struct.pack("!I", len(encrypted_chunk)) + encrypted_chunk)
            file.flush()
            os.fsync(file.fileno())

        temp_target.replace(target)
    except DomainError:
        temp_target.unlink(missing_ok=True)
        raise
    except OSError:
        temp_target.unlink(missing_ok=True)
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Private evidence storage is unavailable.", 503
        ) from None

    if settings.evidence_storage_backend == "s3":
        upload_to_s3(settings, key, target)

    return key, hasher.hexdigest(), total_size


def put(settings: Settings, bid: UUID, eid: UUID, content: bytes) -> tuple[str, str]:
    """Compatibility put: writes full bytes in chunked format."""
    def chunk_iter() -> Iterator[bytes]:
        for start in range(0, len(content), CHUNK_SIZE):
            yield content[start : start + CHUNK_SIZE]

    key, digest, _ = put_stream_sync(settings, bid, eid, chunk_iter())
    return key, digest


def get_stream(settings: Settings, key: str, expected_hash: str | None = None) -> Iterator[bytes]:
    """Stream decrypted chunks; replica recovery fetches missing files from shared S3 storage."""
    target = path(settings, key)
    if not target.exists() and settings.evidence_storage_backend == "s3":
        fetch_from_s3(settings, key, target)

    if not target.exists():
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Evidence could not be read and verified.", 503
        )

    f_cipher = cipher(settings)
    hasher = hashlib.sha256() if expected_hash else None

    try:
        with target.open("rb") as file:
            header = file.read(len(CHUNKED_MAGIC))
            if header == CHUNKED_MAGIC:
                while True:
                    len_bytes = file.read(4)
                    if not len_bytes:
                        break
                    (chunk_len,) = struct.unpack("!I", len_bytes)
                    enc_chunk = file.read(chunk_len)
                    if len(enc_chunk) != chunk_len:
                        raise DomainError(
                            "ERR_INTEGRITY", "Truncated chunk in evidence store.", 503
                        )
                    chunk = f_cipher.decrypt(enc_chunk)
                    if hasher:
                        hasher.update(chunk)
                    yield chunk
            else:
                # Legacy unchunked file fallback
                file.seek(0)
                raw_encrypted = file.read()
                content = f_cipher.decrypt(raw_encrypted)
                if hasher:
                    hasher.update(content)
                yield content
    except (OSError, InvalidToken):
        raise DomainError(
            "ERR_STORAGE_UNAVAILABLE", "Evidence could not be read and verified.", 503
        ) from None

    if hasher and hasher.hexdigest() != expected_hash:
        raise DomainError("ERR_INTEGRITY", "Evidence integrity check failed.", 503)


def get(settings: Settings, key: str, expected_hash: str) -> bytes:
    """Retrieve full content by consuming get_stream and verifying expected SHA-256 hash."""
    return b"".join(get_stream(settings, key, expected_hash=expected_hash))


def delete(settings: Settings, key: str) -> None:
    """Delete evidence from local replica and shared S3 storage."""
    target = path(settings, key)
    target.unlink(missing_ok=True)
    if settings.evidence_storage_backend == "s3":
        delete_from_s3(settings, key)


def exists(settings: Settings, key: str) -> bool:
    """Check if evidence exists in local filesystem or shared S3 storage."""
    target = path(settings, key)
    if target.exists() and target.stat().st_size > 0:
        return True
    if settings.evidence_storage_backend == "s3":
        return exists_in_s3(settings, key)
    return False
