"""Explainability engine for decision justification."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from backend.src.infrastructure.config.settings import settings
from backend.src.production.explainability.domain.explanation import (
    DecisionExplanation,
    ExplainabilityReport,
    ExplanationFactor,
)


class ExplainabilityEngine:
    """Generates human-readable explanations for Director AI decisions."""

    def __init__(self) -> None:
        self.explanations: dict[str, DecisionExplanation] = {}

    def explain_clip_selection(
        self,
        project_id: UUID,
        clip_id: str,
        clip_data: dict[str, Any],
        attention_features: dict[str, Any],
        narrative_features: dict[str, Any],
        video_understanding: dict[str, Any],
    ) -> DecisionExplanation:
        """Generate a full explanation for why a clip was selected."""
        factors = []
        reasoning_chain = []

        # Factor 1: Attention
        att_score = clip_data.get("confidence", {}).get("factors", {}).get("attention_score", 0)
        factors.append(
            ExplanationFactor(
                factor_name="Attention Peak",
                factor_type="attention",
                weight=0.35,
                score=att_score,
                description=f"This moment scored {att_score:.2f} on attention, indicating high viewer engagement",
                evidence=[
                    {
                        "type": "attention_peak",
                        "timestamp": clip_data.get("timestamp", 0),
                        "score": att_score,
                    }
                ],
            )
        )
        reasoning_chain.append(
            f"Detected attention peak at {clip_data.get('timestamp', 0):.1f}s with score {att_score:.2f}"
        )

        # Factor 2: Narrative
        in_climax = clip_data.get("confidence", {}).get("factors", {}).get("in_climax_zone", 0)
        factors.append(
            ExplanationFactor(
                factor_name="Narrative Position",
                factor_type="narrative",
                weight=0.30,
                score=in_climax,
                description="Located within the narrative climax zone, making it structurally significant",
                evidence=[
                    {
                        "type": "climax_proximity",
                        "in_climax_zone": in_climax > 0.5,
                        "score": in_climax,
                    }
                ],
            )
        )
        reasoning_chain.append(
            f"Narrative analysis: {'Inside' if in_climax > 0.5 else 'Outside'} climax zone (score: {in_climax:.2f})"
        )

        # Factor 3: Scene density
        scene_density = clip_data.get("confidence", {}).get("factors", {}).get("scene_density", 0)
        factors.append(
            ExplanationFactor(
                factor_name="Visual Dynamism",
                factor_type="technical",
                weight=0.20,
                score=scene_density,
                description=f"Scene density of {scene_density:.2f} indicates {'high' if scene_density > 0.5 else 'moderate'} visual activity",
                evidence=[
                    {
                        "type": "scene_density",
                        "value": scene_density,
                    }
                ],
            )
        )
        reasoning_chain.append(f"Visual dynamism score: {scene_density:.2f}")

        # Factor 4: Semantic understanding (if available)
        genre = video_understanding.get("genre", "unknown")
        if genre != "unknown":
            factors.append(
                ExplanationFactor(
                    factor_name="Genre Context",
                    factor_type="semantic",
                    weight=0.10,
                    score=0.7,
                    description=f"Video classified as '{genre}', influencing clip selection priorities",
                    evidence=[
                        {
                            "type": "genre_classification",
                            "genre": genre,
                        }
                    ],
                )
            )
            reasoning_chain.append(f"Genre classification: {genre}")

        # Factor 5: Temporal position
        temporal_spread = (
            clip_data.get("confidence", {}).get("factors", {}).get("temporal_spread", 0)
        )
        factors.append(
            ExplanationFactor(
                factor_name="Temporal Position",
                factor_type="technical",
                weight=0.15,
                score=temporal_spread,
                description=f"Temporal position score of {temporal_spread:.2f} (higher = better placement)",
                evidence=[
                    {
                        "type": "temporal_spread",
                        "value": temporal_spread,
                    }
                ],
            )
        )
        reasoning_chain.append(f"Temporal positioning: {temporal_spread:.2f}")

        # Overall confidence
        composite = clip_data.get("confidence", {}).get("composite", 0.5)

        # Build summary
        summary_parts = [
            f"Selected because of high attention ({att_score:.0%})",
        ]
        if in_climax > 0.5:
            summary_parts.append("it's in the climax zone")
        if scene_density > 0.5:
            summary_parts.append("high visual dynamism")
        summary = " and ".join(summary_parts)

        explanation = DecisionExplanation(
            explanation_id=uuid4(),
            project_id=project_id,
            clip_id=clip_id,
            decision_type="clip_selection",
            timestamp=clip_data.get("timestamp", 0),
            overall_confidence=composite,
            factors=factors,
            reasoning_chain=reasoning_chain,
            alternatives_considered=clip_data.get("alternatives", []),
            summary=summary,
        )

        self.explanations[clip_id] = explanation
        return explanation

    def generate_project_report(
        self,
        project_id: UUID,
        clip_explanations: list[DecisionExplanation],
    ) -> ExplainabilityReport:
        """Generate a complete explainability report for a project."""
        return ExplainabilityReport(
            project_id=project_id,
            clip_explanations=clip_explanations,
            pipeline_decisions=[],
            narrative_justification="Based on attention peaks and narrative structure",
            attention_highlights=[],
        )

    def export_explanation_html(self, explanation: DecisionExplanation, output_dir: Path) -> Path:
        """Export a single explanation as HTML."""
        path = output_dir / f"explanation_{explanation.clip_id}.html"
        path.parent.mkdir(parents=True, exist_ok=True)

        factors_html = "\n".join(
            [
                f"""
            <div class="factor">
                <div class="factor-header">
                    <span class="factor-name">{f.factor_name}</span>
                    <span class="factor-score">{f.score:.2f}</span>
                </div>
                <div class="factor-bar">
                    <div class="factor-fill" style="width: {f.score * 100}%"></div>
                </div>
                <p class="factor-desc">{f.description}</p>
                <div class="factor-meta">Weight: {f.weight:.0%} | Type: {f.factor_type}</div>
            </div>
            """
                for f in explanation.factors
            ]
        )

        reasoning_html = "\n".join([f"<li>{step}</li>" for step in explanation.reasoning_chain])

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Explanation: Clip {explanation.clip_id}</title>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #0a0a0a; color: #fff; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #667eea; }}
        .summary {{ background: #1a1a2e; padding: 20px; border-radius: 12px; margin: 20px 0; }}
        .confidence {{ font-size: 2rem; color: #667eea; }}
        .factor {{ background: #111; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #667eea; }}
        .factor-header {{ display: flex; justify-content: space-between; align-items: center; }}
        .factor-name {{ font-weight: bold; }}
        .factor-score {{ font-size: 1.2rem; color: #667eea; }}
        .factor-bar {{ height: 6px; background: #222; border-radius: 3px; margin: 8px 0; }}
        .factor-fill {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); border-radius: 3px; }}
        .factor-desc {{ color: #aaa; margin: 8px 0; }}
        .factor-meta {{ font-size: 0.8rem; color: #666; }}
        .reasoning {{ background: #111; padding: 20px; border-radius: 8px; }}
        .reasoning li {{ margin: 8px 0; color: #ccc; }}
    </style>
</head>
<body>
    <h1>Decision Explanation</h1>
    <p>Clip: <code>{explanation.clip_id}</code> | Type: {explanation.decision_type}</p>

    <div class="summary">
        <div class="confidence">{explanation.overall_confidence:.0%}</div>
        <p>Overall Confidence</p>
        <p>{explanation.summary}</p>
    </div>

    <h2>Contributing Factors</h2>
    {factors_html}

    <h2>Reasoning Chain</h2>
    <div class="reasoning">
        <ol>{reasoning_html}</ol>
    </div>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def persist(self, project_id: UUID) -> Path:
        """Save all explanations for a project."""
        path = settings.PROJECTS_DIR / str(project_id) / "explanations.json"
        data = {
            "project_id": str(project_id),
            "explanations": [asdict(e) for e in self.explanations.values()],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return path
