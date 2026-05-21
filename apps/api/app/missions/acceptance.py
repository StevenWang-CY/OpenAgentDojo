"""Pydantic model for ``missions/<id>/acceptance.yaml`` (plan §19.1).

The score-envelope acceptance file declares the expected band for two
synthetic event streams replayed during mission self-tests:

  * ``min_unmodified`` / ``max_unmodified`` — the embarrassing-middle band a
    user lands in when they submit the unmodified agent patch.
  * ``min_ideal`` — the floor for the ideal-solution replay.

The full scoring engine ships in M5; for M1 we only need the file to parse
cleanly so the validate CLI can confirm it.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_unmodified: int = Field(ge=0, le=100)
    max_unmodified: int = Field(ge=0, le=100)
    min_ideal: int = Field(ge=0, le=100)
    max_empty: int = Field(default=15, ge=0, le=100)

    @model_validator(mode="after")
    def _check_bands(self) -> AcceptanceEnvelope:
        if self.min_unmodified > self.max_unmodified:
            raise ValueError("acceptance.min_unmodified must be <= acceptance.max_unmodified")
        if self.min_ideal < self.max_unmodified:
            # Strong invariant: ideal must beat the embarrassing-middle ceiling.
            raise ValueError("acceptance.min_ideal must be >= acceptance.max_unmodified")
        return self


class MissionAcceptance(BaseModel):
    """Root model matching the ``acceptance.yaml`` shape ``{ acceptance: {...} }``."""

    model_config = ConfigDict(extra="forbid")

    acceptance: AcceptanceEnvelope


def load_acceptance(path: Path) -> MissionAcceptance:
    """Parse and validate an ``acceptance.yaml`` file.

    Raises ``pydantic.ValidationError`` (or yaml errors) on failure.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return MissionAcceptance.model_validate(data)
