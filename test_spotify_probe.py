import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from spotify_probe import PLAYLIST_URI, ProbeError, capture_playlist, fetch_page


def page(offset, count, total=30, revision="revision-1"):
    return {
        "revisionId": revision,
        "content": {
            "pagingInfo": {"offset": offset},
            "totalCount": total,
            "items": [{
                "addedAt": {"isoString": "2026-11-09T05:00:00Z"},
                "itemV2": {"data": {
                    "uri": f"spotify:track:{index:022d}",
                    "name": f"Track {index}",
                    "artists": {"items": [{"profile": {"name": "Artist"}}]},
                }},
            } for index in range(offset, offset + count)],
        },
    }


class ProbeTests(unittest.TestCase):
    def test_all_thirty_tracks_in_source_order(self):
        offsets = []

        def read(offset):
            offsets.append(offset)
            return page(offset, min(25, 30 - offset))

        result = capture_playlist(read, PLAYLIST_URI)
        self.assertEqual(offsets, [0, 25])
        self.assertEqual(result["track_count"], 30)
        self.assertEqual([track["name"] for track in result["tracks"]],
                         [f"Track {index}" for index in range(30)])

    def test_rejects_partial_capture(self):
        with self.assertRaisesRegex(ProbeError, "pagination"):
            capture_playlist(lambda offset: page(offset, 25 if offset == 0 else 0), PLAYLIST_URI)

    def test_rejects_changed_revision(self):
        with self.assertRaisesRegex(ProbeError, "changed"):
            capture_playlist(lambda offset: page(offset, 25 if offset == 0 else 5,
                                               revision=f"revision-{offset}"), PLAYLIST_URI)

    def test_rejects_unavailable_track(self):
        data = page(0, 1, total=1)
        data["content"]["items"][0]["itemV2"]["data"] = None
        with self.assertRaises(ProbeError):
            capture_playlist(lambda offset: data, PLAYLIST_URI)

    def test_http_failure_does_not_expose_tokens(self):
        with patch("spotify_probe.urlopen", side_effect=HTTPError(
                "https://api-partner.spotify.com", 401, "secret-response", {}, None)):
            with self.assertRaisesRegex(ProbeError, "HTTP 401") as caught:
                fetch_page("secret-access", "secret-client", PLAYLIST_URI, 0)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
