from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import TenantContext
from harness_engine.persistence.content_store import (
    ContentConflictError,
    ContentIntegrityError,
    ContentNotFoundError,
    LocalContentStore,
)

TENANT = TenantContext('platform', 'tenant', 'user')
OTHER = TenantContext('platform', 'other', 'user')
KEY = b'k' * 32


def test_roundtrip_is_encrypted_and_tenant_scoped(tmp_path):
    store = LocalContentStore(tmp_path, KEY)
    store.put(TENANT, 'item', b'secret bytes', 'text/plain')
    assert store.get(TENANT, 'item') == b'secret bytes'
    assert all(b'secret bytes' not in p.read_bytes() and b'text/plain' not in p.read_bytes()
               for p in tmp_path.rglob('*') if p.is_file())
    with pytest.raises(ContentNotFoundError):
        store.get(OTHER, 'item')


def test_tampering_and_wrong_key_fail(tmp_path):
    store = LocalContentStore(tmp_path, KEY)
    store.put(TENANT, 'item', b'secret', 'text/plain')
    with pytest.raises(ContentIntegrityError):
        LocalContentStore(tmp_path, b'x' * 32).get(TENANT, 'item')
    path = next(p for p in tmp_path.rglob('*') if p.is_file())
    data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
    with pytest.raises(ContentIntegrityError):
        store.get(TENANT, 'item')


def test_immutable_put_and_idempotent_delete(tmp_path):
    store = LocalContentStore(tmp_path, KEY)
    store.put(TENANT, 'item', b'a', 'text/plain')
    store.put(TENANT, 'item', b'a', 'text/plain')
    for data, content_type in [(b'b', 'text/plain'), (b'a', 'other')]:
        with pytest.raises(ContentConflictError):
            store.put(TENANT, 'item', data, content_type)
    assert store.get(TENANT, 'item') == b'a'
    store.delete(TENANT, 'item'); store.delete(TENANT, 'item')
    with pytest.raises(ContentNotFoundError):
        store.get(TENANT, 'item')


def test_pagination_age_and_cursor(tmp_path):
    store = LocalContentStore(tmp_path, KEY, page_size=1)
    for ref in ('b', 'a'):
        store.put(TENANT, ref, ref.encode(), 'text/plain')
    store.put(OTHER, 'c', b'c', 'text/plain')
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    first = store.list_candidates(TENANT, cutoff)
    assert [item.ref for item in first.items] == ['a']
    assert first.items[0].byte_length == 1
    second = store.list_candidates(TENANT, cutoff, first.next_cursor)
    assert [item.ref for item in second.items] == ['b']
    assert second.next_cursor is None
    assert not store.list_candidates(TENANT, datetime.now(UTC) - timedelta(days=1)).items
    with pytest.raises(ValueError):
        store.list_candidates(TENANT, cutoff, '../bad')


@pytest.mark.parametrize('ref', ['', ' ', '.', '..', '../x', 'a/b', 'a\\b', '/tmp/x', 'C:bad'])
def test_rejects_unsafe_refs(tmp_path, ref):
    with pytest.raises(ValueError):
        LocalContentStore(tmp_path, KEY).put(TENANT, ref, b'a', 'text/plain')

@pytest.mark.parametrize('ref', ['NUL', 'nul.txt', 'CON', 'aux.log', 'PRN', 'COM1', 'com9.txt', 'LPT1', 'lpt9.log'])
def test_rejects_windows_device_names(tmp_path, ref):
    with pytest.raises(ValueError):
        LocalContentStore(tmp_path, KEY).put(TENANT, ref, b'a', 'text/plain')


def test_refs_preserve_case_distinctions(tmp_path):
    store = LocalContentStore(tmp_path, KEY)
    store.put(TENANT, 'Item', b'upper', 'text/plain')
    store.put(TENANT, 'item', b'lower', 'text/plain')
    assert store.get(TENANT, 'Item') == b'upper'
    assert store.get(TENANT, 'item') == b'lower'
    assert [item.ref for item in store.list_candidates(TENANT, datetime.now(UTC) + timedelta(seconds=1)).items] == ['Item', 'item']


def test_rejects_windows_junction_in_root_and_tenant_path(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction regression')
    target = tmp_path / 'target'
    target.mkdir()
    junction = tmp_path / 'junction'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.skip(f'junction creation unavailable: {result.stderr or result.stdout}')
    from harness_engine.persistence.content_store import ContentStorageError
    with pytest.raises(ContentStorageError):
        LocalContentStore(junction, KEY)
    store = LocalContentStore(tmp_path / 'root', KEY)
    tenant_path = store._path(TENANT, 'item').parent.parent
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(tenant_path), str(target)], capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.skip(f'tenant junction creation unavailable: {result.stderr or result.stdout}')
    with pytest.raises(ContentStorageError):
        store.put(TENANT, 'item', b'a', 'text/plain')
    with pytest.raises(ContentStorageError):
        store.get(TENANT, 'item')


def test_failed_publication_cleans_up_temporary_file(tmp_path, monkeypatch):
    from harness_engine.persistence.content_store import ContentStorageError

    store = LocalContentStore(tmp_path, KEY)

    def fail_link(source, destination):
        raise PermissionError('publication denied')

    monkeypatch.setattr('harness_engine.persistence.content_store.os.link', fail_link)
    with pytest.raises(ContentStorageError):
        store.put(TENANT, 'item', b'secret', 'text/plain')
    assert not any(path.is_file() for path in tmp_path.rglob('*'))
    with pytest.raises(ContentNotFoundError):
        store.get(TENANT, 'item')

