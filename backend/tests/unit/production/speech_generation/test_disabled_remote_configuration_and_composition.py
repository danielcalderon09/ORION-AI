from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition.container import build_production_container
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)
from backend.src.production.speech_generation.capability_sources import (
    DisabledRemoteSpeechCapabilitySource,
    StaticSimulatedSpeechCapabilitySource,
)
from backend.src.production.speech_generation.configuration import (
    SpeechRemotePreparationConfiguration,
)
from backend.src.production.speech_generation.exceptions import (
    RemoteSpeechProviderDisabledError,
    SpeechCapabilityConfigurationError,
)
from backend.src.production.speech_generation.models import (
    SUPPORTED_SPEECH_MANIFEST_VERSIONS,
)
from backend.src.production.speech_generation.providers.disabled_remote_provider import (
    DisabledRemoteSpeechProvider,
)
from backend.src.production.speech_generation.remote_ports import (
    RemoteSpeechGenerationRequest,
)
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    speech_configuration,
)


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "ORION_DATABASE_URL": sqlite_url_from_path(tmp_path / "remote-speech.db"),
        "ORION_PRODUCTION_WORKER_ENABLED": False,
    }
    values.update(updates)
    return Settings(**values)


@pytest.mark.asyncio
async def test_static_simulated_capabilities_are_offline_and_close_idempotently() -> None:
    source = StaticSimulatedSpeechCapabilitySource(
        configuration=speech_configuration(),
        clock=lambda: NOW,
    )
    snapshot = await source.discover_capabilities()
    assert snapshot.capabilities.provider == "orion-simulated-speech"
    assert snapshot.metadata == {"network": False, "simulated": True}
    await source.close()
    await source.close()
    with pytest.raises(SpeechCapabilityConfigurationError, match="closed"):
        await source.discover_capabilities()


@pytest.mark.asyncio
async def test_disabled_capability_source_and_provider_always_fail_before_io() -> None:
    capability_source = DisabledRemoteSpeechCapabilitySource()
    with pytest.raises(SpeechCapabilityConfigurationError, match="disabled"):
        await capability_source.discover_capabilities()
    await capability_source.close()
    await capability_source.close()

    provider = DisabledRemoteSpeechProvider()
    request = RemoteSpeechGenerationRequest(
        request_fingerprint="a" * 64,
        narration_text="Texto que nunca sale del proceso.",
    )
    for operation in (
        provider.generate_synchronously(request),
        provider.submit(request),
        provider.poll(remote_job_id="fake-job"),
        provider.download(remote_job_id="fake-job"),
    ):
        with pytest.raises(RemoteSpeechProviderDisabledError, match="disabled"):
            await operation
    await provider.close()
    await provider.close()


def test_remote_preparation_configuration_is_disabled_and_non_billable() -> None:
    configuration = SpeechRemotePreparationConfiguration()
    assert configuration.remote_provider == "disabled"
    assert configuration.allow_billable_requests is False
    assert configuration.remote_model is None
    assert configuration.remote_voice is None
    assert configuration.maximum_estimated_cost is None

    for values in (
        {"allow_billable_requests": True},
        {"remote_model": "fake-model"},
        {"remote_voice": "fake-voice"},
        {"maximum_estimated_cost": Decimal("1")},
    ):
        with pytest.raises(ValidationError):
            SpeechRemotePreparationConfiguration(**values)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS", True),
        ("ORION_SPEECH_GENERATION_REMOTE_PROVIDER", "future"),
        ("ORION_SPEECH_GENERATION_REMOTE_MODEL", "fake-model"),
        ("ORION_SPEECH_GENERATION_REMOTE_VOICE", "fake-voice"),
        ("ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST", "1.00"),
        ("ORION_SPEECH_GENERATION_REMOTE_MAX_POLL_ATTEMPTS", 0),
        ("ORION_SPEECH_GENERATION_REMOTE_POLL_INTERVAL_SECONDS", 0),
        ("ORION_SPEECH_GENERATION_REMOTE_JOB_MAX_BYTES", 4_000_001),
    ],
)
def test_settings_reject_every_remote_enablement_or_unsafe_limit(
    tmp_path: Path,
    name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, **{name: value})


@pytest.mark.asyncio
async def test_normal_container_keeps_simulated_handler_and_closes_capability_source(
    tmp_path: Path,
) -> None:
    container = build_production_container(_settings(tmp_path))
    assert container.speech_generation_provider.name == "orion-simulated-speech"
    assert container.speech_remote_configuration.remote_provider == "disabled"
    assert container.speech_remote_configuration.allow_billable_requests is False
    assert container.remote_speech_job_store is not None
    assert container.remote_speech_reconciler is not None
    assert all(
        not isinstance(resource, DisabledRemoteSpeechProvider)
        for resource in container.async_resources
    )
    snapshot = await container.speech_capability_source.discover_capabilities()
    assert snapshot.capabilities.provider == "orion-simulated-speech"
    await container.aclose()
    with pytest.raises(SpeechCapabilityConfigurationError, match="closed"):
        await container.speech_capability_source.discover_capabilities()


def test_no_remote_speech_key_or_url_setting_exists(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.ORION_SPEECH_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS is False
    assert not hasattr(settings, "ORION_SPEECH_GENERATION_API_KEY")
    assert not hasattr(settings, "ORION_SPEECH_GENERATION_PROVIDER_URL")


def test_phase_5g1_manifest_schema_version_is_unchanged() -> None:
    assert frozenset({"1.0.0"}) == SUPPORTED_SPEECH_MANIFEST_VERSIONS
