"""
fallback_extractor.py
=====================
سیستم fallback برای استخراج لینک ویدیو وقتی yt-dlp شکست می‌خوره.

مراحل:
  1. HTML ساده (requests) → <video>/<source> tags
  2. iframe embed → همین فرآیند روی src iframe
  3. JSON/JS embedded در <script> tags
  4. regex روی کل صفحه برای URL مستقیم ویدیو
  5. (اختیاری) Playwright headless → network requests + DOM

استفاده:
  from fallback_extractor import extract_fallback_video

  url = await extract_fallback_video("https://example.com/video-page")
  # None اگه همه روش‌ها شکست بخورن
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from pydoc import html
import re
import tempfile
import time
import urllib.parse
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  ثابت‌ها
# ══════════════════════════════════════════════════════
TIMEOUT_SECONDS: int = 20
MAX_IFRAME_DEPTH: int = 2   # چند سطح iframe دنبال می‌کنیم
PLAYWRIGHT_ENABLED: bool = os.getenv("FALLBACK_PLAYWRIGHT", "1") == "1"
PLAYWRIGHT_TIMEOUT_MS: int = 15_000

DIRECT_VIDEO_PATTERN = re.compile(
    r'https?://[^\s\'"<>]+\.(?:mp4|webm|m3u8|mpd|mov|avi|mkv|flv|ts)'
    r'(?:\?[^\s\'"<>]*)?',
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ══════════════════════════════════════════════════════
#  ابزارهای کمکی
# ══════════════════════════════════════════════════════

def _resolve_url(base: str, href: str) -> str:
    """تبدیل URL نسبی به مطلق."""
    if href.startswith("//"):
        scheme = urllib.parse.urlsplit(base).scheme
        return f"{scheme}:{href}"
    if href.startswith("http"):
        return href
    return urllib.parse.urljoin(base, href)


def _pick_best_video_url(urls: list[str]) -> Optional[str]:
    """
    از لیست URL، بهترین رو انتخاب می‌کنه:
    اولویت: mp4 > webm > m3u8 > بقیه
    """
    priority = ["mp4", "webm", "m3u8", "mpd", "mov", "avi", "mkv", "flv", "ts"]
    buckets: dict[str, list[str]] = {ext: [] for ext in priority}
    for u in urls:
        ext = u.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext in buckets:
            buckets[ext].append(u)
    for ext in priority:
        if buckets[ext]:
            return buckets[ext][0]
    return urls[0] if urls else None


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """دریافت HTML صفحه با timeout."""
    try:
        async with session.get(
            url,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            ssl=False,
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("Fallback HTTP %s for %s", resp.status, url)
                return None
            ct = resp.headers.get("Content-Type", "")
            if "html" not in ct and "text" not in ct:
                # ممکنه خودش یه فایل ویدیو باشه
                if any(ext in ct for ext in ("video", "octet-stream")):
                    return f"__direct__{url}"
                return None
            return await resp.text(errors="replace")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Fallback fetch error for %s: %s", url, exc)
        return None


# ══════════════════════════════════════════════════════
#  مرحله ۱ — استخراج از HTML (بدون JS)
# ══════════════════════════════════════════════════════

def _extract_from_html(html: str, base_url: str) -> list[str]:
    """
    استخراج URL ویدیو از:
      - <video src=...> / <video><source src=...>
      - meta og:video / twitter:player:stream
      - <link rel=...> برای video
      - JSON/JS در <script> tags
      - regex کل صفحه
    (iframe‌ها جداگانه handle میشن)
    """
    found: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    # ── <video> و <source> ─────────────────────────
    for tag in soup.find_all(["video", "source"]):
        for attr in ("src", "data-src", "data-video-url", "data-url",
                     "data-video-src", "data-mp4", "data-stream"):
            val = tag.get(attr)
            if not isinstance(val, str):
                continue
            val = val.strip()
            if val and not val.startswith("blob:"):
                found.append(_resolve_url(base_url, val))
        
    # ── meta tags — og:video / twitter:stream ───────
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        content = meta.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if prop in (
            "og:video", "og:video:url", "og:video:secure_url",
            "twitter:player:stream", "twitter:player",
        ):
            if content.startswith("http"):
                found.append(content)

    # ── <script> — JSON یا JS embedded ──────────────
    for script in soup.find_all("script"):
        content = script.string or ""
        if not content:
            continue

        # JSON-LD
        if script.get("type") in ("application/json", "application/ld+json"):
            try:
                data = json.loads(content)
                _walk_json_for_video(data, base_url, found)
            except json.JSONDecodeError:
                pass
            continue

        # JSON object داخل JS
        # script با id خاص (مثل Pinterest initial-data)
        script_id = script.get("id", "")
        if script_id:
            try:
                data = json.loads(content)
                _walk_json_for_video(data, base_url, found)
            except json.JSONDecodeError:
                pass
        # JSON object داخل JS — الگوی بیشتر
        for json_str in re.findall(
            r'\{[^{}]{10,}(?:mp4|m3u8|webm|hlsUrl|videoUrl|video_url'
            r'|playbackUrl|streamUrl|mediaUrl|contentUrl)[^{}]{0,800}\}',
            content, re.DOTALL | re.IGNORECASE
        ):
            try:
                data = json.loads(json_str)
                _walk_json_for_video(data, base_url, found)
            except json.JSONDecodeError:
                pass
         # آرایه JSON داخل JS
        for arr_str in re.findall(r'\[[^\[\]]{20,}(?:mp4|m3u8|webm)[^\[\]]{0,400}\]',
                                   content, re.DOTALL | re.IGNORECASE):
            try:
                data = json.loads(arr_str)
                _walk_json_for_video(data, base_url, found)
            except json.JSONDecodeError:
                pass
        # regex روی محتوای script
        found.extend(DIRECT_VIDEO_PATTERN.findall(content))

    # ── regex روی کل HTML ───────────────────────────
    found.extend(DIRECT_VIDEO_PATTERN.findall(html))

    # حذف تکراری‌ها + blob URL
    seen: set[str] = set()
    clean: list[str] = []
    for u in found:
        u = u.strip(" '\"\\")
        if u and not u.startswith("blob:") and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


def _walk_json_for_video(obj, base_url: str, result: list[str]) -> None:
    """جستجوی بازگشتی در JSON برای URL ویدیو."""
    video_keys = {
        "url", "src", "source", "videoUrl", "video_url", "file",
        "hlsUrl", "hls_url", "mp4Url", "mp4", "playbackUrl",
        "streamUrl", "mediaUrl", "directUrl",
    }
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in video_keys and isinstance(v, str) and v.startswith("http"):
                result.append(_resolve_url(base_url, v))
            else:
                _walk_json_for_video(v, base_url, result)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_video(item, base_url, result)


def _extract_iframes(html: str, base_url: str) -> list[str]:
    """استخراج src از iframe‌ها."""
    soup = BeautifulSoup(html, "html.parser")
    srcs = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src")
        if src is None:
            continue
        if isinstance(src, list):
            src = " ".join(src)
        src = str(src).strip()
        if not src:
            continue
        if src.startswith("http"):
            srcs.append(src)
        else:
            srcs.append(_resolve_url(base_url, src))
    return srcs


# ══════════════════════════════════════════════════════
#  مرحله ۲ — Playwright (headless)
# ══════════════════════════════════════════════════════

async def _extract_with_playwright(url: str) -> list[str]:
    """
    رندر صفحه با Playwright و capture شبکه + DOM.
    اگه playwright نصب نباشه، لیست خالی برمیگردونه.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        logger.debug("Playwright not installed — skipping headless fallback")
        return []

    captured: list[str] = []
    is_youtube = "youtube.com" in url or "youtu.be" in url
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
            )

            # مخفی کردن webdriver flag از سایت‌ها
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                window.chrome = {runtime: {}};
            """)

            page = await context.new_page()

            # ─ capture network responses ─
            async def on_response(response) -> None:
                resp_url: str = response.url
                ct = response.headers.get("content-type", "")
                # فایل‌های ویدیویی مستقیم
                if DIRECT_VIDEO_PATTERN.search(resp_url):
                    captured.append(resp_url)
                # stream manifest
                if any(ext in resp_url for ext in (".m3u8", ".mpd", "/manifest")):
                    captured.append(resp_url)
                # content-type ویدیو
                if "video" in ct and resp_url.startswith("http"):
                    captured.append(resp_url)
            page.on("response", on_response)
            try:
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception:
                pass
            # YouTube — کلیک روی دکمه consent اگه ظاهر شد
            if is_youtube:
                try:
                    consent_btn = page.locator(
                        "button:has-text('Accept'), button:has-text('Agree'), "
                        "[aria-label*='Accept'], form[action*='consent'] button"
                    )
                    if await consent_btn.count() > 0:
                        await consent_btn.first.click()
                except Exception:
                    pass
            # صبر برای لود شدن ویدیو
            wait_ms = 8000 if is_youtube else 4000
            await page.wait_for_timeout(wait_ms)
            # ─ DOM video elements ─
            video_srcs = await page.evaluate("""() => {
                const els = [...document.querySelectorAll('video, source')];
                return els.map(e => e.src || e.currentSrc || e.getAttribute('src') || '').filter(Boolean);
            }""")
            captured.extend(v for v in video_srcs if v.startswith("http") and not v.startswith("blob:"))

            # ─ HTML بعد از رندر ─
            html = await page.content()
            captured.extend(_extract_from_html(html, url))

            await browser.close()

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Playwright fallback error: %s", exc)

    logger.debug("[Playwright] Captured %d URLs", len(captured))
    return captured


# ══════════════════════════════════════════════════════
#  تابع اصلی — extract_fallback_video
# ══════════════════════════════════════════════════════

async def extract_fallback_video(url: str) -> Optional[str]:
    """
    تلاش برای پیدا کردن URL مستقیم ویدیو از یک صفحه وب.

    Returns:
        str  — URL مستقیم ویدیو (قابل دانلود)
        None — اگه همه روش‌ها شکست بخورن
    """
    logger.info("[Fallback] Starting extraction for: %s", url)

    async with aiohttp.ClientSession() as session:

        # ── مرحله ۱: HTML ساده ──────────────────────
        html = await _fetch_html(session, url)

        if html and html.startswith("__direct__"):
            direct = html[len("__direct__"):]
            logger.info("[Fallback] Direct video URL detected: %s", direct)
            return direct

        if html:
            candidates = _extract_from_html(html, url)
            if candidates:
                best = _pick_best_video_url(candidates)
                if best:
                    logger.info("[Fallback] Found via static HTML: %s", best)
                    return best

            # ── مرحله ۲: iframe‌ها ───────────────────
            iframes = _extract_iframes(html, url)
            for depth, iframe_url in enumerate(iframes[:3]):
                if depth >= MAX_IFRAME_DEPTH:
                    break
                logger.debug("[Fallback] Checking iframe: %s", iframe_url)
                iframe_html = await _fetch_html(session, iframe_url)
                if iframe_html:
                    candidates = _extract_from_html(iframe_html, iframe_url)
                    if candidates:
                        best = _pick_best_video_url(candidates)
                        if best:
                            logger.info("[Fallback] Found via iframe: %s", best)
                            return best

    # ── مرحله ۳: Playwright (headless) ──────────────
    if PLAYWRIGHT_ENABLED:
        logger.info("[Fallback] Trying Playwright for: %s", url)
        pw_candidates = await _extract_with_playwright(url)
        if pw_candidates:
            best = _pick_best_video_url(pw_candidates)
            if best:
                logger.info("[Fallback] Found via Playwright: %s", best)
                return best

    logger.warning("[Fallback] All methods exhausted for: %s", url)
    return None


# ══════════════════════════════════════════════════════
#  دانلود URL مستقیم (برای استفاده بعد از fallback)
# ══════════════════════════════════════════════════════

async def download_direct_url(video_url: str) -> Optional[str]:
    """
    دانلود URL مستقیم ویدیو و ذخیره در فایل موقت.

    Returns:
        str  — مسیر فایل دانلودشده
        None — در صورت خطا
    """
    uid = int(time.time() * 1000)
    ext = video_url.split("?")[0].rsplit(".", 1)[-1].lower() or "mp4"
    if ext not in ("mp4", "webm", "mov", "avi", "mkv", "m3u8", "ts", "flv"):
        ext = "mp4"

    out_path = os.path.join(tempfile.gettempdir(), f"fb_{uid}.{ext}")

    # اگه m3u8/mpd باشه از yt-dlp استفاده می‌کنیم
    if ext in ("m3u8", "mpd"):
        return await _download_with_ytdlp(video_url, uid)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                video_url,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=120),
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    logger.error("[Fallback Download] HTTP %s", resp.status)
                    return None
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        f.write(chunk)
        logger.info("[Fallback Download] Saved to: %s", out_path)
        return out_path
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("[Fallback Download] Error: %s", exc)
        return None


async def _download_with_ytdlp(stream_url: str, uid: int) -> Optional[str]:
    """دانلود stream (m3u8/mpd) با yt-dlp."""
    import functools
    import yt_dlp

    output_template = os.path.join(tempfile.gettempdir(), f"fb_{uid}.%(ext)s")
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    try:
        loop = asyncio.get_running_loop()
        def _dl():
            with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
                ydl.download([stream_url])
        await loop.run_in_executor(None, _dl)
        # پیدا کردن فایل
        base = tempfile.gettempdir()
        for ext in ("mp4", "mkv", "webm"):
            candidate = os.path.join(base, f"fb_{uid}.{ext}")
            if os.path.exists(candidate):
                return candidate
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("[Fallback yt-dlp stream] Error: %s", exc)
    return None
