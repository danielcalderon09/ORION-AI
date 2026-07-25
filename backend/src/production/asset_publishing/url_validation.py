"""Strict public HTTPS URL validation without performing network I/O."""

from __future__ import annotations

import ipaddress
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from backend.src.production.asset_publishing.exceptions import (
    AssetPublicationUrlError,
)

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def validate_public_https_url(value: str) -> str:
    if not value or any(ord(character) < 32 for character in value):
        raise AssetPublicationUrlError("published URL is empty or contains controls")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise AssetPublicationUrlError("published URL must be public HTTPS")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise AssetPublicationUrlError("published URL cannot use localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise AssetPublicationUrlError("published URL IP is not public")
    else:
        labels = host.split(".")
        if (
            len(labels) < 2
            or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
            or labels[-1].isdigit()
        ):
            raise AssetPublicationUrlError("published URL host is invalid")
    decoded_path = parsed.path
    for _ in range(2):
        decoded_path = unquote(decoded_path)
    if (
        "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
        or ".." in PurePosixPath(decoded_path).parts
    ):
        raise AssetPublicationUrlError("published URL path is unsafe")
    return value


def public_url_hash(value: str) -> str:
    import hashlib

    validate_public_https_url(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
