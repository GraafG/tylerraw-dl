# tylerraw-dl

Download your Tyler Raw videos so you can watch them offline.

Requires an active Tyler Raw membership with access to the member videos.

## How does it work?

Tyler Raw serves videos through Cloudflare Stream behind a logged-in web app. This tool uses CloakBrowser/Playwright to:

1. Open Tyler Raw with a persistent browser profile
2. Log in when needed
3. Fetch the `/api/content` metadata feed
4. Open each watch page to capture the signed Cloudflare Stream manifest URL
5. Download the video with `yt-dlp`

Videos are named:

```text
YYYY-MM-DD - Video Title.mp4
```

Metadata is saved without comments:

```text
metadata.json
```

## Requirements

- **Python 3.11+**
- **A Tyler Raw account** with access to the videos
- **A Chromium profile** for persistent login, or credentials in `.env`

## Installation

1. Clone the repository and install dependencies:

   ```powershell
   git clone https://github.com/GraafG/tylerraw-dl.git
   cd tylerraw-dl
   pip install -r requirements.txt
   ```

2. Copy the example configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Fill in `.env`.

## Usage

Download all videos:

```powershell
python -u download_videos.py
```

Download only a limited number:

```powershell
python -u download_videos.py --limit 5
```

Refresh metadata only:

```powershell
python -u download_videos.py --metadata-only
```

Run without a visible browser window:

```powershell
python -u download_videos.py --headless
```

The tool will:

- Open a browser and use the configured persistent profile
- Log in if needed
- Save video metadata in `metadata.json`
- Save watch URLs in `video_links.txt`
- Download videos into `downloads\`
- Skip previously downloaded `.mp4` files
- Resume safely when you run it again

## Configuration

All settings can be configured in `.env`:

| Variable | Description | Default |
|---|---|---|
| `TYLERRAW_EMAIL` | Tyler Raw email, only needed if profile is not logged in | empty |
| `TYLERRAW_PASSWORD` | Tyler Raw password, only needed if profile is not logged in | empty |
| `TYLERRAW_CHROME_PROFILE` | Persistent browser profile directory | `.browser-data` |
| `OUTPUT_DIR` | Folder for downloaded videos | `./downloads` |
| `YTDLP_CONCURRENT_FRAGMENTS` | Parallel HLS/DASH fragment downloads | `16` |
| `HEADLESS` | Run browser headless (`true` / `false`) | `false` |

## Speed

The main speed setting is `YTDLP_CONCURRENT_FRAGMENTS`.

```powershell
$env:YTDLP_CONCURRENT_FRAGMENTS='32'
python -u download_videos.py
```

Use `16` or `32` as a practical range. Higher values may be faster, but can also trigger throttling or more retries.

## Output

```
downloads/
  2026-02-25 - Example title.mp4
  2026-02-25 - Example title.nfo
  2026-02-25 - Example title.jpg
  2026-05-24 - Another title.mp4
  2026-05-24 - Another title.nfo
  2026-05-24 - Another title.jpg
```

Each video gets:

- `.mp4` — the video
- `.nfo` — Kodi/Jellyfin metadata (title, description, date, studio, duration)
- `.jpg` — thumbnail / poster art

## Jellyfin

Add `downloads/` as a **Movies** library in Jellyfin. The `.nfo` files are read automatically.

To skip NFO generation:

```powershell
python -u download_videos.py --no-nfo
```

## Troubleshooting

- **Login failed** — Check your `.env`, or log in manually with the configured browser profile.
- **401 from Cloudflare Stream** — The signed manifest URL expired or was not captured. Run the script again; it opens the watch page to get a fresh signed URL.
- **Download stops halfway** — Run the script again. Completed files are skipped.
- **Browser window closes** — The script closes the browser when it exits; downloads are handled by `yt-dlp`.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Report bugs or suggest features via [GitHub Issues](../../issues).

Security issues? See [SECURITY.md](SECURITY.md).

## Related tools

Other personal-use download tools for paid subscriptions:

- [elektormagazine-dl](https://github.com/GraafG/elektormagazine-dl) — Elektor Magazine issues as PDF
- [consumentenbond-dl](https://github.com/GraafG/consumentenbond-dl) — Consumentenbond publications as PDF
- [flyaoamedia-dl](https://github.com/GraafG/flyaoamedia-dl) — FlyAOA Media flight-training videos + lesson PDFs

## Disclaimer

This tool is intended for personal use by paying Tyler Raw members to watch their own accessible videos offline. Do not share downloaded files — respect Tyler Raw's copyright and terms.

## License

MIT
