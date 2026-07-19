"""Base contract every pipeline stage implements.

A stage takes a ``PipelineContext``, mutates the ``result`` it carries, and
returns it. Stage-specific failures are wrapped in ``StageError`` so the
orchestrator can route them uniformly without inspecting error strings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pipeline.models.output_schema import PipelineContext


class StageError(Exception):
    """Wraps any exception raised inside a stage's ``process``."""

    def __init__(self, stage_name: str, message: str, original: Exception | None = None):
        self.stage_name = stage_name
        self.original = original
        super().__init__(f"[{stage_name}] {message}")


class BaseStage(ABC):
    """All pipeline stages inherit from this."""

    #: Short, stable identifier used in logs and failure tracking.
    name: str = "base"

    @abstractmethod
    def process(self, context: PipelineContext) -> PipelineContext:
        """Run this stage, mutating and returning the context."""
        raise NotImplementedError
