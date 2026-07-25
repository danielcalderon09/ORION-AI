"""URL policy and immutable contract coverage."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationUrlError,
)
from backend.src.production.asset_publishing.models import (
    AssetPublicationReceipt,
    PublishableAsset,
    PublishedAsset,
    PublishedAssetManifest,
    PublishedAssetManifestStatus,
    PublishedAssetMetadata,
    PublishedAssetStatus,
    PublishedAssetSummary,
    replace_published_asset,
    summarize_published_assets,
    validate_published_manifest_transition,
)
from backend.src.production.asset_publishing.url_validation import (
    public_url_hash,
    validate_public_https_url,
)
from backend.tests.unit.production.asset_publishing.conftest import (
    CONTENT,
    JOB_ID,
    NOW,
    SHA256,
    SOURCE_SHA256,
    published_asset,
    receipt,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://assets.example.test/file.png",
        "https://cdn.example.com/a/b.mp4",
        "https://example.com:443/content",
        "https://8.8.8.8/asset",
        "https://media.example.org/%61sset",
    ],
)
def test_accepts_public_https_urls(url: str) -> None:
    assert validate_public_https_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.com/a",
        "file:///tmp/a",
        "ftp://example.com/a",
        "javascript:alert(1)",
        "data:video/mp4;base64,AAAA",
        "https://localhost/a",
        "https://api.localhost/a",
        "https://127.0.0.1/a",
        "https://127.1/a",
        "https://0.0.0.0/a",
        "https://10.0.0.1/a",
        "https://172.16.0.1/a",
        "https://192.168.1.1/a",
        "https://169.254.1.1/a",
        "https://[::1]/a",
        "https://[fc00::1]/a",
        "https://user@example.com/a",
        "https://user:pass@example.com/a",
        "https://example.com/a#secret",
        "https://example.com/a/../private",
        "https://example.com/a/%252e%252e/private",
        r"https://example.com/a\..\private",
        "https://singlelabel/a",
        "https://-bad.example/a",
        "https://bad-.example/a",
        "https://example.123/a",
        "https://exa mple.com/a",
        "https://example.com/\x00",
        "https://example.com/%00",
    ],
)
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(AssetPublicationUrlError):
        validate_public_https_url(url)


def test_url_hash_is_stable_and_validated() -> None:
    value = "https://assets.example.test/a"
    assert public_url_hash(value) == public_url_hash(value)
    with pytest.raises(AssetPublicationUrlError):
        public_url_hash("http://localhost/a")


@pytest.mark.parametrize(
    ("status", "valid"),
    [
        (PublishedAssetStatus.NOT_PUBLISHED, True),
        (PublishedAssetStatus.PUBLISHING, True),
        (PublishedAssetStatus.PUBLISHED, True),
        (PublishedAssetStatus.FAILED, True),
        (PublishedAssetStatus.EXPIRED, True),
        (PublishedAssetStatus.REMOVED, True),
    ],
)
def test_closed_status_contract(status: PublishedAssetStatus, valid: bool) -> None:
    assert published_asset(status=status).status is status
    assert valid


@pytest.mark.parametrize(
    "content_type",
    ["image/png", "image/jpeg", "image/webp", "video/mp4"],
)
def test_supported_content_types(content_type: str) -> None:
    model = published_asset().model_dump(mode="python")
    model["content_type"] = content_type
    assert PublishedAsset.model_validate(model).content_type == content_type


def test_models_are_frozen_and_forbid_extra() -> None:
    asset = published_asset()
    with pytest.raises(ValidationError):
        asset.status = PublishedAssetStatus.EXPIRED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PublishedAsset.model_validate({**asset.model_dump(), "secret": "no"})


def test_public_url_is_excluded_from_repr_but_present_in_manifest_contract() -> None:
    asset = published_asset()
    assert "https://" not in repr(asset)
    assert asset.model_dump()["public_url"] is not None


def test_publishable_bytes_are_excluded_from_repr_and_serialization() -> None:
    source = PublishableAsset(
        asset_id="publish-a",
        binary_asset_id="image-a",
        source_hash=SHA256,
        content_type="image/png",
        extension="png",
        size_bytes=len(CONTENT),
        content=CONTENT,
        source_manifest_kind="image_acquisition",
        source_manifest_sha256=SOURCE_SHA256,
    )
    assert repr(CONTENT) not in repr(source)
    assert "content" not in source.model_dump()


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "secret"},
        {"authorization": "rejected-value"},
        {"token": "secret"},
        {"path": "C:\\private\\asset.png"},
        {"url": "https://private.example.test/a"},
    ],
)
def test_unsafe_metadata_is_rejected(metadata: dict[str, str]) -> None:
    payload = published_asset().metadata.model_dump(mode="python")
    payload["attributes"] = metadata
    with pytest.raises((ValidationError, ValueError)):
        PublishedAssetMetadata.model_validate(payload)


def test_url_hash_must_match_public_url() -> None:
    payload = published_asset().model_dump(mode="python")
    payload["url_hash"] = "f" * 64
    with pytest.raises(ValidationError):
        PublishedAsset.model_validate(payload)


def test_expired_and_removed_assets_do_not_persist_url() -> None:
    for status in (PublishedAssetStatus.EXPIRED, PublishedAssetStatus.REMOVED):
        assert published_asset(status=status).public_url is None


def test_receipt_requires_aware_ordered_timestamps() -> None:
    payload = receipt().model_dump(mode="python")
    payload["expires_at"] = NOW - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        AssetPublicationReceipt.model_validate(payload)


def test_summary_requires_exact_total() -> None:
    with pytest.raises(ValidationError):
        PublishedAssetSummary(
            total=2,
            not_published=1,
            publishing=0,
            published=0,
            failed=0,
            expired=0,
            removed=0,
        )


def test_manifest(source_manifest) -> None:
    entry = published_asset()
    manifest = PublishedAssetManifest(
        job_id=JOB_ID,
        attempt_number=1,
        publisher="filesystem",
        source_manifests=(source_manifest,),
        entries=(entry,),
        summary=summarize_published_assets((entry,)),
        status=PublishedAssetManifestStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )
    assert manifest.summary.published == 1


def test_completed_manifest_rejects_incomplete_entry(source_manifest) -> None:
    entry = published_asset(status=PublishedAssetStatus.NOT_PUBLISHED)
    with pytest.raises(ValidationError):
        PublishedAssetManifest(
            job_id=JOB_ID,
            attempt_number=1,
            publisher="filesystem",
            source_manifests=(source_manifest,),
            entries=(entry,),
            summary=summarize_published_assets((entry,)),
            status=PublishedAssetManifestStatus.COMPLETED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_entries_must_be_unique_and_sorted(source_manifest) -> None:
    entry = published_asset()
    with pytest.raises(ValidationError):
        PublishedAssetManifest(
            job_id=JOB_ID,
            attempt_number=1,
            publisher="filesystem",
            source_manifests=(source_manifest,),
            entries=(entry, entry),
            summary=summarize_published_assets((entry, entry)),
            status=PublishedAssetManifestStatus.COMPLETED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_transition_validation_accepts_publish_and_rejects_reversal(
    source_manifest,
) -> None:
    initial_entry = published_asset(status=PublishedAssetStatus.NOT_PUBLISHED)
    initial = PublishedAssetManifest(
        job_id=JOB_ID,
        attempt_number=1,
        publisher="filesystem",
        source_manifests=(source_manifest,),
        entries=(initial_entry,),
        summary=summarize_published_assets((initial_entry,)),
        status=PublishedAssetManifestStatus.IN_PROGRESS,
        created_at=NOW,
        updated_at=NOW,
    )
    publishing_entry = published_asset(status=PublishedAssetStatus.PUBLISHING)
    publishing = replace_published_asset(
        initial,
        publishing_entry,
        updated_at=NOW,
    )
    validate_published_manifest_transition(initial, publishing)
    with pytest.raises(ValueError):
        validate_published_manifest_transition(publishing, initial)
