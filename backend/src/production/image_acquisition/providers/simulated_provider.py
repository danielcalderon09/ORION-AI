"""Deterministic offline raster image adapter."""

import hashlib
from io import BytesIO
from time import monotonic

from PIL import Image, ImageDraw

from backend.src.production.image_acquisition.ports import (
    GeneratedImagePayload,
    ImageAcquisitionProviderRequest,
    ImageAcquisitionProviderResponse,
)

_FORMAT = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}
_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class SimulatedImageAcquisitionProvider:
    """Generate recognizable test fixtures, never creative production content."""

    async def generate_image(
        self,
        request: ImageAcquisitionProviderRequest,
    ) -> ImageAcquisitionProviderResponse:
        started = monotonic()
        asset = request.visual_asset
        digest = hashlib.sha256(asset.asset_id.encode("utf-8")).digest()
        color = (digest[0], digest[1], digest[2])
        image = Image.new("RGB", (asset.width, asset.height), color=color)
        draw = ImageDraw.Draw(image)
        margin = max(2, min(asset.width, asset.height) // 20)
        inverse = tuple(255 - component for component in color)
        draw.rectangle(
            (
                margin,
                margin,
                max(margin + 1, asset.width - margin - 1),
                max(margin + 1, asset.height - margin - 1),
            ),
            outline=inverse,
            width=max(1, margin // 3),
        )
        draw.text(
            (margin * 2, margin * 2),
            asset.asset_id[-12:],
            fill=inverse,
        )
        stream = BytesIO()
        output_format = request.configuration.output_format
        image.save(stream, format=_FORMAT[output_format])
        content = stream.getvalue()
        return ImageAcquisitionProviderResponse(
            images=(
                GeneratedImagePayload(
                    content=content,
                    mime_type=_MIME[output_format],
                    index=0,
                    provider_metadata={"deterministic": True, "simulated": True},
                ),
            ),
            provider="orion-simulated",
            requested_model="simulated-image-v1",
            reported_model="simulated-image-v1",
            latency_ms=max(0.0, (monotonic() - started) * 1000),
            finish_reason="completed",
            metadata={"deterministic": True, "simulated": True},
        )

    async def close(self) -> None:
        return None
