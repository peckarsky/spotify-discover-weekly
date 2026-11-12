"""Create a dated private Discover Weekly copy. Dry-run unless --apply is supplied."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from spotify_oauth import access_token
from spotify_probe import PLAYLIST_URI, QUERY_HASH, ProbeError, capture_playlist, fetch_page, web_credentials


class SpotifyAPIError(ProbeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class SpotifyAPI:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None):
        request = Request("https://api.spotify.com/v1" + path, method=method,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            message = ""
            try:
                message = json.loads(error.read()).get("error", {}).get("message", "")
                if not isinstance(message, str):
                    message = ""
            except (ValueError, AttributeError, OSError):
                pass
            finally:
                error.close()
            message = message.replace(self.token, "[redacted]")
            message = " ".join(message.split())[:300]
            endpoint = path.split("?", 1)[0]
            raise SpotifyAPIError(error.code, f"Spotify API HTTP {error.code} during {method} {endpoint}. {message} Rerun to recover partial copies.") from None
        except (URLError, OSError, ValueError):
            # Never automatically retry a write whose server-side outcome is unknown.
            raise ProbeError("Spotify API request failed. Rerun to inspect and recover any partial copy.") from None

    def pages(self, path):
        offset = 0
        for _ in range(1000):
            page = self.request("GET", f"{path}?limit=50&offset={offset}")
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise ProbeError("Unexpected Spotify API pagination response.")
            items = page["items"]
            yield from items
            if not page.get("next"):
                return
            if not items:
                raise ProbeError("Spotify returned an incomplete page.")
            offset += len(items)
        raise ProbeError("Spotify pagination exceeded the expected limit.")


def archive_details(capture):
    try:
        source = capture["source_uri"]
        tracks = capture["tracks"]
        if (not re.fullmatch(r"spotify:playlist:[A-Za-z0-9]{22}", source)
                or not isinstance(tracks, list) or not 1 <= len(tracks) <= 10000
                or type(capture["track_count"]) is not int
                or capture["track_count"] != len(tracks)):
            raise ValueError
        uris = [track["uri"] for track in tracks]
        if any(not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", uri) for uri in uris):
            raise ValueError
        weeks = set()
        for track in tracks:
            added = datetime.fromisoformat(track["added_at"].replace("Z", "+00:00"))
            if added.tzinfo is None:
                raise ValueError
            day = added.astimezone(timezone.utc).date()
            weeks.add((day - timedelta(days=day.weekday())).isoformat())
        if len(weeks) != 1:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProbeError("Invalid capture: require complete track URIs and addition timestamps from one UTC week.") from None
    week = weeks.pop()
    key = hashlib.sha256((source + "\n" + week).encode()).hexdigest()
    digest = hashlib.sha256("\n".join(uris).encode()).hexdigest()
    marker = f"dw-archive:v1:{key}"
    record = f"{marker};sha256={digest};status="
    # Spotify rejects playlist creation when the description contains newlines.
    description = f"Discover Weekly for {week}. Source: {source} | {record}"
    return {
        "week": week, "uris": uris, "marker": marker,
        "name": f"// Archive {week}", "description": description, "record": record,
    }


def playlist_items(api, playlist_id):
    result = []
    for entry in api.pages(f"/playlists/{playlist_id}/items"):
        try:
            item = entry.get("item") or entry.get("track")
            result.append(item["uri"])
        except (TypeError, KeyError, AttributeError):
            raise ProbeError("Cannot verify a destination track. No further changes made.") from None
    return result


def archive(api, capture):
    details = archive_details(capture)
    me = api.request("GET", "/me")
    if not isinstance(me, dict) or not me.get("id"):
        raise ProbeError("Could not identify the authorized Spotify user.")
    matches = []
    for playlist in api.pages("/me/playlists"):
        if not isinstance(playlist, dict):
            raise ProbeError("Unexpected Spotify playlist response.")
        description = playlist.get("description") or ""
        if ((playlist.get("owner") or {}).get("id") == me["id"]
                and re.search(re.escape(details["marker"]) + r";sha256=[a-f0-9]{64};status=(?:pending|complete)\b", description)):
            matches.append(playlist)
    if len(matches) > 1:
        raise ProbeError("Multiple archives have the same marker. Resolve duplicate playlists manually.")
    if matches:
        playlist = matches[0]
        description = playlist.get("description") or ""
        expected_marker = details["record"]
        if re.search(re.escape(expected_marker + "complete") + r"\b", description):
            return playlist["id"], False
        if not re.search(re.escape(expected_marker + "pending") + r"\b", description):
            raise ProbeError("Source tracks changed within an already archived week. Existing archive preserved.")
    else:
        playlist = api.request("POST", "/me/playlists", {
            "name": details["name"], "public": False,
            "description": details["description"] + "pending",
        })
    playlist_id = playlist.get("id") if isinstance(playlist, dict) else None
    if not isinstance(playlist_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", playlist_id):
        raise ProbeError("Spotify returned an invalid destination playlist ID.")
    existing = playlist_items(api, playlist_id)
    uris = details["uris"]
    if existing != uris[:len(existing)]:
        raise ProbeError("Partial archive contents differ from the source. Existing playlist preserved.")
    for offset in range(len(existing), len(uris), 100):
        api.request("POST", f"/playlists/{playlist_id}/items", {"uris": uris[offset:offset + 100]})
    if playlist_items(api, playlist_id) != uris:
        raise ProbeError("Destination verification failed. Archive remains pending for inspection or retry.")
    api.request("PUT", f"/playlists/{playlist_id}", {
        "description": details["description"] + "complete",
    })
    return playlist_id, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="Use an existing capture without reading Pathfinder")
    parser.add_argument("--browser", action="store_true", help="Obtain fresh web tokens using sp_dc and Chromium")
    parser.add_argument("--headed", action="store_true", help="Show Chromium when using --browser")
    parser.add_argument("--playlist-uri", default=os.environ.get("SPOTIFY_SOURCE_URI") or PLAYLIST_URI)
    parser.add_argument("--query-hash", default=QUERY_HASH)
    parser.add_argument("--apply", action="store_true", help="Create or finish the private Spotify archive")
    args = parser.parse_args()
    if not re.fullmatch(r"spotify:playlist:[A-Za-z0-9]{22}", args.playlist_uri):
        parser.error("Supply a Spotify playlist URI.")
    if not re.fullmatch(r"[a-f0-9]{64}", args.query_hash):
        parser.error("Supply a 64-character query hash.")
    if args.capture and (args.browser or args.headed):
        parser.error("Use --capture or --browser.")
    if args.headed and not args.browser:
        parser.error("--headed requires --browser.")
    try:
        if args.capture:
            capture = json.loads(args.capture.read_text())
        else:
            if args.browser:
                from spotify_browser import browser_credentials
                web_access, client = browser_credentials(args.playlist_uri, args.headed)
            else:
                web_access, client = web_credentials()
            capture = capture_playlist(
                lambda offset: fetch_page(web_access, client, args.playlist_uri, offset, args.query_hash),
                args.playlist_uri,
            )
        details = archive_details(capture)
        print(f"Ready: {details['name']} ({len(details['uris'])} tracks).")
        if not args.apply:
            print("Dry run. Add --apply to save a private Spotify playlist.")
            return 0
        playlist_id, changed = archive(SpotifyAPI(access_token()), capture)
        print(("Saved: " if changed else "Already archived: ") + f"https://open.spotify.com/playlist/{playlist_id}")
        return 0
    except ProbeError as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError):
        print("Could not read the capture or update local credentials.", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Archive cancelled.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
