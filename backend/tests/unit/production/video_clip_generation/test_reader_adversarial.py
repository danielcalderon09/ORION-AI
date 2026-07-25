"""Adversarial coverage for the durable image acquisition manifest reader."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.binary_assets.exceptions import BinaryAssetLinkError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestAmbiguousException,
    ImageAcquisitionManifestEncodingException,
    ImageAcquisitionManifestIncompleteException,
    ImageAcquisitionManifestJobException,
    ImageAcquisitionManifestJsonException,
    ImageAcquisitionManifestLinkException,
    ImageAcquisitionManifestMissingFileException,
    ImageAcquisitionManifestPathException,
    ImageAcquisitionManifestSchemaException,
    ImageAcquisitionManifestSizeException,
    ImageAcquisitionManifestTypeException,
    ImageAcquisitionManifestVersionException,
    SourceImageCorruptException,
    SourceImageMissingException,
    SourceImageProvenanceException,
)
from backend.src.production.video_clip_generation.ports import InputArtifactIdentity
from backend.src.production.video_clip_generation.reader import (
    DurableImageAcquisitionManifestReader,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    JOB_ID,
    command_context,
    durable_source,
)


def _reader(root, binary_store, repository, *, maximum=200_000):
    return DurableImageAcquisitionManifestReader(
        workspace_root=root,
        repository=repository,
        binary_reader=binary_store,
        max_manifest_bytes=maximum,
    )


def _manifest_target(root: Path, repository) -> Path:
    return root.joinpath(*repository.manifests[0].relative_path.split("/"))


def _replace_manifest_bytes(root: Path, repository, content: bytes) -> None:
    target = _manifest_target(root, repository)
    target.write_bytes(content)
    repository.manifests = (
        repository.manifests[0].model_copy(
            update={
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ),
    )


@pytest.mark.asyncio
async def test_reader_rejects_ambiguous_explicit_manifests(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    first = repository.manifests[0]
    second_id = UUID("30000000-0000-4000-8000-000000001002")
    second = first.model_copy(update={"artifact_id": second_id})
    repository.manifests = (first, second)
    repository.input_artifacts[second_id] = InputArtifactIdentity(
        artifact_id=second_id,
        job_id=JOB_ID,
        artifact_type=ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
    )
    _, context = command_context(input_ids=(first.artifact_id, second_id))
    with pytest.raises(ImageAcquisitionManifestAmbiguousException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
async def test_reader_rejects_explicit_manifest_from_another_job(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    foreign_id = UUID("30000000-0000-4000-8000-000000001099")
    repository.input_artifacts = {
        foreign_id: InputArtifactIdentity(
            artifact_id=foreign_id,
            job_id=UUID("10000000-0000-4000-8000-000000001099"),
            artifact_type=ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
        )
    }
    _, context = command_context(input_ids=(foreign_id,))
    with pytest.raises(ImageAcquisitionManifestJobException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
async def test_reader_rejects_explicit_wrong_artifact_type(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    wrong_id = UUID("30000000-0000-4000-8000-000000001098")
    repository.input_artifacts = {
        wrong_id: InputArtifactIdentity(
            artifact_id=wrong_id,
            job_id=JOB_ID,
            artifact_type=ArtifactType.SOURCE_IMAGE,
        )
    }
    _, context = command_context(input_ids=(wrong_id,))
    with pytest.raises(ImageAcquisitionManifestTypeException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_path",
    (
        "C:/absolute/image-acquisition-manifest.json",
        "/absolute/image-acquisition-manifest.json",
        "production/../image-acquisition-manifest.json",
        f"production/{JOB_ID}/acquiring_assets/attempt-1/other.json",
    ),
)
async def test_reader_rejects_absolute_traversal_and_noncontractual_paths(
    tmp_path,
    unsafe_path,
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    repository.manifests = (
        repository.manifests[0].model_copy(update={"relative_path": unsafe_path}),
    )
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestPathException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("link_kind", ("symlink", "junction"))
async def test_reader_rejects_link_or_junction_manifest(
    tmp_path,
    monkeypatch,
    link_kind,
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    target = _manifest_target(tmp_path, repository)
    reader = _reader(tmp_path, binary_store, repository)
    original = WorkspaceConfinement._reject_link_or_reparse

    def reject_link(path: Path, *, allow_missing: bool) -> None:
        if path == target:
            raise BinaryAssetLinkError(f"simulated {link_kind}")
        original(path, allow_missing=allow_missing)

    monkeypatch.setattr(
        WorkspaceConfinement,
        "_reject_link_or_reparse",
        staticmethod(reject_link),
    )
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestLinkException):
        await reader.read_for_video_clip_generation(context=context)


@pytest.mark.asyncio
async def test_reader_rejects_missing_file_excessive_and_mismatched_sizes(
    tmp_path,
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    target = _manifest_target(tmp_path, repository)
    target.unlink()
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestMissingFileException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )

    _, binary_store, repository = await durable_source(tmp_path / "large")
    with pytest.raises(ImageAcquisitionManifestSizeException):
        await _reader(
            tmp_path / "large",
            binary_store,
            repository,
            maximum=32,
        ).read_for_video_clip_generation(context=context)

    _, binary_store, repository = await durable_source(tmp_path / "mismatch")
    repository.manifests = (
        repository.manifests[0].model_copy(
            update={"size_bytes": repository.manifests[0].size_bytes + 1}
        ),
    )
    with pytest.raises(ImageAcquisitionManifestSizeException):
        await _reader(
            tmp_path / "mismatch",
            binary_store,
            repository,
        ).read_for_video_clip_generation(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (b"\xff\xfe\x00\x00", ImageAcquisitionManifestEncodingException),
        (b"{", ImageAcquisitionManifestJsonException),
        (b'{"schema_version":NaN}', ImageAcquisitionManifestJsonException),
        (b'{"schema_version":Infinity}', ImageAcquisitionManifestJsonException),
        (
            b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
            ImageAcquisitionManifestJsonException,
        ),
        (
            b'{"schema_version":"1.0.0","status":"completed","entries":[]}',
            ImageAcquisitionManifestSchemaException,
        ),
        (
            b'{"schema_version":"9.9.9","status":"completed","entries":[]}',
            ImageAcquisitionManifestVersionException,
        ),
    ),
)
async def test_reader_rejects_invalid_encoding_json_schema_and_version(
    tmp_path,
    content,
    expected,
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    _replace_manifest_bytes(tmp_path, repository, content)
    _, context = command_context(input_ids=())
    with pytest.raises(expected):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
async def test_reader_rejects_unstored_entry_as_incomplete(tmp_path) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    target = _manifest_target(tmp_path, repository)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["entries"][0]["status"] = "generating"
    content = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    _replace_manifest_bytes(tmp_path, repository, content)
    _, context = command_context(input_ids=())
    with pytest.raises(ImageAcquisitionManifestIncompleteException):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("missing_sidecar", SourceImageMissingException),
        ("corrupt_image", SourceImageCorruptException),
        ("checksum", SourceImageCorruptException),
        ("dimensions", SourceImageCorruptException),
        ("provenance", SourceImageProvenanceException),
    ),
)
async def test_reader_rejects_missing_corrupt_or_unprovenanced_source_image(
    tmp_path,
    mutation,
    expected,
) -> None:
    _, binary_store, repository = await durable_source(tmp_path)
    image = repository.image
    assert image is not None
    target = tmp_path.joinpath(*image.relative_path.split("/"))
    if mutation == "missing_sidecar":
        Path(f"{target}.asset.json").unlink()
    elif mutation == "corrupt_image":
        target.write_bytes(b"corrupt")
    elif mutation == "checksum":
        repository.image = image.model_copy(update={"sha256": "f" * 64})
    elif mutation == "dimensions":
        repository.image = image.model_copy(update={"width": image.width + 1})
    else:
        repository.image = image.model_copy(
            update={
                "metadata": {
                    **image.metadata,
                    "source_visual_asset_plan_sha256": "f" * 64,
                }
            }
        )
    _, context = command_context(input_ids=())
    with pytest.raises(expected):
        await _reader(tmp_path, binary_store, repository).read_for_video_clip_generation(
            context=context
        )
