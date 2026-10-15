import hashlib
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class FileStorage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...


class LocalFileStorage:
    """Для продакшена заменить на S3-совместимое хранилище (MinIO) с той же сигнатурой."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def put(self, key: str, data: bytes) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


def get_storage() -> FileStorage:
    return LocalFileStorage(get_settings().storage_dir)
