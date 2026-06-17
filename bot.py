"""
🤖 Telegram Video Downloader Bot — نسخه نهایی + Fallback Extractor
دانلود ویدیو MP4 از 1000+ سایت با انتخاب کیفیت، progress bar، صف، rate limiting و تاریخچه.
اگه yt-dlp شکست بخوره → سیستم fallback به‌صورت خودکار فعال میشه.
"""

import asyncio
import functools
import logging
import os
import sqlite3
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Optional, Any, cast
import shutil

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

from fallback_extractor import extract_fallback_video, download_direct_url

# ══════════════════════════════════════════════════════
#  تنظیمات
# ══════════════════════════════════════════════════════
BOT_TOKEN: str        = "8634241420:AAES8Fhave30QcHMP-EZuEN1jYa-mlVRY9U"
MAX_FILE_SIZE_MB: int = 300
BASE_DIR: Path        = Path(__file__).parent
DB_PATH: str          = str(BASE_DIR / "bot_history.db")
DOWNLOAD_DIR: str     = tempfile.gettempdir()
DOWNLOAD_TIMEOUT: int = 180
MAX_CONCURRENT: int   = 2
RATE_LIMIT_COUNT: int = 3
RATE_LIMIT_SECS: int  = 60

ALLOWED_USERS: set[int] = set()   # خالی = همه مجاز
BLOCKED_USERS: set[int] = set()   # بلاک‌شده‌ها


# فایل کوکی برای YouTube و سایت‌های نیازمند احراز هویت
# مسیر فایل cookies.txt رو اینجا بذار (فرمت Netscape/Mozilla)
# برای ساختن: افزونه "Get cookies.txt LOCALLY" در Chrome
COOKIES_FILE: str = os.getenv("COOKIES_FILE", str(BASE_DIR / "cookies.txt"))



QUALITY_LABELS: dict[str, str] = {
    "q_1080": "1080p",
    "q_720":  "720p",
    "q_480":  "480p",
    "q_360":  "360p",
}
def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
def _build_format(height: int) -> str:
    """فرمت مناسب بر اساس موجود بودن ffmpeg."""
    if _has_ffmpeg():
        return (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={height}]+bestaudio"
            f"/best[height<={height}][ext=mp4]"
            f"/best[height<={height}]/best[ext=mp4]/best"
        )
    # بدون ffmpeg — فقط فایل‌های از پیش merge‌شده
    return (
        f"best[height<={height}][ext=mp4]"
        f"/best[height<={height}]"
        f"/best[ext=mp4]/best"
    )

FORMAT_MAP: dict[str, str] = {
    "q_1080": _build_format(1080),
    "q_720":  _build_format(720),
    "q_480":  _build_format(480),
    "q_360":  _build_format(360),
}


# ══════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

user_pending_urls: dict[int | str, str]   = {}
user_rate_log:     dict[int, deque] = {}
_STATE: dict[str, Optional[asyncio.Semaphore]] = {"semaphore": None}

# ══════════════════════════════════════════════════════
#  SQLite
# ══════════════════════════════════════════════════════
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            url       TEXT    NOT NULL,
            quality   TEXT    NOT NULL,
            title     TEXT,
            size_mb   REAL,
            status    TEXT DEFAULT 'ok',
            ts        TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def db_save(
    user_id: int,
    url: str,
    quality: str,
    title: str,
    size_mb: float,
    status: str = "ok",
) -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO downloads (user_id,url,quality,title,size_mb,status)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, url, quality, title, round(size_mb, 2), status),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("DB save error: %s", exc)


def db_history(user_id: int, limit: int = 10) -> list[tuple]:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT ts, title, quality, size_mb, status FROM downloads "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error:
        return []

# ══════════════════════════════════════════════════════
#  دسترسی + Rate limit
# ══════════════════════════════════════════════════════
def check_access(user_id: int) -> Optional[str]:
    if user_id in BLOCKED_USERS:
        return "🚫 دسترسی شما مسدود شده."
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        return "🔒 این ربات خصوصی است."
    return None


def check_rate(user_id: int) -> bool:
    now = time.monotonic()
    bucket: deque = user_rate_log.setdefault(user_id, deque())
    while bucket and now - bucket[0] > RATE_LIMIT_SECS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_COUNT:
        return False
    bucket.append(now)
    return True

# ══════════════════════════════════════════════════════
#  کمکی‌ها
# ══════════════════════════════════════════════════════
def safe_remove(path: Optional[str]) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def safe_edit(target, text: str, **kwargs) -> None:
    try:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, **kwargs)
        else:
            await target.edit_text(text, **kwargs)
    except Exception as exc:
        logger.debug("safe_edit ignored: %s", exc)


def cleanup_user(user_id: int) -> None:
    user_pending_urls.pop(user_id, None)
    user_pending_urls.pop(f"title_{user_id}", None)

# ══════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════
async def cmd_start(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.effective_user or (update.message.from_user if update.message else None)
    if not user:
        return
    err = check_access(user.id)
    if err:
        await update.message.reply_text(err)
        return
    await update.message.reply_text(
        "👋 سلام! به ربات دانلود ویدیو خوش اومدی.\n\n"
        "📌 لینک ویدیو رو بفرست → کیفیت انتخاب کن → ویدیو MP4 بگیر!\n\n"
        "🌐 YouTube · Instagram · Twitter/X · TikTok · Vimeo و 1000+ سایت\n\n"
        "/history — تاریخچه دانلودها\n"
        "/help    — راهنما"
    )


async def cmd_help(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "📖 راهنما:\n\n"
        "1️⃣ لینک ویدیو رو بفرست\n"
        "2️⃣ کیفیت مورد نظر رو انتخاب کن\n"
        "3️⃣ صبر کن — ویدیو MP4 برات ارسال میشه ✅\n\n"
        f"⚠️ محدودیت‌ها:\n"
        f"• حداکثر حجم: {MAX_FILE_SIZE_MB} MB\n"
        f"• حداکثر {RATE_LIMIT_COUNT} درخواست در {RATE_LIMIT_SECS} ثانیه\n"
        f"• timeout: {DOWNLOAD_TIMEOUT} ثانیه"
    )
async def cmd_check(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """بررسی وضعیت محیط و ابزارهای نصب‌شده."""
    if not update.message:
        return
    lines = ["🔧 *وضعیت سیستم:*\n"]
    # yt-dlp
    try:
        import yt_dlp as _ydl
        ytdlp_version = getattr(_ydl, "__version__", "unknown")
        lines.append(f"✅ yt\\-dlp: `{ytdlp_version}`")
    except Exception:
        lines.append("❌ yt\\-dlp: نصب نیست")
    # ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        lines.append(f"✅ ffmpeg: `{ffmpeg_path}`")
    else:
        lines.append(
            "⚠️ ffmpeg: *نصب نیست*\n"
            "  → کیفیت‌ها ممکنه کار نکنن\n"
            "  → Windows: [ffmpeg.org](https://ffmpeg.org/download.html) رو نصب کن"
        )
    # Playwright
    try:
        import playwright  # type: ignore  # noqa: F401
        lines.append("✅ Playwright: نصب شده")
    except ImportError:
        lines.append("⚠️ Playwright: نصب نیست \\(fallback محدود میشه\\)")
    # aiohttp
    try:
        import aiohttp  # type: ignore  # noqa: F401
        lines.append("✅ aiohttp: نصب شده")
    except ImportError:
        lines.append("❌ aiohttp: نصب نیست → `pip install aiohttp`")
    # BeautifulSoup
    try:
        import bs4  # type: ignore  # noqa: F401
        lines.append("✅ BeautifulSoup: نصب شده")
    except ImportError:
        lines.append("❌ beautifulsoup4: نصب نیست → `pip install beautifulsoup4`")
    # cookies
    if os.path.isfile(COOKIES_FILE):
        lines.append(f"✅ cookies\\.txt: موجود")
    else:
        lines.append("⚠️ cookies\\.txt: موجود نیست \\(YouTube ممکنه کار نکنه\\)")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_history(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    err = check_access(user_id)
    if err:
        await update.message.reply_text(err)
        return
    rows = db_history(user_id)
    if not rows:
        await update.message.reply_text("📭 هنوز دانلودی نداری.")
        return
    lines = ["📋 *10 دانلود آخر:*\n"]
    for ts, title, quality, size_mb, status in rows:
        icon = "✅" if status == "ok" else "❌"
        safe_title = escape_markdown((title or "بدون عنوان")[:40], version=2)
        safe_ts = escape_markdown(str(ts[:16]), version=2)
        size_str = f"{size_mb:.1f} MB" if size_mb else "—"
        lines.append(f"{icon} `{safe_ts}` | {quality} | {size_str}\n_{safe_title}_\n")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")

# ══════════════════════════════════════════════════════
#  دریافت لینک
# ══════════════════════════════════════════════════════
async def handle_url(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message or not update.message.from_user:
        return
    user_id = update.message.from_user.id

    err = check_access(user_id)
    if err:
        await update.message.reply_text(err)
        return

    if not check_rate(user_id):
        await update.message.reply_text(
            f"⏳ کمی صبر کن! حداکثر {RATE_LIMIT_COUNT} درخواست "
            f"در {RATE_LIMIT_SECS} ثانیه مجاز است."
        )
        return

    text = update.message.text or ""
    url = text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text(
            "❌ لینک معتبر نیست.\nمثال: https://youtube.com/watch?v=..."
        )
        return

    status_msg = await update.message.reply_text("🔍 در حال بررسی لینک...")
    # تلاش برای گرفتن اطلاعات — اگه شکست خورد همچنان ادامه میدیم
    title    = "ویدیوی ناشناس"
    dur_str  = "نامشخص"
    uploader = "نامشخص"
    info_note = ""

    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, fetch_video_info, url)
        title    = str(info.get("title", "ویدیوی ناشناس"))[:60]
        duration = info.get("duration") or 0
        dur_str  = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else "نامشخص"
        uploader = str(info.get("uploader", "نامشخص"))
        if info.get("_type") == "playlist":
            count = len(info.get("entries") or [])
            info_note = f"\n📁 پلی‌لیست — {count} ویدیو \\(اولین ویدیو دانلود میشه\\)"
    except Exception as exc:
        logger.warning("fetch_video_info failed (%s) — proceeding with fallback path", exc)
        info_note = "\n⚠️ اطلاعات ویدیو در دسترس نیست — روش جایگزین فعال میشه"

    user_pending_urls[user_id] = url
    user_pending_urls[f"title_{user_id}"] = title  # type: ignore[assignment]

    keyboard = [
        [
            InlineKeyboardButton("🎬 1080p (Full HD)", callback_data="q_1080"),
            InlineKeyboardButton("📹 720p (HD)",       callback_data="q_720"),
        ],
        [
            InlineKeyboardButton("📱 480p",            callback_data="q_480"),
            InlineKeyboardButton("📺 360p",            callback_data="q_360"),
        ],
        [InlineKeyboardButton("❌ لغو",                callback_data="q_cancel")],
    ]

    title_safe    = escape_markdown(title,    version=2)
    uploader_safe = escape_markdown(uploader, version=2)
    dur_safe      = escape_markdown(dur_str,  version=2)

    await safe_edit(status_msg,
        f"🎬 *{title_safe}*\n\n"
        f"👤 کانال: {uploader_safe}\n"
        f"⏱ مدت: {dur_safe}"
        f"{info_note}\n\n"
        "📊 کیفیت دانلود رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2",
    )

# ══════════════════════════════════════════════════════
#  انتخاب کیفیت
# ══════════════════════════════════════════════════════
async def handle_quality(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()

    user_id = query.from_user.id
    choice  = query.data

    if choice == "q_cancel":
        cleanup_user(user_id)
        await safe_edit(query, "❌ عملیات لغو شد.")
        return

    url = user_pending_urls.get(user_id)
    if not url:
        await safe_edit(query, "⚠️ لینک پیدا نشد. دوباره لینک رو بفرست.")
        return

    label        = QUALITY_LABELS.get(choice or "", "نامشخص")
    title_for_db = str(
        user_pending_urls.get(f"title_{user_id}", "نامشخص")  # type: ignore[arg-type]
    )

    await safe_edit(query,
        f"⏳ در صف دانلود با کیفیت {label}...\n"
        f"(حداکثر {MAX_CONCURRENT} دانلود همزمان)"
    )

    file_path = await _run_download(query, user_id, url, choice or "", label, title_for_db, _ctx)

    if file_path:
        await _send_video(query, _ctx, user_id, url, label, title_for_db, file_path)


# ══════════════════════════════════════════════════════
#  دانلود اصلی + Fallback
# ══════════════════════════════════════════════════════
async def _run_download(
    query,
    user_id: int,
    url: str,
    choice: str,
    label: str,
    title_for_db: str,
    _context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    دانلود ویدیو با صف و timeout.
    اگه yt-dlp شکست بخوره → fallback فعال میشه.
    مسیر فایل یا None برمیگردونه.
    """
    loop = asyncio.get_running_loop()
    progress_state: dict = {"text": "", "last_edit": 0.0}

    async def push_progress(text: str) -> None:
        now = time.monotonic()
        if text != progress_state["text"] and now - progress_state["last_edit"] > 2.5:
            await safe_edit(query, text)
            progress_state["text"]      = text
            progress_state["last_edit"] = now

    def build_hook(ev_loop: asyncio.AbstractEventLoop):
        def _hook(info_dict: dict) -> None:
            if info_dict["status"] != "downloading" or ev_loop.is_closed():
                return
            pct       = info_dict.get("_percent_str",        "?%").strip()
            speed     = info_dict.get("_speed_str",           "—").strip()
            eta       = info_dict.get("_eta_str",             "—").strip()
            total_raw = info_dict.get("_total_bytes_str") or \
                        info_dict.get("_total_bytes_estimate_str") or "—"
            total = total_raw.strip()
            try:
                filled   = int(float(pct.replace("%", "")) / 10)
                progress = "█" * filled + "░" * (10 - filled)
            except ValueError:
                progress = "░░░░░░░░░░"
            msg = (
                f"⬇️ دانلود با کیفیت {label}...\n\n"
                f"{progress} {pct}\n"
                f"🚀 سرعت: {speed}\n"
                f"⏳ زمان باقی: {eta}\n"
                f"📦 حجم کل: {total}"
            )
            asyncio.run_coroutine_threadsafe(push_progress(msg), ev_loop)
        return _hook

    dl_func = functools.partial(download_video, url, choice, build_hook(loop))

    sem: asyncio.Semaphore = _STATE["semaphore"]  # type: ignore[assignment]
    async with sem:  # pylint: disable=not-async-context-manager
        # ── مرحله اول: yt-dlp ────────────────────────
        ytdlp_error: Optional[Exception] = None
        try:
            file_path = await asyncio.wait_for(
                loop.run_in_executor(None, dl_func),
                timeout=DOWNLOAD_TIMEOUT,
            )
            cleanup_user(user_id)
            return file_path

        except asyncio.TimeoutError:
            logger.warning("[yt-dlp] Timeout — activating fallback")
            await safe_edit(query,
                f"⌛ yt-dlp بیش از {DOWNLOAD_TIMEOUT // 60} دقیقه طول کشید.\n"
                "🔄 در حال امتحان روش جایگزین..."
            )
            ytdlp_error = asyncio.TimeoutError()

        except Exception as exc:
            short_err = str(exc)[:120]
            logger.warning("[yt-dlp] Failed: %s — activating fallback", short_err)
            await safe_edit(query,
                f"⚠️ روش اصلی دانلود شکست خورد:\n`{short_err}`\n\n"
                "🔄 در حال امتحان روش جایگزین...",
                parse_mode="Markdown",
            )
            ytdlp_error = exc

        # ── مرحله دوم: Fallback ───────────────────────
        if ytdlp_error is not None:
            file_path = await _run_fallback(query, user_id, url, label, title_for_db)
            cleanup_user(user_id)
            return file_path

    return None


async def _run_fallback(
    query,
    user_id: int,
    url: str,
    label: str,
    title_for_db: str,
) -> Optional[str]:
    """
    اجرای سیستم fallback:
      1. extract_fallback_video → پیدا کردن URL مستقیم
      2. download_direct_url   → دانلود فایل
    """
    await safe_edit(query,
        "🔍 جستجو برای لینک مستقیم ویدیو...\n"
        "_(ممکنه چند ثانیه طول بکشه)_",
        parse_mode="Markdown",
    )
    direct_url: Optional[str] = None

    try:
        direct_url = await asyncio.wait_for(
            extract_fallback_video(url),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.error("[Fallback] Extraction timed out for: %s", url)
        await safe_edit(query,
            "❌ روش جایگزین هم timeout شد.\n\n"
            "💡 راه‌حل‌ها:\n"
            "• Playwright رو نصب کن: `pip install playwright && playwright install chromium`\n"
            "• yt-dlp رو آپدیت کن: `pip install -U yt-dlp`"
        )
        db_save(user_id, url, label, title_for_db, 0, "fallback_timeout")
        return None
    except Exception as exc:
        logger.error("[Fallback] Extraction error: %s", exc)
        await safe_edit(query,
            f"❌ خطا در روش جایگزین:\n`{str(exc)[:150]}`",
            parse_mode="Markdown",
        )
        db_save(user_id, url, label, title_for_db, 0, "fallback_error")
        return None

    if not direct_url:
        await safe_edit(query,
            "❌ نتونستم لینک مستقیم ویدیو پیدا کنم.\n\n"
            "💡 راه‌حل‌ها:\n"
            "• Playwright نصب کن (برای سایت‌های JS-heavy مثل YouTube/Pinterest):\n"
            "  `pip install playwright && playwright install chromium`\n"
            "• yt-dlp آپدیت کن: `pip install -U yt-dlp`\n"
            "• شاید این سایت محدودیت IP داره"
        )
        db_save(user_id, url, label, title_for_db, 0, "fallback_no_url")
        return None

    logger.info("[Fallback] Found direct URL: %s", direct_url)
    await safe_edit(query, "⬇️ لینک پیدا شد، در حال دانلود...")

    try:
        file_path = await asyncio.wait_for(
            download_direct_url(direct_url),
            timeout=DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        file_path = None
        await safe_edit(query, "⌛ دانلود timeout شد. دوباره امتحان کن.")
    except Exception as exc:
        logger.error("[Fallback] Download error: %s", exc)
        await safe_edit(query,
            f"❌ خطا در دانلود:\n`{str(exc)[:150]}`",
            parse_mode="Markdown",
        )
        file_path = None

    if not file_path:
        
        db_save(user_id, url, label, title_for_db, 0, "fallback_dl_error")
        return None

    logger.info("[Fallback] Success — file: %s", file_path)
    db_save(user_id, url, label, title_for_db, 0, "fallback_ok")
    return file_path


async def _send_video(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    url: str,
    label: str,
    title_for_db: str,
    file_path: str,
) -> None:
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
    except OSError:
        size_mb = 0.0

    if size_mb > MAX_FILE_SIZE_MB:
        safe_remove(file_path)
        await safe_edit(query,
            f"⚠️ حجم فایل ({size_mb:.1f} MB) بیشتر از "
            f"حد مجاز ({MAX_FILE_SIZE_MB} MB) است.\n"
            "💡 کیفیت پایین‌تری انتخاب کن."
        )
        db_save(user_id, url, label, title_for_db, size_mb, "too_large")
        return

    await safe_edit(query, f"📤 در حال آپلود ({size_mb:.1f} MB)...")
    try:
        with open(file_path, "rb") as video_file:
            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=video_file,
                caption=f"🎬 {label} | {size_mb:.1f} MB",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )
        await safe_edit(query, "✅ ویدیو با موفقیت ارسال شد!")
        db_save(user_id, url, label, title_for_db, size_mb, "ok")
    except Exception as exc:
        logger.error("Send error: %s", exc)
        await safe_edit(query, f"❌ خطا در ارسال فایل: {str(exc)[:200]}")
        db_save(user_id, url, label, title_for_db, size_mb, "send_error")
    finally:
        safe_remove(file_path)

# ══════════════════════════════════════════════════════
#  توابع sync (اجرا در thread pool)
# ══════════════════════════════════════════════════════
def _cookies_opts() -> dict:
    """اگه cookies.txt وجود داشت، آپشن‌های کوکی برمیگردونه."""
    if os.path.isfile(COOKIES_FILE):
        logger.info("Using cookies file: %s", COOKIES_FILE)
        return {"cookiefile": COOKIES_FILE}
    return {}

def fetch_video_info(url: str):
    """گرفتن metadata ویدیو بدون دانلود."""
    opts: dict = {
        "quiet":         True,
        "no_warnings":   True,
        "skip_download": True,
        "noplaylist":    False,
        **_cookies_opts(),
    }
    with yt_dlp.YoutubeDL(cast(Any, opts)) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(url: str, quality: str, progress_hook) -> str:
    """دانلود ویدیو با yt-dlp و برگرداندن مسیر فایل MP4."""
    uid             = int(time.time() * 1000)
    output_template = str(Path(DOWNLOAD_DIR) / f"dl_{uid}.%(ext)s")
    final_files: list[str] = []

    def finish_hook(info_dict: dict) -> None:
        if info_dict["status"] == "finished":
            final_files.append(info_dict["filename"])

    ydl_opts: dict = {
        "format":         FORMAT_MAP.get(quality, "best[ext=mp4]/best"),
        "outtmpl":        output_template,
        "quiet":          True,
        "no_warnings":    True,
        "progress_hooks": [progress_hook, finish_hook],
        "noplaylist":     True,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "android", "mweb"],
            }
        },
        "http_headers": {
            "User-Agent": (
                "com.google.ios.youtube/19.29.1 "
                "(iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X)"
            ),
        },
        **_cookies_opts(),
    }
    
    if _has_ffmpeg():
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }]


    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        ydl.download([url])

    return _find_downloaded_file(uid, final_files)


def _find_downloaded_file(uid: int, final_files: list[str]) -> str:
    """پیدا کردن فایل دانلودشده با چند روش fallback."""
    base       = Path(DOWNLOAD_DIR)
    video_exts = ["mp4", "mkv", "webm", "mov", "avi"]

    for fpath in final_files:
        stem = Path(fpath).stem
        for ext in video_exts:
            candidate = base / f"{stem}.{ext}"
            if candidate.exists():
                return str(candidate)
        if Path(fpath).exists():
            return fpath

    for ext in video_exts:
        candidate = base / f"dl_{uid}.{ext}"
        if candidate.exists():
            return str(candidate)

    newest = sorted(
        (f for f in base.iterdir() if f.suffix.lstrip(".") in video_exts),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if newest:
        return str(newest[0])

    raise FileNotFoundError("فایل دانلودشده پیدا نشد")

# ══════════════════════════════════════════════════════
#  اجرا
# ══════════════════════════════════════════════════════
def main() -> None:
    _STATE["semaphore"] = asyncio.Semaphore(MAX_CONCURRENT)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("check",   cmd_check))
    app.add_handler(CallbackQueryHandler(handle_quality, pattern="^q_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("🤖 ربات شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
