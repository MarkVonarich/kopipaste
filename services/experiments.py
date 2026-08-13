from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Experiment:
    key: str
    version: int
    variants: tuple[str, ...]
    surfaces: tuple[str, ...]


EXPERIMENTS = {
    "spendable-explanation-v1": Experiment(
        key="spendable-explanation-v1",
        version=1,
        variants=("compact", "reasons_first"),
        surfaces=("home_spendable", "forecast_detail"),
    ),
}


def assign_variant(experiment_key: str, user_id: int) -> str:
    experiment = EXPERIMENTS.get(experiment_key)
    if experiment is None:
        raise ValueError("unknown_experiment")
    digest = hashlib.sha256(f"{experiment.key}:{experiment.version}:{int(user_id)}".encode("utf-8")).digest()
    return experiment.variants[int.from_bytes(digest[:8], "big") % len(experiment.variants)]


def exposure_properties(experiment_key: str, user_id: int, surface: str, quality_tier: str) -> dict[str, str | int]:
    experiment = EXPERIMENTS.get(experiment_key)
    if experiment is None or surface not in experiment.surfaces:
        raise ValueError("unknown_experiment_surface")
    return {
        "experiment_key": experiment.key,
        "experiment_version": experiment.version,
        "variant": assign_variant(experiment_key, user_id),
        "surface": surface,
        "quality_tier": quality_tier,
    }
