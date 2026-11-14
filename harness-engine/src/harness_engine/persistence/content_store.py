"""Encrypted, tenant-scoped storage for conversation content."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NewType, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..core import TenantContext
from .errors import PersistenceError, ResourceNotFoundError
from .models import Page

ContentRef = NewType('ContentRef', str)


@dataclass(frozen=True, slots=True)
class ContentObjectInfo:
    ref: ContentRef
    created_at: datetime
    byte_length: int
    content_type: str


class ContentStorageError(PersistenceError):
    """A content storage operation failed."""


class ContentIntegrityError(ContentStorageError):
    """Content could not be authenticated or validated."""


class ContentNotFoundError(ResourceNotFoundError):
    """The content object does not exist in this tenant."""


class ContentConflictError(ContentStorageError):
    """An immutable reference already holds different content."""


class ContentStore(Protocol):
    def put(self, tenant: TenantContext, ref: ContentRef, data: bytes, content_type: str) -> None: ...
    def get(self, tenant: TenantContext, ref: ContentRef) -> bytes: ...
    def delete(self, tenant: TenantContext, ref: ContentRef) -> None: ...
    def list_candidates(self, tenant: TenantContext, older_than: datetime, cursor: str | None = None) -> Page[ContentObjectInfo]: ...


_REF = re.compile(r'^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}$')
_VERSION = b'content-store-v1'


def _validate_ref(ref: str) -> None:
    if not isinstance(ref, str) or not _REF.fullmatch(ref) or '..' in ref or ref.endswith('.') or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', ref.split('.')[0]):
        raise ValueError('invalid content reference')


def _scope(tenant: TenantContext) -> bytes:
    return json.dumps([tenant.platform_id, tenant.tenant_id], ensure_ascii=True, separators=(',', ':')).encode()


class LocalContentStore:
    def __init__(self, root: str | Path, master_key: bytes, *, page_size: int = 100):
        if not isinstance(master_key, bytes) or len(master_key) != 32:
            raise ValueError('master_key must be 32 bytes')
        if not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise ValueError('page_size must be between 1 and 1000')
        self.root = Path(root).absolute()
        self._key = master_key
        self.page_size = page_size
        try:
            self._check_path(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            self._check_path(self.root)
        except OSError as exc:
            raise ContentStorageError('content storage unavailable') from exc

    @staticmethod
    def _check_path(path: Path) -> None:
        for part in (path, *path.parents):
            try:
                attributes = part.lstat().st_file_attributes if os.name == 'nt' else 0
                if part.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ContentStorageError('reparse points and symlinks are not permitted in content storage')
            except FileNotFoundError:
                pass

    def _path(self, tenant: TenantContext, ref: str) -> Path:
        _validate_ref(ref)
        directory = self.root / hashlib.sha256(_scope(tenant)).hexdigest()
        encoded = ref.encode('utf-8').hex()
        path = directory / ('r' + encoded[:200]) / ('r' + encoded[200:])
        self._check_path(path)
        return path

    def _cipher(self, tenant: TenantContext) -> AESGCM:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=_VERSION + b'\0' + _scope(tenant)).derive(self._key)
        return AESGCM(key)

    def _aad(self, tenant: TenantContext, ref: str) -> bytes:
        return json.dumps([_VERSION.decode(), tenant.platform_id, tenant.tenant_id, ref],
                          ensure_ascii=True, separators=(',', ':')).encode()

    def _read(self, tenant: TenantContext, ref: str) -> tuple[ContentObjectInfo, bytes]:
        path = self._path(tenant, ref)
        try:
            blob = path.read_bytes()
        except FileNotFoundError as exc:
            raise ContentNotFoundError('content object not found') from exc
        except OSError as exc:
            raise ContentStorageError('content storage unavailable') from exc
        try:
            if not blob.startswith(_VERSION + b'\0'):
                raise ValueError('invalid format')
            payload = blob[len(_VERSION) + 1:]
            plain = self._cipher(tenant).decrypt(payload[:12], payload[12:], self._aad(tenant, ref))
            obj = json.loads(plain)
            data = base64.b64decode(obj['data'], validate=True)
            created = datetime.fromisoformat(obj['created_at'])
            if (created.tzinfo is None or not isinstance(obj['content_type'], str)
                    or obj['byte_length'] != len(data)
                    or obj['sha256'] != hashlib.sha256(data).hexdigest()):
                raise ValueError('invalid metadata')
            return ContentObjectInfo(ContentRef(ref), created, len(data), obj['content_type']), data
        except (InvalidTag, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ContentIntegrityError('content authentication or validation failed') from exc

    def put(self, tenant: TenantContext, ref: ContentRef, data: bytes, content_type: str) -> None:
        path = self._path(tenant, ref)
        if not isinstance(data, bytes) or not isinstance(content_type, str) or not content_type.strip():
            raise ValueError('data must be bytes and content_type must not be blank')
        obj = {'created_at': datetime.now(UTC).isoformat(), 'byte_length': len(data),
               'content_type': content_type, 'sha256': hashlib.sha256(data).hexdigest(),
               'data': base64.b64encode(data).decode('ascii')}
        nonce = os.urandom(12)
        blob = _VERSION + b'\0' + nonce + self._cipher(tenant).encrypt(
            nonce, json.dumps(obj, separators=(',', ':')).encode(), self._aad(tenant, ref))
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._check_path(path)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tmp-', delete=False) as stream:
                temp = Path(stream.name)
                stream.write(blob)
            try:
                os.link(temp, path)
            except FileExistsError:
                info, existing = self._read(tenant, ref)
                if existing != data or info.content_type != content_type:
                    raise ContentConflictError('content reference already exists')
        except OSError as exc:
            raise ContentStorageError('content storage unavailable') from exc
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError as exc:
                    raise ContentStorageError('content storage unavailable') from exc

    def get(self, tenant: TenantContext, ref: ContentRef) -> bytes:
        return self._read(tenant, ref)[1]

    def delete(self, tenant: TenantContext, ref: ContentRef) -> None:
        path = self._path(tenant, ref)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ContentStorageError('content storage unavailable') from exc

    def list_candidates(self, tenant: TenantContext, older_than: datetime, cursor: str | None = None) -> Page[ContentObjectInfo]:
        if older_than.tzinfo is None:
            raise ValueError('older_than must be timezone-aware')
        if cursor is not None:
            _validate_ref(cursor)
        directory = self._path(tenant, 'placeholder').parent.parent
        try:
            if not directory.exists():
                return Page(())
            names = []
            for shard in directory.iterdir():
                self._check_path(shard)
                if not shard.is_dir() or not shard.name.startswith('r'):
                    raise ContentStorageError('invalid content storage entry')
                for item in shard.iterdir():
                    if item.name.startswith('.tmp-'):
                        continue
                    self._check_path(item)
                    if not item.name.startswith('r'):
                        raise ContentStorageError('invalid content storage entry')
                    encoded = shard.name[1:] + item.name[1:]
                    try:
                        name = bytes.fromhex(encoded).decode('utf-8')
                        _validate_ref(name)
                        if self._path(tenant, name) != item:
                            raise ValueError('noncanonical content path')
                    except (ValueError, UnicodeError) as exc:
                        raise ContentStorageError('invalid content storage entry') from exc
                    names.append(name)
            matches = []
            for name in sorted(names):
                if cursor is not None and name <= cursor:
                    continue
                info, _ = self._read(tenant, name)
                if info.created_at < older_than:
                    matches.append(info)
                    if len(matches) > self.page_size:
                        break
            return Page(tuple(matches[:self.page_size]), matches[self.page_size - 1].ref if len(matches) > self.page_size else None)
        except OSError as exc:
            raise ContentStorageError('content storage unavailable') from exc
