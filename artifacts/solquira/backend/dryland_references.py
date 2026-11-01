"""Declared reference-table examples, not sampled soil observations.

Coordinates identify the selectable demonstration point, not a measured parcel.
Every SOC/water input below is an explicit synthetic teaching assumption. The
table must never be relabelled as SoilGrids, FAO, or a nearest-site estimate.
No network call, model request, personal coordinate or mutable cache is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType

REFERENCE_VERSION = "solquira.reference-table/1"
SOIL_SOURCE = "reference-table"


@dataclass(frozen=True)
class ReferenceSite:
    id: str
    label: str
    latitude: float
    longitude: float
    soc_g_per_kg: float
    field_capacity: float
    wilting_point: float
    depth_mm: float
    pathways: tuple[str, str, str]


# Four deliberately contrasting inputs make the deterministic response visible.
# They are not claims about regional average soil quality or crop suitability.
REFERENCE_SITES = MappingProxyType({
    "ouagadougou": ReferenceSite(
        "ouagadougou", "Ouagadougou", 12.371, -1.519,
        6.0, 0.23, 0.11, 600.0,
        ("sorghum_cowpea", "millet_cowpea", "amaranth"),
    ),
    "andalusia": ReferenceSite(
        "andalusia", "Andalusia farmland", 37.1, -4.6,
        11.0, 0.28, 0.13, 600.0,
        ("sorghum_cowpea", "quinoa", "opuntia"),
    ),
    "east_african_plateau": ReferenceSite(
        "east_african_plateau", "East African plateau", -0.5, 36.9,
        18.0, 0.34, 0.15, 800.0,
        ("sorghum_cowpea", "millet_cowpea", "amaranth"),
    ),
})


def get_reference(place: str = "ouagadougou") -> ReferenceSite:
    if not isinstance(place, str) or place not in REFERENCE_SITES:
        raise ValueError("Choose a supported reference point.")
    return REFERENCE_SITES[place]


def reference_payload(place: str = "ouagadougou", scenario: str = "dry"):
    """Build canonical bands in Python; weather remains an explicit scenario."""
    from dryland_brief import (
        FieldPayload, PathwayId, available_water_capacity, band_awc, band_soc,
    )

    site = get_reference(place)
    if scenario not in {"dry", "rain"}:
        raise ValueError("Choose the dry or rain example.")
    awc = available_water_capacity(
        site.field_capacity, site.wilting_point, site.depth_mm,
    )
    return FieldPayload(
        lat=site.latitude,
        lon=site.longitude,
        soc_band=band_soc(site.soc_g_per_kg),
        awc_band=band_awc(awc),
        rain_phrase="Dry week ahead — prepare water pockets"
        if scenario == "dry"
        else "Rain may return — inspect water pathways",
        dry_days_ahead=6 if scenario == "dry" else 1,
        fao_crops=[PathwayId(value) for value in site.pathways],
    )


def reference_metadata(place: str = "ouagadougou") -> dict:
    """Provenance is inseparable from the numerical teaching assumptions."""
    from dryland_brief import available_water_capacity

    site = get_reference(place)
    return {
        "schema_version": REFERENCE_VERSION,
        "id": site.id,
        "label": site.label,
        "latitude": site.latitude,
        "longitude": site.longitude,
        "soil_source": SOIL_SOURCE,
        "synthetic": True,
        "observed_at": None,
        "assumptions": {
            "soc_g_per_kg": site.soc_g_per_kg,
            "field_capacity_fraction": site.field_capacity,
            "wilting_point_fraction": site.wilting_point,
            "depth_mm": site.depth_mm,
            "awc_mm": available_water_capacity(
                site.field_capacity, site.wilting_point, site.depth_mm,
            ),
        },
        "method": "Synthetic SOC and water fractions; AWC is fraction difference times depth.",
        "pathway_source": "Demonstration candidate list; not a local suitability assessment.",
    }


def list_reference_sites() -> list[dict]:
    return [reference_metadata(place) for place in REFERENCE_SITES]


def reference_for_coordinates(latitude: float, longitude: float) -> str | None:
    """Match exact selectable points only; never infer soil from proximity."""
    for value, bound in ((latitude, 90), (longitude, 180)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not -bound <= value <= bound):
            raise ValueError("Use finite geographic coordinates.")
    return next((site.id for site in REFERENCE_SITES.values()
                 if (site.latitude, site.longitude) == (latitude, longitude)), None)


def resolve_reference_payload(payload):
    """Rebuild server values; reject client edits, including changed coordinates."""
    from dryland_brief import FieldPayload

    if not isinstance(payload, FieldPayload):
        raise ValueError("Use a validated field payload.")
    place = reference_for_coordinates(payload.lat, payload.lon)
    if place is not None:
        for scenario in ("dry", "rain"):
            canonical = reference_payload(place, scenario)
            if payload.model_dump() == canonical.model_dump():
                return place, scenario, canonical
    raise ValueError("Reload the selected reference; its evidence cannot be replaced.")
