"""
SQLite Indexer & Search Engine for College Google Drive.

Provides:
- Local persistent SQLite index in data/drive_index.db
- Sub-10ms search across files and folder paths
- Arabic & English text normalization (alif, ta marbuta, ya, harakat)
- Full breadcrumb path matching (e.g. 'Networks Lecture 1' matches file 'Lecture 1.pdf' in 'Networks')
- Type filtering (PDF, Slides, Docs, Folders)
"""

import asyncio
import datetime
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path("./data")
DB_PATH = DATA_DIR / "drive_index.db"


def normalize_search_text(text: str) -> str:
    """Normalize Arabic and English text for forgiving search matching."""
    if not text:
        return ""
    # Lowercase
    s = text.lower()
    # Remove Arabic diacritics / tashkeel
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    # Normalize Alif forms (أ, إ, آ, ٱ -> ا)
    s = re.sub(r"[أإآٱ]", "ا", s)
    # Normalize Ta Marbuta (ة -> ه)
    s = re.sub(r"ة", "ه", s)
    # Normalize Ya / Alif Maqsura (ى -> ي)
    s = re.sub(r"ى", "ي", s)
    # Replace punctuation, underscores, dashes with space
    s = re.sub(r"[-_/\\.,;:!?(){}\[\]_]", " ", s)
    # Collapse multiple whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


from src.drive.drive_client import format_file_size


def get_icon_for_item(mime_type: str, name: str, is_folder: bool) -> str:
    """Return appropriate emoji icon for a drive item."""
    if is_folder:
        return "📁"
    lower_name = name.lower()
    if "pdf" in mime_type or lower_name.endswith(".pdf"):
        return "📄"
    if (
        "presentation" in mime_type
        or lower_name.endswith((".ppt", ".pptx", ".key"))
    ):
        return "📊"
    if (
        "word" in mime_type
        or "document" in mime_type
        or lower_name.endswith((".doc", ".docx", ".txt", ".rtf"))
    ):
        return "📝"
    if "spreadsheet" in mime_type or lower_name.endswith((".xls", ".xlsx", ".csv")):
        return "📈"
    if "video" in mime_type or lower_name.endswith((".mp4", ".mkv", ".avi")):
        return "🎥"
    if "image" in mime_type or lower_name.endswith((".jpg", ".jpeg", ".png")):
        return "🖼️"
    if "zip" in mime_type or lower_name.endswith((".zip", ".rar", ".7z")):
        return "📦"
    return "📎"


class DriveIndexer:
    """SQLite-backed indexer and search engine for Google Drive."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create database tables and indexes."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drive_items (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_id TEXT,
                    full_path TEXT NOT NULL,
                    mime_type TEXT,
                    web_view_link TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    modified_time TEXT,
                    is_folder INTEGER DEFAULT 0,
                    normalized_name TEXT NOT NULL,
                    normalized_path TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_normalized_name ON drive_items(normalized_name);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_normalized_path ON drive_items(normalized_path);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_is_folder ON drive_items(is_folder);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            conn.commit()

    def get_indexed_root_id(self) -> Optional[str]:
        """Return the root folder ID that was last indexed."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM index_meta WHERE key = 'root_folder_id'")
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def count_items(self) -> int:
        """Return total number of indexed items."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM drive_items")
            return cur.fetchone()[0]

    def get_stats(self) -> dict:
        """Return index statistics."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM drive_items WHERE is_folder = 0")
            files_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM drive_items WHERE is_folder = 1")
            folders_count = cur.fetchone()[0]
            cur.execute("SELECT MAX(updated_at) FROM drive_items")
            last_updated = cur.fetchone()[0] or "Never"
            return {
                "total_items": files_count + folders_count,
                "files_count": files_count,
                "folders_count": folders_count,
                "last_updated": last_updated,
            }

    async def sync_from_drive(self, drive_client) -> dict:
        """
        Crawl Drive folder tree using drive_client and store in SQLite.
        Runs crawler in asyncio loop, inserts in a single transaction.
        """
        start_time = datetime.datetime.now()
        logger.info("Starting Drive index synchronization...")

        items = await drive_client.crawl_folder_tree()
        if not items:
            logger.warning("No items returned from crawl.")
            return {"status": "empty", "total": 0}

        now_iso = datetime.datetime.utcnow().isoformat()

        def _save_to_db():
            with self._get_connection() as conn:
                # Clear and insert fresh snapshot
                conn.execute("BEGIN TRANSACTION")
                conn.execute("DELETE FROM drive_items")

                records = []
                for item in items:
                    norm_name = normalize_search_text(item["name"])
                    norm_path = normalize_search_text(item["full_path"])
                    records.append((
                        item["id"],
                        item["name"],
                        item.get("parent_id", ""),
                        item["full_path"],
                        item.get("mime_type", ""),
                        item.get("web_view_link", ""),
                        item.get("size_bytes", 0),
                        item.get("modified_time", ""),
                        1 if item.get("is_folder") else 0,
                        norm_name,
                        norm_path,
                        now_iso,
                    ))

                conn.executemany(
                    """
                    INSERT INTO drive_items (
                        id, name, parent_id, full_path, mime_type, web_view_link,
                        size_bytes, modified_time, is_folder, normalized_name,
                        normalized_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('root_folder_id', ?)",
                    (getattr(drive_client, "root_folder_id", ""),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('last_synced', ?)",
                    (now_iso,),
                )
                conn.commit()

        await asyncio.to_thread(_save_to_db)

        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        stats = self.get_stats()
        logger.info(
            "Drive index sync complete in %.2fs: %d files, %d folders",
            elapsed,
            stats["files_count"],
            stats["folders_count"],
        )
        return {
            "status": "success",
            "elapsed_seconds": elapsed,
            "total_items": stats["total_items"],
            "files_count": stats["files_count"],
            "folders_count": stats["folders_count"],
            "last_updated": now_iso,
        }

    def search(
        self,
        query: str,
        file_type: str = "all",
        limit: int = 10,
    ) -> list[dict]:
        """
        Search indexed materials by keywords across name and full path.

        Args:
            query: User's search query (English or Arabic)
            file_type: Filter by 'pdf', 'slides', 'doc', 'sheet', 'folder', or 'all'
            limit: Max results to return (default 10)

        Returns:
            List of matching dicts formatted with icons, human sizes, and links.
        """
        query_norm = normalize_search_text(query)
        if not query_norm:
            return []

        tokens = [t for t in query_norm.split(" ") if t]
        if not tokens:
            return []

        type_filter = file_type.lower() if file_type else "all"

        with self._get_connection() as conn:
            cur = conn.cursor()

            # Base query
            sql = """
                SELECT id, name, parent_id, full_path, mime_type, web_view_link, size_bytes,
                       modified_time, is_folder, normalized_name, normalized_path
                FROM drive_items
                WHERE 1=1
            """
            params: list[Any] = []

            # Apply file type filter
            if type_filter == "folder":
                sql += " AND is_folder = 1"
            elif type_filter == "pdf":
                sql += " AND is_folder = 0 AND (mime_type LIKE '%pdf%' OR name LIKE '%.pdf')"
            elif type_filter in ("slides", "presentation", "ppt"):
                sql += (
                    " AND is_folder = 0 AND ("
                    "mime_type LIKE '%presentation%' OR name LIKE '%.ppt%' OR name LIKE '%.pptx%'"
                    ")"
                )
            elif type_filter in ("doc", "document", "word"):
                sql += (
                    " AND is_folder = 0 AND ("
                    "mime_type LIKE '%word%' OR mime_type LIKE '%document%' "
                    "OR name LIKE '%.doc%' OR name LIKE '%.docx%' OR name LIKE '%.txt'"
                    ")"
                )
            elif type_filter in ("sheet", "excel", "sheets"):
                sql += (
                    " AND is_folder = 0 AND ("
                    "mime_type LIKE '%spreadsheet%' OR name LIKE '%.xls%' OR name LIKE '%.xlsx%'"
                    ")"
                )

            # We match items where all tokens appear in normalized_path or normalized_name
            for token in tokens:
                sql += " AND (normalized_path LIKE ? OR normalized_name LIKE ?)"
                params.extend([f"%{token}%", f"%{token}%"])

            cur.execute(sql, params)
            rows = cur.fetchall()

            # Rank results
            ranked = []
            for row in rows:
                score = 0
                name_norm = row["normalized_name"]
                path_norm = row["normalized_path"]

                # Exact query in name
                if query_norm in name_norm:
                    score += 100
                elif query_norm in path_norm:
                    score += 50

                # All tokens in name
                if all(t in name_norm for t in tokens):
                    score += 40

                # Token frequency and position
                for t in tokens:
                    if name_norm.startswith(t):
                        score += 20
                    elif t in name_norm:
                        score += 10
                    elif t in path_norm:
                        score += 5

                # Boost files over generic folders when searching for materials
                if row["is_folder"] == 0:
                    score += 15
                elif type_filter == "folder":
                    score += 50

                # Penalize root folder
                if not row["parent_id"] or "/" not in row["full_path"]:
                    score -= 50

                icon = get_icon_for_item(
                    row["mime_type"], row["name"], bool(row["is_folder"])
                )
                size_str = format_file_size(row["size_bytes"])

                ranked.append({
                    "id": row["id"],
                    "name": row["name"],
                    "full_path": row["full_path"],
                    "mime_type": row["mime_type"],
                    "web_view_link": row["web_view_link"],
                    "size_str": size_str,
                    "is_folder": bool(row["is_folder"]),
                    "icon": icon,
                    "modified_time": row["modified_time"],
                    "score": score,
                })

            # Sort by score descending, then modified_time
            ranked.sort(key=lambda x: (x["score"], x["modified_time"]), reverse=True)
            return ranked[:limit]


# Global singleton instance
drive_indexer = DriveIndexer()

