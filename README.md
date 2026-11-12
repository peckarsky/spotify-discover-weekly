# Discover Weekly archives

Save a separate private Spotify playlist each week, preserving the source's track
order and duplicates. For example: `Discover Weekly — 2026-11-09`.

The source reader uses Spotify's internal Pathfinder endpoint. A GitHub-hosted
read of all 30 Discover Weekly tracks and private playlist creation have been
verified with the configured account. Unit tests cover
pagination, archive recovery, repeat runs, and credential handling.

## Save the capture you already have

Python 3.10+ is sufficient for this path. No dependencies are needed.

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
   The app owner needs Spotify Premium in development mode. Add this exact redirect URI:
   `http://127.0.0.1:8765/callback`. Enable Web API access and ensure your account
   has access to the app under its user management settings.
2. Authorize your app:

   ```sh
   python3 spotify_oauth.py
   ```

   Enter its client ID, then approve in your browser. The helper uses PKCE, so no
   client secret is required. It requests `playlist-read-private` and
   `playlist-modify-private` and saves refresh credentials to
   `.spotify-oauth.json` with owner-only permissions. Git ignores this file.
3. Preview, then create the dated private playlist:

   ```sh
   python3 spotify_archive.py --capture captures/discover-weekly.json
   python3 spotify_archive.py --capture captures/discover-weekly.json --apply
   ```

   The first command makes no network requests. The second creates the playlist,
   adds the exact track URIs, verifies the order, and prints the playlist URL.

## Read fresh tracks

You can still use copied web-player credentials with hidden prompts:

```sh
python3 spotify_probe.py --output captures/another-capture.json
python3 spotify_archive.py --apply
```

The probe reads and optionally saves JSON. The archiver reads fresh source tracks
and creates a playlist only when `--apply` is present. Neither saves the pasted
credentials. Existing capture files are never overwritten.

For noninteractive token-based reads, set `SPOTIFY_WEB_ACCESS_TOKEN` and
`SPOTIFY_CLIENT_TOKEN`. These are separate from your developer app's OAuth
credentials. You cannot assume developer-app OAuth tokens work with Pathfinder.

## Test browser-based credential renewal

Copying short-lived bearer tokens into Actions secrets would require repeated
manual updates. The experimental browser path loads the real Spotify web player
with your `sp_dc` session cookie and observes the credentials it sends to
Pathfinder. It does not reproduce Spotify's internal token-generation algorithm.

Install Playwright and Chromium inside a local virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

In your signed-in Spotify web player, open DevTools, then Application, Cookies,
and `https://open.spotify.com`. Copy the **value** of `sp_dc`. Run:

```sh
python spotify_archive.py --browser
```

Paste the cookie at the hidden prompt. The script opens headless Chromium, reads
fresh tracks, validates their timestamps and count, then stops without writing.
For troubleshooting locally, try `--browser --headed` to show the browser.

After that succeeds, test creation and a repeat run:

```sh
python spotify_archive.py --browser --apply
```

Repeat the dry run later, after the previously copied web tokens have expired,
to confirm you can obtain new credentials from the session. The cookie itself
can expire or be revoked. Refresh it when Spotify requires another login.

## GitHub Actions

The workflow lives in `.github/workflows/archive.yml`. Push this project to the
default branch of your GitHub repository. Under Settings, Secrets and variables,
Actions, add these repository **secrets**:

| Secret | Value |
| --- | --- |
| `SPOTIFY_SP_DC` | Your Spotify web-player `sp_dc` cookie value |
| `SPOTIFY_CLIENT_ID` | Your developer app's client ID |
| `SPOTIFY_REFRESH_TOKEN` | The `refresh_token` in local `.spotify-oauth.json` |

You do not need to store the web bearer token or client token. Chromium obtains
them during each run. Never commit the cookie or OAuth file. Do not paste
credentials into workflow YAML or workflow dispatch inputs.

Under Actions, run **Archive Discover Weekly** manually. Keep **dry_run** checked
for the first run. Once that succeeds, run again with **dry_run** unchecked to
create the private playlist. A completed copy from a local run counts as already
archived when you use the same Spotify account and source playlist.

To enable daily archiving at 16:17 UTC, add the repository **variable**
`ARCHIVE_SCHEDULE_ENABLED` with the value `true`. Scheduling remains disabled
until you set this variable. Daily runs allow multiple chances to capture the
weekly refresh. Keep failure notifications enabled in your GitHub notification
settings. For another account, also set the `SPOTIFY_SOURCE_URI` repository
variable to that account's Discover Weekly URI.

The runner uses Spotify playlist descriptions as persistent archive records.
It does not need a writable GitHub token, committed state, or credential-bearing
artifacts. Concurrent workflow runs are serialized. Avoid running a local apply
at the same time as Actions: Spotify does not provide atomic playlist creation
by archive key, so simultaneous writers can still create duplicates.

GitHub may disable scheduled workflows in public repositories after 60 days
without repository activity. Consider a private automation repo alongside a
public source repo, or check scheduling regularly.
[GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

## Recovery and limitations

- Each archive has a marker for its source URI and UTC week, plus a hash of its
  ordered track URIs. The date comes from source addition timestamps, not the run
  date. Missing timestamps or a mixture of weeks cause an error.
- A completed archive is left intact on repeat runs, including your later edits.
  Keep its description marker if you want the script to recognize it.
- Generated playlist descriptions stay on one line because Spotify rejects
  creation requests containing multiline descriptions.
- After an interrupted write, the script checks the pending destination and
  appends only the missing suffix. It aborts if existing tracks differ, instead
  of overwriting them. It never automatically retries an ambiguous write.
- If Spotify changes the source tracks within an already archived week, the
  script reports the conflict and preserves the previous copy. Resolve that
  week's copy manually. It resumes normally for a new week.
- Deleting an archive or removing its marker makes it eligible for recreation
  during the same source week. Once the source refreshes, previous weeks cannot
  be recovered unless you saved a capture or playlist.
- Browser renewal is experimental. Spotify can reject headless sessions or
  change its private query. Use `--query-hash` to replace the captured hash if
  needed. A successful local test does not guarantee the same session works on
  a GitHub-hosted runner.
- Spotify currently documents six-month refresh-token lifetimes for developer
  apps. Re-run `spotify_oauth.py` and update your GitHub refresh-token secret when
  required. Local refresh-token rotations are saved automatically. If Spotify
  rotates a token supplied through environment variables, the script stops and
  requests reauthorization rather than losing the replacement in an ephemeral
  runner. [Spotify token lifecycle](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens).

Spotify restricts access to algorithmic playlists for new API use cases. We use
its internal endpoint only for source reads and official OAuth for private
playlist creation. [Spotify's announcement](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api),
[current development-mode rules](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide),
[playlist creation](https://developer.spotify.com/documentation/web-api/reference/create-playlist).

## Tests

```sh
python3 -m unittest -v
```

Tests use fake Spotify responses and never create live playlists. Captures,
OAuth credentials, `.env` files, and the local virtual environment are ignored
by Git. No credentials are included in this repository's source files.
