"""Isolated local Tesseract/Poppler parser; no DB, financial tools or credentials.

This module runs in the dedicated resource-limited OCR image. Its only interface
accepts bounded bytes after the worker's clean ClamAV verdict.
"""

import csv
import hashlib
import io
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
SLOTS = threading.BoundedSemaphore(1)
SUPPORTED = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}


def execute(args: list[str], deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("OCR deadline exhausted")
    result = subprocess.run(
        args,
        capture_output=True,
        check=True,
        timeout=min(remaining, 30),
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    if len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError("Parser output cap exceeded")
    return result.stdout.decode("utf-8", errors="replace")


@lru_cache
def version() -> str:
    engine = execute(["tesseract", "--version"], time.monotonic() + 5).splitlines()[0]
    models = list(Path("/usr/share/tesseract-ocr").glob("*/tessdata/eng.traineddata"))
    if len(models) != 1:
        raise ValueError("English model must resolve to one pinned installed file")
    digest = hashlib.sha256(models[0].read_bytes()).hexdigest()
    return f"{engine};eng.sha256={digest}"


def unsupported(reason: str) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "model_version": None,
        "extractor_version": "local-ocr-v1",
        "language": "eng",
        "confidence_kind": "uncalibrated_ocr_score",
        "raw_text": None,
        "fields": [],
        "reason": reason,
        "requires_review": True,
    }


def embedded_pdf(source: Path, root: Path, deadline: float) -> dict[str, Any] | None:
    output = root / "embedded.xhtml"
    execute(["pdftotext", "-f", "1", "-l", "10", "-bbox", str(source), str(output)], deadline)
    if output.stat().st_size > 8 * 1024 * 1024:
        return None
    document = ET.fromstring(output.read_bytes())
    fields: list[dict[str, Any]] = []
    words = []
    for page_index, page in enumerate(document.findall(".//{*}page"), 1):
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        if width <= 0 or height <= 0:
            continue
        for word in page.findall(".//{*}word"):
            text = word.text or ""
            left, top = float(word.attrib["xMin"]), float(word.attrib["yMin"])
            right, bottom = float(word.attrib["xMax"]), float(word.attrib["yMax"])
            if not text.strip() or not (0 <= left < right <= width and 0 <= top < bottom <= height):
                continue
            kind, value = typed_value(text)
            fields.append(
                {
                    "name": f"candidate-{len(fields) + 1}",
                    "value": value[:1000],
                    "value_type": kind,
                    "page": page_index,
                    "left": left / width,
                    "top": top / height,
                    "right": right / width,
                    "bottom": bottom / height,
                    "confidence": None,
                    "semantic_role": "unassigned",
                }
            )
            words.append(text)
            if len(fields) >= 20000:
                return None
    if not fields:
        return None
    binary = subprocess.run(["pdftotext", "-v"], check=True, capture_output=True, timeout=5)
    parser_version = binary.stderr.decode(errors="replace").splitlines()[0]
    return {
        "status": "extracted",
        "model_version": "embedded-text/" + parser_version,
        "extractor_version": "local-embedded-v1",
        "language": "und",
        "confidence_kind": "embedded_text_no_probability",
        "raw_text": " ".join(words)[:200000],
        "fields": fields,
        "reason": None,
        "requires_review": True,
    }


def typed_value(text: str) -> tuple[str, str]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            date.fromisoformat(text)
            return "date", text
        except ValueError:
            pass
    if re.fullmatch(r"\$?[0-9]{1,18}\.[0-9]{1,6}", text):
        return "amount", text.lstrip("$")
    return "text", text


def image_size_allowed(image: Path) -> bool:
    data = image.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return 0 < width * height <= 16000000
    if data.startswith(b"\xff\xd8"):
        position = 2
        while position + 4 < min(len(data), 1048576):
            if data[position] != 255:
                return False
            while position < len(data) and data[position] == 255:
                position += 1
            if position >= len(data):
                return False
            marker = data[position]
            position += 1
            if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if marker in {0xDA, 0xD9} or position + 2 > len(data):
                return False
            size = int.from_bytes(data[position : position + 2], "big")
            if size < 2 or position + size > len(data):
                return False
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                height = int.from_bytes(data[position + 3 : position + 5], "big")
                width = int.from_bytes(data[position + 5 : position + 7], "big")
                return 0 < width * height <= 16000000
            position += size
    return False


def parse(content: bytes, media_type: str) -> dict[str, Any]:
    if media_type not in SUPPORTED:
        return unsupported("Supported automatic matrix: English PDF, PNG and JPEG only.")
    deadline = time.monotonic() + 85
    with tempfile.TemporaryDirectory(prefix="ledgerpilot-ocr-") as directory:
        root = Path(directory)
        source = root / ("original" + SUPPORTED[media_type])
        source.write_bytes(content)
        images = [source]
        if media_type == "application/pdf":
            metadata = execute(["pdfinfo", str(source)], deadline)
            match = re.search(r"^Pages:\s+(\d+)\s*$", metadata, re.MULTILINE)
            if not match or not 1 <= int(match.group(1)) <= 10:
                return unsupported("The local PDF profile supports one to ten pages.")
            embedded = embedded_pdf(source, root, deadline)
            if embedded:
                return embedded
            execute(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    match.group(1),
                    "-scale-to",
                    "2400",
                    "-gray",
                    "-png",
                    str(source),
                    str(root / "page"),
                ],
                deadline,
            )
            images = sorted(root.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        fields: list[dict[str, Any]] = []
        text = []
        for page_number, image in enumerate(images, 1):
            if not image_size_allowed(image):
                return unsupported(
                    "Malformed image or image dimensions outside the 16-megapixel profile."
                )
            tsv = execute(
                ["tesseract", str(image), "stdout", "-l", "eng", "--psm", "3", "tsv"], deadline
            )
            rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
            page = next((r for r in rows if r["level"] == "1"), None)
            if page is None:
                continue
            width, height = int(page["width"]), int(page["height"])
            if width <= 0 or height <= 0 or width * height > 16000000:
                return unsupported("Image dimensions exceed the approved local profile.")
            for row in rows:
                value = row.get("text", "").strip()
                if row["level"] != "5" or not value:
                    continue
                if len(fields) >= 20000:
                    return unsupported("The extraction word limit was exceeded.")
                left, top = int(row["left"]), int(row["top"])
                right, bottom = left + int(row["width"]), top + int(row["height"])
                if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                    continue
                kind, value = typed_value(value)
                fields.append(
                    {
                        "name": f"candidate-{len(fields) + 1}",
                        "value": value[:1000],
                        "value_type": kind,
                        "page": page_number,
                        "left": left / width,
                        "top": top / height,
                        "right": right / width,
                        "bottom": bottom / height,
                        "confidence": max(0, min(1, float(row["conf"]) / 100)),
                        "semantic_role": "unassigned",
                    }
                )
                text.append(row["text"])
        if not fields:
            return unsupported(
                "No readable English text was found; manual review remains available."
            )
        return {
            "status": "extracted",
            "model_version": version(),
            "extractor_version": "local-ocr-v1",
            "language": "eng",
            "confidence_kind": "uncalibrated_ocr_score",
            "raw_text": " ".join(text)[:200000],
            "fields": fields,
            "reason": None,
            "requires_review": True,
        }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model_version": version()}


@app.post("/extract")
async def extract(request: Request) -> JSONResponse:
    token = os.environ.get("OCR_SERVICE_TOKEN", "")
    provided = request.headers.get("authorization", "")
    if len(token) < 32 or not secrets.compare_digest(provided, "Bearer " + token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not SLOTS.acquire(blocking=False):
        return JSONResponse({"error": "busy"}, status_code=503)
    try:
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 26214400:
                return JSONResponse({"error": "file_size"}, status_code=413)
            chunks.append(chunk)
        try:
            result = await run_in_threadpool(
                parse, b"".join(chunks), request.headers.get("content-type", "")
            )
            return JSONResponse(result)
        except (
            OSError,
            ValueError,
            TimeoutError,
            subprocess.SubprocessError,
            KeyError,
            ET.ParseError,
        ):
            return JSONResponse(unsupported("The parser could not safely extract this input."))
    finally:
        SLOTS.release()
