"""The same contract for every BlobStorage backend."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from pcb_inspection.settings import Settings
from pcb_inspection.storage import BlobNotFound, BlobStorage, LocalStorage, build_storage
from pcb_inspection.storage.base import validate_key
from pcb_inspection.storage.s3 import S3Storage


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[BlobStorage]:
    if request.param == "local":
        yield LocalStorage(tmp_path / "blobs")
        return
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test")
        yield S3Storage("test", access_key="x", secret_key="x")


def test_put_get_exists_delete(storage: BlobStorage) -> None:
    storage.put("a/b/c.jpg", b"data", "image/jpeg")
    assert storage.exists("a/b/c.jpg")
    assert storage.get("a/b/c.jpg") == b"data"
    storage.put("a/b/c.jpg", b"new", "image/jpeg")
    assert storage.get("a/b/c.jpg") == b"new"
    storage.delete("a/b/c.jpg")
    assert not storage.exists("a/b/c.jpg")
    storage.delete("a/b/c.jpg")  # idempotent


def test_missing_key(storage: BlobStorage) -> None:
    with pytest.raises(BlobNotFound):
        storage.get("nope.jpg")
    assert not storage.exists("nope.jpg")


def test_ping(storage: BlobStorage) -> None:
    storage.ping()


@pytest.mark.parametrize("key", ["../etc/passwd", "/abs", "a/../../b", "", "a b"])
def test_invalid_keys(key: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key"):
        validate_key(key)


def test_local_storage_leaves_no_temp_files(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    s.put("x/y.bin", b"1", "application/octet-stream")
    assert [p.name for p in (tmp_path / "x").iterdir()] == ["y.bin"]


def test_build_storage(tmp_path: Path) -> None:
    local = build_storage(Settings(_env_file=None, storage_path=str(tmp_path)))  # type: ignore[call-arg]
    assert isinstance(local, LocalStorage)
    with mock_aws():
        s3 = build_storage(Settings(_env_file=None, storage_backend="s3", s3_bucket="b"))  # type: ignore[call-arg]
    assert isinstance(s3, S3Storage)
