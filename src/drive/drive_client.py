"""
Google Drive API Client for College Assistant AI.

Handles:
- OAuth2 authentication using credentials.json & token.json
- Querying Google Drive API v3 (free tier)
- Recursive crawling of college folder hierarchies (e.g. Level 4 materials)
- Direct Drive API search
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

logger = logging.getLogger(__name__)

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

# Google Workspace MIME types
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class GoogleDriveClient:
    """Async wrapper around Google Drive API v3."""

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
        root_folder_id: Optional[str] = None,
    ):
        self.credentials_path = credentials_path or os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "./credentials.json"
        )
        self.token_path = token_path or "./token.json"
        self.root_folder_id = root_folder_id or os.getenv(
            "GOOGLE_DRIVE_FOLDER_ID", "1oVFxVUEWi1m98vyIGd8pkei6mQrwo47o"
        )
        self._service = None

    def is_authorized(self) -> bool:
        """Check if token.json exists and contains drive scope."""
        token_file = Path(self.token_path)
        if not token_file.is_file():
            return False
        try:
            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds.scopes:
                # Check if drive scope is in scopes
                for s in creds.scopes:
                    if "drive" in s:
                        return True
            return False
        except Exception:
            return False

    def _get_service(self):
        """Authenticate and return Google Drive API v3 service."""
        if self._service:
            return self._service

        token_file = Path(self.token_path)
        if not token_file.is_file():
            raise RuntimeError(
                "Google Drive access not authorized yet. Please run 'python setup_google.py' to authenticate."
            )

        creds = Credentials.from_authorized_user_file(str(token_file))

        # Verify drive scope is authorized
        has_drive_scope = False
        if creds.scopes:
            for s in creds.scopes:
                if "drive" in s:
                    has_drive_scope = True
                    break

        if not has_drive_scope:
            raise RuntimeError(
                "Your token.json does not have Google Drive permissions. "
                "Please run 'python setup_google.py' to grant Drive access."
            )

        # Refresh if expired
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google Drive OAuth token...")
            creds.refresh(Request())
            token_file.write_text(creds.to_json(), encoding="utf-8")
        elif not creds or not creds.valid:
            raise RuntimeError(
                "Invalid Google credentials. Please re-run 'python setup_google.py'."
            )

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def list_folder_children(self, folder_id: str) -> list[dict]:
        """
        List all direct child files and folders inside folder_id.
        Handles pagination automatically.
        """
        def _fetch():
            service = self._get_service()
            items = []
            page_token = None
            query = f"'{folder_id}' in parents and trashed = false"

            fields = (
                "nextPageToken, files("
                "id, name, mimeType, size, modifiedTime, webViewLink, parents"
                ")"
            )

            while True:
                response = service.files().list(
                    q=query,
                    pageSize=100,
                    fields=fields,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()

                items.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            return items

        return await asyncio.to_thread(_fetch)

    async def crawl_folder_tree(
        self, root_folder_id: Optional[str] = None, max_depth: int = 8
    ) -> list[dict]:
        """
        Recursively crawl the entire folder tree starting from root_folder_id.
        Returns a flat list of all files and folders with their computed full_path.
        """
        root_id = root_folder_id or self.root_folder_id
        if not root_id:
            raise ValueError("No root folder ID specified.")

        logger.info("Starting recursive crawl for Google Drive folder: %s", root_id)

        # First, get root folder name
        def _get_root_name():
            service = self._get_service()
            try:
                meta = service.files().get(
                    fileId=root_id,
                    fields="id, name, mimeType, webViewLink",
                    supportsAllDrives=True,
                ).execute()
                return meta.get("name", "College Drive")
            except Exception as e:
                logger.warning("Could not fetch root folder name (%s), defaulting to 'Level 4'", e)
                return "Level 4"

        root_name = await asyncio.to_thread(_get_root_name)

        all_items: list[dict] = []
        # Queue contains: (folder_id, folder_path, current_depth)
        queue: list[tuple[str, str, int]] = [(root_id, root_name, 0)]
        visited_folders = {root_id}

        # Add root folder itself to all_items
        all_items.append({
            "id": root_id,
            "name": root_name,
            "parent_id": "",
            "full_path": root_name,
            "mime_type": FOLDER_MIME_TYPE,
            "web_view_link": f"https://drive.google.com/drive/folders/{root_id}",
            "size_bytes": 0,
            "modified_time": "",
            "is_folder": True,
        })

        while queue:
            current_id, current_path, depth = queue.pop(0)
            if depth >= max_depth:
                logger.warning("Reached max crawl depth (%d) at %s", max_depth, current_path)
                continue

            try:
                children = await self.list_folder_children(current_id)
                logger.debug("Crawled '%s' (depth %d): %d items", current_path, depth, len(children))
            except Exception as err:
                logger.error("Failed to list children of '%s' (%s): %s", current_path, current_id, err)
                continue

            for item in children:
                item_id = item.get("id")
                item_name = item.get("name", "Untitled")
                mime_type = item.get("mimeType", "")
                is_folder = mime_type == FOLDER_MIME_TYPE
                item_path = f"{current_path} / {item_name}"
                size = int(item.get("size", 0)) if item.get("size") else 0
                modified = item.get("modifiedTime", "")
                link = item.get("webViewLink") or f"https://drive.google.com/file/d/{item_id}/view"

                all_items.append({
                    "id": item_id,
                    "name": item_name,
                    "parent_id": current_id,
                    "full_path": item_path,
                    "mime_type": mime_type,
                    "web_view_link": link,
                    "size_bytes": size,
                    "modified_time": modified,
                    "is_folder": is_folder,
                })

                if is_folder and item_id not in visited_folders:
                    visited_folders.add(item_id)
                    queue.append((item_id, item_path, depth + 1))

        logger.info(
            "Crawl complete for '%s'. Total indexed items: %d (across %d folders)",
            root_name,
            len(all_items),
            len(visited_folders),
        )
        return all_items

    async def search_drive_api(
        self, query_text: str, max_results: int = 10
    ) -> list[dict]:
        """
        Direct Drive API search fallback across accessible files.
        """
        def _search():
            service = self._get_service()
            escaped_text = query_text.replace("'", "\\'")
            q = f"name contains '{escaped_text}' and trashed = false"

            fields = "files(id, name, mimeType, size, modifiedTime, webViewLink, parents)"
            resp = service.files().list(
                q=q,
                pageSize=max_results,
                fields=fields,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            return resp.get("files", [])

        try:
            return await asyncio.to_thread(_search)
        except Exception as e:
            logger.error("Direct Drive API search error: %s", e)
            return []
