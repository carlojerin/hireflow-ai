"""
ONE-TIME LOCAL SETUP: grants JobPilot AI's Gmail API OAuth client permission
to send email as your Gmail account, and writes the resulting refresh token
into .env.

Run this yourself, once, from the project root:

    python backend/scripts/gmail_oauth_setup.py

What happens:
1. Your default browser opens to a real Google consent screen (sign in as
   the Gmail account you added as a "test user" in Google Cloud Console,
   then click Allow).
2. Google redirects back to a temporary local server this script starts
   on your machine (http://localhost:<port>) -- nothing leaves your machine.
3. The refresh token is written into .env's GOOGLE_REFRESH_TOKEN line
   automatically. Nothing is printed to the terminal or sent anywhere else.

Safe to re-run any time (e.g. if the refresh token is ever revoked) --
it will overwrite the existing GOOGLE_REFRESH_TOKEN line.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".env",
)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _write_refresh_token(refresh_token: str) -> None:

    with open(ENV_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "GOOGLE_REFRESH_TOKEN=" in content:
        content = re.sub(
            r"GOOGLE_REFRESH_TOKEN=.*",
            f"GOOGLE_REFRESH_TOKEN={refresh_token}",
            content,
        )
    else:
        content += f"\nGOOGLE_REFRESH_TOKEN={refresh_token}\n"

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def main() -> None:

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in .env. "
            "Add them first (from the OAuth client in Google Cloud Console), "
            "then re-run this script."
        )
        return

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("Opening your browser for the Google consent screen...")
    print("Sign in with the Gmail account you added as a test user, then click Allow.")

    credentials = flow.run_local_server(port=0)

    if not credentials.refresh_token:
        print(
            "No refresh token was returned. This usually means you've already "
            "granted this app access before -- revoke it at "
            "https://myaccount.google.com/permissions and re-run this script."
        )
        return

    _write_refresh_token(credentials.refresh_token)

    print("Done. GOOGLE_REFRESH_TOKEN has been written to .env.")
    print("Restart the backend server so it picks up the new value.")


if __name__ == "__main__":
    main()
