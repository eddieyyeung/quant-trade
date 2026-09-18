"""Base class for service parameter objects.

Every service takes a single pydantic model describing its run. That makes the
run record in C2 a ``model_dump_json()`` away from being reproducible, and lets
validation reject bad input before any domain computation starts.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from quant_trade.config import AppConfig


class ServiceParams(BaseModel):
    """Base for all service parameter objects.

    Subclasses declare :meth:`config_defaults` to say which of their fields fall
    back to :class:`AppConfig`; callers that have a config use
    :meth:`from_config` and get those defaults for free.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_inverted_range(self) -> Self:
        """Reject a window whose start is after its end.

        Subclasses name the bounds either ``start``/``end`` or
        ``start_date``/``end_date``; an inverted window is otherwise accepted
        silently and surfaces much later as an empty result.
        """
        start = getattr(self, "start", None) or getattr(self, "start_date", None)
        end = getattr(self, "end", None) or getattr(self, "end_date", None)
        if isinstance(start, date) and isinstance(end, date) and start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")
        return self

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, Any]:
        """Field values taken from ``config`` when not supplied explicitly."""
        return {}

    @classmethod
    def from_config(cls, config: AppConfig, **overrides: Any) -> Self:
        """Build params from config defaults, with ``overrides`` winning."""
        merged = cls.config_defaults(config)
        merged.update(overrides)
        return cls(**merged)
