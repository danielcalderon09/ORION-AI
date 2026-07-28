from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.src.production.scene_planning.exceptions import (
    ProductionScriptNotFoundException,
)
from backend.src.production.scene_planning.ports import ReadProductionScript
from backend.src.production.speech_generation.exceptions import (
    SpeechSourceScriptNotFoundError,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentStatus,
    replace_speech_entry,
    validate_speech_manifest_transition,
)
from backend.src.production.speech_generation.segment_builder import (
    build_speech_segments,
    normalize_narration_text,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
    serialize_speech_manifest,
)
from backend.src.production.speech_generation.source_reader import (
    SpeechSourceScriptReaderAdapter,
)
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    command_context,
    source_script,
    speech_configuration,
)


def _manifest(source):
    from backend.src.production.speech_generation.handler import SpeechGenerationHandler

    segments = build_speech_segments(source, speech_configuration())
    handler = object.__new__(SpeechGenerationHandler)
    handler._configuration = speech_configuration()
    handler._clock = lambda: NOW
    from backend.tests.unit.production.speech_generation.conftest import command_context

    command, _ = command_context()
    return handler._initial_manifest(
        command=command,
        source=source,
        segments=segments,
    )


def test_segments_are_stable_ordered_and_bound_to_script(source) -> None:
    configuration = speech_configuration()
    first = build_speech_segments(source, configuration)
    second = build_speech_segments(source, configuration)

    assert first == second
    assert tuple(item.sequence_index for item in first) == (0, 1)
    assert tuple(item.scene_id for item in first) == ("scene-001", "scene-002")
    assert all(item.source_script_sha256 == source.sha256 for item in first)
    assert first[0].normalized_text_hash != first[1].normalized_text_hash
    assert first[0].target_duration_ms == 500


def test_segment_identity_changes_with_durable_input() -> None:
    configuration = speech_configuration()
    original = build_speech_segments(source_script(), configuration)[0]
    changed_text = build_speech_segments(
        source_script(first_narration="Hola, universo."),
        configuration,
    )[0]
    changed_checksum = build_speech_segments(
        source_script(sha256="b" * 64),
        configuration,
    )[0]

    assert original.segment_id != changed_text.segment_id
    assert original.segment_id != changed_checksum.segment_id


@pytest.mark.asyncio
async def test_source_reader_adapter_preserves_verified_script_identity(source) -> None:
    class Reader:
        async def read_for_scene_planning(self, *, context):
            del context
            return ReadProductionScript.model_validate(source.model_dump())

    _, context = command_context()
    adapted = await SpeechSourceScriptReaderAdapter(Reader()).read_for_speech_generation(
        context=context
    )

    assert adapted == source


@pytest.mark.asyncio
async def test_source_reader_adapter_maps_missing_script_to_speech_error() -> None:
    class Reader:
        async def read_for_scene_planning(self, *, context):
            del context
            raise ProductionScriptNotFoundException("internal repository detail")

    _, context = command_context()
    with pytest.raises(SpeechSourceScriptNotFoundError, match="no durable"):
        await SpeechSourceScriptReaderAdapter(Reader()).read_for_speech_generation(context=context)


def test_text_normalization_is_deterministic() -> None:
    assert normalize_narration_text("  Hola \n mundo  ") == "Hola mundo"
    assert normalize_narration_text("Cafe\u0301") == "Caf\u00e9"


def test_contracts_are_immutable_and_reject_extra_fields(source) -> None:
    segment = build_speech_segments(source, speech_configuration())[0]
    with pytest.raises(ValidationError):
        segment.sequence_index = 9
    with pytest.raises(ValidationError):
        type(segment).model_validate({**segment.model_dump(mode="python"), "unexpected": True})


def test_manifest_serialization_is_canonical_and_strict(source) -> None:
    manifest = _manifest(source)
    content = serialize_speech_manifest(manifest)

    assert content.endswith(b"\n")
    assert deserialize_speech_manifest(content) == manifest
    with pytest.raises(ValueError, match="duplicate"):
        deserialize_speech_manifest(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(ValueError, match="constant"):
        deserialize_speech_manifest(b'{"value":NaN}')


def test_legal_and_illegal_transitions_are_enforced(source) -> None:
    manifest = _manifest(source)
    entry = manifest.entries[0].model_copy(
        update={
            "status": SpeechSegmentStatus.GENERATING,
            "generation_started_at": NOW,
            "generation_attempt_count": 1,
        }
    )
    generating = replace_speech_entry(
        manifest,
        entry,
        updated_at=NOW + timedelta(seconds=1),
    )
    validate_speech_manifest_transition(manifest, generating)

    illegal_entry = entry.model_copy(update={"status": SpeechSegmentStatus.PENDING})
    illegal = replace_speech_entry(
        generating,
        illegal_entry,
        status=SpeechGenerationManifestStatus.IN_PROGRESS,
        updated_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ValueError, match="invalid speech transition"):
        validate_speech_manifest_transition(generating, illegal)


def test_sensitive_metadata_and_naive_timestamps_fail_closed(source) -> None:
    manifest = _manifest(source)
    with pytest.raises(ValidationError):
        type(manifest).model_validate(
            {
                **manifest.model_dump(mode="python"),
                "metadata": {"authorization": "Bearer fake"},
            }
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        type(manifest).model_validate(
            {
                **manifest.model_dump(mode="python"),
                "created_at": NOW.replace(tzinfo=None),
            }
        )
