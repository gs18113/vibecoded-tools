"""Tkinter GUI for Photo Orphan Cleaner."""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import __version__
from .core import FORMAT_LABELS, FORMATS, PhotoFile, ScanResult, find_orphans, format_size
from .recycle import RecycleError, send_to_recycle_bin, unsupported_volumes

APP_TITLE = "Photo Orphan Cleaner - 짝 없는 사진 정리"
DEFAULT_REFERENCE_FORMAT = "JPG"
MAX_LISTED_PROBLEMS = 15

INTRO_TEXT = (
    "레퍼런스 폴더(이미 정리를 끝낸 사진)에 같은 이름의 사진이 없는 대상 사진을 찾아 "
    "휴지통으로 옮깁니다. 이름은 확장자를 뺀 파일 이름으로 비교하며, 대소문자는 구분하지 않습니다. "
    "(예: 레퍼런스에 DSC01234.JPG가 있으면 대상의 DSC01234.ARW는 남깁니다.)"
)


def _enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _use_korean_font(root: tk.Tk) -> None:
    families = set(tkfont.families(root))
    for family in ("Malgun Gothic", "맑은 고딕"):
        if family in families:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                tkfont.nametofont(name, root).configure(family=family)
            return


class App(ttk.Frame):
    POLL_MS = 100

    COLUMNS = (
        ("name", "파일명", 260, "w"),
        ("type", "형식", 60, "center"),
        ("size", "크기", 90, "e"),
        ("mtime", "수정한 날짜", 140, "center"),
        ("folder", "위치 (대상 폴더 기준)", 300, "w"),
    )

    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=10)
        self.root = root
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.reference_dir = tk.StringVar()
        self.reference_format = tk.StringVar(value=DEFAULT_REFERENCE_FORMAT)
        self.reference_recursive = tk.BooleanVar(value=False)
        self.target_dir = tk.StringVar()
        self.target_formats = {fmt: tk.BooleanVar(value=True) for fmt in FORMATS}
        self.target_recursive = tk.BooleanVar(value=False)

        self._items: dict[str, PhotoFile] = {}
        self._result: ScanResult | None = None
        self._result_target_dir = ""
        self._scan_queue: queue.Queue = queue.Queue()
        self._controls: list[ttk.Widget] = []
        self._sort_column: str | None = None
        self._sort_reverse = False

        self._pending: collections.deque[str] = collections.deque()
        self._delete_total = 0
        self._deleted = 0
        self._failures: list[tuple[PhotoFile, str]] = []
        self._stop_requested = False
        self._cancelled_by_user = False

        self._build()
        self._update_buttons()

        watched = [
            self.reference_dir,
            self.reference_format,
            self.reference_recursive,
            self.target_dir,
            self.target_recursive,
            *self.target_formats.values(),
        ]
        for var in watched:
            var.trace_add("write", self._on_settings_changed)

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        intro = ttk.Label(self, text=INTRO_TEXT, wraplength=900, justify="left")
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.bind("<Configure>", lambda e: intro.configure(wraplength=max(300, e.width - 30)))

        ref = ttk.LabelFrame(self, text=" 1. 레퍼런스 (남길 사진의 기준) ", padding=8)
        ref.grid(row=1, column=0, sticky="ew")
        self._folder_row(ref, self.reference_dir, "레퍼런스 폴더 선택")
        fmt_row = ttk.Frame(ref)
        fmt_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(fmt_row, text="확장자:").pack(side="left", padx=(0, 6))
        for fmt in FORMATS:
            rb = ttk.Radiobutton(
                fmt_row, text=FORMAT_LABELS[fmt], value=fmt, variable=self.reference_format
            )
            rb.pack(side="left", padx=(0, 10))
            self._controls.append(rb)
        cb = ttk.Checkbutton(fmt_row, text="하위 폴더 포함", variable=self.reference_recursive)
        cb.pack(side="left", padx=(16, 0))
        self._controls.append(cb)

        tgt = ttk.LabelFrame(self, text=" 2. 대상 (정리할 사진) ", padding=8)
        tgt.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._folder_row(tgt, self.target_dir, "대상 폴더 선택")
        fmt_row = ttk.Frame(tgt)
        fmt_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(fmt_row, text="확장자:").pack(side="left", padx=(0, 6))
        for fmt, var in self.target_formats.items():
            c = ttk.Checkbutton(fmt_row, text=FORMAT_LABELS[fmt], variable=var)
            c.pack(side="left", padx=(0, 10))
            self._controls.append(c)
        cb = ttk.Checkbutton(fmt_row, text="하위 폴더 포함", variable=self.target_recursive)
        cb.pack(side="left", padx=(16, 0))
        self._controls.append(cb)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=8)
        self.scan_button = ttk.Button(actions, text="검색", command=self._start_scan)
        self.scan_button.pack(side="left")
        self._controls.append(self.scan_button)
        self.status = ttk.Label(actions, text="폴더와 확장자를 고른 뒤 [검색]을 누르세요.")
        self.status.pack(side="left", padx=10)

        res = ttk.LabelFrame(
            self, text=" 3. 짝 없는 사진 (휴지통으로 이동할 목록) ", padding=8
        )
        res.grid(row=4, column=0, sticky="nsew")
        res.columnconfigure(0, weight=1)
        res.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            res, columns=[c[0] for c in self.COLUMNS], show="headings", selectmode="extended"
        )
        for key, title, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("name", "folder"))
        ysb = ttk.Scrollbar(res, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(res, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Delete>", lambda e: self._exclude_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="파일 열기", command=self._open_selected)
        self.menu.add_command(label="탐색기에서 위치 보기", command=self._reveal_selected)
        self.menu.add_separator()
        self.menu.add_command(label="목록에서 제외 (삭제하지 않음)", command=self._exclude_selected)

        bottom = ttk.Frame(res)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        self.summary = ttk.Label(bottom, text="")
        self.summary.grid(row=0, column=0, sticky="w")
        self.exclude_button = ttk.Button(
            bottom, text="선택 항목 제외", command=self._exclude_selected
        )
        self.exclude_button.grid(row=0, column=1, padx=(8, 0))
        self.delete_button = ttk.Button(
            bottom, text="목록 전체를 휴지통으로 이동…", command=self._start_delete
        )
        self.delete_button.grid(row=0, column=2, padx=(8, 0))
        self.stop_button = ttk.Button(bottom, text="중지", command=self._request_stop)
        self.stop_button.grid(row=0, column=3, padx=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        hint = ttk.Label(
            res,
            text="더블클릭: 파일 열기 · 오른쪽 클릭: 메뉴 · Delete 키: 선택 항목을 목록에서 제외",
            foreground="gray40",
        )
        hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _folder_row(self, parent: ttk.Frame, var: tk.StringVar, dialog_title: str) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="폴더:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=0, column=1, sticky="ew")
        button = ttk.Button(
            parent, text="찾아보기…", command=lambda: self._browse(var, dialog_title)
        )
        button.grid(row=0, column=2, padx=(6, 0))
        self._controls.extend([entry, button])

    def _browse(self, var: tk.StringVar, title: str) -> None:
        current = var.get().strip().strip('"')
        chosen = filedialog.askdirectory(
            parent=self.root,
            title=title,
            mustexist=True,
            initialdir=current if os.path.isdir(current) else None,
        )
        if chosen:
            var.set(os.path.normpath(chosen))

    # --------------------------------------------------------------- state

    def _busy(self) -> bool:
        return bool(self._pending) or self.scan_button.instate(["disabled"])

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in self._controls:
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _update_buttons(self) -> None:
        deleting = bool(self._pending)
        has_items = bool(self.tree.get_children())
        has_selection = bool(self.tree.selection())
        idle = not deleting and self.scan_button.instate(["!disabled"])
        self.delete_button.state(["!disabled"] if idle and has_items else ["disabled"])
        self.exclude_button.state(["!disabled"] if idle and has_selection else ["disabled"])
        self.stop_button.state(["!disabled"] if deleting else ["disabled"])

    def _on_settings_changed(self, *_args) -> None:
        if self._result is not None:
            self._clear_results()
            self.status.configure(text="설정이 바뀌어 이전 검색 결과를 지웠습니다. 다시 [검색]을 누르세요.")

    def _clear_results(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._items.clear()
        self._result = None
        self.summary.configure(text="")
        self.progress.configure(value=0)
        self._update_buttons()

    def _update_summary(self) -> None:
        if self._result is None:
            self.summary.configure(text="")
            return
        r = self._result
        remaining = [self._items[i] for i in self.tree.get_children()]
        total = sum(f.size for f in remaining)
        excluded = len(r.orphans) - len(remaining) - self._deleted
        text = (
            f"레퍼런스 {r.reference_count}개 · 대상 {r.target_count}개 중 "
            f"짝 있음 {r.matched_count}개 / 짝 없음 {len(r.orphans)}개"
            f"  →  이동 예정 {len(remaining)}개 ({format_size(total)})"
        )
        if excluded > 0:
            text += f" · 제외 {excluded}개"
        if self._deleted:
            text += f" · 이동 완료 {self._deleted}개"
        self.summary.configure(text=text)

    # ---------------------------------------------------------------- scan

    def _start_scan(self) -> None:
        params = dict(
            reference_dir=self.reference_dir.get(),
            reference_format=self.reference_format.get(),
            reference_recursive=self.reference_recursive.get(),
            target_dir=self.target_dir.get(),
            target_formats=[f for f, v in self.target_formats.items() if v.get()],
            target_recursive=self.target_recursive.get(),
        )
        self._clear_results()
        self._deleted = 0
        self._set_controls_enabled(False)
        self._update_buttons()
        self.status.configure(text="검색 중…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        threading.Thread(target=self._scan_worker, args=(params,), daemon=True).start()
        self.after(self.POLL_MS, self._poll_scan)

    def _scan_worker(self, params: dict) -> None:
        def progress(n: int) -> None:
            self._scan_queue.put(("progress", n))

        try:
            result = find_orphans(**params, progress=progress)
        except ValueError as exc:
            self._scan_queue.put(("invalid", str(exc)))
        except Exception as exc:  # unexpected; show it instead of dying silently
            self._scan_queue.put(("error", f"{type(exc).__name__}: {exc}"))
        else:
            self._scan_queue.put(("done", (result, os.path.abspath(params["target_dir"].strip().strip('"')))))

    def _poll_scan(self) -> None:
        try:
            while True:
                kind, payload = self._scan_queue.get_nowait()
                if kind == "progress":
                    self.status.configure(text=f"검색 중… 파일 {payload}개 확인")
                    continue
                self._finish_scan(kind, payload)
                return
        except queue.Empty:
            self.after(self.POLL_MS, self._poll_scan)

    def _finish_scan(self, kind: str, payload) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_controls_enabled(True)
        if kind != "done":
            self.status.configure(text="검색하지 못했습니다.")
            self._update_buttons()
            if kind == "invalid":
                messagebox.showwarning("확인 필요", payload, parent=self.root)
            else:
                messagebox.showerror("오류", payload, parent=self.root)
            return

        result, target_root = payload
        self._result = result
        self._result_target_dir = target_root
        self._sort_column, self._sort_reverse = None, False
        for f in result.orphans:
            iid = self.tree.insert("", "end", values=self._row_values(f))
            self._items[iid] = f
        self._update_summary()
        self._update_buttons()
        if result.orphans:
            self.status.configure(
                text="목록을 확인하세요. 남기고 싶은 파일은 [선택 항목 제외]로 뺄 수 있습니다."
            )
        else:
            self.status.configure(text="짝 없는 사진이 없습니다. 지울 파일이 없습니다.")
        if result.errors:
            self._show_problems(
                "일부 폴더/파일을 읽지 못했습니다",
                "다음 항목은 검색에서 빠졌습니다:",
                result.errors,
                warning=True,
            )

    def _row_values(self, f: PhotoFile) -> tuple:
        folder = os.path.relpath(os.path.dirname(f.path), self._result_target_dir)
        return (
            f.name,
            f.extension.lstrip(".").upper(),
            format_size(f.size),
            datetime.fromtimestamp(f.mtime).strftime("%Y-%m-%d %H:%M"),
            "(대상 폴더)" if folder == "." else folder,
        )

    def _sort_by(self, column: str) -> None:
        if self._busy():
            return
        reverse = self._sort_column == column and not self._sort_reverse
        keys = {
            "name": lambda f: f.name.casefold(),
            "type": lambda f: (f.extension, f.name.casefold()),
            "size": lambda f: f.size,
            "mtime": lambda f: f.mtime,
            "folder": lambda f: f.path.casefold(),
        }
        iids = sorted(self.tree.get_children(), key=lambda i: keys[column](self._items[i]), reverse=reverse)
        for index, iid in enumerate(iids):
            self.tree.move(iid, "", index)
        self._sort_column, self._sort_reverse = column, reverse

    # ------------------------------------------------------ list actions

    def _selected_files(self) -> list[PhotoFile]:
        return [self._items[i] for i in self.tree.selection() if i in self._items]

    def _exclude_selected(self) -> None:
        if self._busy():
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self._items.pop(iid, None)
        self._update_summary()
        self._update_buttons()

    def _on_double_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._open_selected()

    def _show_context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        state = "disabled" if self._busy() else "normal"
        self.menu.entryconfigure(3, state=state)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _open_selected(self) -> None:
        files = self._selected_files()
        if not files:
            return
        try:
            if sys.platform == "win32":
                os.startfile(files[0].path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", files[0].path])
        except OSError as exc:
            messagebox.showerror("파일 열기 실패", str(exc), parent=self.root)

    def _reveal_selected(self) -> None:
        files = self._selected_files()
        if not files:
            return
        path = files[0].path
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])

    # -------------------------------------------------------------- delete

    def _start_delete(self) -> None:
        iids = list(self.tree.get_children())
        if not iids or self._result is None:
            return
        files = [self._items[i] for i in iids]

        bad_volumes = unsupported_volumes(f.path for f in files)
        if bad_volumes:
            messagebox.showerror(
                "휴지통을 쓸 수 없는 드라이브",
                "다음 드라이브에는 Windows 휴지통이 없어(USB 메모리, SD 카드, 네트워크 드라이브 등) "
                "파일을 지우면 영구 삭제됩니다:\n\n"
                + "\n".join(bad_volumes)
                + "\n\n영구 삭제를 막기 위해 작업을 중단합니다. "
                "사진을 PC의 로컬 디스크로 복사한 뒤 다시 시도하세요.",
                parent=self.root,
            )
            return

        total = sum(f.size for f in files)
        message = f"{len(files)}개 파일(총 {format_size(total)})을 휴지통으로 이동합니다."
        if self._result.matched_count == 0:
            message += (
                "\n\n⚠ 주의: 대상 사진 중 레퍼런스와 이름이 같은 사진이 하나도 없습니다. "
                "폴더나 확장자를 잘못 고르지 않았는지 확인하세요."
            )
        message += "\n\n휴지통에서 언제든 복원할 수 있습니다. 계속할까요?"
        if not messagebox.askyesno(
            "휴지통으로 이동", message, icon="warning", default="no", parent=self.root
        ):
            return

        self._pending = collections.deque(iids)
        self._delete_total = len(iids)
        self._failures = []
        self._stop_requested = False
        self._cancelled_by_user = False
        self._set_controls_enabled(False)
        self.progress.configure(mode="determinate", maximum=len(iids), value=0)
        self._update_buttons()
        self.after(1, self._delete_step)

    def _window_handle(self) -> int | None:
        try:
            return int(self.root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return None

    def _delete_step(self) -> None:
        # Runs on the GUI thread in short slices so the window stays
        # responsive and any Windows confirmation dialog is owned by it.
        hwnd = self._window_handle()
        deadline = time.monotonic() + 0.05
        while self._pending and not self._stop_requested and time.monotonic() < deadline:
            iid = self._pending.popleft()
            f = self._items[iid]
            try:
                send_to_recycle_bin(f.path, hwnd)
            except RecycleError as exc:
                if exc.cancelled:
                    self._cancelled_by_user = True
                    self._stop_requested = True
                    self._pending.appendleft(iid)
                    break
                self._failures.append((f, str(exc)))
            else:
                self.tree.delete(iid)
                del self._items[iid]
                self._deleted += 1
            done = self._delete_total - len(self._pending)
            self.progress.configure(value=done)
            self.status.configure(text=f"휴지통으로 이동 중… {done}/{self._delete_total}")

        if self._pending and not self._stop_requested:
            self.after(1, self._delete_step)
        else:
            self._finish_delete()

    def _request_stop(self) -> None:
        self._stop_requested = True

    def _finish_delete(self) -> None:
        not_processed = len(self._pending)
        self._pending.clear()
        self._set_controls_enabled(True)
        self._update_summary()
        self._update_buttons()

        lines = [f"휴지통으로 이동: {self._deleted}개"]
        if self._failures:
            lines.append(f"실패: {len(self._failures)}개 (목록에 남겨 두었습니다)")
        if not_processed:
            reason = "Windows 확인 창에서 취소" if self._cancelled_by_user else "사용자가 중지"
            lines.append(f"{reason}하여 처리하지 않은 파일: {not_processed}개 (목록에 남아 있음)")
        self.status.configure(text=" · ".join(lines))

        if self._failures:
            self._show_problems(
                "일부 파일을 옮기지 못했습니다",
                "\n".join(lines) + "\n\n실패한 파일:",
                [f"{f.path}\n    → {reason}" for f, reason in self._failures],
                warning=True,
            )
        else:
            messagebox.showinfo("완료", "\n".join(lines), parent=self.root)

    def _show_problems(self, title: str, header: str, items: list[str], warning: bool) -> None:
        shown = items[:MAX_LISTED_PROBLEMS]
        body = header + "\n\n" + "\n".join(shown)
        if len(items) > len(shown):
            body += f"\n… 외 {len(items) - len(shown)}건"
        (messagebox.showwarning if warning else messagebox.showinfo)(title, body, parent=self.root)


def main() -> None:
    _enable_high_dpi()
    root = tk.Tk()
    root.title(f"{APP_TITLE}  v{__version__}")
    _use_korean_font(root)
    root.geometry("1000x700")
    root.minsize(820, 560)
    App(root)
    root.mainloop()
