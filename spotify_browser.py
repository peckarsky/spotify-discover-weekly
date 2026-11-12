"""Obtain web-player credentials through an ordinary authenticated page load."""

import getpass
import os
import sys
from urllib.parse import urlsplit

from spotify_probe import ProbeError


def credentials_from_request(url, headers):
    """Only accept credentials sent to Spotify's own Pathfinder endpoint."""
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.hostname, parsed.path) != (
            "https", "api-partner.spotify.com", "/pathfinder/v2/query"):
        return None
    headers = {key.lower(): value for key, value in headers.items()}
    access = headers.get("authorization", "")
    client = headers.get("client-token", "")
    if access.startswith("Bearer ") and client:
        return access.removeprefix("Bearer "), client
    return None


def browser_credentials(uri, headed=False):
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        raise ProbeError("Install requirements-browser.txt and run: python -m playwright install chromium") from None
    cookie = os.environ.get("SPOTIFY_SP_DC")
    if not cookie and sys.stdin.isatty():
        cookie = getpass.getpass("Spotify sp_dc session cookie (hidden): ")
    if not cookie or any(char in cookie for char in "\r\n;"):
        raise ProbeError("Supply the sp_dc cookie value through SPOTIFY_SP_DC or the hidden prompt.")
    found = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            try:
                context = browser.new_context()
                context.add_cookies([{
                    "name": "sp_dc", "value": cookie,
                    "domain": ".spotify.com", "path": "/", "secure": True,
                    "httpOnly": True, "sameSite": "Lax",
                }])

                def observe(request):
                    # Do not inspect or persist unrelated browser traffic.
                    if urlsplit(request.url).hostname == "api-partner.spotify.com":
                        credentials = credentials_from_request(request.url, request.all_headers())
                        if credentials:
                            found.append(credentials)

                context.on("request", observe)
                page = context.new_page()
                page.goto("https://open.spotify.com/playlist/" + uri.rsplit(":", 1)[-1],
                          wait_until="domcontentloaded", timeout=45000)
                for _ in range(120):
                    if found:
                        return found[-1]
                    page.wait_for_timeout(250)
                raise ProbeError("No web-player credentials observed. Refresh sp_dc or try --headed locally.")
            finally:
                browser.close()
    except PlaywrightError:
        # Playwright exception messages can contain browser network headers.
        raise ProbeError("Could not load the Spotify web player. Check browser installation, session, and network.") from None
