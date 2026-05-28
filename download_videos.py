"""
Tyler Raw video downloader
Uses CloakBrowser (C++ patched Chromium, humanize=True) to bypass Cloudflare,
log in via the nav Sign In modal, calls /api/content to get all video metadata,
then downloads each via Cloudflare Stream using the cf_stream_uid.
Files are named: YYYY-MM-DD - Title.mp4
Metadata (without comments) is saved to metadata.json.
"""
import asyncio
import argparse
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from cloakbrowser import launch_persistent_context_async

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

BASE_URL = "https://tylerraw.com"
VIDEOS_URL = f"{BASE_URL}/videos"
ROOT_DIR = Path(__file__).parent
USERNAME = os.getenv("TYLERRAW_EMAIL", "")
PASSWORD = os.getenv("TYLERRAW_PASSWORD", "")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT_DIR / "downloads"))
COOKIES_FILE = Path(__file__).parent / "cookies.json"
COOKIES_TXT = Path(__file__).parent / "cookies.txt"
METADATA_FILE = Path(__file__).parent / "metadata.json"
FRAGMENT_CONCURRENCY = os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "16")

CHROME_PROFILE_DIR = os.getenv("TYLERRAW_CHROME_PROFILE", str(ROOT_DIR / ".browser-data"))
HEADLESS = os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def _delay(min_ms=400, max_ms=1500):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _wait_for_cloudflare(page, timeout=30):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        title = await page.title()
        if "just a moment" not in title.lower():
            print(f"[*] Ready — {title}")
            return
        print("[*] Waiting for Cloudflare...")
        await asyncio.sleep(1)
    print("[!] CF timeout — proceeding anyway")


async def login(page):
    """Log in via the Sign In modal. Returns True if successful."""
    sign_in = page.get_by_role("button", name="Sign In").or_(
        page.get_by_role("button", name="Sign in").or_(
            page.get_by_role("link", name="Sign In")
        )
    )
    if await sign_in.count() == 0:
        print("[*] Already logged in")
        return True

    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Not logged in and credentials are not configured. "
            "Set TYLERRAW_EMAIL and TYLERRAW_PASSWORD, or log in with the Chrome profile."
        )

    print("[*] Not logged in — clicking Sign In to open modal...")
    await sign_in.first.click()

    print("[*] Waiting for login form...")
    try:
        await page.wait_for_selector("input[type='email']", timeout=10000)
    except Exception:
        print("[!] No email input — trying /login directly")
        await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60000)
        await _wait_for_cloudflare(page)
        await page.wait_for_selector("input[type='email']", timeout=10000)

    email_field = page.locator("input[type='email'], input[name='email']").first
    await email_field.click()
    await email_field.press_sequentially(USERNAME, delay=80)
    await _delay(400, 800)

    pwd_field = page.locator("input[type='password']").first
    await pwd_field.click()
    await pwd_field.press_sequentially(PASSWORD, delay=80)
    await _delay(500, 1000)

    # Press Enter to submit (triggers React form submit)
    await pwd_field.press("Enter")
    await _delay(3000, 4000)
    await _wait_for_cloudflare(page)

    if await page.get_by_role("button", name="Sign In").count():
        print("[!] Still not logged in!")
        return False

    print("[*] Login successful!")
    return True


async def collect_videos(page):
    """
    Load /videos and intercept /api/content?limit=100.
    Returns list of metadata dicts (comments field excluded).
    Also saves metadata.json.
    """
    uuid_re = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
    content_data = {}

    async def on_response(response):
        if "/api/content" in response.url and response.status == 200:
            try:
                data = await response.json()
                content_data["data"] = data
            except Exception:
                pass

    page.on("response", on_response)
    print("[*] Loading /videos page to trigger API call...")
    await page.goto(VIDEOS_URL, wait_until="domcontentloaded", timeout=60000)
    await _wait_for_cloudflare(page)
    await asyncio.sleep(4)
    page.remove_listener("response", on_response)

    raw_items = []
    if "data" in content_data:
        data = content_data["data"]
        raw_items = data if isinstance(data, list) else data.get("items", data.get("content", data.get("data", [])))
    else:
        print("[!] /api/content not intercepted — trying JS fetch...")
        result = await page.evaluate("""
            async () => {
                const r = await fetch('/api/content?limit=100', {credentials: 'include'});
                return await r.json();
            }
        """)
        if result:
            raw_items = result if isinstance(result, list) else result.get("items", result.get("content", result.get("data", [])))

    # Filter to video items with a valid UUID, strip comments field
    EXCLUDE_FIELDS = {"comments"}
    videos = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        uid = item.get("id") or item.get("uuid")
        if not uid or uid in seen or not uuid_re.match(str(uid)):
            continue
        seen.add(uid)
        clean = {k: v for k, v in item.items() if k not in EXCLUDE_FIELDS}
        # Parse date prefix from published_at
        pub = item.get("published_at") or item.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            clean["_date_prefix"] = dt.strftime("%Y-%m-%d")
        except Exception:
            clean["_date_prefix"] = "0000-00-00"
        videos.append(clean)

    # Save metadata (sorted by date)
    videos.sort(key=lambda v: v.get("_date_prefix", ""))
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    print(f"[*] Saved metadata for {len(videos)} videos → {METADATA_FILE}")

    print("[*] Video date range:", videos[0].get("_date_prefix"), "->", videos[-1].get("_date_prefix"))
    return videos


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def output_basename(video):
    uid = video.get("id", "")
    title = video.get("title", uid)
    date_prefix = video.get("_date_prefix", "0000-00-00")
    return f"{date_prefix} - {safe_filename(title)}"


def rename_existing_downloads(videos):
    """Rename already-downloaded title-only files to the new date-prefixed convention."""
    renamed = 0
    for video in videos:
        uid = video.get("id", "")
        title = video.get("title", uid)
        old_path = OUTPUT_DIR / f"{safe_filename(title)}.mp4"
        new_path = OUTPUT_DIR / f"{output_basename(video)}.mp4"
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)
            renamed += 1
    if renamed:
        print(f"[*] Renamed {renamed} existing downloads with date prefixes")


async def get_signed_stream_url(page, uuid):
    """
    Navigate to /watch/{uuid} and intercept the signed Cloudflare Stream URL (JWT-authenticated).
    Returns the first .mpd or .m3u8 URL found, or None.
    """
    stream_urls = []

    async def on_response(r):
        url = r.url
        if "cloudflarestream.com" in url and (".mpd" in url or ".m3u8" in url or "manifest" in url):
            if url not in stream_urls:
                stream_urls.append(url)

    page.on("response", on_response)
    try:
        await page.goto(f"{BASE_URL}/watch/{uuid}", wait_until="domcontentloaded", timeout=60000)
        await _wait_for_cloudflare(page)
        await _delay(3000, 5000)
    finally:
        page.remove_listener("response", on_response)

    # Prefer .mpd (DASH) — contains both video+audio
    mpd = [u for u in stream_urls if ".mpd" in u]
    if mpd:
        return mpd[0]
    m3u8 = [u for u in stream_urls if ".m3u8" in u]
    if m3u8:
        return m3u8[0]
    if stream_urls:
        return stream_urls[0]
    return None


def write_nfo(video, out_path):
    """Write a Kodi/Jellyfin-style .nfo file alongside the video."""
    nfo_path = out_path.with_suffix(".nfo")
    if nfo_path.exists():
        return  # already written

    title = video.get("title", "")
    plot = video.get("description", "") or ""
    premiered = video.get("_date_prefix", "")
    year = premiered[:4] if premiered else ""
    uid = video.get("id", "")
    duration = video.get("duration_display", "")
    views = video.get("views", "")
    likes = video.get("likes", "")
    watch_url = f"{BASE_URL}/watch/{uid}"

    # Build a user-readable plot; append stats if description is empty
    if not plot.strip() and (views or likes):
        parts = []
        if views:
            parts.append(f"{views} views")
        if likes:
            parts.append(f"{likes} likes")
        plot = ", ".join(parts)

    nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>{_xml(title)}</title>
  <originaltitle>{_xml(title)}</originaltitle>
  <plot>{_xml(plot)}</plot>
  <year>{_xml(year)}</year>
  <premiered>{_xml(premiered)}</premiered>
  <dateadded>{_xml(premiered)}</dateadded>
  <studio>Tyler Raw</studio>
  <genre>Documentary</genre>
  <tag>Tyler Raw</tag>
  <uniqueid type="tylerraw" default="true">{_xml(uid)}</uniqueid>
  <source>{_xml(watch_url)}</source>{f'{chr(10)}  <runtime>{_xml(duration)}</runtime>' if duration else ''}
</movie>
"""
    nfo_path.write_text(nfo, encoding="utf-8")


def _xml(value):
    """Escape a string for XML content."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def download_thumb(video, out_path):
    """Download the thumbnail as a poster image for Jellyfin (no auth needed for CF thumbnails)."""
    thumb_path = out_path.with_suffix(".jpg")
    if thumb_path.exists():
        return

    thumb_url = video.get("cf_thumbnail_url", "")
    if not thumb_url:
        return
    if thumb_url.startswith("/"):
        thumb_url = BASE_URL + thumb_url

    try:
        import urllib.request
        req = urllib.request.Request(thumb_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            thumb_path.write_bytes(resp.read())
        print(f"  [thumb] Saved {thumb_path.name}")
    except Exception:
        pass  # thumbnail is optional


def cookies_to_netscape(cookies, filepath):
    """Convert a list of Playwright cookie dicts to a Netscape cookie file for yt-dlp."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expires = int(c.get("expires", 0)) if c.get("expires") and c["expires"] > 0 else 0
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    with open(filepath, "w") as f:
        f.write("\n".join(lines) + "\n")


def download_with_ytdlp(url, cookies_path, output_dir, title="video"):
    """Download via yt-dlp — stream URLs are direct CDN (no CF protection)."""
    safe_title = safe_filename(title)
    out_path = output_dir / f"{safe_title}.mp4"

    # Skip if already downloaded
    if out_path.exists() and out_path.stat().st_size > 1_000_000:
        print(f"  [skip] Already exists: {out_path.name}")
        return True

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--output", str(output_dir / f"{safe_title}.%(ext)s"),
        "--no-playlist",
        "--concurrent-fragments", FRAGMENT_CONCURRENCY,
        "--retries", "5",
        "--fragment-retries", "10",
        "--user-agent", USER_AGENT,
        url,
    ]
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


async def main():
    parser = argparse.ArgumentParser(description="Download Tyler Raw videos for offline personal use.")
    parser.add_argument("--headless", action="store_true", help="Run browser without a visible window.")
    parser.add_argument("--limit", type=int, default=0, help="Download at most N videos.")
    parser.add_argument("--metadata-only", action="store_true", help="Only refresh metadata and video_links.txt.")
    parser.add_argument("--no-nfo", action="store_true", help="Skip writing .nfo and .jpg files for Jellyfin.")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    print("=== Tyler Raw Video Downloader ===\n")

    context = await launch_persistent_context_async(
        user_data_dir=CHROME_PROFILE_DIR,
        headless=args.headless or HEADLESS,
        humanize=not (args.headless or HEADLESS),
    )
    page = context.pages[0] if context.pages else await context.new_page()

    # Go to homepage and log in
    print("[*] Navigating to homepage...")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    await _wait_for_cloudflare(page)
    await _delay(2000, 3000)
    await login(page)

    # Collect all video metadata
    videos = await collect_videos(page)
    if args.limit > 0:
        videos = videos[:args.limit]

    if not videos:
        print("[!] No videos found.")
        await context.close()
        return

    # Save watch URL list
    links_file = Path(__file__).parent / "video_links.txt"
    with open(links_file, "w") as f:
        f.write("\n".join(f"{BASE_URL}/watch/{v['id']}" for v in videos))
    print(f"[*] Saved {len(videos)} watch URLs to {links_file}")

    # Save cookies (needed for authenticated DASH segments if any)
    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    cookies_to_netscape(cookies, COOKIES_TXT)
    print(f"[*] Saved {len(cookies)} cookies")
    rename_existing_downloads(videos)

    if args.metadata_only:
        await context.close()
        print("[*] Metadata refreshed; no downloads requested.")
        return

    # For each video: navigate in browser to get fresh signed stream URL, then download
    success = 0
    failed = []

    for i, video in enumerate(videos, 1):
        uid = video.get("id", "")
        title = video.get("title", uid)
        date_prefix = video.get("_date_prefix", "0000-00-00")
        filename = output_basename(video)
        watch_url = f"{BASE_URL}/watch/{uid}"
        out_path = OUTPUT_DIR / f"{filename}.mp4"

        print(f"\n[{i}/{len(videos)}] {date_prefix} — {title[:60]}")

        if out_path.exists() and out_path.stat().st_size > 1_000_000:
            print("  [skip] Already exists")
            if not args.no_nfo:
                write_nfo(video, out_path)
                download_thumb(video, out_path)
            success += 1
            continue

        # Navigate to watch page to get JWT-signed stream URL
        stream_url = await get_signed_stream_url(page, uid)

        # Refresh cookies after navigation
        cookies = await context.cookies()
        cookies_to_netscape(cookies, COOKIES_TXT)

        if not stream_url:
            print("  [!] No stream URL intercepted — skipping")
            failed.append(watch_url)
            continue

        print(f"  [stream] {stream_url[:90]}...")
        ok = download_with_ytdlp(stream_url, COOKIES_TXT, OUTPUT_DIR, title=filename)
        if ok:
            success += 1
            if not args.no_nfo:
                write_nfo(video, out_path)
                download_thumb(video, out_path)
            print("  ✓ Downloaded")
        else:
            failed.append(watch_url)
            print("  ✗ Failed")

    await context.close()

    print(f"\n=== Done: {success}/{len(videos)} downloaded ===")
    if failed:
        failed_file = Path(__file__).parent / "failed_downloads.txt"
        with open(failed_file, "w") as f:
            f.write("\n".join(failed))
        print(f"[!] {len(failed)} failed — saved to {failed_file}")


if __name__ == "__main__":
    asyncio.run(main())
