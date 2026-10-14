"""Core logic: find photos in a target folder that have no same-named
counterpart in a reference folder.

"Same name" means the file name without its extension, compared
case-insensitively (as Windows does) and after Unicode NFC normalization
(so Korean names written by macOS, which uses NFD, still match).
This module has no GUI or Windows dependencies so it can be unit-tested
anywhere.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

# Format key -> file extensions (lower case, with dot) that belong to it.
# Sony/Canon/Nikon/Fujifilm cameras write HEIF stills as ".HIF".
FORMATS: dict[str, tuple[str, ...]] = {
    "TIFF": (".tif", ".tiff"),
    "ARW": (".arw",),
    "JPG": (".jpg", ".jpeg"),
    "HEIF": (".heif", ".heic", ".hif"),
}

FORMAT_LABELS: dict[str, str] = {
    "TIFF": "TIFF (.tif .tiff)",
    "ARW": "ARW (.arw)",
    "JPG": "JPG (.jpg .jpeg)",
    "HEIF": "HEIF (.heif .heic .hif)",
}

ProgressCallback = Callable[[int], None]

_PROGRESS_EVERY = 250


@dataclass(frozen=True)
class PhotoFile:
    path: str
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.path)[1].lower()


@dataclass
class ScanResult:
    reference_count: int
    target_count: int
    orphans: list[PhotoFile]
    errors: list[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return self.target_count - len(self.orphans)


def extensions_for(formats: Iterable[str]) -> frozenset[str]:
    return frozenset(ext for fmt in formats for ext in FORMATS[fmt])


def name_key(path: str) -> str:
    """Key used to decide whether two photos are "the same picture"."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return unicodedata.normalize("NFC", stem).casefold()


def iter_photo_files(
    root: str,
    extensions: frozenset[str],
    recursive: bool,
    errors: list[str],
) -> Iterator[os.DirEntry]:
    """Yield directory entries under ``root`` whose extension is in
    ``extensions``. Unreadable folders are recorded in ``errors`` and skipped.
    Symbolic links and junctions to folders are not followed, so a recursive
    scan cannot loop or wander outside ``root``.
    """
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name.casefold())
        except OSError as exc:
            errors.append(f"{directory}: {exc.strerror or exc}")
            continue

        subdirs = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    is_junction = getattr(entry, "is_junction", lambda: False)()
                    if recursive and not is_junction:
                        subdirs.append(entry.path)
                elif (
                    os.path.splitext(entry.name)[1].lower() in extensions
                    and entry.is_file()
                ):
                    yield entry
            except OSError as exc:
                errors.append(f"{entry.path}: {exc.strerror or exc}")
        # Reversed so that folders are visited in alphabetical order.
        stack.extend(reversed(subdirs))


def _clean_dir(path: str, label: str) -> str:
    path = path.strip().strip('"').strip()
    if not path:
        raise ValueError(f"{label} 폴더를 선택하세요.")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError(f"{label} 폴더를 찾을 수 없습니다:\n{path}")
    return path


def find_orphans(
    reference_dir: str,
    reference_format: str,
    reference_recursive: bool,
    target_dir: str,
    target_formats: Iterable[str],
    target_recursive: bool,
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Return the target photos whose name does not appear among the
    reference photos.

    Raises ``ValueError`` (with a user-facing Korean message) for invalid
    input, including the case where the reference folder contains no photo
    of the chosen format: proceeding would mark every target photo as an
    orphan, which is almost certainly a wrong folder/format choice.
    """
    reference_dir = _clean_dir(reference_dir, "레퍼런스")
    target_dir = _clean_dir(target_dir, "대상")
    if reference_format not in FORMATS:
        raise ValueError("레퍼런스 확장자를 선택하세요.")
    target_formats = [f for f in FORMATS if f in set(target_formats)]
    if not target_formats:
        raise ValueError("대상 확장자를 하나 이상 선택하세요.")

    errors: list[str] = []
    seen = 0

    def tick() -> None:
        nonlocal seen
        seen += 1
        if progress is not None and seen % _PROGRESS_EVERY == 0:
            progress(seen)

    reference_keys: set[str] = set()
    for entry in iter_photo_files(
        reference_dir, extensions_for([reference_format]), reference_recursive, errors
    ):
        reference_keys.add(name_key(entry.name))
        tick()
    reference_count = seen

    if reference_count == 0:
        raise ValueError(
            f"레퍼런스 폴더에서 {FORMAT_LABELS[reference_format]} 파일을 하나도 찾지 못했습니다.\n"
            "이대로 진행하면 대상 사진 전부가 삭제 대상이 되므로 중단합니다.\n"
            "폴더, 확장자, 하위 폴더 포함 옵션을 확인하세요."
        )

    target_count = 0
    orphans: list[PhotoFile] = []
    for entry in iter_photo_files(
        target_dir, extensions_for(target_formats), target_recursive, errors
    ):
        target_count += 1
        tick()
        if name_key(entry.name) in reference_keys:
            continue
        try:
            st = entry.stat()
        except OSError as exc:
            errors.append(f"{entry.path}: {exc.strerror or exc}")
            continue
        orphans.append(PhotoFile(entry.path, st.st_size, st.st_mtime))

    if progress is not None:
        progress(seen)
    return ScanResult(reference_count, target_count, orphans, errors)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
