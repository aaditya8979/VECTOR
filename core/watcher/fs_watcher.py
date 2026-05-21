"""
File System Watcher — monitors the project directory for source file changes.
On every save: triggers CPGUpdater.on_file_changed() and saves the updated graph.
Runs in a background daemon thread. Zero impact on model inference.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
from watchdog.observers import Observer

from core.cpg.builder  import CPGBuilder
from core.cpg.updater  import CPGUpdater
from core.cpg.language_registry import is_supported
from config            import CPG_FILE, BRAIN_DIR


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        updater:     CPGUpdater,
        cpg_path:    str,
        on_change:   Optional[Callable[[str, List[str]], None]] = None,
        debounce_ms: int = 300,
    ):
        self.updater     = updater
        self.cpg_path    = cpg_path
        self.on_change   = on_change
        self.debounce_ms = debounce_ms
        self._pending:   dict[str, float] = {}
        self._lock       = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def on_modified(self, event):
        if not event.is_directory and is_supported(event.src_path):
            self._debounce(event.src_path)

    def on_created(self, event):
        if not event.is_directory and is_supported(event.src_path):
            self._debounce(event.src_path)

    def _debounce(self, path: str):
        """Coalesce rapid successive saves (e.g. editor auto-save)."""
        with self._lock:
            self._pending[path] = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(
                self.debounce_ms / 1000.0,
                self._flush,
            )
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self._lock:
            paths         = list(self._pending.keys())
            self._pending = {}

        for path in paths:
            try:
                changed = self.updater.on_file_changed(path)
                if changed:
                    # Persist the updated graph
                    self.updater.builder.save(self.cpg_path)
                    if self.on_change:
                        self.on_change(path, changed)
            except Exception as e:
                print(f"[watcher] Error processing {path}: {e}")


class FSWatcher:
    def __init__(
        self,
        builder:     CPGBuilder,
        project_root: str,
        on_change:   Optional[Callable[[str, List[str]], None]] = None,
    ):
        self.builder      = builder
        self.project_root = project_root
        brain_dir         = str(Path(project_root) / BRAIN_DIR)
        cpg_path          = str(Path(project_root) / BRAIN_DIR / CPG_FILE)

        self.updater  = CPGUpdater(builder)
        self.handler  = _Handler(self.updater, cpg_path, on_change)
        self.observer = Observer()
        self.observer.schedule(self.handler, project_root, recursive=True)

    def start(self):
        self.observer.start()
        print(f"[watcher] Watching {self.project_root} for source file changes...")

    def stop(self):
        self.observer.stop()
        self.observer.join()
        print("[watcher] Stopped.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()