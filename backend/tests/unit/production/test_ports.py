"""Import-safety tests for production application ports."""

import inspect
from typing import Protocol


def test_all_ports_are_importable_without_io() -> None:
    from backend.src.production.application import ports

    expected = {
        "PlannerPort",
        "ScriptWriterPort",
        "ScenePlannerPort",
        "AssetProviderPort",
        "NarrationProviderPort",
        "SubtitleProviderPort",
        "MusicProviderPort",
        "EditorPort",
        "ClipHandoffPort",
        "ArtifactStorePort",
        "ProductionJobRepositoryPort",
    }

    assert expected.issubset(set(ports.__all__))
    for name in expected:
        port = getattr(ports, name)
        assert inspect.isclass(port)
        assert Protocol in port.__mro__


def test_editor_port_exposes_required_operations() -> None:
    from backend.src.production.application.ports import EditorPort

    assert {
        "validate_environment",
        "create_project",
        "build_timeline",
        "render",
        "inspect_render",
    }.issubset(EditorPort.__dict__)
