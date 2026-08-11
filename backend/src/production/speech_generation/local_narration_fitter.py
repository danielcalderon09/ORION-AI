"""Conservative deterministic Spanish narration fitting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from backend.src.production.speech_generation.narration_fitting import (
    LocalNarrationFittingResult,
    NarrationFittingProviderError,
    NarrationFittingRequest,
    validate_narration_revision,
)


@dataclass(frozen=True, slots=True)
class _ReductionRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


_RULES = (
    _ReductionRule("purpose_para", re.compile(r"\bcon el objetivo de\b", re.I), "para"),
    _ReductionRule("purpose_para", re.compile(r"\bcon la finalidad de\b", re.I), "para"),
    _ReductionRule("cause_porque", re.compile(r"\bdebido al hecho de que\b", re.I), "porque"),
    _ReductionRule("contrast_aunque", re.compile(r"\ba pesar del hecho de que\b", re.I), "aunque"),
    _ReductionRule("time_cuando", re.compile(r"\ben el momento en que\b", re.I), "cuando"),
    _ReductionRule("current_hoy", re.compile(r"\ben la actualidad\b", re.I), "hoy"),
    _ReductionRule("location_esta", re.compile(r"\bse encuentra ubicad[oa]\b", re.I), "está"),
    _ReductionRule("ability_puede", re.compile(r"\bes capaz de\b", re.I), "puede"),
    _ReductionRule("ability_pueden", re.compile(r"\bson capaces de\b", re.I), "pueden"),
    _ReductionRule("action_realiza", re.compile(r"\blleva a cabo\b", re.I), "realiza"),
    _ReductionRule("threshold_hasta", re.compile(r"\bhasta un punto de\b", re.I), "hasta"),
    _ReductionRule("still_aun", re.compile(r"\btodavía\b", re.I), "aún"),
)

_TOKEN = re.compile(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ]+\b", re.UNICODE)
_NUMBER = re.compile(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)?)(?:\s*%)?(?!\w)")
_NEGATIONS = frozenset({"no", "nunca", "jamás", "sin", "ningún", "ninguna", "ni"})


class DeterministicSpanishNarrationFitter:
    """Apply a bounded, auditable set of meaning-preserving Spanish rewrites."""

    name = "deterministic_local"
    model = "spanish_rules_v1"
    maximum_overrun_ratio = Decimal("0.30")
    maximum_reduction_ratio = Decimal("0.35")
    minimum_reduction_ratio = Decimal("0.05")
    minimum_word_retention_ratio = Decimal("0.65")

    def revise(self, request: NarrationFittingRequest) -> LocalNarrationFittingResult | None:
        if not request.language.lower().startswith("es"):
            return None
        overrun_ratio = Decimal(
            request.current_duration_ms - request.target_duration_ms
        ) / Decimal(request.target_duration_ms)
        required_reduction = Decimal(
            request.current_duration_ms - request.target_duration_ms
        ) / Decimal(request.current_duration_ms)
        if (
            overrun_ratio <= 0
            or overrun_ratio > self.maximum_overrun_ratio
            or required_reduction > self.maximum_reduction_ratio
        ):
            return None

        source = _normalize(request.current_narration)
        candidate = source
        applied: list[str] = []
        for rule in _RULES:
            revised, count = rule.pattern.subn(rule.replacement, candidate)
            if count:
                candidate = revised
                applied.extend(rule.name for _ in range(count))
        candidate = _normalize(candidate)
        if source[:1].isupper() and candidate[:1].islower():
            candidate = candidate[:1].upper() + candidate[1:]
        if not applied or not _semantically_safe(source, candidate):
            return None
        try:
            candidate = validate_narration_revision(source, candidate)
        except NarrationFittingProviderError:
            return None
        return LocalNarrationFittingResult(
            revised_narration=candidate,
            rules_applied=tuple(applied),
        )


def _normalize(value: str) -> str:
    value = " ".join(value.split()).strip()
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,.;:!?])(?=\w)", r"\1 ", value)
    return value


def _semantically_safe(source: str, candidate: str) -> bool:
    source_tokens = _TOKEN.findall(source)
    candidate_tokens = _TOKEN.findall(candidate)
    if len(source_tokens) < 6 or len(candidate_tokens) < 3:
        return False
    retention = Decimal(len(candidate_tokens)) / Decimal(len(source_tokens))
    if retention < DeterministicSpanishNarrationFitter.minimum_word_retention_ratio:
        return False
    reduction = Decimal(len(source) - len(candidate)) / Decimal(len(source))
    if (
        reduction < DeterministicSpanishNarrationFitter.minimum_reduction_ratio
        or reduction > DeterministicSpanishNarrationFitter.maximum_reduction_ratio
    ):
        return False
    if _NUMBER.findall(source) != _NUMBER.findall(candidate):
        return False
    if _negations(source_tokens) != _negations(candidate_tokens):
        return False
    return _named_entities(source_tokens).issubset(set(candidate_tokens))


def _negations(tokens: list[str]) -> tuple[str, ...]:
    return tuple(token.casefold() for token in tokens if token.casefold() in _NEGATIONS)


def _named_entities(tokens: list[str]) -> frozenset[str]:
    return frozenset(token for token in tokens[1:] if token[:1].isupper())


__all__ = ["DeterministicSpanishNarrationFitter"]
