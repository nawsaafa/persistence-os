"""Phase 2.1c — filesystem CAS for blobs (Design §6)."""
import hashlib
from pathlib import Path

import pytest

from persistence.http.blob_store import BlobStore


@pytest.fixture
def store(tmp_path: Path) -> BlobStore:
    return BlobStore(root=tmp_path)


def test_put_returns_sha256_hash_and_size(store: BlobStore):
    content = b"hello world"
    expected_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    h, size, duplicate = store.put(content)
    assert h == expected_hash
    assert size == len(content)
    assert duplicate is False


def test_put_idempotent_same_content(store: BlobStore):
    content = b"hello"
    h1, _, dup1 = store.put(content)
    h2, _, dup2 = store.put(content)
    assert h1 == h2
    assert dup1 is False
    assert dup2 is True


def test_put_creates_sharded_path(store: BlobStore):
    content = b"x"
    expected_hex = hashlib.sha256(content).hexdigest()
    store.put(content)
    sharded = store.root / expected_hex[:2] / expected_hex[2:]
    assert sharded.exists()


def test_get_returns_bytes_for_known_hash(store: BlobStore):
    content = b"some bytes"
    h, _, _ = store.put(content)
    got = store.get(h)
    assert got == content


def test_get_returns_none_for_absent_hash(store: BlobStore):
    assert store.get("sha256:" + "0" * 64) is None


def test_get_raises_on_malformed_hash(store: BlobStore):
    with pytest.raises(ValueError):
        store.get("not-a-sha256")
