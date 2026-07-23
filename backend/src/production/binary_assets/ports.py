"""Provider-neutral ports for durable binary production assets."""

from typing import Protocol

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


class BinaryAssetStore(BinaryAssetWriter, BinaryAssetReader, Protocol):
    """Combined durable storage boundary used by future acquisition handlers."""


class RegisteredBinaryAssetReader(Protocol):
    def list_registered_binary_assets(self) -> tuple[ProductionBinaryAsset, ...]: ...
