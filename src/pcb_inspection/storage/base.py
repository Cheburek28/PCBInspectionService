from __future__ import annotations

import re
from typing import Protocol

_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-./]*$")


class BlobNotFound(KeyError):
    pass


def validate_key(key: str) -> str:
    """Keys are relative POSIX paths without '..' so that no backend can escape its root."""
    if not _KEY.match(key) or ".." in key.split("/"):
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes:
        """Raises BlobNotFound."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Deleting a missing key is not an error."""
        ...

    def ping(self) -> None:
        """Raises if the backend is unusable."""
        ...
