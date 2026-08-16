"""Structured separation of audience content from production instructions."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from backend.src.production.domain.base import ContractModel


class ContentIntent(ContractModel):
    """The narrative contract extracted from a user's production request."""

    explicit_narration: str | None = Field(default=None, max_length=6_000)
    narration_authority: Literal["user_supplied", "model_generated"] = "model_generated"


_NARRATION_LABEL = re.compile(
    r"(?is)(?:^|\n)\s*(?:narraci[oó]n|narration|voice[- ]over)(?:\s+[^:\n]{0,40})?\s*:\s*"
)
_QUOTED_NARRATION = re.compile(r"(?s)[\"'“‘](.+?)[\"'”’]")


def extract_content_intent(prompt: str) -> ContentIntent:
    """Extract only explicitly labelled narration; retain all other text for planning."""

    match = _NARRATION_LABEL.search(prompt)
    if match is None:
        return ContentIntent()
    remainder = prompt[match.end() :]
    quoted = _QUOTED_NARRATION.search(remainder)
    narration = quoted.group(1).strip() if quoted is not None else _until_next_section(remainder)
    if not narration:
        return ContentIntent()
    return ContentIntent(
        explicit_narration=narration,
        narration_authority="user_supplied",
    )


def split_explicit_narration(text: str, scene_count: int) -> tuple[str, ...]:
    """Split supplied narration into ordered scene chunks without rewriting it."""

    if scene_count < 1:
        raise ValueError("scene count must be positive")
    sentences = tuple(
        part.strip()
        for part in re.findall(r"[^.!?¿¡]+[.!?]+|[^.!?¿¡]+$", text, flags=re.UNICODE)
        if part.strip()
    )
    if len(sentences) < scene_count:
        words = text.split()
        boundaries = [round(index * len(words) / scene_count) for index in range(scene_count + 1)]
        return tuple(
            " ".join(words[boundaries[index] : boundaries[index + 1]]).strip()
            or text.strip()
            for index in range(scene_count)
        )
    groups: list[list[str]] = [[] for _ in range(scene_count)]
    for index, sentence in enumerate(sentences):
        groups[min(index, scene_count - 1)].append(sentence)
    return tuple(" ".join(group).strip() for group in groups)


def _until_next_section(value: str) -> str:
    section = re.search(r"(?im)^\s*(?:escena|scene)\s+\d+\s*:", value)
    return value[: section.start()].strip() if section is not None else value.strip()
