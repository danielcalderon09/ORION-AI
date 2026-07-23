"""Provider-neutral ports for durable binary production assets."""

from typing import Protocol
from uuid import UUID

from backend.src.production.binary_assets.models import (
    BinaryAssetWriteRequest,
    ProductionBinaryAsset,
    ProductionBinaryAssetReference,
    ReadProductionBinaryAsset,
)


class BinaryAssetWriter(Protocol):
    async def write(
        self,
        *,
        request: BinaryAssetWriteRequest,
        content: bytes,
    ) -> ProductionBinaryAsset: ...


class BinaryAssetReader(Protocol):
    async def read(
        self,
        *,
        reference: ProductionBinaryAssetReference,
    ) -> ReadProductionBinaryAsset: ...

    async def resolve(
        self,
        *,
        job_id: UUID,
        asset_id: str,
        extension: str,
    ) -> ReadProductionBinaryAsset: ...


class BinaryAssetStore(BinaryAssetWriter, BinaryAssetReader, Protocol):
    """Combined durable storage boundary used by future acquisition handlers."""


class RegisteredBinaryAssetReader(Protocol):
    def list_registered_binary_assets(self) -> tuple[ProductionBinaryAsset, ...]: ...
