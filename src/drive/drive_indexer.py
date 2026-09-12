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


def format_file_size(size_bytes: int) -> str:
    """Format bytes into readable MB/KB."""
    if not size_bytes or size_bytes <= 0:
        return ""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


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
            conn.commit()

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
                SELECT id, name, full_path, mime_type, web_view_link, size_bytes,
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
                if row["full_path"].strip() == "Level 4":
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

    def get_term_overview(self, query: str) -> Optional[dict]:
        """
        Check if query is asking for a semester/term overview (e.g. 1st term, 2nd term).
        Returns subjects and folders for that term.
        """
        q_norm = normalize_search_text(query)
        target_term = None
        if any(w in q_norm for w in ["1st", "first", "اول", "term 1", "semester 1"]):
            target_term = "1st Term"
        elif any(w in q_norm for w in ["2nd", "second", "تاني", "ثاني", "term 2", "semester 2"]):
            target_term = "2nd Term"
        elif any(w in q_norm for w in ["grad", "graduation", "تخرج"]):
            target_term = "Graduation Project"

        if not target_term:
            return None

        with self._get_connection() as conn:
            cur = conn.cursor()
            # Find the term folder
            cur.execute(
                "SELECT id, name, full_path, web_view_link FROM drive_items WHERE full_path = ? OR full_path = ?",
                (f"Level 4 / {target_term}", f"Level 4 / {target_term} "),
            )
            term_row = cur.fetchone()
            if not term_row:
                cur.execute(
                    "SELECT id, name, full_path, web_view_link FROM drive_items WHERE is_folder = 1 AND normalized_name LIKE ?",
                    (f"%{normalize_search_text(target_term)}%",),
                )
                term_row = cur.fetchone()

            if not term_row:
                return None

            term_path = term_row["full_path"]

            # Get immediate subfolders in this term (e.g. 01.Subjects, 02.Weekly Post, etc.)
            cur.execute(
                """
                SELECT id, name, full_path, web_view_link, is_folder
                FROM drive_items
                WHERE full_path LIKE ? AND full_path NOT LIKE ?
                ORDER BY full_path
                """,
                (f"{term_path} / %", f"{term_path} / % / %"),
            )
            top_folders = cur.fetchall()

            # Get subjects under 01.Subjects
            subjects_path = f"{term_path} / 01.Subjects"
            cur.execute(
                """
                SELECT id, name, full_path, web_view_link, is_folder
                FROM drive_items
                WHERE full_path LIKE ? AND full_path NOT LIKE ?
                ORDER BY full_path
                """,
                (f"{subjects_path} / %", f"{subjects_path} / % / %"),
            )
            raw_subjects = cur.fetchall()

            subjects = []
            standard_cats = {"lectures", "sections", "labs", "exams", "projects", "references", "students effort"}
            for s in raw_subjects:
                s_name = s["name"]
                s_link = s["web_view_link"]
                s_path = s["full_path"]

                # If this subject has elective options (e.g. Elective III has 3 sub-subjects)
                cur.execute(
                    """
                    SELECT id, name, full_path, web_view_link
                    FROM drive_items
                    WHERE full_path LIKE ? AND full_path NOT LIKE ?
                    ORDER BY full_path
                    """,
                    (f"{s_path} / %", f"{s_path} / % / %"),
                )
                sub_options = cur.fetchall()
                # Check if sub-options are course names rather than standard material folders
                elective_items = []
                for opt in sub_options:
                    opt_norm = normalize_search_text(opt["name"])
                    if not any(cat in opt_norm for cat in standard_cats):
                        elective_items.append({
                            "name": opt["name"],
                            "clean_name": re.sub(r"^\d+\.\s*", "", opt["name"]).strip(),
                            "link": opt["web_view_link"],
                        })

                is_elective = len(elective_items) > 0

                subjects.append({
                    "name": s_name,
                    "clean_name": re.sub(r"^\d+\.\s*", "", s_name).strip(),
                    "link": s_link,
                    "is_elective": is_elective,
                    "options": elective_items if is_elective else [],
                })

            other_folders = []
            for f in top_folders:
                if "01.Subjects" not in f["name"]:
                    other_folders.append({
                        "name": f["name"],
                        "clean_name": re.sub(r"^\d+\.\s*", "", f["name"]).strip(),
                        "link": f["web_view_link"],
                    })

            return {
                "term_name": target_term,
                "term_link": term_row["web_view_link"],
                "subjects": subjects,
                "other_folders": other_folders,
            }


    def get_course_details(self, query: str) -> Optional[dict]:
        """
        Inspect a specific subject/course folder in Level 4 and return its
        lectures, sections, labs, exams breakdown with exact counts and links.
        """
        q_norm = normalize_search_text(query)
        # Strip common filler words
        clean_q = re.sub(r"\b(how|many|lectures|lecture|in|first|second|semester|term|مادة|كام|محاضرة|محاضرات|في|ترم|اول|تاني|الترم)\b", " ", q_norm)
        clean_q = re.sub(r"\s+", " ", clean_q).strip()
        search_target = clean_q if clean_q else q_norm

        with self._get_connection() as conn:
            cur = conn.cursor()

            # Find matching course folder (e.g. '02.Digital IC', '04.Networks', '02.Control')
            cur.execute(
                """
                SELECT id, name, full_path, web_view_link
                FROM drive_items
                WHERE is_folder = 1 AND (normalized_name LIKE ? OR normalized_path LIKE ?)
                ORDER BY
                    CASE
                        WHEN normalized_name = ? THEN 1
                        WHEN normalized_name LIKE ? THEN 2
                        ELSE 3
                    END,
                    LENGTH(full_path) ASC
                """,
                (f"%{search_target}%", f"%{search_target}%", search_target, f"{search_target}%"),
            )
            candidates = cur.fetchall()

            # Filter candidates to find the actual subject folder (not the 'Lectures' folder or root)
            subject_folder = None
            for c in candidates:
                c_name = c["name"]
                # Skip generic folders
                if any(g in c_name for g in ["Lectures", "Sections", "Labs", "Exams", "Projects", "References", "Level 4", "1st Term", "2nd Term", "01.Subjects"]):
                    continue
                subject_folder = c
                break

            # If no candidate found without generic keywords, try the best match
            if not subject_folder and candidates:
                subject_folder = candidates[0]

            if not subject_folder:
                return None

            subj_path = subject_folder["full_path"]
            subj_name = subject_folder["name"]
            clean_title = re.sub(r"^\d+\.\s*", "", subj_name).strip()

            # Fetch all items under this subject folder
            cur.execute(
                """
                SELECT id, name, full_path, mime_type, web_view_link, size_bytes, is_folder
                FROM drive_items
                WHERE full_path LIKE ?
                ORDER BY full_path
                """,
                (f"{subj_path} / %",),
            )
            sub_items = cur.fetchall()

            # Categorize sub items
            categories = {
                "lectures": {"name": "Lectures", "folder_link": None, "files": [], "has_folder": False},
                "sections": {"name": "Sections", "folder_link": None, "files": [], "has_folder": False},
                "labs": {"name": "Labs", "folder_link": None, "files": [], "has_folder": False},
                "exams": {"name": "Exams & Midterms", "folder_link": None, "files": [], "has_folder": False},
                "projects": {"name": "Projects", "folder_link": None, "files": [], "has_folder": False},
                "references": {"name": "References", "folder_link": None, "files": [], "has_folder": False},
                "students_effort": {"name": "Students Effort", "folder_link": None, "files": [], "has_folder": False},
            }

            for item in sub_items:
                path = item["full_path"]
                is_f = bool(item["is_folder"])
                link = item["web_view_link"]
                name = item["name"]

                cat_key = None
                if "Lectures" in path or "محاضر" in path:
                    cat_key = "lectures"
                elif "Sections" in path or "سكاشن" in path:
                    cat_key = "sections"
                elif "Labs" in path or "معامل" in path:
                    cat_key = "labs"
                elif "Exams" in path or "امتحان" in path:
                    cat_key = "exams"
                elif "Projects" in path or "مشروع" in path:
                    cat_key = "projects"
                elif "References" in path or "مراجع" in path:
                    cat_key = "references"
                elif "Students Effort" in path:
                    cat_key = "students_effort"

                if cat_key:
                    if is_f:
                        categories[cat_key]["has_folder"] = True
                        if not categories[cat_key]["folder_link"]:
                            categories[cat_key]["folder_link"] = link
                    else:
                        categories[cat_key]["files"].append({
                            "name": name,
                            "link": link,
                            "size": format_file_size(item["size_bytes"]),
                        })

            # Check term from path
            term_name = "1st Term" if "1st Term" in subj_path else ("2nd Term" if "2nd Term" in subj_path else "College Drive")

            total_files = sum(len(c["files"]) for c in categories.values())

            return {
                "subject_name": subj_name,
                "clean_title": clean_title,
                "term": term_name,
                "full_path": subj_path,
                "folder_link": subject_folder["web_view_link"],
                "total_files": total_files,
                "categories": categories,
            }

    def format_term_overview_message(self, term_data: dict) -> str:
        """Format term subjects and resources into a clean WhatsApp message."""
        term_name = term_data["term_name"]
        term_link = term_data["term_link"]
        subjects = term_data["subjects"]
        other_folders = term_data["other_folders"]

        lines = [f"📚 *Level 4 — {term_name} Subjects & Folders*"]
        if term_link:
            lines.append(f"🔗 [Open {term_name} on Google Drive]({term_link})\n")

        lines.append("📖 *Registered Subjects:*")
        for idx, s in enumerate(subjects, 1):
            s_clean = s["clean_name"]
            s_link = s["link"]
            if s["is_elective"]:
                lines.append(f"{idx}. 📁 *{s_clean}* (Elective Group):")
                for opt in s["options"]:
                    lines.append(f"   • 🔹 [{opt['clean_name']}]({opt['link']})")
            else:
                lines.append(f"{idx}. 📁 [{s_clean}]({s_link})")

        if other_folders:
            lines.append("\n📂 *Term Resources & Activity:*")
            for f in other_folders:
                lines.append(f"• 📁 [{f['clean_name']}]({f['link']})")

        return "\n".join(lines)

    def format_course_details_message(self, course_data: dict) -> str:
        """Format course breakdown, lecture counts, and links into a clean WhatsApp message."""
        clean_title = course_data["clean_title"]
        term = course_data["term"]
        folder_link = course_data["folder_link"]
        cats = course_data["categories"]
        total_files = course_data["total_files"]

        lines = [
            f"📚 *Course Overview: {clean_title}*",
            f"📍 *Term:* {term}",
            f"🔗 [Open Course Folder in Google Drive]({folder_link})\n",
            "📊 *Materials Status & File Counts:*",
        ]

        icons = {
            "lectures": "🎓",
            "sections": "📐",
            "labs": "🔬",
            "exams": "📝",
            "projects": "📦",
            "references": "📖",
            "students_effort": "👥",
        }

        for k, cat in cats.items():
            if not cat["has_folder"] and not cat["files"]:
                continue
            icon = icons.get(k, "📁")
            c_name = cat["name"]
            files = cat["files"]
            cnt = len(files)
            f_link = cat["folder_link"]

            if cnt == 0:
                link_text = f" — [Folder Ready]({f_link})" if f_link else ""
                lines.append(f"• {icon} *{c_name}:* 0 files uploaded yet{link_text}")
            else:
                lines.append(f"• {icon} *{c_name}:* {cnt} files")
                for idx, f in enumerate(files[:5], 1):
                    size = f" ({f['size']})" if f.get("size") else ""
                    lines.append(f"   {idx}. [{f['name']}]({f['link']}){size}")
                if cnt > 5:
                    lines.append(f"   ... and {cnt - 5} more files.")

        if total_files == 0:
            lines.append(
                "\nℹ️ *Note:* The full folder structure for this course is prepared on your college drive, but no lecture/study files have been uploaded yet."
            )

        return "\n".join(lines)



# Global singleton instance
drive_indexer = DriveIndexer()

