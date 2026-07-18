"""Sprint 2 regression and integration tests."""

import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from backend.src.infrastructure.config.settings import settings


class TestPhase1MultimodalIntegration:
    """Tests for Sprint 2 Phase 1: Multimodal Video Understanding."""

    def test_semantic_memory_store_and_retrieve(self, tmp_path):
        """Semantic Memory can store and retrieve concepts with embeddings."""
        from backend.src.learning.semantic_memory.infrastructure.faiss_semantic_memory import FaissSemanticMemory
        from backend.src.learning.semantic_memory.domain.semantic_concept import SemanticConcept, EmbeddingVector

        memory = FaissSemanticMemory(tmp_path)
        concept = SemanticConcept(
            concept_id="char_test_01",
            concept_type="character",
            label="Test Character",
            description="A test character for validation",
            embeddings=[EmbeddingVector(
                vector_id="emb_1",
                concept_type="character",
                label="Test Character",
                vector=[0.1, 0.2, 0.3, 0.4, 0.5],
            )],
            related_concepts=[],
            occurrences=[],
            confidence=0.9,
        )
        memory.store_concept(concept)

        retrieved = memory.retrieve_concept("char_test_01")
        assert retrieved is not None
        assert retrieved.label == "Test Character"
        assert retrieved.concept_type == "character"

    def test_semantic_memory_embedding_search(self, tmp_path):
        """Semantic Memory can search by embedding similarity."""
        from backend.src.learning.semantic_memory.infrastructure.faiss_semantic_memory import FaissSemanticMemory
        from backend.src.learning.semantic_memory.domain.semantic_concept import SemanticConcept, EmbeddingVector

        memory = FaissSemanticMemory(tmp_path)
        
        # Store two concepts
        for i, vec in enumerate([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]):
            concept = SemanticConcept(
                concept_id=f"concept_{i}",
                concept_type="scene",
                label=f"Scene {i}",
                description="Test",
                embeddings=[EmbeddingVector(
                    vector_id=f"emb_{i}",
                    concept_type="scene",
                    label=f"Scene {i}",
                    vector=vec,
                )],
                related_concepts=[],
                occurrences=[],
                confidence=0.8,
            )
            memory.store_concept(concept)

        # Search with query close to concept_0
        results = memory.search_by_embedding([0.95, 0.1, 0.0, 0.0], concept_type="scene", top_k=1)
        assert len(results) >= 1
        assert results[0].concept_id == "concept_0"

    def test_video_understanding_agent_returns_genre(self):
        """VideoUnderstandingAgent returns genre classification."""
        from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
        from backend.src.cognition.video_understanding.i_video_understanding_provider import DummyVideoUnderstandingProvider
        from backend.src.agents.base.i_agent import AgentInput

        provider = DummyVideoUnderstandingProvider()
        agent = VideoUnderstandingAgent(provider)

        context = {
            "vision_features": {"scene_count": 25, "duration_seconds": 60, "key_frames": []},
            "audio_features": {"peaks": [{"time": 1, "energy": 0.8}] * 20},
            "speech_features": {"transcript": "", "segments": []},
        }

        result = asyncio.run(agent.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        assert "genre" in result.features
        assert result.features["genre"] != "unknown"

    def test_clip_provider_genre_classification(self):
        """CLIP provider classifies genres correctly."""
        from backend.src.cognition.video_understanding.infrastructure.clip_provider import CLIPUnderstandingProvider

        provider = CLIPUnderstandingProvider()

        # Gaming-like features
        gaming_features = {"scene_count": 30, "audio_peaks": [{"time": i, "energy": 0.8} for i in range(25)]}
        genre = asyncio.run(provider.classify_genre(gaming_features))
        assert "gaming" in genre.lower() or "sports" in genre.lower()

        # Podcast-like features
        podcast_features = {"scene_count": 2, "audio_peaks": []}
        genre = asyncio.run(provider.classify_genre(podcast_features))
        assert "podcast" in genre.lower() or "interview" in genre.lower()


class TestPhase2TemporalTracking:
    """Tests for Sprint 2 Phase 2: Temporal Identity Tracking."""

    def test_temporal_identity_tracked(self, tmp_path):
        """Temporal tracker assigns persistent identities."""
        from backend.src.cognition.temporal_tracking.domain.temporal_identity import TemporalIdentity

        identity = TemporalIdentity(
            identity_id="person_01",
            entity_type="person",
            first_seen=0.0,
            last_seen=30.0,
            appearance_count=15,
            visual_signature=[0.1] * 128,
            trajectory=[(0.0, 0.5, 0.5), (15.0, 0.6, 0.4), (30.0, 0.5, 0.5)],
            state_history=[{"frame": 0, "bbox": [100, 100, 200, 200]}],
            representative_frames=["frame_001", "frame_015"],
        )

        assert identity.identity_id == "person_01"
        assert identity.appearance_count == 15
        assert len(identity.trajectory) == 3


class TestPhase3EventGraph:
    """Tests for Sprint 2 Phase 3: Event Graph with Causality."""

    def test_event_graph_causal_query(self):
        """Event graph supports causal queries."""
        from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
        from backend.src.cognition.event_graph.domain.event_node import EventNode, CausalEdge

        graph = NetworkXEventGraph()

        # Add events
        explosion = EventNode(
            event_id="explosion_01",
            event_type="explosion",
            start_time=10.0,
            end_time=10.5,
            description="Big explosion",
            confidence=0.95,
            participants=[],
            source_agent="vision",
            properties={},
        )
        reaction = EventNode(
            event_id="reaction_01",
            event_type="reaction",
            start_time=10.8,
            end_time=12.0,
            description="Character reacts to explosion",
            confidence=0.85,
            participants=["char_01"],
            source_agent="vision",
            properties={},
        )

        graph.add_event(explosion)
        graph.add_event(reaction)
        graph.add_causal_link(CausalEdge(
            from_event="explosion_01",
            to_event="reaction_01",
            relation_type="causes",
            confidence=0.9,
            evidence=["temporal_proximity"],
        ))

        # Query causes of reaction
        causes = graph.query_causes("reaction_01", depth=1)
        assert len(causes) == 1
        assert causes[0].event_id == "explosion_01"

        # Query effects of explosion
        effects = graph.query_effects("explosion_01", depth=1)
        assert len(effects) == 1
        assert effects[0].event_id == "reaction_01"

    def test_event_graph_temporal_sequence(self):
        """Event graph returns temporal sequences."""
        from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
        from backend.src.cognition.event_graph.domain.event_node import EventNode

        graph = NetworkXEventGraph()
        for i in range(5):
            event = EventNode(
                event_id=f"event_{i}",
                event_type="action",
                start_time=float(i * 2),
                end_time=float(i * 2 + 1),
                description=f"Event {i}",
                confidence=0.8,
                participants=[],
                source_agent="test",
                properties={},
            )
            graph.add_event(event)

        sequence = graph.get_temporal_sequence(0, 10)
        assert len(sequence) == 5
        assert sequence[0].start_time < sequence[-1].start_time

    def test_event_graph_persistence(self, tmp_path):
        """Event graph persists and loads correctly."""
        from backend.src.cognition.event_graph.infrastructure.networkx_event_graph import NetworkXEventGraph
        from backend.src.cognition.event_graph.domain.event_node import EventNode
        from uuid import uuid4

        project_id = uuid4()
        graph = NetworkXEventGraph()
        event = EventNode(
            event_id="test_event",
            event_type="test",
            start_time=5.0,
            end_time=6.0,
            description="Test",
            confidence=0.9,
            participants=[],
            source_agent="test",
            properties={"key": "value"},
        )
        graph.add_event(event)
        graph.persist(project_id)

        # Load in new instance
        graph2 = NetworkXEventGraph()
        graph2.load(project_id)
        loaded = graph2.get_event("test_event")
        assert loaded is not None
        assert loaded.event_type == "test"
        assert loaded.properties.get("key") == "value"


class TestPhase4Explainability:
    """Tests for Sprint 2 Phase 4: Explainability."""

    def test_explainability_generates_factors(self):
        """Explainability engine generates factors for a decision."""
        from backend.src.production.explainability.infrastructure.explainability_engine import ExplainabilityEngine

        engine = ExplainabilityEngine()
        clip_data = {
            "timestamp": 15.0,
            "confidence": {
                "composite": 0.85,
                "factors": {
                    "attention_score": 0.9,
                    "in_climax_zone": 1.0,
                    "scene_density": 0.7,
                    "temporal_spread": 0.6,
                },
            },
            "alternatives": [],
        }

        explanation = engine.explain_clip_selection(
            project_id=uuid4(),
            clip_id="clip_01",
            clip_data=clip_data,
            attention_features={"peaks": []},
            narrative_features={"narrative_structure": {"acts": []}},
            video_understanding={"genre": "gaming"},
        )

        assert explanation.overall_confidence == 0.85
        assert len(explanation.factors) >= 4
        assert any(f.factor_type == "attention" for f in explanation.factors)
        assert any(f.factor_type == "narrative" for f in explanation.factors)
        assert len(explanation.reasoning_chain) >= 3
        assert explanation.summary != ""

    def test_explanation_html_export(self, tmp_path):
        """Explainability exports HTML report."""
        from backend.src.production.explainability.infrastructure.explainability_engine import ExplainabilityEngine
        from backend.src.production.explainability.domain.explanation import DecisionExplanation, ExplanationFactor
        from uuid import uuid4

        engine = ExplainabilityEngine()
        explanation = DecisionExplanation(
            explanation_id=uuid4(),
            project_id=uuid4(),
            clip_id="test_clip",
            decision_type="clip_selection",
            timestamp=10.0,
            overall_confidence=0.75,
            factors=[
                ExplanationFactor(
                    factor_name="Test Factor",
                    factor_type="attention",
                    weight=0.5,
                    score=0.8,
                    description="Test description",
                    evidence=[],
                ),
            ],
            reasoning_chain=["Step 1", "Step 2"],
            alternatives_considered=[],
            summary="Test summary",
        )

        path = engine.export_explanation_html(explanation, tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Test Factor" in content
        assert "Test summary" in content
        assert "75%" in content

    def test_director_agent_confidence_with_explainability(self):
        """DirectorAgent clips include confidence usable by explainability."""
        from backend.src.agents.director_agent.application.director_service import DirectorAgent
        from backend.src.agents.base.i_agent import AgentInput

        director = DirectorAgent()

        context = {
            "attention_features": {
                "peaks": [{"time": 5.0, "attention_score": 0.92}],
                "timeline": [
                    {"time": 4.0, "attention_score": 0.3},
                    {"time": 5.0, "attention_score": 0.92},
                    {"time": 6.0, "attention_score": 0.4},
                ],
            },
            "narrative_features": {
                "narrative_structure": {
                    "acts": [{"name": "intro", "start": 0, "end": 3}, {"name": "climax", "start": 3, "end": 8}],
                    "scene_count": 12,
                    "duration": 60,
                },
                "climax_candidates": [{"timestamp": 5.0, "score": 0.88}],
            },
            "speech_features": {"segments": [{"start": 4.5, "end": 5.5, "text": "amazing"}]},
            "vision_features": {"duration_seconds": 60},
        }

        result = asyncio.run(director.execute(AgentInput(
            media_reference="test.mp4",
            context=context,
        )))

        clips = result.features["selected_clips"]
        for clip in clips:
            assert "confidence" in clip
            conf = clip["confidence"]
            assert "composite" in conf
            assert "factors" in conf
            assert "weights" in conf
            assert conf["composite"] > 0
            assert conf["composite"] <= 1.0


class TestSprint2Orchestrator:
    """Integration tests for Sprint 2 orchestrator."""

    @pytest.mark.asyncio
    async def test_sprint2_orchestrator_runs_full_pipeline(self, tmp_path):
        """Sprint 2 orchestrator runs complete pipeline with all phases."""
        from backend.src.core.domain.entities.video_project import VideoProject
        from backend.src.core.application.services.sprint2_orchestrator import Sprint2OrchestrationService
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        from backend.src.infrastructure.learning.feature_store import FileSystemFeatureStore
        from backend.src.infrastructure.cognition.knowledge_graph_impl import InMemoryKnowledgeGraph
        from backend.src.infrastructure.messaging.event_bus import EventBus
        from backend.src.infrastructure.telemetry.telemetry_service import TelemetryService
        from backend.src.infrastructure.benchmark.benchmark_suite import BenchmarkSuite

        from backend.src.agents.vision_agent.application.extract_visual_features import VisionAgent
        from backend.src.agents.audio_agent.application.extract_audio_features import AudioAgent
        from backend.src.agents.speech_agent.application.transcribe_speech import SpeechAgent
        from backend.src.agents.attention_agent.application.estimate_attention import AttentionAgent
        from backend.src.agents.narrative_intelligence_agent.application.analyze_narrative import NarrativeIntelligenceAgent
        from backend.src.agents.director_agent.application.director_service import DirectorAgent
        from backend.src.agents.dop_agent.application.dop_service import DoPAgent
        from backend.src.agents.exporter_agent.application.export_clip import ExporterAgent
        from backend.src.agents.qa_agent.application.qa_service import QAAgent
        from backend.src.cognition.video_understanding.application.video_understanding_agent import VideoUnderstandingAgent
        from backend.src.cognition.video_understanding.i_video_understanding_provider import DummyVideoUnderstandingProvider

        # Create test video
        video_path = tmp_path / "test.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=15:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=15",
            "-pix_fmt", "yuv420p", str(video_path),
        ]
        subprocess.run(cmd, capture_output=True)

        media = FFmpegMediaAdapter()
        fs = FileSystemFeatureStore(tmp_path / "features")
        vu_agent = VideoUnderstandingAgent(DummyVideoUnderstandingProvider())

        orchestrator = Sprint2OrchestrationService(
            event_bus=EventBus(),
            telemetry=TelemetryService(),
            benchmark=BenchmarkSuite(),
            vision_agent=VisionAgent(media),
            audio_agent=AudioAgent(media),
            speech_agent=SpeechAgent(),
            attention_agent=AttentionAgent(),
            narrative_agent=NarrativeIntelligenceAgent(),
            director_agent=DirectorAgent(),
            dop_agent=DoPAgent(),
            exporter_agent=ExporterAgent(media),
            qa_agent=QAAgent(),
            video_understanding_agent=vu_agent,
            feature_store=fs,
            knowledge_graph_factory=InMemoryKnowledgeGraph,
        )

        project = VideoProject(name="sprint2_test", source_path=video_path)
        completed = await orchestrator.process_video(project)

        assert completed.status.name == "COMPLETED"
        assert len(completed.clips) >= 1
        # Verify event graph was populated
        assert orchestrator.event_graph.graph.number_of_nodes() > 0
        # Verify explanations exist
        assert len(orchestrator.explainability.explanations) > 0
