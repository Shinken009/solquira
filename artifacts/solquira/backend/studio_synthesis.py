"""Constrained evidence composition, not free-form agronomic advice.

The model selects and orders supported statements/checks and a conversation.
Only this request's catalogue can render text: no model quantities, URLs,
instructions or arbitrary prose reach the report. No persistence or network.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from studio_brief import weather_evidence


class SynthesisValidationError(ValueError):
    """Allowlisted failure category, never the rejected value or model text."""

    def __init__(self, category):
        super().__init__("Unsupported synthesis.")
        self.category = category


def fresh_forecast(context):
    if context is None:
        return None
    stamp = datetime.fromisoformat(context["retrieved_at"])
    if (
        stamp.tzinfo is None
        or not 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() < 600
    ):
        raise ValueError("Reload the expired forecast before attaching it.")
    return weather_evidence(context)


def record_evidence(record):
    if record is None:
        return None
    from studio_schema import StudioFieldRecord

    value = StudioFieldRecord.model_validate(record).model_dump()
    return {
        **value,
        "provenance": "self_reported",
        "missing_fields": [
            key for key in ("soil_cover", "water_access") if value[key] == "unknown"
        ],
    }


def catalogue(context=None, record=None):
    forecast = fresh_forecast(context)
    record = record_evidence(record)
    sources = [
        {"id": "demo-rules", "label": "Synthetic crop comparison", "kind": "synthetic"},
        {
            "id": "field-inputs",
            "label": "Field observations still needed",
            "kind": "missing_inputs",
        },
    ]
    statements = {
        "example-limit": {
            "text": "The crop example helps compare assumptions; it does not establish field suitability.",
            "evidence_ids": ["demo-rules"],
        }
    }
    checks = {}

    def check(key, title, detail, evidence, destination="question"):
        checks[key] = {
            "id": key,
            "title": title,
            "detail": detail,
            "evidence_ids": evidence,
            "destination": destination,
        }

    check(
        "confirm-soil-water",
        "Check water in the soil",
        "Record root-zone moisture, recent watering and crop stage before discussing an irrigation amount.",
        ["field-inputs"],
        "weather",
    )
    check(
        "observe-cover",
        "Record ground-cover gaps",
        "Note bare ground, residue, living cover and visible erosion from a safe, permitted location.",
        ["field-inputs"],
        "soil",
    )
    questions = {
        "crop-stage": "What crop and growth stage are present, and what moisture is observed in the root zone?"
    }
    followups = {
        "bring-observations": "After your next safe observation, bring the date and what changed to discuss the next check with local support."
    }
    required_statement = None
    preferred = []
    if forecast:
        sources.append(
            {
                "id": "forecast-7d",
                "label": "Open-Meteo seven-day model forecast",
                "kind": "model_forecast",
            }
        )
        dry = forecast["rain_mm"] < forecast["reference_et0_mm"]
        key = "forecast-dry" if dry else "forecast-wet"
        statements[key] = {
            "text": "Forecast rain is below reference atmospheric demand; actual soil water still needs checking."
            if dry
            else "Forecast rain meets or exceeds reference atmospheric demand; infiltration and runoff still need checking.",
            "evidence_ids": ["forecast-7d", "field-inputs"],
        }
        preferred.append(key)
        if dry:
            checks["confirm-soil-water"]["evidence_ids"].append("forecast-7d")
        else:
            check(
                "check-infiltration",
                "Check infiltration after rain",
                "From a safe location after rain, note ponding, runoff and whether water entered the soil. Forecast rain is not stored root-zone water.",
                ["forecast-7d", "field-inputs"],
                "weather",
            )
        check(
            "compare-forecast",
            "Compare the forecast with your observations",
            "Record whether local rain and field conditions match the forecast period; keep differences visible.",
            ["forecast-7d", "field-inputs"],
            "weather",
        )
    else:
        statements["forecast-missing"] = {
            "text": "Start with a dated field observation. If a forecast is available, attach it for context.",
            "evidence_ids": ["field-inputs"],
        }
        preferred.append("forecast-missing")
    if record:
        sources.append(
            {
                "id": "field-record",
                "label": "Your self-reported field record · " + record["observed_at"],
                "kind": "self_reported",
            }
        )
        cover = record["soil_cover"]
        if cover != "unknown":
            labels = {
                "mostly_bare": "mostly bare",
                "patchy": "patchy",
                "well_covered": "well covered",
            }
            statements["reported-cover"] = {
                "text": "Your observation on "
                + record["observed_at"]
                + " describes "
                + labels[cover]
                + " ground cover. Compare it with the same patch on your next visit.",
                "evidence_ids": ["field-record"],
            }
            preferred.append("reported-cover")
            checks["observe-cover"]["evidence_ids"].append("field-record")
        else:
            questions["cover-missing"] = (
                "Is the ground mostly bare, patchy or well covered, and when did you observe it?"
            )
        access = record["water_access"]
        if access != "unknown":
            statements["reported-water"] = {
                "text": "You reported "
                + access
                + " water access; access to water and forecast rainfall are different planning inputs.",
                "evidence_ids": ["field-record"],
            }
            check(
                "confirm-water-access",
                "Confirm usable water access",
                "Clarify whether the reported supply is available when needed before discussing any irrigation plan.",
                ["field-record"],
                "question",
            )
        else:
            questions["water-access-missing"] = (
                "Is usable water access reliable, limited or unpredictable for the next visit?"
            )
        constraint = record["constraint"]
    else:
        constraint = "none"
        questions["cover-missing"] = (
            "What ground cover can you observe safely, and on what date?"
        )
        questions["water-access-missing"] = (
            "What usable water access is available, if any?"
        )
    required_check = required_followup = None
    if constraint != "none":
        wording = {
            "water": (
                "water access",
                "Clarify what water is actually accessible before discussing irrigation-dependent options.",
                "Who can clarify usable water access, and what observation can you bring to that conversation?",
            ),
            "cost": (
                "cost",
                "List what can be observed without purchases; ask local support about feasible options before spending.",
                "Which observation can you gather without spending, and who can review low-cost options with you?",
            ),
            "tools": (
                "tools",
                "Identify what can be checked safely without new equipment and where practical support is available.",
                "What can you observe with the tools already available, and what help would you need?",
            ),
            "time": (
                "time",
                "Choose a short, safe observation that fits the next available visit rather than a larger intervention.",
                "When is a realistic next visit, and which single observation can fit that time?",
            ),
            "advice": (
                "access to advice",
                "Identify a local adviser or experienced support person before choosing an intervention.",
                "Who could review your dated observations and help choose a locally suitable next step?",
            ),
            "permission": (
                "land-access permission",
                "Confirm permission before entering the land or changing anything; use existing observations meanwhile.",
                "Who can confirm permission to enter or make changes before a field visit is arranged?",
            ),
        }
        label, detail, followup = wording[constraint]
        required_statement = "constraint-" + constraint
        statements[required_statement] = {
            "text": "Plan the next conversation around your reported "
            + label
            + " constraint and the support available to you.",
            "evidence_ids": ["field-record"],
        }
        preferred = preferred[:2] + [required_statement]
        required_check = "discuss-" + constraint
        required_followup = "follow-" + constraint
        check(required_check, "Plan around " + label, detail, ["field-record"])
        followups = {required_followup: followup}
    first_check = (
        "check-infiltration" if "check-infiltration" in checks else "confirm-soil-water"
    )
    default_checks = ([required_check] if required_check else []) + [
        first_check,
        "observe-cover",
    ]
    default = {
        "statement_ids": preferred[:3],
        "check_ids": default_checks[:3],
        "question_id": "water-access-missing"
        if "water-access-missing" in questions
        else "crop-stage",
        "follow_up_id": required_followup or "bring-observations",
    }
    return {
        "statements": statements,
        "checks": checks,
        "questions": questions,
        "followups": followups,
        "sources": sources,
        "record": record,
        "default": default,
        "required_statement": required_statement,
        "required_check": required_check,
    }


def render_synthesis(catalog, selection=None):
    origin = "model_selected" if selection is not None else "deterministic"
    selection = copy.deepcopy(
        selection if selection is not None else catalog["default"]
    )
    if not isinstance(selection, dict) or set(selection) != {
        "statement_ids",
        "check_ids",
        "question_id",
        "follow_up_id",
    }:
        raise SynthesisValidationError("synthesis_shape")
    for field, available in (
        ("statement_ids", catalog["statements"]),
        ("check_ids", catalog["checks"]),
    ):
        ids = selection[field]
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 3
            or any(not isinstance(item, str) or item not in available for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise SynthesisValidationError(
                "synthesis_statement_reference"
                if field == "statement_ids"
                else "synthesis_check_reference"
            )
    for field, available in (
        ("question_id", catalog["questions"]),
        ("follow_up_id", catalog["followups"]),
    ):
        if not isinstance(selection[field], str) or selection[field] not in available:
            raise SynthesisValidationError(
                "synthesis_question_reference"
                if field == "question_id"
                else "synthesis_followup_reference"
            )
    if (
        catalog["required_statement"]
        and catalog["required_statement"] not in selection["statement_ids"]
    ):
        raise SynthesisValidationError("synthesis_constraint_omitted")
    if (
        catalog["required_check"]
        and catalog["required_check"] not in selection["check_ids"]
    ):
        raise SynthesisValidationError("synthesis_constraint_check_omitted")
    statements = [catalog["statements"][key] for key in selection["statement_ids"]]
    evidence_ids = list(
        dict.fromkeys(e for row in statements for e in row["evidence_ids"])
    )
    checks = [copy.deepcopy(catalog["checks"][key]) for key in selection["check_ids"]]
    source_ids = {s["id"] for s in catalog["sources"]}
    if (
        not set(evidence_ids + [e for row in checks for e in row["evidence_ids"]])
        <= source_ids
    ):
        raise SynthesisValidationError("synthesis_source_reference")
    result = {
        "schema_version": "solquira.synthesis/1",
        "origin": origin,
        "summary": " ".join(row["text"] for row in statements),
        "evidence_ids": evidence_ids,
        "checks": checks,
        "missing_input_question": catalog["questions"][selection["question_id"]],
        "follow_up": catalog["followups"][selection["follow_up_id"]],
        "sources": copy.deepcopy(catalog["sources"]),
        "external_actions_executed": False,
    }
    if catalog["record"] is not None:
        result["record"] = copy.deepcopy(catalog["record"])
    return result
