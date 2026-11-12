"""Official Spotify OAuth with PKCE and a local loopback callback."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from spotify_probe import ProbeError

TOKEN_URL = "https://accounts.spotify.com/api/token"
TOKEN_FILE = Path(".spotify-oauth.json")
REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPES = "playlist-read-private playlist-modify-private"


def token_request(values):
    request = Request(TOKEN_URL, data=urlencode(values).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        raise ProbeError(f"Spotify OAuth HTTP {error.code}. Check your app details or authorize again.") from None
    except (URLError, OSError, ValueError):
        raise ProbeError("Could not obtain a Spotify OAuth token. Check network access.") from None
    if not isinstance(result, dict) or not result.get("access_token"):
        raise ProbeError("Spotify returned an invalid OAuth response.")
    return result


def save_credentials(path, credentials):
    """Atomic credential updates with owner-only permissions, including rotations."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        with open(temporary, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
            json.dump(credentials, output)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def access_token(path=TOKEN_FILE):
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    from_environment = bool(client_id or refresh)
    if from_environment:
        if not client_id or not refresh:
            raise ProbeError("Set both SPOTIFY_CLIENT_ID and SPOTIFY_REFRESH_TOKEN.")
    else:
        try:
            stored = json.loads(Path(path).read_text())
            client_id, refresh = stored["client_id"], stored["refresh_token"]
        except (OSError, ValueError, TypeError, KeyError):
            raise ProbeError("Run python3 spotify_oauth.py to authorize playlist access first.") from None
    result = token_request({"grant_type": "refresh_token", "refresh_token": refresh,
                            "client_id": client_id})
    rotated = result.get("refresh_token", refresh)
    if rotated != refresh:
        if from_environment:
            # Never lose a new credential in an ephemeral runner or print it.
            raise ProbeError("Spotify rotated the OAuth refresh token. Reauthorize locally and update the GitHub secret.")
        save_credentials(path, {"client_id": client_id, "refresh_token": rotated})
    return result["access_token"]


def authorize(client_id, path=TOKEN_FILE):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    callback = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # OAuth callback URLs contain authorization codes.

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            valid = url.path == "/callback" and secrets.compare_digest(query.get("state", [""])[0], state)
            if valid:
                callback.update(query)
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Return to your terminal." if valid else b"Invalid authorization callback.")

    url = "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": state,
        "code_challenge_method": "S256", "code_challenge": challenge,
    })
    with HTTPServer(("127.0.0.1", 8765), Handler) as server:
        server.timeout = 1
        print("Opening Spotify authorization. If needed, open this URL locally:\n" + url)
        webbrowser.open(url)
        deadline = time.monotonic() + 180
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    if not callback.get("code") or callback.get("error"):
        raise ProbeError("Spotify authorization was denied or timed out.")
    result = token_request({"grant_type": "authorization_code", "code": callback["code"][0],
                            "redirect_uri": REDIRECT_URI, "client_id": client_id,
                            "code_verifier": verifier})
    if not result.get("refresh_token"):
        raise ProbeError("Spotify did not issue a refresh token.")
    save_credentials(path, {"client_id": client_id, "refresh_token": result["refresh_token"]})
    print("Authorized. Saved credentials to the ignored .spotify-oauth.json file.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default=os.environ.get("SPOTIFY_CLIENT_ID"))
    args = parser.parse_args()
    try:
        client_id = args.client_id or input("Spotify developer app client ID: ").strip()
        if not client_id:
            parser.error("Supply your developer app client ID.")
        authorize(client_id)
        return 0
    except ProbeError as error:
        print(str(error), file=sys.stderr)
    except OSError:
        print("Could not open the local callback port or save OAuth credentials.", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Authorization cancelled.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
