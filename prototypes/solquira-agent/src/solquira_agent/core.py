"""Framework-neutral, bounded tool loop over the existing Solquira demo engine."""

from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import sys
import time
import inspect
import threading
from pathlib import Path
from urllib.parse import urlparse


class AgentError(ValueError):
    """Sanitized domain failure with a machine-readable outcome."""

    def __init__(self, message, *, code="invalid_output", diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


MAX_TURNS = 4
MAX_TOOL_CALLS = 6
MAX_MESSAGE_CHARS = 1000
MAX_CONTEXT_CHARS = 40000
MAX_REPLY_CHARS = 16000
FOCUS_IDS = {
    "water": "agua",
    "history": "historial",
    "evidence": "evidencia",
    "resilience": "resiliencia",
    "decision": "decision",
}
FOCUSES = set(FOCUS_IDS) | set(FOCUS_IDS.values())
CROP_LABELS = {
    "cereal": "Cereal",
    "leguminosa": "Legume",
    "cubierta": "Cover crop",
    "descanso": "Fallow",
}
PROFILE_LABELS = {
    "valle": "Irrigated valley",
    "secano": "Semi-arid dryland",
    "precordillera": "Foothill landscape",
}
MISSING_LABELS = {
    "Agua disponible": "Available water",
    "Tipo de suelo": "Soil type",
    "Historial de cultivos (aún no informado)": "Crop history",
    "Prioridades agrícolas": "Planning priorities",
}
_ENGINE_LOCK = threading.RLock()
ALLOWED_HOSTS = {
    "api.tokenfactory.nebius.com",
    "api.tokenfactory.us-central1.nebius.com",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _parse_json(value):
    """Provider arguments/final output must not hide duplicate keys or NaN."""

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise AgentError("Duplicate model-output fields are not accepted.")
            result[key] = item
        return result

    def constant(_value):
        raise AgentError("Non-finite model-output values are not accepted.")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def _checked_engine(module, source_sha):
    if getattr(module, "_solquira_source_sha", None) != source_sha:
        raise AgentError(
            "The loaded engine does not match its source; restart the local Studio.",
            code="engine_restart_required",
        )
    return module


def _load_engine(repo):
    backend = repo / "artifacts/solquira/backend"
    file = backend / "main.py"
    if not file.is_file():
        raise AgentError(
            "The Solquira engine was not found in the authorized repository."
        )
    source_sha = hashlib.sha256(file.read_bytes()).hexdigest()
    running = sys.modules.get("main")
    if (
        running is not None
        and Path(getattr(running, "__file__", "")).resolve() == file.resolve()
        and hasattr(running, "evaluate")
    ):
        return _checked_engine(running, source_sha)
    name = (
        "_solquira_agent_demo_engine_"
        + hashlib.sha256((str(file) + source_sha).encode()).hexdigest()[:12]
    )
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, file)
        module = importlib.util.module_from_spec(spec)
        old_path = list(sys.path)
        sys.modules[name] = module
        try:
            sys.path.insert(0, str(backend))
            spec.loader.exec_module(module)
            if hashlib.sha256(file.read_bytes()).hexdigest() != source_sha:
                raise AgentError(
                    "The engine changed while loading; restart the local Studio.",
                    code="engine_restart_required",
                )
            _checked_engine(module, source_sha)
        except AgentError:
            sys.modules.pop(name, None)
            raise
        except Exception:
            sys.modules.pop(name, None)
            raise AgentError(
                "The local engine could not be loaded; check its dependencies."
            ) from None
        finally:
            sys.path[:] = old_path
    return _checked_engine(sys.modules[name], source_sha)


def _engine(repo):
    with _ENGINE_LOCK:
        return _load_engine(repo)


def _check_deadline(deadline, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise AgentError("The request was cancelled.", code="cancelled")
    if time.monotonic() >= deadline:
        raise AgentError("The request time budget was exhausted.", code="timeout")


class DemoTools:
    """One instance per conversation. Ephemeral, synthetic, local-only context."""

    names = (
        "leer_escenario",
        "comparar_escenarios",
        "revisar_evidencia",
        "revisar_suelo_cobertura",
        "preparar_decision",
    )

    def __init__(
        self, repo_root: Path, profile="valle", history="cereal", water_percent=100
    ):
        self.repo = Path(repo_root).resolve()
        fixture = self.repo / "artifacts/solquira/src/studio/fixtures.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        if (
            not isinstance(profile, str)
            or profile not in {"valle", "secano", "precordillera"}
            or profile not in data["parcels"]
        ):
            raise AgentError("The selected profile is outside the demonstration.")
        self._validate_changes(
            {"water_percent": water_percent, "previous_crop": history}
        )
        self.parcel = copy.deepcopy(data["parcels"][profile])
        self.parcel["previousCrops"] = [history]
        self.parcel["name"] = "Synthetic planning example"
        self.water_percent = water_percent
        self.comparisons = {}
        self._reviews = {}
        self.engine = _engine(self.repo)
        self.engine_sha = self.engine._solquira_source_sha

    @staticmethod
    def _validate_changes(args):
        if set(args) - {"water_percent", "previous_crop"}:
            raise AgentError("Unsupported tool arguments.")
        if "water_percent" in args:
            n = args["water_percent"]
            if type(n) is not int or n not in range(40, 121, 10):
                raise AgentError(
                    "Demo water must be an integer from 40 to 120 in steps of 10."
                )
        if "previous_crop" in args and (
            not isinstance(args["previous_crop"], str)
            or args["previous_crop"]
            not in {"cereal", "leguminosa", "cubierta", "descanso"}
        ):
            raise AgentError("Unsupported crop-history category.")

    def validate(self, name, args):
        if name not in self.names or not isinstance(args, dict):
            raise AgentError("The tool or its arguments are not authorized.")
        if name == "leer_escenario":
            if args:
                raise AgentError("leer_escenario does not accept arguments.")
        elif name == "comparar_escenarios":
            self._validate_changes(args)
        elif set(args) != {"comparison_id"} or not isinstance(
            args["comparison_id"], str
        ):
            raise AgentError("A comparison_id from this conversation is required.")
        elif args["comparison_id"] not in self.comparisons:
            raise AgentError("The comparison is unknown or belongs to another context.")

    def dispatch(self, name, args):
        self.validate(name, args)
        if name == "leer_escenario":
            return {
                "scope": "demo_coquimbo",
                "synthetic": True,
                "profile_label": PROFILE_LABELS[self.parcel["profileId"]],
                "parcel": copy.deepcopy(self.parcel),
                "water_percent": self.water_percent,
            }
        if name == "comparar_escenarios":
            if len(self.comparisons) >= 20:
                raise AgentError("The local context is full; start a new session.")
            parcel = copy.deepcopy(self.parcel)
            if "previous_crop" in args:
                parcel["previousCrops"] = [args["previous_crop"]]
            water = args.get("water_percent", self.water_percent)
            request = self.engine.EvaluationInput(
                parcel=self.engine.Parcel.model_validate(parcel), waterPercent=water
            )
            raw = self.engine.evaluate(request)
            revision = hashlib.sha256(
                _json(
                    {"parcel": parcel, "waterPercent": water, "engine": self.engine_sha}
                ).encode()
            ).hexdigest()
            result = {
                "schema_version": "solquira.comparison/0.1",
                "comparison_id": "slq-" + revision[:20],
                "scenario_revision": revision,
                "rule_version": raw["ruleVersion"],
                "engine_sha256": self.engine_sha,
                "scope": "demo_coquimbo",
                "synthetic": True,
                "agronomicViability": "not_assessed",
                "status": raw["status"],
                "missing_fields": [
                    MISSING_LABELS.get(x, "Additional planning information")
                    for x in raw.get("missingFields", [])
                ],
                "scenario": {
                    "profile_id": parcel["profileId"],
                    "water_percent": water,
                    "previous_crop": parcel["previousCrops"][-1]
                    if parcel["previousCrops"]
                    else None,
                },
                "water_percent": water,
                "previous_crop": parcel["previousCrops"][-1]
                if parcel["previousCrops"]
                else None,
                "available_water_m3_ha": raw.get("availableWaterM3Ha"),
                "plans": [
                    {
                        "id": row["id"],
                        "sequence_ids": [
                            item.split(" (")[0].strip().lower() for item in row["crops"]
                        ],
                        "sequence": [
                            CROP_LABELS[item.split(" (")[0].strip().lower()]
                            for item in row["crops"]
                        ],
                        "name": " → ".join(
                            CROP_LABELS[item.split(" (")[0].strip().lower()]
                            for item in row["crops"]
                        ),
                        "demand_m3_ha": row["demandM3Ha"],
                        "score": row["score"],
                        "coverage_percent": row["coveragePercent"],
                        "balance_m3_ha": row["waterBalanceM3Ha"],
                        "history_ok": row["rotationAllowed"],
                        "eligible": row["eligibleUnderDemoRules"],
                    }
                    for row in raw.get("rotations", [])
                ],
            }
            self.comparisons[result["comparison_id"]] = copy.deepcopy(result)
            return result
        result = self.comparisons[args["comparison_id"]]
        if name == "revisar_suelo_cobertura":
            review = {
                "comparison_id": result["comparison_id"],
                "objective": "contribuir_a_disminuir_desertificacion",
                "assessment_status": "not_assessed",
                "environmental_impact": "not_measured",
                "observations": [],
                "open_questions": [
                    "What soil cover has been observed, and when?",
                    "Are signs of erosion documented by a local observation?",
                    "Which soil and land-management data have supporting evidence?",
                ],
                "note": "A synthetic scenario does not measure degradation or establish the absence of desertification.",
            }
            self._reviews[(name, result["comparison_id"])] = copy.deepcopy(review)
            return review
        if name == "revisar_evidencia":
            review = {
                "comparison_id": result["comparison_id"],
                "sources": [
                    {
                        "id": "demo-rules",
                        "kind": "synthetic",
                        "rule_version": result["rule_version"],
                    },
                    {"id": "parcel-input", "kind": "synthetic_example"},
                ],
                "not_available": [
                    "weather observations",
                    "validated imagery",
                    "validated sensor data",
                    "agronomic validation",
                    "soil and cover observations",
                ],
                "agronomicViability": "not_assessed",
            }
            self._reviews[(name, result["comparison_id"])] = copy.deepcopy(review)
            return review
        eligible = [row["id"] for row in result["plans"] if row["eligible"]]
        review = {
            "comparison_id": result["comparison_id"],
            "fits_demo_rules": eligible,
            "decision_status": "human_review_required",
            "pending": [
                "Check water and soil assumptions against local evidence",
                "Review the assumptions with agronomic support",
            ],
            "external_actions_executed": False,
        }
        self._reviews[(name, result["comparison_id"])] = copy.deepcopy(review)
        return review

    def missing_reviews(self, comparison_id):
        return sum(
            (name, comparison_id) not in self._reviews
            for name in (
                "revisar_evidencia",
                "revisar_suelo_cobertura",
                "preparar_decision",
            )
        )

    def _review(self, name, comparison_id):
        cached = self._reviews.get((name, comparison_id))
        return (
            copy.deepcopy(cached)
            if cached is not None
            else self.dispatch(name, {"comparison_id": comparison_id})
        )

    def schemas(self):
        definitions = [
            (
                "leer_escenario",
                "Read the current synthetic example; it is not a measured parcel.",
                {},
            ),
            (
                "comparar_escenarios",
                "Run the Solquira engine for exploratory changes without saving the parcel.",
                {
                    "water_percent": {
                        "type": "integer",
                        "enum": list(range(40, 121, 10)),
                    },
                    "previous_crop": {
                        "type": "string",
                        "enum": ["cereal", "leguminosa", "cubierta", "descanso"],
                    },
                },
            ),
            (
                "revisar_evidencia",
                "Review sources and gaps in an executed comparison.",
                {"comparison_id": {"type": "string"}},
            ),
            (
                "revisar_suelo_cobertura",
                "Identify soil/cover questions and evidence gaps without diagnosing degradation.",
                {"comparison_id": {"type": "string"}},
            ),
            (
                "preparar_decision",
                "Prepare a human-review checklist; do not execute agricultural actions.",
                {"comparison_id": {"type": "string"}},
            ),
        ]
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "additionalProperties": False,
                        "required": ["comparison_id"]
                        if "comparison_id" in props
                        else [],
                    },
                },
            }
            for name, desc, props in definitions
        ]

    def report(self, comparison_id, focus):
        if comparison_id not in self.comparisons or focus not in FOCUSES:
            raise AgentError(
                "The final result is not supported by tools from this execution."
            )
        result = copy.deepcopy(self.comparisons[comparison_id])
        count = sum(bool(row["eligible"]) for row in result["plans"])
        focus = next(
            (key for key, alias in FOCUS_IDS.items() if focus in (key, alias)),
            "decision",
        )
        result["focus"] = focus
        result["language"] = "en"
        result["evidence"] = self._review("revisar_evidencia", comparison_id)
        result["resilience"] = self._review("revisar_suelo_cobertura", comparison_id)
        result["decision"] = self._review("preparar_decision", comparison_id)
        water_amount = (
            f"{result['available_water_m3_ha']:,.0f}"
            if result["available_water_m3_ha"] is not None
            else "unknown"
        )
        summaries = {
            "water": f"At {water_amount} m³/ha in this example, {count} of {len(result['plans'])} plans fit the synthetic rules.",
            "history": f"{sum(not row['history_ok'] for row in result['plans'])} plans start with the previous crop group. Changing water alone does not resolve that repetition.",
            "evidence": f"This comparison uses {len(result['evidence']['sources'])} synthetic sources. Before a field decision, gather and review the missing evidence: "
            + ", ".join(result["evidence"]["not_available"])
            + ".",
            "resilience": "Bring local knowledge into the land review. "
            + " ".join(result["resilience"]["open_questions"]),
            "decision": f"{count} alternatives fit the demo rules. Your next checks: "
            + ". ".join(result["decision"]["pending"])
            + ".",
        }
        result["explanation"] = (
            summaries[focus] + " Agronomic viability has not been assessed."
        )
        if result["status"] != "ok":
            result["explanation"] = (
                "More information is required: "
                + ", ".join(result["missing_fields"])
                + ". No alternative is recommended."
            )
        return result


def _call_limit_s() -> float:
    """Transport cap for one model call. 3.0 s by default, as the product states.

    An operator may widen it for a bounded diagnostic; it never becomes unbounded
    and never changes the shipped default.
    """
    import os

    raw = os.environ.get("SOLQUIRA_MODEL_CALL_LIMIT_MS", "")
    try:
        value = float(raw)
    except ValueError:
        return 3.0
    return value / 1000.0 if 0 < value <= 60000 else 3.0


class NebiusProvider:
    """Explicit opt-in only. Does not read credentials, files or environment."""

    mode = "nebius_live"

    def __init__(
        self,
        *,
        enabled=False,
        api_key="",
        model="",
        base_url="https://api.tokenfactory.nebius.com/v1/",
        transport=None,
        disable_reasoning=False,
    ):
        parsed = urlparse(base_url)
        if not enabled:
            raise AgentError(
                "Remote inference is disabled and requires explicit authorization."
            )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"
        ):
            raise AgentError("The inference endpoint is not allowed.")
        if (
            not api_key
            or not model.lower().startswith("nvidia/")
            or "nemotron" not in model.lower()
        ):
            raise AgentError(
                "An injected credential and a catalogue-verified Nemotron model ID are required."
            )
        self._key, self.model, self._url, self._transport = (
            api_key,
            model,
            base_url.rstrip("/"),
            transport,
        )
        self.mode = "provider_stub" if transport is not None else "nebius_live"
        self.last_usage, self.last_http_status = {}, None
        self._disable_reasoning = disable_reasoning
        if disable_reasoning and model != "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B":
            raise AgentError("Reasoning control is limited to the verified Nano model.")

    def complete(self, messages, tools, *, deadline=None, max_tokens=700, on_delta=None):
        import httpx

        # Receipts describe this attempt, including failures before headers or
        # streams without usage. Never carry metrics from an earlier request.
        self.last_usage, self.last_http_status = {}, None

        # The admitted reservation envelope covers 700 output tokens; the field
        # brief caps at350; the route deadline, not the cap, bounds UI waiting.
        if type(max_tokens) is not int or not 1 <= max_tokens <= 700:
            raise AgentError(
                "The generation cap must fit the reserved output envelope."
            )
        limit = _call_limit_s()
        deadline = (
            min(deadline, time.monotonic() + limit)
            if deadline is not None
            else time.monotonic() + limit
        )
        _check_deadline(deadline)
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0 if not tools else 0.2,
        }
        if tools:
            body.update(tools=tools, tool_choice="auto")
        else:
            body["response_format"] = {"type": "json_object"}
        if on_delta is not None:
            if tools:
                raise AgentError("Field streaming does not permit tools.")
            body.update(stream=True, stream_options={"include_usage": True})
        if self._disable_reasoning:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            with httpx.Client(
                timeout=max(0.01, deadline - time.monotonic()),
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                _check_deadline(deadline)
                with client.stream(
                    "POST",
                    self._url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self._key},
                    json=body,
                ) as response:
                    self.last_http_status = response.status_code
                    response.raise_for_status()
                    if on_delta is not None:
                        content, size, finished = [], 0, False
                        for line in response.iter_lines():
                            _check_deadline(deadline)
                            size += len(line.encode("utf-8"))
                            if size > 256000:
                                raise AgentError("Provider stream size exceeded.")
                            if not line or line.startswith(":"):
                                continue
                            if not line.startswith("data: "):
                                raise AgentError("Invalid provider stream event.")
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            event = _parse_json(data)
                            usage = event.get("usage")
                            if isinstance(usage, dict):
                                self.last_usage = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                                   if type(usage.get(key)) is int and usage[key] >= 0}
                            choices = event.get("choices", [])
                            if not choices:
                                continue
                            if len(choices) != 1 or choices[0].get("index", 0) != 0:
                                raise AgentError("Unexpected provider choice.")
                            choice = choices[0]
                            if choice.get("finish_reason") == "length":
                                raise AgentError("Response was truncated.", code="response_truncated")
                            delta = choice.get("delta", {})
                            if delta.get("tool_calls"):
                                raise AgentError("Tools are not permitted in this stream.")
                            fragment = delta.get("content")
                            if fragment:
                                if not isinstance(fragment, str) or finished:
                                    raise AgentError("Invalid provider delta.")
                                content.append(fragment)
                                if sum(map(len, content)) > MAX_REPLY_CHARS:
                                    raise AgentError("Field response too large.")
                                on_delta(fragment)
                            if choice.get("finish_reason") is not None:
                                if choice["finish_reason"] != "stop":
                                    raise AgentError("Provider did not finish normally.")
                                finished = True
                        if not finished:
                            raise AgentError("Provider stream ended before completion.")
                        return {"content": "".join(content)}
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        _check_deadline(deadline)
                        size += len(chunk)
                        if size > 256000:
                            raise AgentError(
                                "The provider response exceeded the size limit."
                            )
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
                    usage = payload.get("usage", {})
                    self.last_usage = {
                        key: usage[key]
                        for key in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                        )
                        if isinstance(usage, dict)
                        and type(usage.get(key)) is int
                        and usage[key] >= 0
                    }
                    if payload["choices"][0].get("finish_reason") == "length":
                        raise AgentError(
                            "The provider exhausted its response budget before finishing.",
                            code="response_truncated",
                        )
                    return payload["choices"][0]["message"]
        except AgentError:
            raise
        except httpx.TimeoutException:
            raise AgentError("Remote inference timed out.", code="timeout") from None
        except Exception:
            raise AgentError(
                "Remote inference failed; it was not retried and raw output was not shown.",
                code="provider_error",
            ) from None


class Agent:
    """Provider must return a Chat Completions message; no provider is chosen implicitly."""

    def __init__(self, tools: DemoTools, provider):
        self.tools, self.provider = tools, provider
        self.base = Path(__file__).resolve().parents[2]

    def run(self, query, *, cancel_event=None, deadline=None, grounded=False):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_MESSAGE_CHARS
        ):
            raise AgentError("The question is empty or too long.")
        instructions = (self.base / "IDENTIDAD.md").read_text(encoding="utf-8")
        for f in sorted((self.base / "skills").glob("*/SKILL.md")):
            instructions += "\n" + f.read_text(encoding="utf-8")
        instructions += getattr(self.tools, "additional_instructions", "")
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": query},
        ]
        trace, count = [], 0
        # Only comparison IDs produced during this run can become the final result.
        produced = set()
        all_call_ids = set()
        if grounded:
            # Supply actual selected-state tools before asking a remote model to
            # explain them. This avoids guessing IDs or spending calls on setup.
            initial_calls = []
            initial_results = []
            current_comparison = None
            for name in getattr(
                self.tools, "grounding_names", ("leer_escenario", "comparar_escenarios")
            ):
                if count >= MAX_TOOL_CALLS:
                    raise AgentError(
                        "The grounding tool budget was exhausted.",
                        diagnostic="tool_budget",
                    )
                call_id = "solquira-current-" + name
                args = (
                    {"comparison_id": current_comparison}
                    if name
                    in (
                        "revisar_evidencia",
                        "revisar_suelo_cobertura",
                        "preparar_decision",
                    )
                    else {}
                )
                value = self.tools.dispatch(name, args)
                if name == "comparar_escenarios":
                    produced.add(value["comparison_id"])
                    current_comparison = value["comparison_id"]
                initial_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": _json(args)},
                    }
                )
                initial_results.append(
                    {"role": "tool", "tool_call_id": call_id, "content": _json(value)}
                )
                trace.append(
                    {
                        "tool": name,
                        "comparison_id": value.get("comparison_id"),
                        "status": "executed",
                    }
                )
                all_call_ids.add(call_id)
                count += 1
            messages.append(
                {"role": "assistant", "content": None, "tool_calls": initial_calls}
            )
            messages.extend(initial_results)
            if hasattr(self.tools, "completion_instructions"):
                messages.append(
                    {
                        "role": "system",
                        "content": self.tools.completion_instructions(
                            current_comparison
                        ),
                    }
                )
        final_only = grounded and getattr(self.tools, "grounded_final_only", False)
        schemas = [] if final_only else self.tools.schemas()
        started = time.monotonic()
        deadline = min(deadline, started + 45) if deadline is not None else started + 45
        for _ in range(MAX_TURNS):
            _check_deadline(deadline, cancel_event)
            if (
                time.monotonic() - started > 45
                or len(_json(messages)) > MAX_CONTEXT_CHARS
            ):
                raise AgentError("The time or context budget was exhausted.")
            try:
                stage = "provider_message"
                complete = self.provider.complete
                if "deadline" in inspect.signature(complete).parameters:
                    message = complete(
                        copy.deepcopy(messages), schemas, deadline=deadline
                    )
                else:
                    message = complete(copy.deepcopy(messages), schemas)
                _check_deadline(deadline, cancel_event)
                if (
                    not isinstance(message, dict)
                    or len(_json(message)) > MAX_REPLY_CHARS
                ):
                    raise AgentError("The model response is invalid.")
                if time.monotonic() - started > 45:
                    raise AgentError(
                        "The time budget was exhausted after the model call."
                    )
                calls = message.get("tool_calls")
                if calls is None:
                    calls = []
                if not isinstance(calls, list):
                    raise AgentError("The tool calls are malformed.")
                if calls:
                    if final_only:
                        raise AgentError(
                            "Grounded evidence is complete; additional model tools are not authorized.",
                            diagnostic="unexpected_tool_call",
                        )
                    if count + len(calls) > MAX_TOOL_CALLS:
                        raise AgentError(
                            "The tool-call budget was exhausted.",
                            diagnostic="tool_budget",
                        )
                    stage = "tool_validation"
                    seen, validated = set(), []
                    for call in calls:
                        if (
                            not isinstance(call, dict)
                            or call.get("type") != "function"
                            or not isinstance(call.get("id"), str)
                            or not call["id"]
                            or call["id"] in seen
                            or call["id"] in all_call_ids
                        ):
                            raise AgentError("The tool-call identifier is invalid.")
                        seen.add(call["id"])
                        all_call_ids.add(call["id"])
                        fn = call["function"]
                        args = _parse_json(fn["arguments"])
                        self.tools.validate(fn["name"], args)
                        validated.append((call["id"], fn["name"], args))
                    messages.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": copy.deepcopy(calls),
                        }
                    )
                    for call_id, name, args in validated:
                        _check_deadline(deadline, cancel_event)
                        result = self.tools.dispatch(name, args)
                        if name == "comparar_escenarios":
                            produced.add(result["comparison_id"])
                        trace.append(
                            {
                                "tool": name,
                                "comparison_id": result.get("comparison_id"),
                                "status": "executed",
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": _json(result),
                            }
                        )
                        count += 1
                    continue
                stage = "final_json"
                final = _parse_json(message.get("content") or "")
                stage = "final_shape"
                accepted_fields = getattr(
                    self.tools, "final_fields", ({"comparison_id", "focus"},)
                )
                if not isinstance(final, dict) or set(final) not in accepted_fields:
                    raise AgentError("The final result has an unsupported shape.")
                stage = "comparison_reference"
                if (
                    not isinstance(final.get("comparison_id"), str)
                    or final["comparison_id"] not in produced
                ):
                    raise AgentError(
                        "The final result has no comparison executed during this turn."
                    )
                stage = "focus_reference"
                if (
                    not isinstance(final.get("focus"), str)
                    or final["focus"] not in FOCUSES
                ):
                    raise AgentError("The final focus is not authorized.")
                if (
                    count + self.tools.missing_reviews(final["comparison_id"])
                    > MAX_TOOL_CALLS
                ):
                    raise AgentError(
                        "The tool-call budget does not allow final evidence assembly.",
                        code="budget_exceeded",
                    )
                final_report = getattr(self.tools, "final_report", None)
                stage = "report_validation"
                report = (
                    final_report(final)
                    if final_report
                    else self.tools.report(final["comparison_id"], final["focus"])
                )
                return {
                    "report": report,
                    "trace": trace,
                    "inference_mode": self.provider.mode,
                    "model": getattr(self.provider, "model", "scripted-test"),
                    "outcome": "success",
                    "runtime": "solquira-bounded-loop/0.1",
                }
            except AgentError as exc:
                if exc.code == "invalid_output" and exc.diagnostic is None:
                    exc.diagnostic = stage
                raise
            except TimeoutError:
                raise AgentError("The provider timed out.", code="timeout") from None
            except Exception:
                raise AgentError(
                    "The response or tool call is invalid; output was not accepted.",
                    diagnostic=stage,
                ) from None
        raise AgentError(
            "The model-turn budget was exhausted; no additional calls were made."
        )
