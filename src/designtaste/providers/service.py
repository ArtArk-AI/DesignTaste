#!/usr/bin/env python3
"""Call Azure, Baidu VOD nano-banana-pro, Volcengine Seedream, and ArtArk models.

Examples:
    python -m designtaste.providers.service text "Describe this poster" --image poster.jpg
    python -m designtaste.providers.service image "A red paper kite" --output kite.png
    python -m designtaste.providers.service image "A red paper kite" --model artark --tier L3 --output kite.png
    python -m designtaste.providers.service image "A red paper kite" --model nano-banana-pro --quality 2K --output kite.png
    python -m designtaste.providers.service image "A red paper kite" --model doubao-seedream-5-0-pro-260628 --output kite.png

Provider service classes are also available for use in other scripts.
"""

import argparse
import base64
import binascii
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import parse_qsl, quote, urlsplit
from uuid import UUID, uuid4

import httpx


LANGUAGE_MODELS = ("gpt-6.1-sol", "gpt-5.6-terra", "gpt-6-luna")
AZURE_IMAGE_MODELS = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2")
NANO_BANANA_PRO_MODEL = "nano-banana-pro"
_BAIDU_PROVIDER_MODEL_ID = "".join(("N", "B", "2"))
VOLCENGINE_SEEDREAM_MODEL = "doubao-seedream-5-0-pro-260628"
IMAGE_MODELS = AZURE_IMAGE_MODELS + (NANO_BANANA_PRO_MODEL, VOLCENGINE_SEEDREAM_MODEL)
IMAGE_MODEL_QUALITY_OPTIONS = {
    "gpt-image-2.5-flare": ("auto", "low", "medium", "high", "xhigh", "max"),
    "gpt-image-2.5-sunburst": ("auto", "low", "medium", "high", "xhigh", "max"),
    "gpt-image-2": ("auto", "low", "medium", "high"),
    NANO_BANANA_PRO_MODEL: ("512", "1K", "2K", "4K"),
}
IMAGE_QUALITY_OPTIONS = tuple(dict.fromkeys(
    quality for options in IMAGE_MODEL_QUALITY_OPTIONS.values() for quality in options
))
IMAGE_MODEL_MAX_CONCURRENCY = {model: 10 for model in AZURE_IMAGE_MODELS}
IMAGE_MODEL_MAX_CONCURRENCY["gpt-image-2"] = 2
DEFAULT_TEXT_MODEL = "gpt-6-luna"
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare"
ARTARK_MODEL = "artark"
ARTARK_TIERS = ("L1", "L2", "L3")
ARTARK_MAX_ACTIVE_TASKS = 10
LIMITS_PER_MINUTE = {"image": 10, "language": 100}
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / ".azure_openai.env"
ARTARK_CONFIG_FILE = ROOT / ".artark.env"
BAIDU_VOD_CONFIG_FILE = ROOT / ".baidu_vod.env"
VOLCENGINE_CONFIG_FILE = ROOT / ".volcengine.env"
ARTARK_DEFAULT_ENDPOINT = "https://open.api.designgpt.art/api/v1"
BAIDU_VOD_DEFAULT_ENDPOINT = "https://vod.bj.baidubce.com"
BAIDU_VOD_DEFAULT_TASK_PATH = "/v2/tasks/{task_id}"
VOLCENGINE_DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3"
RATE_DB = ROOT / ".designtaste_rate_limit.sqlite3"


class ModelServiceError(RuntimeError):
    pass


def _file_settings(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ModelServiceError("{} must be readable only by its owner (chmod 600)".format(path.name))
    settings = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            if not separator:
                raise ModelServiceError("Invalid line in " + path.name)
            settings[key.strip()] = value.strip()
    return settings


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "No error details returned"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error.get("code") or "No error details returned")
    return str(payload.get("message") or payload.get("detail") or "No error details returned")


def _find_image_result(value: Any) -> Optional[tuple]:
    """Find an image URL or base64 image in provider response structures."""
    if isinstance(value, dict):
        for key in ("result", "output", "images", "final_images", "content"):
            if key in value:
                found = _find_image_result(value[key])
                if found:
                    return found
        for key in ("b64_json", "image_base64", "image_data"):
            encoded = value.get(key)
            if isinstance(encoded, str) and encoded:
                if encoded.startswith("data:image/"):
                    return "url", encoded
                return "base64", encoded
        for key in ("image_url", "url", "image"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("https://", "data:image/")):
                return "url", candidate
            if isinstance(candidate, dict):
                found = _find_image_result(candidate)
                if found:
                    return found
        for key, item in value.items():
            if key not in {"text", "prompt", "message", "result", "output", "images", "final_images", "content"}:
                found = _find_image_result(item)
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _find_image_result(item)
            if found:
                return found
    elif isinstance(value, str) and value.startswith(("https://", "data:image/")):
        return "url", value
    return None


def _decode_image_result(kind: str, value: str) -> bytes:
    if kind == "base64":
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ModelServiceError("Provider returned invalid base64 image data") from exc
    if value.startswith("data:image/"):
        _, separator, encoded = value.partition(",")
        if not separator or ";base64" not in value.split(",", 1)[0]:
            raise ModelServiceError("Provider returned an unsupported image data URI")
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ModelServiceError("Provider returned invalid image data") from exc
    return b""


class SlidingWindowLimiter:
    """Share strict rolling-minute request counts across local processes."""

    def __init__(self, path: Path = RATE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests "
                "(category TEXT NOT NULL, requested_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS requests_window "
                "ON requests (category, requested_at)"
            )
            db.commit()
        os.chmod(str(self.path), 0o600)

    def acquire(self, category: str) -> None:
        limit = LIMITS_PER_MINUTE[category]
        while True:
            with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
                db.execute("BEGIN IMMEDIATE")
                now = time.time()
                db.execute("DELETE FROM requests WHERE requested_at <= ?", (now - 60,))
                count, oldest = db.execute(
                    "SELECT COUNT(*), MIN(requested_at) FROM requests WHERE category = ?",
                    (category,),
                ).fetchone()
                if count < limit:
                    db.execute(
                        "INSERT INTO requests (category, requested_at) VALUES (?, ?)",
                        (category, now),
                    )
                    db.commit()
                    return
                wait = max(0.01, oldest + 60 - now)
                db.commit()
            time.sleep(wait)


class ArtArkTaskSlots:
    """Track queued and running ArtArk tasks across local processes."""

    def __init__(self, path: Path = RATE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            # Serialize schema migration across concurrent ArtArk workers.
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artark_active_tasks "
                "(request_id TEXT PRIMARY KEY, task_id TEXT UNIQUE, reserved_at REAL NOT NULL, request_hash TEXT)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(artark_active_tasks)")}
            if "request_hash" not in columns:
                db.execute("ALTER TABLE artark_active_tasks ADD COLUMN request_hash TEXT")
            db.commit()
        os.chmod(str(self.path), 0o600)

    def try_reserve(self, request_id: str, request_hash: Optional[str] = None) -> bool:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            db.execute("BEGIN IMMEDIATE")
            # A process may exit before it receives and records the task ID.
            db.execute(
                "DELETE FROM artark_active_tasks WHERE task_id IS NULL AND reserved_at <= ?",
                (time.time() - 120,),
            )
            db.execute(
                "DELETE FROM artark_active_tasks WHERE reserved_at <= ?",
                (time.time() - 24 * 3600,),
            )
            if db.execute(
                "SELECT 1 FROM artark_active_tasks WHERE request_id = ?", (request_id,)
            ).fetchone():
                raise ModelServiceError("ArtArk request ID is already active: " + request_id)
            count = db.execute("SELECT COUNT(*) FROM artark_active_tasks").fetchone()[0]
            if count < ARTARK_MAX_ACTIVE_TASKS:
                db.execute(
                    "INSERT INTO artark_active_tasks (request_id, task_id, reserved_at, request_hash) "
                    "VALUES (?, NULL, ?, ?)",
                    (request_id, time.time(), request_hash),
                )
                db.commit()
                return True
            db.commit()
            return False

    def attach(self, request_id: str, task_id: str) -> None:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            cursor = db.execute(
                "UPDATE artark_active_tasks SET task_id = ? WHERE request_id = ?",
                (task_id, request_id),
            )
            db.commit()
            if cursor.rowcount != 1:
                raise ModelServiceError("ArtArk task reservation was lost: " + request_id)

    def lookup_request(self, request_id: str, request_hash: Optional[str] = None) -> Optional[str]:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            db.execute(
                "DELETE FROM artark_active_tasks WHERE task_id IS NULL AND reserved_at <= ?",
                (time.time() - 120,),
            )
            row = db.execute(
                "SELECT task_id, request_hash FROM artark_active_tasks WHERE request_id = ?", (request_id,)
            ).fetchone()
            db.commit()
            if row and request_hash and row[1] and request_hash != row[1]:
                raise ModelServiceError("ArtArk request ID was used with different content: " + request_id)
            if row and request_hash and not row[1]:
                raise ModelServiceError("Cannot safely resume an older ArtArk request without its content hash: " + request_id)
            return row[0] if row else None

    def active_task_ids(self) -> List[str]:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            return [row[0] for row in db.execute("SELECT task_id FROM artark_active_tasks WHERE task_id IS NOT NULL")]

    def release_request(self, request_id: str) -> None:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            db.execute("DELETE FROM artark_active_tasks WHERE request_id = ?", (request_id,))
            db.commit()

    def release_task(self, task_id: str) -> None:
        with closing(sqlite3.connect(str(self.path), timeout=30)) as db:
            db.execute("DELETE FROM artark_active_tasks WHERE task_id = ?", (task_id,))
            db.commit()


class ModelService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        proxy: Optional[str] = None,
        rate_db: Path = RATE_DB,
    ) -> None:
        settings = _file_settings(CONFIG_FILE) if not all((api_key, endpoint, proxy)) else {}
        self.api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY") or settings.get("AZURE_OPENAI_API_KEY")
        self.endpoint = (endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or settings.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
        self.proxy = proxy or os.environ.get("AZURE_OPENAI_PROXY") or settings.get("AZURE_OPENAI_PROXY") or "http://127.0.0.1:7895"
        if not self.api_key:
            raise ModelServiceError("Set AZURE_OPENAI_API_KEY or add it to .azure_openai.env")
        if urlsplit(self.endpoint).scheme != "https":
            raise ModelServiceError("AZURE_OPENAI_ENDPOINT must be an HTTPS URL")
        if urlsplit(self.proxy).scheme != "http":
            raise ModelServiceError("AZURE_OPENAI_PROXY must be an HTTP proxy URL")
        self._limiter = SlidingWindowLimiter(rate_db)
        self._http = httpx.Client(
            proxy=self.proxy,
            trust_env=False,
            timeout=httpx.Timeout(120, connect=15),
            headers={"Authorization": "Bearer " + self.api_key},
        )
        self._download_http = httpx.Client(proxy=self.proxy, trust_env=False, timeout=60)

    def __enter__(self) -> "ModelService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._download_http.close()

    def _post(
        self, path: str, body: Dict[str, Any], category: str,
        timings: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        total_rate_wait = 0.0
        total_api_response = 0.0
        max_rate_limit_retries = 3
        for attempt in range(max_rate_limit_retries + 1):
            wait_started = time.monotonic()
            self._limiter.acquire(category)
            total_rate_wait += time.monotonic() - wait_started
            request_started = time.monotonic()
            try:
                response = self._http.post(self.endpoint + path, json=body)
            except httpx.HTTPError as exc:
                total_api_response += time.monotonic() - request_started
                if timings is not None:
                    timings["rate_limit_wait_seconds"] = total_rate_wait
                    timings["api_response_seconds"] = total_api_response
                raise ModelServiceError("Azure request failed through the configured proxy: " + str(exc)) from exc
            total_api_response += time.monotonic() - request_started
            try:
                data = response.json()
            except ValueError as exc:
                if timings is not None:
                    timings["rate_limit_wait_seconds"] = total_rate_wait
                    timings["api_response_seconds"] = total_api_response
                raise ModelServiceError(
                    "Azure returned a non-JSON response (HTTP {})".format(response.status_code)
                ) from exc
            if response.status_code == 429 and attempt < max_rate_limit_retries:
                error = data.get("error", {}) if isinstance(data, dict) else {}
                message = error.get("message") or ""
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else None
                except ValueError:
                    delay = None
                if delay is None:
                    match = re.search(r"retry after\s+(\d+(?:\.\d+)?)\s+seconds?", message, re.IGNORECASE)
                    delay = float(match.group(1)) if match else min(2 ** (attempt + 1), 30)
                delay = max(0.0, delay)
                time.sleep(delay)
                total_rate_wait += delay
                continue
            if response.status_code >= 400:
                error = data.get("error", {}) if isinstance(data, dict) else {}
                code = error.get("code") or error.get("type") or "request_failed"
                message = error.get("message") or "No error details returned"
                if timings is not None:
                    timings["rate_limit_wait_seconds"] = total_rate_wait
                    timings["api_response_seconds"] = total_api_response
                raise ModelServiceError("Azure HTTP {} ({}): {}".format(response.status_code, code, message))
            if not isinstance(data, dict):
                if timings is not None:
                    timings["rate_limit_wait_seconds"] = total_rate_wait
                    timings["api_response_seconds"] = total_api_response
                raise ModelServiceError("Azure returned an unexpected response format")
            if timings is not None:
                timings["rate_limit_wait_seconds"] = total_rate_wait
                timings["api_response_seconds"] = total_api_response
            return data
        raise ModelServiceError("Azure rate-limit retries exhausted")

    def create_response(
        self,
        input_data: Union[str, Sequence[Dict[str, Any]]],
        model: str = DEFAULT_TEXT_MODEL,
        instructions: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        if model not in LANGUAGE_MODELS:
            raise ValueError("Unknown language deployment: " + model)
        body = {"model": model, "input": input_data}
        if instructions is not None:
            body["instructions"] = instructions
        if max_output_tokens is not None:
            if max_output_tokens < 1:
                raise ValueError("max_output_tokens must be positive")
            body["max_output_tokens"] = max_output_tokens
        return self._post("/responses", body, "language")

    def generate_text(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: Sequence[Union[str, Path]] = (),
        instructions: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
    ) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        input_data = prompt
        if images:
            content = [{"type": "input_text", "text": prompt}]
            for image in images:
                path = Path(image)
                mime = mimetypes.guess_type(path.name)[0]
                if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                    raise ValueError("Unsupported image type: " + str(path))
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "input_image", "image_url": "data:{};base64,{}".format(mime, encoded)})
            input_data = [{"role": "user", "content": content}]
        result = self.create_response(input_data, model, instructions, max_output_tokens)
        text = "".join(
            part.get("text", "")
            for item in result.get("output", [])
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        if not text:
            raise ModelServiceError("Azure returned no text (response status: {})".format(result.get("status", "unknown")))
        return text

    def generate_image(
        self,
        prompt: str,
        output: Union[str, Path],
        model: str = DEFAULT_IMAGE_MODEL,
        size: Optional[str] = None,
        timings: Optional[Dict[str, float]] = None,
        quality: Optional[str] = None,
    ) -> Path:
        if model not in AZURE_IMAGE_MODELS:
            raise ValueError("Unknown image deployment: " + model)
        if quality is not None and quality not in IMAGE_MODEL_QUALITY_OPTIONS[model]:
            raise ValueError(
                "Unsupported quality for {}: {} (choose {})".format(
                    model, quality, ", ".join(IMAGE_MODEL_QUALITY_OPTIONS[model])
                )
            )
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        output_path = Path(output)
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError(str(output_path))
        body = {"model": model, "prompt": prompt, "n": 1}
        if size is not None:
            body["size"] = size
        if quality is not None:
            body["quality"] = quality
        result = self._post("/images/generations", body, "image", timings)
        entries = result.get("data") or []
        if not entries:
            raise ModelServiceError("Azure returned no generated image")
        first = entries[0]
        if first.get("b64_json"):
            try:
                image_bytes = base64.b64decode(first["b64_json"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ModelServiceError("Azure returned invalid image data") from exc
        elif first.get("url"):
            image_url = first["url"]
            if urlsplit(image_url).scheme != "https":
                raise ModelServiceError("Azure returned an insecure image URL")
            download_started = time.monotonic()
            try:
                image_response = self._download_http.get(image_url)
                image_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ModelServiceError("Failed to download the generated image: " + str(exc)) from exc
            finally:
                if timings is not None:
                    timings["download_seconds"] = time.monotonic() - download_started
            image_bytes = image_response.content
        else:
            raise ModelServiceError("Azure returned no image bytes or URL")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as file:
            file.write(image_bytes)
        return output_path


class BaiduNanoBananaProService:
    """Generate images with Baidu VOD's asynchronous nano-banana-pro API."""

    SUPPORTED_SIZES = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
                       "1:4", "1:8", "4:1", "8:1")
    SUPPORTED_QUALITIES = IMAGE_MODEL_QUALITY_OPTIONS[NANO_BANANA_PRO_MODEL]
    SUCCESS_STATES = {"success", "succeeded", "complete", "completed", "finished", "done"}
    FAILURE_STATES = {"failed", "failure", "error", "cancelled", "canceled"}

    def __init__(
        self,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        endpoint: Optional[str] = None,
        task_path: Optional[str] = None,
        rate_db: Path = RATE_DB,
    ) -> None:
        settings = _file_settings(BAIDU_VOD_CONFIG_FILE) if not (access_key_id and access_key_secret) else {}
        self.access_key_id = access_key_id or os.environ.get("BAIDU_VOD_ACCESS_KEY_ID") or settings.get("BAIDU_VOD_ACCESS_KEY_ID")
        self.access_key_secret = access_key_secret or os.environ.get("BAIDU_VOD_ACCESS_KEY_SECRET") or settings.get("BAIDU_VOD_ACCESS_KEY_SECRET")
        self.endpoint = (endpoint or os.environ.get("BAIDU_VOD_ENDPOINT") or BAIDU_VOD_DEFAULT_ENDPOINT).rstrip("/")
        self.task_path = task_path or os.environ.get("BAIDU_VOD_TASK_PATH") or BAIDU_VOD_DEFAULT_TASK_PATH
        if not self.access_key_id or not self.access_key_secret:
            raise ModelServiceError(
                "Set BAIDU_VOD_ACCESS_KEY_ID and BAIDU_VOD_ACCESS_KEY_SECRET, or add them to .baidu_vod.env"
            )
        if urlsplit(self.endpoint).scheme != "https":
            raise ModelServiceError("BAIDU_VOD_ENDPOINT must be an HTTPS URL")
        if not self.task_path.startswith("/") or "{task_id}" not in self.task_path:
            raise ModelServiceError("BAIDU_VOD_TASK_PATH must be an absolute path containing {task_id}")
        self._limiter = SlidingWindowLimiter(rate_db)
        self._http = httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(120, connect=15),
        )
        self._download_http = httpx.Client(trust_env=False, timeout=httpx.Timeout(120, connect=15))

    def __enter__(self) -> "BaiduNanoBananaProService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._download_http.close()

    def _authorization(self, method: str, path: str, date: str, request_id: str) -> str:
        host = urlsplit(self.endpoint).netloc.lower()
        prefix = "bce-auth-v1/{}/{}/1800".format(self.access_key_id, date)
        signing_key = hmac.new(
            self.access_key_secret.encode("utf-8"), prefix.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        parsed_path = urlsplit(path)
        canonical_uri = quote(parsed_path.path or "/", safe="/-_.~")
        canonical_query = "&".join(
            "{}={}".format(quote(key, safe="-_.~"), quote(value, safe="-_.~"))
            for key, value in sorted(
                (parse_qsl(parsed_path.query, keep_blank_values=True)),
                key=lambda item: (quote(item[0], safe="-_.~"), quote(item[1], safe="-_.~")),
            )
        )
        # Sign exactly the two headers listed in the Authorization value below.
        # x-bce-request-id is sent for tracing, but is intentionally not part of
        # this explicit signed-header set; including it here while omitting it
        # from signedHeaders makes the server canonicalize a different request.
        canonical_headers = "host:{}\nx-bce-date:{}".format(
            quote(host, safe="-_.~"), quote(date, safe="-_.~")
        )
        canonical_request = "{}\n{}\n{}\n{}".format(
            method.upper(), canonical_uri, canonical_query, canonical_headers
        )
        signature = hmac.new(
            signing_key.encode("utf-8"), canonical_request.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return "{}/host;x-bce-date/{}".format(prefix, signature)

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 timings: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        request_id = str(uuid4())
        headers = {
            "Authorization": self._authorization(method, path, date, request_id),
            "Content-Type": "application/json",
            "x-bce-date": date,
            "x-bce-request-id": request_id,
        }
        started = time.monotonic()
        try:
            if body is None:
                response = self._http.request(method, self.endpoint + path, headers=headers)
            else:
                response = self._http.request(method, self.endpoint + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            if timings is not None:
                timings["api_response_seconds"] = timings.get("api_response_seconds", 0.0) + time.monotonic() - started
            raise ModelServiceError("Baidu VOD request failed: " + str(exc)) from exc
        elapsed = time.monotonic() - started
        if timings is not None:
            timings["api_response_seconds"] = timings.get("api_response_seconds", 0.0) + elapsed
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelServiceError("Baidu VOD returned non-JSON data (HTTP {})".format(response.status_code)) from exc
        if response.status_code >= 400:
            code = payload.get("code", "request_failed") if isinstance(payload, dict) else "request_failed"
            raise ModelServiceError("Baidu VOD HTTP {} ({}): {}".format(
                response.status_code, code, _error_message(payload)
            ))
        if not isinstance(payload, dict):
            raise ModelServiceError("Baidu VOD returned an unexpected response format")
        return payload

    @staticmethod
    def _reference_uri(reference: Union[str, Path]) -> str:
        value = str(reference)
        if value.startswith(("https://", "data:image/")):
            return value
        path = Path(reference)
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Unsupported Nano Banana Pro reference image type: " + str(path))
        if not path.is_file() or not 0 < path.stat().st_size <= 10 * 1024 * 1024:
            raise ValueError("Nano Banana Pro reference image must be a file of at most 10 MiB: " + str(path))
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return "data:{};base64,{}".format(mime, encoded)

    def create_image_task(
        self,
        prompt: str,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        thinking_level: Optional[str] = None,
        reference_images: Sequence[Union[str, Path]] = (),
        timings: Optional[Dict[str, float]] = None,
    ) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        if size is not None and size != "auto" and size not in self.SUPPORTED_SIZES:
            raise ValueError("Unsupported Nano Banana Pro size: {} (choose {})".format(size, ", ".join(self.SUPPORTED_SIZES)))
        if quality is not None and quality not in self.SUPPORTED_QUALITIES:
            raise ValueError("Unsupported Nano Banana Pro quality: {} (choose {})".format(quality, ", ".join(self.SUPPORTED_QUALITIES)))
        if thinking_level is not None:
            thinking_level = thinking_level.upper()
            if thinking_level not in {"MINIMAL", "HIGH"}:
                raise ValueError("nano-banana-pro thinking_level must be MINIMAL or HIGH")
        if len(reference_images) > 14:
            raise ValueError("Nano Banana Pro accepts at most 14 reference images")
        content = [{"type": "text", "text": prompt}]
        content.extend({
            "type": "image_url",
            "image_item": {"image_url": self._reference_uri(reference)},
        } for reference in reference_images)
        body: Dict[str, Any] = {
            "model": _BAIDU_PROVIDER_MODEL_ID,
            "messages": [{"role": "user", "content": content}],
            "n": 1,
            "response_modalities": ["IMAGE"],
        }
        # Nano Banana Pro has no literal `auto` size value; omission delegates canvas choice to the provider.
        if size is not None and size != "auto":
            body["size"] = size
        if quality is not None:
            body["quality"] = quality
        if thinking_level is not None:
            body["extra_body"] = {"g_model": {"thinking_config": {"thinking_level": thinking_level}}}
        wait_started = time.monotonic()
        self._limiter.acquire("image")
        if timings is not None:
            timings["rate_limit_wait_seconds"] = timings.get("rate_limit_wait_seconds", 0.0) + time.monotonic() - wait_started
        payload = self._request("POST", "/v2/aigc/image", body, timings)
        data = payload.get("data")
        task_id = payload.get("taskId") or (data.get("taskId") if isinstance(data, dict) else None)
        if not task_id:
            raise ModelServiceError("Baidu VOD did not return a taskId")
        return str(task_id)

    def wait_for_task(self, task_id: str, timeout: float = 1200, poll_interval: float = 3,
                      timings: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        deadline = time.monotonic() + timeout
        escaped_id = quote(str(task_id), safe="")
        path = self.task_path.format(task_id=escaped_id)
        while True:
            payload = self._request("GET", path, timings=timings)
            result = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            task_info = result.get("task")
            nested_status = task_info.get("status") if isinstance(task_info, dict) else None
            status = str(
                result.get("status") or result.get("state") or result.get("taskStatus")
                or nested_status or ""
            ).lower()
            if status in self.SUCCESS_STATES or _find_image_result(result):
                return payload
            if status in self.FAILURE_STATES:
                raise ModelServiceError("Baidu VOD Nano Banana Pro task {} failed: {}".format(task_id, _error_message(result)))
            if status not in {"", "created", "queued", "submitted", "pending", "running", "processing", "waiting", "in_progress", "ready"}:
                raise ModelServiceError("Baidu VOD Nano Banana Pro task {} returned unexpected status: {}".format(task_id, status))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelServiceError("Baidu VOD Nano Banana Pro task {} did not finish within {} seconds".format(task_id, timeout))
            time.sleep(min(poll_interval, remaining))

    def generate_image(
        self,
        prompt: str,
        output: Union[str, Path],
        size: Optional[str] = None,
        quality: Optional[str] = None,
        thinking_level: Optional[str] = None,
        reference_images: Sequence[Union[str, Path]] = (),
        timeout: float = 1200,
        poll_interval: float = 3,
        timings: Optional[Dict[str, float]] = None,
    ) -> Path:
        output_path = Path(output)
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError(str(output_path))
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        task_id = self.create_image_task(prompt, size, quality, thinking_level, reference_images, timings)
        payload = self.wait_for_task(task_id, timeout, poll_interval, timings)
        result = _find_image_result(payload)
        if not result:
            raise ModelServiceError("Baidu VOD Nano Banana Pro task {} succeeded without an image".format(task_id))
        kind, value = result
        if kind == "base64" or value.startswith("data:image/"):
            image_bytes = _decode_image_result(kind, value)
        else:
            if urlsplit(value).scheme != "https":
                raise ModelServiceError("Baidu VOD returned an insecure image URL")
            download_started = time.monotonic()
            try:
                response = self._download_http.get(value)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ModelServiceError("Failed to download the Nano Banana Pro image: " + str(exc)) from exc
            finally:
                if timings is not None:
                    timings["download_seconds"] = time.monotonic() - download_started
            image_bytes = response.content
        if not image_bytes:
            raise ModelServiceError("Baidu VOD Nano Banana Pro returned an empty image")
        with output_path.open("xb") as file:
            file.write(image_bytes)
        if timings is not None:
            timings["generation_seconds"] = time.monotonic() - started
        return output_path


class VolcengineSeedreamService:
    """Generate one image with Volcengine Ark's Seedream image API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        rate_db: Path = RATE_DB,
    ) -> None:
        settings = _file_settings(VOLCENGINE_CONFIG_FILE) if not api_key else {}
        self.api_key = api_key or os.environ.get("VOLCENGINE_ARK_API_KEY") or settings.get("VOLCENGINE_ARK_API_KEY")
        self.endpoint = (endpoint or os.environ.get("VOLCENGINE_ARK_ENDPOINT") or VOLCENGINE_DEFAULT_ENDPOINT).rstrip("/")
        if not self.api_key:
            raise ModelServiceError("Set VOLCENGINE_ARK_API_KEY or add it to .volcengine.env")
        if urlsplit(self.endpoint).scheme != "https":
            raise ModelServiceError("VOLCENGINE_ARK_ENDPOINT must be an HTTPS URL")
        self._limiter = SlidingWindowLimiter(rate_db)
        self._http = httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(300, connect=15),
            headers={"Authorization": "Bearer " + self.api_key},
        )
        self._download_http = httpx.Client(trust_env=False, timeout=httpx.Timeout(120, connect=15))

    def __enter__(self) -> "VolcengineSeedreamService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._download_http.close()

    def generate_image(
        self,
        prompt: str,
        output: Union[str, Path],
        size: Optional[str] = None,
        timings: Optional[Dict[str, float]] = None,
    ) -> Path:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        output_path = Path(output)
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError(str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        body: Dict[str, Any] = {
            "model": VOLCENGINE_SEEDREAM_MODEL,
            "prompt": prompt,
            "response_format": "url",
        }
        # Seedream's standard image endpoint selects its default canvas when size is omitted.
        if size is not None and size != "auto":
            body["size"] = size
        wait_started = time.monotonic()
        self._limiter.acquire("image")
        if timings is not None:
            timings["rate_limit_wait_seconds"] = time.monotonic() - wait_started
        started = time.monotonic()
        try:
            response = self._http.post(self.endpoint + "/images/generations", json=body)
        except httpx.HTTPError as exc:
            if timings is not None:
                timings["api_response_seconds"] = time.monotonic() - started
            raise ModelServiceError("Volcengine Ark image request failed: " + str(exc)) from exc
        if timings is not None:
            timings["api_response_seconds"] = time.monotonic() - started
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelServiceError("Volcengine Ark returned non-JSON data (HTTP {})".format(response.status_code)) from exc
        if response.status_code >= 400:
            raise ModelServiceError("Volcengine Ark HTTP {}: {}".format(response.status_code, _error_message(payload)))
        result = _find_image_result(payload.get("data", payload))
        if not result:
            raise ModelServiceError("Volcengine Ark returned no generated image")
        kind, value = result
        if kind == "base64" or value.startswith("data:image/"):
            image_bytes = _decode_image_result(kind, value)
        else:
            if urlsplit(value).scheme != "https":
                raise ModelServiceError("Volcengine Ark returned an insecure image URL")
            download_started = time.monotonic()
            try:
                image_response = self._download_http.get(value)
                image_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ModelServiceError("Failed to download the Seedream image: " + str(exc)) from exc
            finally:
                if timings is not None:
                    timings["download_seconds"] = time.monotonic() - download_started
            image_bytes = image_response.content
        if not image_bytes:
            raise ModelServiceError("Volcengine Ark returned an empty image")
        with output_path.open("xb") as file:
            file.write(image_bytes)
        if timings is not None:
            timings["generation_seconds"] = time.monotonic() - started
        return output_path


class ArtArkService:
    """Create an ArtArk task, wait for completion, and save image outputs only."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        proxy: Optional[str] = None,
        rate_db: Path = RATE_DB,
    ) -> None:
        settings = _file_settings(ARTARK_CONFIG_FILE)
        self.api_key = api_key or os.environ.get("ARTARK_API_KEY") or settings.get("ARTARK_API_KEY")
        self.endpoint = (
            endpoint or os.environ.get("ARTARK_ENDPOINT") or settings.get("ARTARK_ENDPOINT") or ARTARK_DEFAULT_ENDPOINT
        ).rstrip("/")
        self.proxy = proxy if proxy is not None else (os.environ.get("ARTARK_PROXY") or settings.get("ARTARK_PROXY"))
        if not self.api_key:
            raise ModelServiceError("Set ARTARK_API_KEY or add it to .artark.env")
        if urlsplit(self.endpoint).scheme != "https":
            raise ModelServiceError("ARTARK_ENDPOINT must be an HTTPS URL")
        if self.proxy and urlsplit(self.proxy).scheme != "http":
            raise ModelServiceError("ARTARK_PROXY must be an HTTP proxy URL")
        self._slots = ArtArkTaskSlots(rate_db)
        self._http = httpx.Client(
            proxy=self.proxy or None,
            trust_env=False,
            timeout=httpx.Timeout(60, connect=15),
            headers={"X-API-Key": self.api_key},
        )
        self._download_http = httpx.Client(
            proxy=self.proxy or None, trust_env=False, timeout=httpx.Timeout(120, connect=15)
        )

    def __enter__(self) -> "ArtArkService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._download_http.close()

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            if body is None:
                response = self._http.request(method, self.endpoint + path)
            else:
                response = self._http.request(method, self.endpoint + path, json=body)
        except httpx.HTTPError as exc:
            raise ModelServiceError("ArtArk request failed: " + str(exc)) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelServiceError("ArtArk returned non-JSON data (HTTP {})".format(response.status_code)) from exc
        if not isinstance(payload, dict):
            raise ModelServiceError("ArtArk returned an unexpected response format")
        if response.status_code >= 400 or payload.get("code") != 0:
            code = payload.get("code") or response.status_code
            message = payload.get("message") or payload.get("detail") or "No error details returned"
            raise ModelServiceError("ArtArk HTTP {} ({}): {}".format(response.status_code, code, message))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ModelServiceError("ArtArk response has no data object")
        return data

    @staticmethod
    def _reference_uri(reference: Union[str, Path]) -> str:
        value = str(reference)
        if value.startswith("data:image/") or urlsplit(value).scheme in {"http", "https"}:
            return value
        path = Path(reference)
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff"}:
            raise ValueError("Unsupported ArtArk reference image type: " + str(path))
        if not path.is_file() or not 0 < path.stat().st_size <= 10 * 1024 * 1024:
            raise ValueError("ArtArk reference image must be a file of at most 10 MiB: " + str(path))
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return "data:{};base64,{}".format(mime, encoded)

    def _reserve_slot(self, request_id: str, request_hash: str, timeout: float, poll_interval: float) -> Optional[str]:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        deadline = time.monotonic() + timeout
        while True:
            existing_task = self._slots.lookup_request(request_id, request_hash)
            if existing_task:
                return existing_task
            try:
                if self._slots.try_reserve(request_id, request_hash):
                    return None
            except ModelServiceError:
                # The first caller may still be attaching its task ID.
                pass
            for task_id in self._slots.active_task_ids():
                try:
                    self.get_task(task_id)
                except ModelServiceError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelServiceError("ArtArk task queue stayed at its 10-task limit for {} seconds".format(timeout))
            time.sleep(min(poll_interval, remaining))

    def create_task(
        self,
        prompt: str,
        tier: str = "L3",
        reference_images: Sequence[Union[str, Path]] = (),
        retrieval: bool = True,
        client_request_id: Optional[str] = None,
        queue_timeout: float = 600,
        poll_interval: float = 3,
    ) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        tier = tier.upper()
        if tier not in ARTARK_TIERS:
            raise ValueError("ArtArk tier must be L1, L2, or L3")
        if len(reference_images) > 14:
            raise ValueError("ArtArk accepts at most 14 reference images")
        request_id = str(uuid4()) if client_request_id is None else client_request_id
        if not 1 <= len(request_id) <= 128:
            raise ValueError("client_request_id must be 1-128 characters")
        body = {
            "prompt": prompt,
            "tier": tier,
            "retrieval": retrieval,
            "client_request_id": request_id,
        }
        if reference_images:
            body["image"] = [self._reference_uri(reference) for reference in reference_images]
        request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        existing_task = self._reserve_slot(request_id, request_hash, queue_timeout, poll_interval)
        if existing_task:
            return existing_task
        try:
            data = self._request("POST", "/images/generations", body)
        except ModelServiceError as exc:
            self._slots.release_request(request_id)
            raise ModelServiceError("{} (client_request_id={})".format(exc, request_id)) from exc
        task_id = data.get("task_id")
        if not task_id:
            self._slots.release_request(request_id)
            raise ModelServiceError("ArtArk did not return a task ID (client_request_id={})".format(request_id))
        self._slots.attach(request_id, task_id)
        return task_id

    def get_task(self, task_id: str) -> Dict[str, Any]:
        task_id = str(UUID(task_id))
        data = self._request("GET", "/tasks/" + quote(task_id, safe=""))
        if data.get("status") in {"succeeded", "failed"}:
            self._slots.release_task(task_id)
        return data

    def wait_for_task(self, task_id: str, timeout: float = 600, poll_interval: float = 3) -> Dict[str, Any]:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        deadline = time.monotonic() + timeout
        while True:
            data = self.get_task(task_id)
            status = data.get("status")
            if status == "succeeded":
                return data
            if status == "failed":
                raise ModelServiceError("ArtArk task {} failed: {}".format(task_id, data.get("error", "unknown error")))
            if status != "running":
                raise ModelServiceError("ArtArk task {} returned unexpected status: {}".format(task_id, status))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelServiceError("ArtArk task {} is still running after {} seconds".format(task_id, timeout))
            time.sleep(min(poll_interval, remaining))

    def generate_images(
        self,
        prompt: str,
        output: Union[str, Path],
        tier: str = "L3",
        reference_images: Sequence[Union[str, Path]] = (),
        retrieval: bool = True,
        timeout: float = 600,
        poll_interval: float = 3,
        client_request_id: Optional[str] = None,
    ) -> List[Path]:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        output_path = Path(output)
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError(str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        task_id = self.create_task(prompt, tier, reference_images, retrieval, client_request_id, timeout, poll_interval)
        data = self.wait_for_task(task_id, timeout, poll_interval)
        groups = (data.get("result") or {}).get("final_images") or []
        urls = [image.get("url") for group in groups for image in group.get("images", []) if image.get("url")]
        if not urls:
            raise ModelServiceError("ArtArk task {} succeeded without image URLs".format(task_id))
        paths = [
            output_path if index == 1 else output_path.with_name("{}-{}{}".format(output_path.stem, index, output_path.suffix))
            for index in range(1, len(urls) + 1)
        ]
        for path in paths:
            if path.exists() or path.is_symlink():
                raise FileExistsError(str(path))
        images = []
        for url in urls:
            if urlsplit(url).scheme != "https":
                raise ModelServiceError("ArtArk returned an insecure image URL")
            try:
                response = self._download_http.get(url, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ModelServiceError("Failed to download ArtArk image: " + str(exc)) from exc
            if urlsplit(str(response.url)).scheme != "https" or not response.content:
                raise ModelServiceError("ArtArk returned invalid image data")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type and content_type != "application/octet-stream" and not content_type.startswith("image/"):
                raise ModelServiceError("ArtArk image URL returned non-image content")
            images.append(response.content)
        for path, image_bytes in zip(paths, images):
            with path.open("xb") as file:
                file.write(image_bytes)
        return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Call Azure, Baidu VOD, Volcengine Ark, and ArtArk models")
    commands = parser.add_subparsers(dest="command", required=True)
    text_command = commands.add_parser("text", help="Generate text, optionally from local images")
    text_command.add_argument("prompt", help="Text prompt, or - to read it from stdin")
    text_command.add_argument("--model", choices=LANGUAGE_MODELS, default=DEFAULT_TEXT_MODEL)
    text_command.add_argument("--image", action="append", default=[], help="Local image to analyze; repeatable")
    text_command.add_argument("--instructions")
    text_command.add_argument("--max-output-tokens", type=int)
    image_command = commands.add_parser("image", help="Generate one image")
    image_command.add_argument("prompt", help="Image prompt, or - to read it from stdin")
    image_command.add_argument("--model", choices=IMAGE_MODELS + (ARTARK_MODEL,), default=DEFAULT_IMAGE_MODEL)
    image_command.add_argument("--output", type=Path, required=True)
    image_command.add_argument("--size", help="Provider image size or aspect ratio")
    image_command.add_argument("--quality", choices=IMAGE_QUALITY_OPTIONS,
                               help="Image quality or resolution, depending on the selected model")
    image_command.add_argument("--thinking-level", type=str.upper, choices=("MINIMAL", "HIGH"),
                               help="Nano Banana Pro reasoning level")
    image_command.add_argument("--tier", type=str.upper, choices=ARTARK_TIERS, default="L3", help="ArtArk tier (default: L3)")
    image_command.add_argument("--reference-image", action="append", default=[],
                               help="Reference image for ArtArk or nano-banana-pro; repeatable")
    image_command.add_argument("--no-retrieval", action="store_true", help="Disable ArtArk retrieval")
    image_command.add_argument("--timeout", type=float, default=600, help="ArtArk/Nano Banana Pro task timeout in seconds")
    image_command.add_argument("--poll-interval", type=float, default=3, help="ArtArk/Nano Banana Pro polling interval in seconds")
    image_command.add_argument("--client-request-id", help="ArtArk idempotency key for retries")
    commands.add_parser("models", help="Show configured deployments and limits")
    args = parser.parse_args()
    if args.command == "models":
        print("Language (100 requests/minute): " + ", ".join(LANGUAGE_MODELS))
        print("Azure image: " + ", ".join(AZURE_IMAGE_MODELS))
        print("Baidu VOD image: Nano Banana Pro")
        print("Volcengine Ark image: " + VOLCENGINE_SEEDREAM_MODEL)
        print("Image APIs share a 10-requests-per-minute local limit")
        print("Azure image batch concurrency recommendations: " + ", ".join(
            "{}={}".format(model, IMAGE_MODEL_MAX_CONCURRENCY[model]) for model in IMAGE_MODELS
            if model in IMAGE_MODEL_MAX_CONCURRENCY
        ))
        print("ArtArk image (up to 10 queued/running tasks): artark; tiers L1, L2, L3 (default L3)")
        return 0
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    try:
        if args.command == "text":
            with ModelService() as service:
                print(service.generate_text(prompt, args.model, args.image, args.instructions, args.max_output_tokens))
        elif args.model == ARTARK_MODEL:
            if args.size or args.quality or args.thinking_level:
                raise ValueError("--size, --quality, and --thinking-level do not apply to ArtArk")
            with ArtArkService() as service:
                paths = service.generate_images(
                    prompt, args.output, args.tier, args.reference_image, not args.no_retrieval,
                    args.timeout, args.poll_interval, args.client_request_id,
                )
                for path in paths:
                    print(path)
        elif args.model == NANO_BANANA_PRO_MODEL:
            if args.no_retrieval or args.client_request_id or args.tier != "L3":
                raise ValueError("--tier, --no-retrieval, and --client-request-id apply only to ArtArk")
            with BaiduNanoBananaProService() as service:
                print(service.generate_image(
                    prompt, args.output, args.size, args.quality, args.thinking_level,
                    args.reference_image, args.timeout, args.poll_interval,
                ))
        elif args.model == VOLCENGINE_SEEDREAM_MODEL:
            if args.quality or args.thinking_level or args.reference_image or args.no_retrieval or args.client_request_id or args.tier != "L3":
                raise ValueError("--quality, --thinking-level, and ArtArk/Nano Banana Pro options do not apply to Seedream")
            with VolcengineSeedreamService() as service:
                print(service.generate_image(prompt, args.output, args.size))
        else:
            if args.reference_image or args.no_retrieval or args.client_request_id or args.tier != "L3" or args.thinking_level:
                raise ValueError("--tier, --reference-image, --no-retrieval, --client-request-id, and --thinking-level do not apply to Azure")
            with ModelService() as service:
                print(service.generate_image(prompt, args.output, args.model, args.size, quality=args.quality))
    except (ModelServiceError, OSError, ValueError) as exc:
        parser.exit(1, "error: {}\n".format(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
