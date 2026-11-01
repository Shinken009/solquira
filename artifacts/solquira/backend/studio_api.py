"""Local Studio surface: opaque bounded sessions, CSRF, admission and no live adapters.

The session is an unpredictable bearer cookie mapped to server state, not a
client-asserted identity. This in-process store is intentionally single-worker.
No scenarios, queries or reports are retained in the session. A single
source-validated forecast has at most ten minutes of usable freshness.
"""

from __future__ import annotations

import asyncio
import copy
import hmac
import importlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from studio_schema import StudioQuestion, StudioScenario, StudioLocation
from studio_runtime import current_provider
from dryland_brief import (
    Attempt,
    FieldBrief,
    FieldPayload,
    TIMEOUT_S,
    MODEL,
    attempt_mode,
    golden_payload,
    resolve_payload,
    fallback_brief,
    compose_brief,
)

SCHEMA_VERSION = "solquira.studio/1"
COOKIE = "solquira_studio_session"
CSRF_HEADER = "X-Solquira-CSRF"
SESSION_TTL = 3600
MAX_SESSIONS = 512
MAX_BODY_BYTES = 8192
MAX_ACTIVE = 4
SESSION_REQUESTS = 24
GLOBAL_REQUESTS = 120
IP_BOOTSTRAPS = 30
GLOBAL_BOOTSTRAPS = 240
WINDOW = 60
studio_router = APIRouter(prefix="/solquira-api/studio", tags=["studio"])
field_router = APIRouter(prefix="/v1", tags=["field-brief"])
_FIXTURES = Path(__file__).resolve().parents[1] / "src/studio/fixtures.json"


def studio_mode() -> bool:
    return os.environ.get("SOLQUIRA_STUDIO_MODE", "0") == "1"


def _service():
    # Lazy import lets the existing evaluator remain independently importable.
    return importlib.import_module("studio_service")


class Admission:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = OrderedDict()
        self.bootstrap_ips = OrderedDict()
        self.bootstrap_global = deque()
        self.compute_global = deque()
        self.active = 0

    def reset(self):
        with self.lock:
            self.sessions.clear()
            self.bootstrap_ips.clear()
            self.bootstrap_global.clear()
            self.compute_global.clear()
            self.active = 0

    @staticmethod
    def _trim(events, now):
        while events and events[0] <= now - WINDOW:
            events.popleft()

    def _expire(self, now):
        for token in [
            key for key, value in self.sessions.items() if now >= value["expires"]
        ]:
            self.sessions.pop(token, None)
        for current in self.sessions.values():
            stored = current.get("field_context")
            if stored and now >= stored[0]:
                current.pop("field_context", None)
        for key in list(self.bootstrap_ips):
            events = self.bootstrap_ips[key]
            self._trim(events, now)
            if not events:
                self.bootstrap_ips.pop(key, None)

    def bootstrap(self, token, ip):
        now = self.clock()
        with self.lock:
            self._expire(now)
            self._trim(self.bootstrap_global, now)
            events = self.bootstrap_ips.setdefault(ip, deque())
            self._trim(events, now)
            if (
                len(events) >= IP_BOOTSTRAPS
                or len(self.bootstrap_global) >= GLOBAL_BOOTSTRAPS
            ):
                raise HTTPException(
                    429,
                    "Too many sessions requested. Please wait a minute.",
                    headers={"Retry-After": "60"},
                )
            events.append(now)
            self.bootstrap_global.append(now)
            self.bootstrap_ips.move_to_end(ip)
            while len(self.bootstrap_ips) > MAX_SESSIONS:
                self.bootstrap_ips.popitem(last=False)
            current = self.sessions.get(token)
            if current is not None:
                return token, current, max(0, int(current["expires"] - now))
            while len(self.sessions) >= MAX_SESSIONS:
                self.sessions.popitem(last=False)
            token = secrets.token_urlsafe(32)
            current = {
                "csrf": secrets.token_urlsafe(32),
                "expires": now + SESSION_TTL,
                "events": deque(),
                "context_revision": 0,
            }
            self.sessions[token] = current
            return token, current, SESSION_TTL

    def acquire(self, token, csrf):
        now = self.clock()
        with self.lock:
            self._expire(now)
            current = self.sessions.get(token)
            if current is None:
                raise HTTPException(
                    401, "Your session expired. Refresh the Studio to continue."
                )
            if not csrf or not hmac.compare_digest(
                current["csrf"].encode("utf-8"), csrf.encode("utf-8")
            ):
                raise HTTPException(
                    403, "The request could not be verified. Refresh the Studio."
                )
            self._trim(current["events"], now)
            self._trim(self.compute_global, now)
            if (
                len(current["events"]) >= SESSION_REQUESTS
                or len(self.compute_global) >= GLOBAL_REQUESTS
            ):
                raise HTTPException(
                    429,
                    "The Studio is at its request limit. Please wait a minute.",
                    headers={"Retry-After": "60"},
                )
            if self.active >= MAX_ACTIVE:
                raise HTTPException(
                    429,
                    "The Studio is busy. Please try again shortly.",
                    headers={"Retry-After": "3"},
                )
            current["events"].append(now)
            self.compute_global.append(now)
            self.active += 1

    def release(self):
        with self.lock:
            self.active = max(0, self.active - 1)

    def begin_context(self, token):
        with self.lock:
            self._expire(self.clock())
            current = self.sessions.get(token)
            if current is None:
                raise HTTPException(401, "Your session expired. Reconnect to continue.")
            current.pop("field_context", None)
            current["context_revision"] += 1
            return current["context_revision"]

    def bind_context(self, token, revision, value):
        from datetime import datetime, timezone

        # A cache hit must not reset the source's freshness window.
        age = max(
            0,
            (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(value["retrieved_at"])
            ).total_seconds(),
        )
        with self.lock:
            now = self.clock()
            self._expire(now)
            current = self.sessions.get(token)
            if current is None or current["context_revision"] != revision or age >= 600:
                return None
            if value["weather"]["state"] not in {"ready", "cached"}:
                return None
            bound = copy.deepcopy(value)
            bound["context_ref"] = "slq-context-" + secrets.token_urlsafe(24)
            current["field_context"] = (min(current["expires"], now + 600 - age), bound)
            return bound["context_ref"]

    def resolve_context(self, token, reference):
        with self.lock:
            now = self.clock()
            self._expire(now)
            current = self.sessions.get(token)
            stored = current.get("field_context") if current else None
            if (
                not stored
                or now >= stored[0]
                or not hmac.compare_digest(stored[1]["context_ref"], reference)
            ):
                raise HTTPException(
                    409,
                    "Reload the location forecast and attach it again. Its context expired or changed.",
                )
            return copy.deepcopy(stored[1])


ADMISSION = Admission()


class AdmissionLease:
    """Release a compute slot once, only after any delegated worker has ended."""

    def __init__(self, admission):
        self.admission = admission
        self.lock = threading.Lock()
        self.transferred = False
        self.started = False
        self.released = False

    def transfer(self):
        with self.lock:
            self.transferred = True

    def start_worker(self):
        with self.lock:
            if self.released:
                return False
            self.started = True
            return True

    def release(self):
        with self.lock:
            if self.released:
                return
            self.released = True
        self.admission.release()

    def release_if_unstarted(self):
        with self.lock:
            if self.started or self.released:
                return
            self.released = True
        self.admission.release()

    def finish_request(self):
        with self.lock:
            transferred = self.transferred
        if not transferred:
            self.release()


async def _run_service(lease, function, *args, **kwargs):
    # The lease ends in the synchronous worker's finally, not when an aborted
    # HTTP request stops awaiting its result. That prevents abort-driven bypass.
    def invoke():
        if not lease.start_worker():
            return None
        try:
            return function(*args, **kwargs)
        finally:
            lease.release()

    async def work():
        return await run_in_threadpool(invoke)

    task = asyncio.create_task(work())
    lease.transfer()

    def finished(completed):
        # Covers a task cancelled before the worker starts. Its wrapper checks
        # the same lease under lock, so late queued work cannot run after release.
        lease.release_if_unstarted()
        if not completed.cancelled():
            completed.exception()  # Retrieve orphaned failures after HTTP abort.

    task.add_done_callback(finished)
    return await asyncio.shield(task)


def _require_mode():
    if not studio_mode():
        raise HTTPException(404, "Studio mode is not enabled.")


def _host_name(value):
    """Canonical plain host, never a URL, wildcard, userinfo or host:port."""
    if not value or len(value) > 253 or any(ord(c) > 127 for c in value):
        raise ValueError("Invalid host.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        labels = value.lower().split(".")
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("Invalid host.") from None
        return value.lower()


def _trusted_host(request):
    """Block DNS-rebinding-shaped Host values before issuing a session/CSRF."""
    default = {"127.0.0.1", "localhost", "::1"}
    configured = os.environ.get("SOLQUIRA_STUDIO_ALLOWED_HOSTS", "").strip()
    try:
        allowed = default | (
            {_host_name(item.strip()) for item in configured.split(",")}
            if configured
            else set()
        )
    except ValueError:
        raise HTTPException(503, "The Studio host configuration is invalid.") from None
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        raise HTTPException(400, "Send one valid request host.")
    raw = hosts[0]
    try:
        if (
            raw != raw.strip()
            or any(ord(c) <= 32 or ord(c) > 127 for c in raw)
            or raw.endswith(":")
        ):
            raise ValueError()
        parsed = urlsplit("//" + raw)
        if (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
        host = _host_name(parsed.hostname)
    except ValueError:
        raise HTTPException(400, "Send one valid request host.") from None
    if host not in allowed:
        raise HTTPException(403, "This request host is not allowed.")


def _origin(request):
    _trusted_host(request)
    if len(request.headers.getlist("origin")) > 1:
        raise HTTPException(400, "Send one request origin.")
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site == "cross-site":
        raise HTTPException(403, "Cross-site requests are not allowed.")
    origin = request.headers.get("origin")
    if not origin:
        return
    expected = f"{request.url.scheme}://{request.url.netloc}"
    allowed = {
        item.strip().rstrip("/")
        for item in os.environ.get("SOLQUIRA_STUDIO_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin == "null" or (
        origin.rstrip("/") != expected and origin.rstrip("/") not in allowed
    ):
        raise HTTPException(403, "This request origin is not allowed.")


@asynccontextmanager
async def _admitted(request):
    _require_mode()
    _origin(request)
    ADMISSION.acquire(request.cookies.get(COOKIE), request.headers.get(CSRF_HEADER))
    lease = AdmissionLease(ADMISSION)
    try:
        yield lease
    finally:
        lease.finish_request()


async def _body(request):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "Send a JSON request.")
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
            if length < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid request length.") from None
        if length > MAX_BODY_BYTES:
            raise HTTPException(413, "The request is too large.")

    async def consume():
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise HTTPException(413, "The request is too large.")
            chunks.append(chunk)
        return b"".join(chunks)

    try:
        raw = await asyncio.wait_for(consume(), timeout=5)

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON field.")
                result[key] = value
            return result

        return json.loads(
            raw,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            object_pairs_hook=unique_object,
        )
    except asyncio.TimeoutError:
        raise HTTPException(408, "The request took too long to arrive.") from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The request contains invalid JSON.") from None


def _validate(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        # Never echo user input, raw validation context or query text.
        allowed_fields = {
            "latitude",
            "longitude",
            "context_ref",
            "profile_id",
            "water_percent",
            "previous_crop",
            "scenario",
            "query",
            "scenario.profile_id",
            "scenario.water_percent",
            "scenario.previous_crop",
        }
        errors = []
        for item in exc.errors(include_input=False, include_context=False):
            field = ".".join(map(str, item["loc"]))
            # Unknown field names are also untrusted user input, not safe labels.
            errors.append(
                {
                    "field": field if field in allowed_fields else "request",
                    "message": item["msg"],
                }
            )
        raise HTTPException(
            422, {"message": "Check the scenario fields.", "fields": errors}
        ) from None


def _json(value, status_code=200):
    return JSONResponse(
        value,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _envelope(scenario, report, run):
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario": scenario,
        "report": report,
        "run": run,
        "request_id": "slq-request-" + secrets.token_hex(12),
    }


@studio_router.get("/bootstrap")
async def bootstrap(request: Request):
    _require_mode()
    _origin(request)
    ip = request.client.host if request.client else "unknown"
    token, session, expires = ADMISSION.bootstrap(request.cookies.get(COOKIE), ip)
    data = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    labels = {
        "valle": "Irrigated valley",
        "secano": "Semi-arid dryland",
        "precordillera": "Andean foothills",
    }
    locations = {
        "valle": "No location assigned · reference valley",
        "secano": "No location assigned · reference dryland",
        "precordillera": "No location assigned · reference upland",
    }
    try:
        inference = _service().studio_status()
    except Exception:
        raise HTTPException(
            503, "The local Studio service is unavailable. Please try again shortly."
        ) from None
    response = _json(
        {
            "schema_version": SCHEMA_VERSION,
            "csrf_token": session["csrf"],
            "session_expires_in": expires,
            "inference": inference,
            "geodata": {"enabled": os.environ.get("SOLQUIRA_STUDIO_GEODATA", "0") == "1"},
            "profiles": [
                {
                    "id": item["id"],
                    "name": labels[item["id"]],
                    "location": locations[item["id"]],
                    "water_m3_ha": item["waterM3Ha"],
                }
                for item in data["profiles"]
            ],
            "history_options": [
                {"id": key, "label": label}
                for key, label in [
                    ("cereal", "Cereal"),
                    ("leguminosa", "Legume"),
                    ("cubierta", "Cover crop"),
                    ("descanso", "Fallow"),
                ]
            ],
            "rule_version": data["ruleVersion"],
            "synthetic": True,
        }
    )
    # The same opaque session protects both Studio and the typed /v1 surface.
    cookie_path = request.scope.get("root_path", "").rstrip("/") + "/"
    response.set_cookie(
        COOKIE,
        token,
        max_age=expires,
        path=cookie_path,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    return response


@studio_router.post("/evaluate")
async def evaluate(request: Request):
    async with _admitted(request) as lease:
        scenario = _validate(StudioScenario, await _body(request)).model_dump()
        try:
            report = await _run_service(lease, _service().evaluate_studio, scenario)
        except Exception:
            raise HTTPException(
                503, "The local comparison is unavailable. No result was invented."
            ) from None
        return _json(
            _envelope(
                scenario,
                report,
                {
                    "inference_mode": "deterministic",
                    "outcome": "success",
                    "model": None,
                    "trace": [],
                },
            )
        )


@studio_router.post("/location")
async def location(request: Request):
    async with _admitted(request) as lease:
        coordinates = _validate(StudioLocation, await _body(request)).model_dump()
        token = request.cookies.get(COOKIE)
        version = ADMISSION.begin_context(token)
        from studio_geo import location_context

        result = await _run_service(lease, location_context, coordinates)
        result["context_ref"] = ADMISSION.bind_context(token, version, result)
        return _json(result)


@studio_router.post("/history")
async def history(request: Request):
    """Explicit optional public reanalysis; same admission/CSRF, never inference."""
    async with _admitted(request) as lease:
        coordinates = _validate(StudioLocation, await _body(request)).model_dump()
        from environmental_history import historical_context

        result = await _run_service(lease, historical_context, coordinates)
        return _json(result)


@studio_router.post("/agent")
async def agent(request: Request):
    async with _admitted(request) as lease:
        question = _validate(StudioQuestion, await _body(request))
        scenario = question.scenario.model_dump()
        context = (
            ADMISSION.resolve_context(request.cookies.get(COOKIE), question.context_ref)
            if question.context_ref
            else None
        )
        try:
            provider = current_provider()
            kwargs = {"provider": provider}
            if question.field_record is not None:
                kwargs["field_record"] = question.field_record.model_dump()
            if context is not None:
                kwargs["field_context"] = context
            result = await _run_service(
                lease, _service().explain_studio, scenario, question.query, **kwargs
            )
            mode = result["inference_mode"]
            if mode not in {"local_fallback", "deterministic"} and not (
                provider is not None
                and mode == provider.mode
                and mode in {"nebius_live", "provider_stub"}
            ):
                raise ValueError(
                    "External inference is not authorized on this surface."
                )
            response = _envelope(
                scenario,
                result["report"],
                {
                    "inference_mode": mode,
                    "outcome": result.get("outcome", "disabled"),
                    "model": result.get("model"),
                    "trace": result.get("trace", []),
                    **(
                        {"diagnostic": result["diagnostic"]}
                        if "diagnostic" in result
                        else {}
                    ),
                },
            )
            for field in ("proposed_scenario", "requested_scenario"):
                if field in result:
                    response[field] = result[field]
        except Exception:
            raise HTTPException(
                503,
                "The explanation is unavailable. Your comparison remains unchanged.",
            ) from None
        if question.context_ref:
            ADMISSION.resolve_context(request.cookies.get(COOKIE), question.context_ref)
        return _json(response)


def _field_receipt(payload, scenario, outcome, attempt, elapsed_ms, model_actions=0):
    import hashlib
    from dryland_references import resolve_reference_payload
    place, _, _ = resolve_reference_payload(payload)
    observed = attempt.header_values()
    return {"evidence": "synthetic-golden-path", "soil": "reference-table", "place": place,
            "scenario": scenario, "outcome": outcome, "inference": attempt.mode,
            "model": MODEL if attempt.mode != "disabled" else None, "latencyMs": elapsed_ms,
            "attempt": observed["X-Solquira-Attempt"], "reserved": observed["X-Solquira-Reserved"],
            "transitions": observed["X-Solquira-Transitions"], "modelActions": model_actions,
            "payloadHash": hashlib.sha256(payload.model_dump_json().encode()).hexdigest()}


def _field_response(value, scenario, *, outcome=None, attempt=None, elapsed_ms=0, payload=None):
    response = _json(value.model_dump(mode="json"))
    payload = payload or value
    from dryland_references import resolve_reference_payload
    place, _, _ = resolve_reference_payload(payload)
    response.headers.update({"X-Solquira-Evidence": "synthetic-golden-path",
                             "X-Solquira-Soil": "reference-table", "X-Solquira-Place": place,
                             "X-Solquira-Scenario": scenario})
    if outcome is not None:
        receipt = _field_receipt(payload, scenario, outcome, attempt or Attempt("disabled"), elapsed_ms,
                                 3 if outcome == "success" else 0)
        mapping = {"outcome": "Outcome", "inference": "Inference", "model": "Model", "latencyMs": "Latency-Ms",
                   "attempt": "Attempt", "reserved": "Reserved", "transitions": "Transitions",
                   "payloadHash": "Payload-Hash", "modelActions": "Model-Actions"}
        for key, suffix in mapping.items():
            response.headers["X-Solquira-" + suffix] = str(receipt[key]) if receipt[key] is not None else ""
        response.headers["X-Solquira-Max-Tokens"] = "350"
        response.headers["X-Solquira-Deadline-Ms"] = "3000"
    return response


@field_router.get("/field-payload", response_model=FieldPayload)
async def field_payload(request: Request):
    async with _admitted(request):
        scenario = request.query_params.get("scenario")
        place = request.query_params.get("place", "ouagadougou")
        if (scenario not in {"dry", "rain"} or set(request.query_params) - {"scenario", "place"}
                or any(len(request.query_params.getlist(key)) > 1 for key in request.query_params)):
            raise HTTPException(422, "Choose a reference point and weather pattern.")
        try:
            payload = golden_payload(scenario, place)
        except ValueError:
            raise HTTPException(422, "Choose a supported reference point.") from None
        return _field_response(payload, scenario)


def _stream_brief(lease, payload, scenario, provider, started, deadline):
    from fastapi.responses import StreamingResponse
    queue = asyncio.Queue(maxsize=16)
    loop = asyncio.get_running_loop()
    state = {"closed": False}
    trace = []
    fallback = fallback_brief(payload)

    def emit(event, data):
        def enqueue():
            if not state["closed"] and not queue.full():
                queue.put_nowait((event, data))
        loop.call_soon_threadsafe(enqueue)

    # A disconnected reader must not release compute admission while a paid
    # worker is still running. The existing shielded worker owns this lease.
    lease.transfer()
    work = asyncio.create_task(_run_service(lease, compose_brief, payload, provider,
                                          deadline=deadline, on_event=emit, observed_trace=trace))
    work.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    def frame(event, data):
        return "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"

    async def events():
        try:
            yield frame("skeleton", {"brief": fallback.model_dump(mode="json"),
                        "receipt": _field_receipt(payload, scenario, "disabled", Attempt("disabled"), 0)})
            while not work.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pending = asyncio.create_task(queue.get())
                try:
                    done, _ = await asyncio.wait({pending, work}, timeout=remaining,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if pending in done:
                        event, data = pending.result()
                        yield frame(event, data)
                    if not done:
                        break
                finally:
                    if not pending.done():
                        pending.cancel()
                        try:
                            await pending
                        except asyncio.CancelledError:
                            pass
            while not queue.empty():
                event, data = queue.get_nowait()
                yield frame(event, data)
            if work.done():
                try:
                    brief, outcome, attempt = work.result()
                except Exception:
                    brief, outcome, attempt = fallback, "provider_error", Attempt(attempt_mode(provider), tuple(trace), "reserved" in trace)
            else:
                brief, outcome, attempt = fallback, "timeout", Attempt(attempt_mode(provider), tuple(trace) + ("route_timeout",), "reserved" in trace)
            if outcome != "success":
                yield frame("degraded", {"path": "brief", "reason": "rejected" if outcome == "invalid_output" else outcome})
            yield frame("final", {"brief": brief.model_dump(mode="json"),
                         "receipt": _field_receipt(payload, scenario, outcome, attempt,
                            round((time.monotonic() - started) * 1000), 3 if outcome == "success" else 0)})
        finally:
            state["closed"] = True
    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})


@field_router.post("/field-brief", response_model=FieldBrief)
async def field_brief(request: Request):
    async with _admitted(request) as lease:
        submitted = _validate(FieldPayload, await _body(request))
        try:
            scenario, payload = resolve_payload(submitted)
        except ValueError:
            raise HTTPException(409, "Reload the server-built field example before creating its brief.") from None
        started = time.monotonic()
        deadline = started + TIMEOUT_S - 0.1
        provider = current_provider()
        if "text/event-stream" in request.headers.get("accept", "").split(","):
            return _stream_brief(lease, payload, scenario, provider, started, deadline)
        try:
            brief, outcome, attempt = await asyncio.wait_for(
                _run_service(lease, compose_brief, payload, provider, deadline=deadline),
                timeout=max(0.001, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            brief, outcome, attempt = fallback_brief(payload), "timeout", Attempt(attempt_mode(provider), ("route_timeout",), None)
        except Exception:
            brief, outcome, attempt = fallback_brief(payload), "provider_error", Attempt(attempt_mode(provider), ("route_error",), None)
        return _field_response(brief, scenario, outcome=outcome, attempt=attempt,
                               elapsed_ms=round((time.monotonic() - started) * 1000), payload=payload)
