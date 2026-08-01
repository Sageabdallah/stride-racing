"""Racing data provider registry.

Select a provider with RACING_DATA_PROVIDER (default: puntingform — The
Racing API ceased Australian coverage, so its adapter stays selectable but
can no longer be the default). Adding a provider = one adapter module
implementing RacingDataProvider plus a registry entry here; no downstream
pipeline code changes.
"""

import os
from typing import Optional

from .base import RacingDataProvider
from .puntingform import PuntingFormProvider
from .theracingapi import TheRacingAPIProvider
from .validation import (
    IngestValidationError,
    ValidationReport,
    validate_meet_cards,
    validate_results,
)

_REGISTRY = {
    "puntingform": PuntingFormProvider,
    "theracingapi": TheRacingAPIProvider,
    "racing_api": TheRacingAPIProvider,
}


def get_provider(name: Optional[str] = None) -> RacingDataProvider:
    name = (name or os.environ.get("RACING_DATA_PROVIDER", "puntingform")).lower()
    provider_cls = _REGISTRY.get(name)
    if provider_cls is None:
        available = ", ".join(sorted(set(_REGISTRY)))
        raise ValueError(f"Unknown racing data provider '{name}'. Available: {available}")
    return provider_cls()


__all__ = [
    "RacingDataProvider",
    "PuntingFormProvider",
    "TheRacingAPIProvider",
    "IngestValidationError",
    "ValidationReport",
    "validate_meet_cards",
    "validate_results",
    "get_provider",
]
