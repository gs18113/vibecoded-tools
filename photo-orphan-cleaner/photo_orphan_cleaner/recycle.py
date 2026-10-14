"""Move files to the Windows Recycle Bin (never delete permanently).

Uses the Win32 ``SHFileOperationW`` call with ``FOF_ALLOWUNDO``, which is
what Explorer's "Delete" does. ``FOF_WANTNUKEWARNING`` makes Windows ask
before it would *permanently* destroy a file instead of recycling it (for
example when the Recycle Bin is disabled or the file is too large for it);
answering "No" leaves the file untouched and is reported here as a
cancellation.

Drives without a Recycle Bin at all (USB sticks, SD cards, network shares)
are detected up front with :func:`unsupported_volumes` so the GUI can refuse
to touch them.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

# SHFileOperationW needs a classic path: MAX_PATH (260) including the
# terminating NUL; the "\\?\" long-path prefix is not supported.
_MAX_PATH_CHARS = 259


class RecycleError(Exception):
    def __init__(self, message: str, cancelled: bool = False):
        super().__init__(message)
        self.cancelled = cancelled


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004  # no progress dialog
    FOF_NOCONFIRMATION = 0x0010  # no "are you sure?" (we asked already)
    FOF_ALLOWUNDO = 0x0040  # recycle instead of delete
    FOF_WANTNUKEWARNING = 0x4000  # but DO ask if it would be permanent

    DRIVE_FIXED = 3

    class SHFILEOPSTRUCTW(ctypes.Structure):
        # shellapi.h packs this struct to 1 byte on 32-bit Windows only.
        if ctypes.sizeof(ctypes.c_void_p) == 4:
            _pack_ = 1
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    _shell32.SHFileOperationW.restype = ctypes.c_int

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetVolumePathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    _kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT

    def volume_root(path: str) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        if _kernel32.GetVolumePathNameW(os.path.abspath(path), buf, len(buf)):
            return buf.value
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        return drive + "\\"

    def _volume_has_recycle_bin(root: str) -> bool:
        # Windows only provides a Recycle Bin on fixed (internal or
        # fixed-reporting external) disks.
        return _kernel32.GetDriveTypeW(root) == DRIVE_FIXED

    def send_to_recycle_bin(path: str, hwnd: int | None = None) -> None:
        path = os.path.abspath(path)
        if not os.path.lexists(path):
            raise RecycleError("파일이 이미 없습니다.")
        if len(path) > _MAX_PATH_CHARS:
            raise RecycleError("경로가 너무 깁니다(260자 이상). 폴더 경로를 줄여 주세요.")

        # pFrom is a list of NUL-terminated strings ending with an extra
        # NUL. The buffer is zero-filled, so two NULs follow the path.
        buf = ctypes.create_unicode_buffer(path, len(path) + 2)
        op = SHFILEOPSTRUCTW()
        op.hwnd = hwnd
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
        op.pTo = None
        op.fFlags = (
            FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_WANTNUKEWARNING
        )

        rc = _shell32.SHFileOperationW(ctypes.byref(op))
        if op.fAnyOperationsAborted:
            raise RecycleError("사용자가 취소했습니다.", cancelled=True)
        if rc != 0:
            raise RecycleError(f"Windows 오류 코드 {rc} (0x{rc:X})")
        if os.path.lexists(path):
            raise RecycleError("휴지통으로 이동되지 않았습니다.")

else:

    def volume_root(path: str) -> str:
        return os.path.abspath(os.sep)

    def _volume_has_recycle_bin(root: str) -> bool:
        return False

    def send_to_recycle_bin(path: str, hwnd: int | None = None) -> None:
        raise RecycleError("휴지통 이동은 Windows에서만 지원됩니다.")


def unsupported_volumes(paths: Iterable[str]) -> list[str]:
    """Return the volume roots (e.g. ``"E:\\"``) among ``paths`` that have no
    Recycle Bin. Files there could only be deleted permanently."""
    roots: dict[str, None] = {}
    seen_dirs: set[str] = set()
    for p in paths:
        directory = os.path.dirname(os.path.abspath(p))
        if directory not in seen_dirs:
            seen_dirs.add(directory)
            roots.setdefault(volume_root(directory), None)
    return sorted(r for r in roots if not _volume_has_recycle_bin(r))
