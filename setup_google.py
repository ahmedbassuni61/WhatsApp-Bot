"""
Google APIs One-Time Authorization Script (Calendar + Drive).

Run this script once from your terminal to authorize Google Calendar and Google Drive access:
    python setup_google.py

It will:
1. Read credentials.json
2. Request scopes for Google Calendar and Google Drive (Read-Only)
3. Open your default web browser for Google account login & consent
4. Save the resulting access token into token.json
"""

import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
TOKEN_PATH = "./token.json"


def main():
    if not Path(CREDENTIALS_PATH).exists():
        print(f"❌ Error: {CREDENTIALS_PATH} not found.")
        print("Please download credentials.json from Google Cloud Console and place it in the project root.")
        return

    print("🔑 Starting Google OAuth authorization for Calendar & Drive...")
    print("Scopes requested:")
    for scope in SCOPES:
        print(f" - {scope}")
    print("\nA browser window will open. Sign in with your Google account and click 'Allow'.")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    Path(TOKEN_PATH).write_text(creds.to_json(), encoding="utf-8")
    print(f"\n✅ Authorization successful! Credentials saved to {TOKEN_PATH}.")
    print("Google Calendar and Google Drive access are now active for College Assistant AI!")


if __name__ == "__main__":
    main()
