"""Debug exporter for timeline visualization."""

import json
from pathlib import Path
from uuid import UUID

from backend.src.infrastructure.config.settings import settings


class DebugTimelineExporter:
    """Exports debug timeline data for visualization."""

    def __init__(self, project_id: UUID):
        self.project_id = project_id
        self.workspace = settings.PROJECTS_DIR / str(project_id)
        self.debug_dir = self.workspace / "debug"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def export_timeline_json(self, timeline_data: dict) -> Path:
        """Export timeline as JSON for external visualization."""
        path = self.debug_dir / "timeline.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(timeline_data, f, indent=2, ensure_ascii=False)
        return path

    def export_timeline_html(self, timeline_data: dict) -> Path:
        """Generate a self-contained HTML viewer."""
        path = self.debug_dir / "timeline_viewer.html"

        points = timeline_data.get("points", [])
        clips = timeline_data.get("clips", [])
        summary = timeline_data.get("summary", {})

        # Prepare chart data
        times = [p["time"] for p in points]
        attention = [p["attention_score"] for p in points]
        audio = [p["audio_energy"] for p in points]
        scene = [p["scene_change"] for p in points]

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Orion AI - Debug Timeline</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #0a0a0a; color: #fff; margin: 0; padding: 20px; }}
        h1 {{ color: #667eea; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 20px; }}
        .stat {{ background: #1a1a1a; padding: 15px; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 1.5rem; font-weight: bold; color: #667eea; }}
        .stat-label {{ font-size: 0.85rem; color: #888; margin-top: 5px; }}
        .chart-container {{ background: #111; padding: 20px; border-radius: 12px; margin-bottom: 20px; height: 400px; }}
        .clips {{ background: #111; padding: 20px; border-radius: 12px; }}
        .clip {{ display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid #222; }}
        .clip-bar {{ height: 8px; background: linear-gradient(90deg, #667eea, #764ba2); border-radius: 4px; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Orion AI - Debug Timeline Viewer</h1>
        <p>Project: {self.project_id}</p>

        <div class="summary">
            <div class="stat">
                <div class="stat-value">{summary.get("peak_count", 0)}</div>
                <div class="stat-label">Attention Peaks</div>
            </div>
            <div class="stat">
                <div class="stat-value">{summary.get("valley_count", 0)}</div>
                <div class="stat-label">Attention Valleys</div>
            </div>
            <div class="stat">
                <div class="stat-value">{summary.get("scene_count", 0)}</div>
                <div class="stat-label">Scene Changes</div>
            </div>
            <div class="stat">
                <div class="stat-value">{summary.get("speech_segments", 0)}</div>
                <div class="stat-label">Speech Segments</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(clips)}</div>
                <div class="stat-label">Selected Clips</div>
            </div>
        </div>

        <div class="chart-container">
            <canvas id="timelineChart"></canvas>
        </div>

        <div class="clips">
            <h2>Selected Clips</h2>
            {self._render_clips_html(clips)}
        </div>
    </div>

    <script>
        const ctx = document.getElementById('timelineChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {json.dumps(times)},
                datasets: [
                    {{
                        label: 'Attention Score',
                        data: {json.dumps(attention)},
                        borderColor: '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                    }},
                    {{
                        label: 'Audio Energy',
                        data: {json.dumps(audio)},
                        borderColor: '#f59e0b',
                        backgroundColor: 'transparent',
                        borderDash: [5, 5],
                        tension: 0.4,
                        pointRadius: 0,
                    }},
                    {{
                        label: 'Scene Change',
                        data: {json.dumps(scene)},
                        borderColor: '#10b981',
                        backgroundColor: 'transparent',
                        stepped: true,
                        pointRadius: 0,
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                plugins: {{
                    legend: {{ labels: {{ color: '#fff' }} }},
                    tooltip: {{ mode: 'index', intersect: false }}
                }},
                scales: {{
                    x: {{ grid: {{ color: '#222' }}, ticks: {{ color: '#888' }} }},
                    y: {{ grid: {{ color: '#222' }}, ticks: {{ color: '#888' }}, min: 0, max: 1.2 }}
                }}
            }}
        }});
    </script>
</body>
</html>'''

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def _render_clips_html(self, clips: list) -> str:
        if not clips:
            return "<p>No clips selected</p>"
        rows = []
        for c in clips:
            conf = c.get("confidence_composite", 0)
            width = int(conf * 100)
            rows.append(f'''
            <div class="clip">
                <div>
                    <strong>Clip: {c["start"]:.1f}s - {c["end"]:.1f}s</strong>
                    <div style="font-size: 0.85rem; color: #888;">
                        Source: {c["source"]} | Score: {c["score"]:.2f}
                    </div>
                    <div style="margin-top: 5px;">
                        Confidence: {conf:.2f}
                        <div class="clip-bar" style="width: {width}%;"></div>
                    </div>
                </div>
            </div>
            ''')
        return "\n".join(rows)
