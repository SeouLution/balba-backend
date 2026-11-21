import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.shared.types import InvalidRequest

CHUNK_BYTES = 1024 * 1024
SIGNATURE_BYTES = 4096


@dataclass(frozen=True)
class FileRef:
    key: str
    sha256: str
    media_type: str
    byte_size: int
    original_name: str | None


class FileStore:
    """FILE_STORAGE_ROOT 아래 <storeId>/<종류>/<uuid>에 저장한다. 클라이언트 파일명은 위치에 쓰지 않는다."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("file key escapes the storage root")
        return path

    async def put(
        self,
        store_id: str,
        kind: str,
        extension: str,
        upload,
        max_bytes: int,
        media_type: str,
        has_valid_signature: Callable[[bytes], bool],
    ) -> FileRef:
        key = f"{store_id}/{kind}/{uuid.uuid4().hex}.{extension}"
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        byte_size = 0
        head = b""
        try:
            with path.open("wb") as out:
                while chunk := await upload.read(CHUNK_BYTES):
                    byte_size += len(chunk)
                    if byte_size > max_bytes:
                        raise InvalidRequest("file exceeds the size limit")
                    if len(head) < SIGNATURE_BYTES:
                        head += chunk[: SIGNATURE_BYTES - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
            if byte_size == 0 or not has_valid_signature(head):
                raise InvalidRequest("file is empty or has an unexpected signature")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return FileRef(key, digest.hexdigest(), media_type, byte_size, getattr(upload, "filename", None))

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)
