"""JSONL writer with SHA-256 content-hash dedup (port from web-scraping-practice-2) + CSV export.

Day 2. JSONL (one JSON object per line) is the right sink for a stream of records: appendable,
line-addressable, and every line is independently valid — a crash mid-crawl leaves a readable
file, unlike a half-written JSON array.

Dedup is by **content hash**, not by position: the SHA-256 of a record's canonical JSON (keys
sorted, so field order never matters). Seeding the seen-set from an existing file means re-runs
skip records already captured — idempotent crawls, the pattern ported from practice-2.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from types import TracebackType

from pydantic import BaseModel

from scrapekit.logging import get_logger

log = get_logger(__name__)


def _content_hash(payload: dict[str, object], fields: tuple[str, ...] | None) -> str:
    """SHA-256 over canonical JSON. ``fields`` restricts the hash to an identity subset."""
    if fields is not None:
        payload = {k: payload[k] for k in fields if k in payload}
    # sort_keys + compact separators => the same record always hashes identically.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JsonlStore:
    """Append validated records to a JSONL file, skipping content-duplicate rows.

    Use as a context manager so the file handle is owned and flushed::

        with JsonlStore("data/quotes.jsonl") as store:
            is_new = store.append(quote)   # False if a content-identical row already exists

    ``dedup_fields`` narrows the identity to specific fields (e.g. a book's ``url``); the
    default hashes the whole record, so any field change counts as a new record.
    """

    def __init__(self, path: str | Path, *, dedup_fields: tuple[str, ...] | None = None) -> None:
        self.path = Path(path)
        self._dedup_fields = dedup_fields
        self._seen: set[str] = set()
        self._records: list[dict[str, object]] = []  # kept in-memory for CSV export
        self._fh = None  # opened lazily on first append / __enter__

        # Seed dedup + export buffer from any existing file so re-runs are idempotent.
        if self.path.exists():
            self._load_existing()

    def _load_existing(self) -> None:
        loaded = 0
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                self._records.append(payload)
                self._seen.add(_content_hash(payload, self._dedup_fields))
                loaded += 1
        log.info("storage.loaded_existing", path=str(self.path), records=loaded)

    def __enter__(self) -> JsonlStore:
        self._open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _open(self) -> None:
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")

    def append(self, record: BaseModel) -> bool:
        """Write ``record`` unless a content-identical row exists. True if written (new)."""
        payload = record.model_dump(mode="json")
        digest = _content_hash(payload, self._dedup_fields)
        if digest in self._seen:
            log.debug("storage.duplicate", digest=digest[:12])
            return False

        self._open()
        assert self._fh is not None
        self._fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._fh.flush()  # durable per-record: a crash mid-crawl keeps everything so far
        self._seen.add(digest)
        self._records.append(payload)
        return True

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def export_csv(self, path: str | Path) -> Path:
        """Flatten everything written so far to CSV. Columns = union of all record keys."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        columns: list[str] = []
        for rec in self._records:
            for key in rec:
                if key not in columns:
                    columns.append(key)

        with out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for rec in self._records:
                # Serialize list/dict cells to JSON so the CSV stays single-valued per column.
                writer.writerow(
                    {
                        k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in rec.items()
                    }
                )
        log.info("storage.export_csv", path=str(out), rows=len(self._records))
        return out
