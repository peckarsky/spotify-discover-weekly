"""Read a complete playlist through the experimental Spotify Pathfinder API."""

import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://api-partner.spotify.com/pathfinder/v2/query"
PLAYLIST_URI = "spotify:playlist:37i9dQZEVXcFNfcEeFLHMw"
QUERY_HASH = "21ea11a6ac75a5f3e01fd365d29c18e0a4a8b7dc09f89bbb5d73910190c6acaf"


class ProbeError(Exception):
    """An error safe to display without leaking response bodies or credentials."""


def fetch_page(access_token, client_token, uri, offset, query_hash=QUERY_HASH):
    payload = {
        "variables": {
            "uri": uri,
            "offset": offset,
            "limit": 25,
            "enableWatchFeedEntrypoint": True,
            "includeEpisodeContentRatingsV2": True,
        },
        "operationName": "fetchPlaylist",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}},
    }
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + access_token.removeprefix("Bearer ").strip(),
        "client-token": client_token.strip(),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "app-platform": "WebPlayer",
        "Origin": "https://open.spotify.com",
        "Referer": "https://open.spotify.com/",
        "User-Agent": "Mozilla/5.0",
    }, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        hints = {
            401: "The bearer token may have expired. Supply fresh credentials.",
            403: "Spotify rejected this session or client token.",
            429: "Spotify rate-limited the request. Try again later.",
        }
        raise ProbeError(f"Spotify HTTP {error.code}. " + hints.get(error.code, "Request failed.")) from None
    except URLError:
        raise ProbeError("Could not reach Spotify. Check network access and DNS.") from None
    except (ValueError, TimeoutError, OSError):
        raise ProbeError("Could not read a valid JSON response from Spotify.") from None
    if not isinstance(result, dict) or result.get("errors"):
        raise ProbeError("Spotify returned a GraphQL error. Credentials or the persisted query may need updating.")
    try:
        playlist = result["data"]["playlistV2"]
        if playlist.get("__typename") != "Playlist" or playlist["uri"] != uri:
            raise ProbeError("Spotify did not return the requested playlist.")
        return playlist
    except (KeyError, TypeError, AttributeError):
        raise ProbeError("Unexpected Pathfinder response structure.") from None


def capture_playlist(page_reader, uri):
    """Reject incomplete captures and revisions that change during pagination."""
    offset = 0
    tracks = []
    revision = None
    total = None
    while True:
        page = page_reader(offset)
        try:
            content = page["content"]
            items = content["items"]
            page_total = content["totalCount"]
            page_revision = page["revisionId"]
            actual_offset = content["pagingInfo"]["offset"]
        except (KeyError, TypeError):
            raise ProbeError("Missing playlist contents, revision, or pagination metadata.") from None
        if (not isinstance(items, list) or type(page_total) is not int
                or page_total < 1 or page_total > 10000 or not page_revision):
            raise ProbeError("Invalid or empty playlist contents.")
        if offset == 0:
            total, revision = page_total, page_revision
        if page_total != total or page_revision != revision:
            raise ProbeError("Playlist changed while reading. Retry the complete capture.")
        if actual_offset != offset or not items or offset + len(items) > total:
            raise ProbeError("Incomplete or inconsistent playlist pagination.")
        for item in items:
            try:
                track = item["itemV2"]["data"]
                track_uri = track["uri"]
                if not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", track_uri):
                    raise ProbeError("A playlist item has no usable track URI. Capture aborted.")
                tracks.append({
                    "uri": track_uri,
                    "name": track["name"],
                    "artists": [artist["profile"]["name"] for artist in track["artists"]["items"]],
                    "added_at": (item.get("addedAt") or {}).get("isoString"),
                })
            except (KeyError, TypeError, AttributeError):
                raise ProbeError("A playlist item is unavailable or has an unexpected structure.") from None
        offset += len(items)
        if offset == total:
            return {
                "source_uri": uri,
                "source_revision": revision,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "track_count": total,
                "tracks": tracks,
            }


def web_credentials():
    access_token = os.environ.get("SPOTIFY_WEB_ACCESS_TOKEN")
    client_token = os.environ.get("SPOTIFY_CLIENT_TOKEN")
    if not sys.stdin.isatty() and (not access_token or not client_token):
        raise ProbeError("Set SPOTIFY_WEB_ACCESS_TOKEN and SPOTIFY_CLIENT_TOKEN in noninteractive runs.")
    return (
        access_token or getpass.getpass("Spotify web bearer token (hidden): "),
        client_token or getpass.getpass("Spotify client token (hidden): "),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist-uri", default=PLAYLIST_URI)
    parser.add_argument("--query-hash", default=QUERY_HASH)
    parser.add_argument("--output", type=Path, help="Optional JSON capture; existing files are never overwritten")
    args = parser.parse_args()
    if not re.fullmatch(r"spotify:playlist:[A-Za-z0-9]{22}", args.playlist_uri):
        parser.error("--playlist-uri must be a Spotify playlist URI")
    if not re.fullmatch(r"[a-f0-9]{64}", args.query_hash):
        parser.error("--query-hash must contain 64 lowercase hex characters")
    try:
        access_token, client_token = web_credentials()
        capture = capture_playlist(
            lambda offset: fetch_page(access_token, client_token, args.playlist_uri, offset, args.query_hash),
            args.playlist_uri,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x") as output:
                json.dump(capture, output, indent=2)
                output.write("\n")
        print(f"Read {capture['track_count']} tracks successfully. No Spotify playlists were modified.")
        return 0
    except ProbeError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Could not save capture. Check permissions and choose a new output filename.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Probe cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
