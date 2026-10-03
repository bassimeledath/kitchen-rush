#!/usr/bin/env python3
"""Versioned OpenRouter streaming transport with a hard request deadline.

The HTTP request runs in a child process. The parent can terminate that process
after the total deadline, and retains every complete stream event printed before
failure. Only allowlisted provider error fields are persisted.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import continue_extreme_sol_20260926 as prior

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
SAFE_ERROR_KEYS = ("code", "message", "type", "provider_name")


class TransportFailure(prior.legacy.ProviderError):
    def __init__(self, details: dict):
        self.details = details
        super().__init__(f"{details.get('kind', 'transport_error')}: {details.get('status', details.get('exception', 'unknown'))}")


def _safe_error(value):
    if not isinstance(value, dict):
        return {"message": str(value)[:500]}
    error = value.get("error", value)
    if not isinstance(error, dict):
        return {"message": str(error)[:500]}
    result = {}
    for key in SAFE_ERROR_KEYS:
        item = error.get(key)
        if isinstance(item, (str, int, float, bool)):
            result[key] = str(item)[:500]
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        result["metadata"] = {key: str(metadata[key])[:200]
                              for key in ("provider_name", "status", "code")
                              if isinstance(metadata.get(key), (str, int, float, bool))}
    return result


def _emit(event, **fields):
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)


def worker(payload: dict, endpoint: str, socket_timeout: float):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        _emit("error", kind="configuration_error", exception="missing_OPENROUTER_API_KEY")
        return
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=socket_timeout) as response:
            _emit("headers", status=response.status,
                  request_id=response.headers.get("x-request-id"))
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    _emit("done")
                    return
                chunk = json.loads(value)
                if "error" in chunk:
                    _emit("error", kind="stream_error", provider_error=_safe_error(chunk))
                    return
                _emit("chunk", data=chunk)
            _emit("error", kind="incomplete_stream", exception="missing_done_marker")
    except urllib.error.HTTPError as error:
        try:
            body = error.read(65536)
            parsed = json.loads(body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            parsed = {}
        _emit("error", kind="http_error", status=error.code,
              request_id=error.headers.get("x-request-id") if error.headers else None,
              retry_after=error.headers.get("Retry-After") if error.headers else None,
              provider_error=_safe_error(parsed))
    except (http.client.HTTPException, OSError, ValueError) as error:
        _emit("error", kind="transport_error", exception=type(error).__name__)


def _events(raw: bytes):
    events = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(item, dict) and isinstance(item.get("event"), str):
            events.append(item)
    return events


class BoundedStreamingClient(prior.StreamingClient):
    def __init__(self, model, *, total_timeout=600, socket_timeout=600,
                 endpoint=ENDPOINT):
        super().__init__(model, timeout=socket_timeout)
        self.total_timeout = total_timeout
        self.endpoint = endpoint

    def evaluate(self, payload):
        started = time.monotonic()
        command = [sys.executable, "-u", str(Path(__file__).resolve()), "--worker"]
        request = {"payload": payload, "endpoint": self.endpoint,
                   "socket_timeout": self.timeout}
        try:
            completed = subprocess.run(
                command, input=json.dumps(request).encode(), capture_output=True,
                timeout=self.total_timeout, check=False,
            )
            events = _events(completed.stdout)
            returncode = completed.returncode
        except subprocess.TimeoutExpired as error:
            events = _events(error.stdout or b"")
            returncode = None
        chunks = [item["data"] for item in events if item["event"] == "chunk"]
        headers = next((item for item in events if item["event"] == "headers"), {})
        failure = next((item for item in events if item["event"] == "error"), None)
        if returncode is None:
            failure = {"kind": "total_request_timeout", "deadline_seconds": self.total_timeout}
        elif failure is None and not any(item["event"] == "done" for item in events):
            failure = {"kind": "worker_failure", "returncode": returncode}
        if failure is not None:
            details = {key: value for key, value in failure.items() if key != "event"}
            details.update(elapsed_seconds=round(time.monotonic() - started, 3),
                           response_headers=headers, partial_stream_chunks=chunks)
            raise TransportFailure(details)
        result = prior.aggregate_stream(chunks)
        result["stream_chunks"] = chunks
        if not result.get("usage") or not result.get("provider"):
            raise TransportFailure({"kind": "incomplete_stream_metadata",
                                    "elapsed_seconds": round(time.monotonic() - started, 3),
                                    "response_headers": headers,
                                    "partial_stream_chunks": chunks})
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if not args.worker:
        parser.error("worker invocation required")
    request = json.load(sys.stdin)
    worker(request["payload"], request["endpoint"], request["socket_timeout"])


if __name__ == "__main__":
    main()
