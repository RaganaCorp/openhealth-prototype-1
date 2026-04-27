"""
watchdog-based directory watcher.

Monitors each patient's uploads/ subfolder. When new files appear (via manual copy),
triggers an incremental ingestion job.

The watchdog observer runs in a background thread. To dispatch async jobs onto the
main asyncio event loop, asyncio.run_coroutine_threadsafe is used.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from watchdog.events import FileCreatedEvent, FileMovedEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

import jobs
import patients as pt
from config import DATA_PATH

logger = logging.getLogger(__name__)

_observer: Optional[Observer] = None
# Set by start_watcher; needed by the event handler to schedule coroutines.
_loop: Optional[asyncio.AbstractEventLoop] = None


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------

class _UploadsHandler(PatternMatchingEventHandler):
    def __init__(self, patient_id: str, uploads_dir: Path):
        # Watch supported extensions; ignore .extracted sidecars.
        super().__init__(
            patterns=[
                "*.pdf", "*.tif", "*.tiff", "*.txt", "*.html", "*.json",
            ],
            ignore_patterns=["*.extracted"],
            ignore_directories=True,
            case_sensitive=False,
        )
        self.patient_id = patient_id
        self.uploads_dir = uploads_dir

    def _trigger(self, src_path: str) -> None:
        if _loop is None:
            return
        # Only react to files directly inside uploads/ (not subdirectories).
        try:
            Path(src_path).relative_to(self.uploads_dir)
        except ValueError:
            return
        if Path(src_path).parent != self.uploads_dir:
            return  # subdirectory — ignore

        logger.info("Watcher: new file detected for patient %s: %s", self.patient_id, src_path)
        asyncio.run_coroutine_threadsafe(
            jobs.start_incremental_ingestion(self.patient_id),
            _loop,
        )

    def on_created(self, event: FileCreatedEvent) -> None:
        self._trigger(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        # A file moved/renamed into uploads/ should also trigger ingestion.
        self._trigger(event.dest_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_patient_watch(patient_id: str, uploads_dir: Path) -> None:
    """Add a watch for a patient's uploads/ directory (thread-safe)."""
    if _observer is None:
        return
    uploads_dir.mkdir(parents=True, exist_ok=True)
    handler = _UploadsHandler(patient_id=patient_id, uploads_dir=uploads_dir)
    _observer.schedule(handler, str(uploads_dir), recursive=False)
    logger.info("Watcher: watching %s for patient %s", uploads_dir, patient_id)


async def start_watcher() -> None:
    """
    Start the watchdog observer and register watches for all existing patients.
    Must be called from the asyncio startup event (lifespan) so _loop is set correctly.
    """
    global _observer, _loop
    _loop = asyncio.get_event_loop()
    _observer = Observer()

    patients = await pt.load_patients_index()
    for entry in patients:
        uploads_dir = Path(entry["folder_path"]) / "uploads"
        add_patient_watch(entry["id"], uploads_dir)

    _observer.start()
    logger.info("Watcher: observer started, watching %d patient(s)", len(patients))


def stop_watcher() -> None:
    if _observer is not None:
        _observer.stop()
        _observer.join()
        logger.info("Watcher: observer stopped")
