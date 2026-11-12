import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from spotify_archive import SpotifyAPI, archive, archive_details
from spotify_browser import credentials_from_request
from spotify_oauth import access_token, save_credentials
from spotify_probe import ProbeError


PLAYLIST_ID = "A" * 22


def capture(count=30):
    return {
        "source_uri": "spotify:playlist:" + "B" * 22,
        "source_revision": "revision",
        "track_count": count,
        "tracks": [{"uri": f"spotify:track:{index:022d}",
                    "added_at": "2026-11-09T05:00:00Z"} for index in range(count)],
    }


class FakeSpotify:
    def __init__(self):
        self.playlists = []
        self.tracks = []
        self.writes = []
        self.fail_after_add = False

    def pages(self, path):
        if path == "/me/playlists":
            return iter(copy.deepcopy(self.playlists))
        if path == f"/playlists/{PLAYLIST_ID}/items":
            return iter([{"item": {"uri": uri}} for uri in self.tracks])
        raise AssertionError(path)

    def request(self, method, path, body=None):
        if (method, path) == ("GET", "/me"):
            return {"id": "owner"}
        self.writes.append((method, path, body))
        if (method, path) == ("POST", "/me/playlists"):
            playlist = {**body, "id": PLAYLIST_ID, "owner": {"id": "owner"}}
            self.playlists.append(playlist)
            return copy.deepcopy(playlist)
        if (method, path) == ("POST", f"/playlists/{PLAYLIST_ID}/items"):
            self.tracks.extend(body["uris"])
            if self.fail_after_add:
                self.fail_after_add = False
                raise ProbeError("Simulated lost response after Spotify saved the tracks")
            return {}
        if (method, path) == ("PUT", f"/playlists/{PLAYLIST_ID}"):
            self.playlists[0].update(body)
            return {}
        raise AssertionError((method, path))


class ArchiveTests(unittest.TestCase):
    def test_private_copy_and_repeat_without_writes(self):
        api = FakeSpotify()
        self.assertEqual(archive(api, capture()), (PLAYLIST_ID, True))
        self.assertFalse(api.playlists[0]["public"])
        self.assertEqual(len(api.tracks), 30)
        self.assertIn("2026-11-09", api.playlists[0]["name"])
        count = len(api.writes)
        self.assertEqual(archive(api, capture()), (PLAYLIST_ID, False))
        self.assertEqual(len(api.writes), count)

    def test_retry_after_server_accepted_add_but_response_lost(self):
        api = FakeSpotify()
        api.fail_after_add = True
        with self.assertRaises(ProbeError):
            archive(api, capture())
        self.assertEqual(len(api.tracks), 30)
        archive(api, capture())
        self.assertEqual(len(api.playlists), 1)
        self.assertEqual(len(api.tracks), 30)
        self.assertTrue(api.playlists[0]["description"].endswith("status=complete"))

    def test_resume_partial_copy_in_order(self):
        api = FakeSpotify()
        details = archive_details(capture())
        api.playlists = [{"id": PLAYLIST_ID, "owner": {"id": "owner"},
                          "description": details["description"] + "pending"}]
        api.tracks = details["uris"][:12]
        archive(api, capture())
        self.assertEqual(api.tracks, details["uris"])
        self.assertEqual(len(api.writes[0][2]["uris"]), 18)

    def test_completed_user_edits_preserved(self):
        api = FakeSpotify()
        archive(api, capture())
        api.tracks.pop()
        self.assertEqual(archive(api, capture()), (PLAYLIST_ID, False))
        self.assertEqual(len(api.tracks), 29)

    def test_changed_source_preserves_archive(self):
        api = FakeSpotify()
        archive(api, capture())
        count = len(api.writes)
        changed = capture()
        changed["tracks"].reverse()
        with self.assertRaisesRegex(ProbeError, "Source tracks changed"):
            archive(api, changed)
        self.assertEqual(len(api.writes), count)

    def test_partial_user_edits_stop_recovery(self):
        api = FakeSpotify()
        details = archive_details(capture())
        api.playlists = [{"id": PLAYLIST_ID, "owner": {"id": "owner"},
                          "description": details["description"] + "pending"}]
        api.tracks = list(reversed(details["uris"][:3]))
        with self.assertRaisesRegex(ProbeError, "contents differ"):
            archive(api, capture())
        self.assertFalse(api.writes)

    def test_hundred_item_batches_preserve_duplicates(self):
        api = FakeSpotify()
        data = capture(201)
        data["tracks"][2] = copy.deepcopy(data["tracks"][1])
        archive(api, data)
        batches = [body["uris"] for method, path, body in api.writes if path.endswith("/items")]
        self.assertEqual(list(map(len, batches)), [100, 100, 1])
        self.assertEqual(api.tracks, [track["uri"] for track in data["tracks"]])

    def test_rejects_mixed_weeks_and_incomplete_capture(self):
        data = capture()
        data["tracks"][0]["added_at"] = "2026-11-02T05:00:00Z"
        with self.assertRaises(ProbeError):
            archive_details(data)
        data = capture()
        data["track_count"] = 31
        with self.assertRaises(ProbeError):
            archive_details(data)


class CredentialTests(unittest.TestCase):
    def test_api_diagnostics_include_endpoint_and_redact_access_token(self):
        body = io.BytesIO(json.dumps({"error": {"message": "Invalid request fake-secret-token"}}).encode())
        error = HTTPError("https://api.spotify.com/v1/me/playlists", 400, "Bad request", {}, body)
        with patch("spotify_archive.urlopen", side_effect=error):
            with self.assertRaisesRegex(ProbeError, "POST /me/playlists") as caught:
                SpotifyAPI("fake-secret-token").request("POST", "/me/playlists", {})
        self.assertNotIn("fake-secret-token", str(caught.exception))
        self.assertIn("Invalid request [redacted]", str(caught.exception))

    def test_browser_headers_only_accepted_from_spotify_pathfinder(self):
        headers = {"Authorization": "Bearer fake-access", "Client-Token": "fake-client"}
        url = "https://api-partner.spotify.com/pathfinder/v2/query"
        self.assertEqual(credentials_from_request(url, headers), ("fake-access", "fake-client"))
        self.assertIsNone(credentials_from_request("https://example.com/pathfinder/v2/query", headers))
        self.assertIsNone(credentials_from_request(url, {"Authorization": "Bearer fake-access"}))

    def test_local_refresh_rotation_saved_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oauth.json"
            save_credentials(path, {"client_id": "client", "refresh_token": "old"})
            with patch.dict("os.environ", {}, clear=True), patch("spotify_oauth.token_request", return_value={
                    "access_token": "access", "refresh_token": "new"}):
                self.assertEqual(access_token(path), "access")
            self.assertEqual(json.loads(path.read_text())["refresh_token"], "new")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_environment_refresh_rotation_reports_action_without_secret(self):
        with patch.dict("os.environ", {"SPOTIFY_CLIENT_ID": "client", "SPOTIFY_REFRESH_TOKEN": "old"}), \
                patch("spotify_oauth.token_request", return_value={"access_token": "access", "refresh_token": "secret-new"}):
            with self.assertRaisesRegex(ProbeError, "rotated") as caught:
                access_token()
        self.assertNotIn("secret-new", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
