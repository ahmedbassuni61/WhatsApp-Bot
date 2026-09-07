"""
Google Calendar One-Time Authorization Script.

Run this script once from your terminal to authorize Google Calendar access:
    python setup_calendar.py

It will:
1. Read credentials.json
2. Open your default web browser for Google account login & consent
3. Save the resulting access token into token.json
"""

import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
TOKEN_PATH = "./token.json"

def main():
    if not Path(CREDENTIALS_PATH).exists():
        print(f"❌ Error: {CREDENTIALS_PATH} not found.")
        print("Please download credentials.json from Google Cloud Console and place it in the project root.")
        return

    print("🔑 Starting Google Calendar OAuth authorization...")
    print("A browser window will open. Sign in with your Google account and click 'Allow'.")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    Path(TOKEN_PATH).write_text(creds.to_json())
    print(f"✅ Authorization successful! Credentials saved to {TOKEN_PATH}.")
    print("Google Calendar sync is now fully active for College Assistant AI!")

if __name__ == "__main__":
    main()
