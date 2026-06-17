# 🎬 Video Downloader Bot

> **یک ربات تلگرام قدرتمند برای دانلود ویدیو از 1000+ سایت** 🚀

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)](#)

---

## ✨ ویژگی‌ها

| ویژگی | توضیح |
|------|--------|
| 🌐 **پشتیبان سایت‌ها** | YouTube · Instagram · TikTok · Twitter/X · Vimeo · Pinterest · و 1000+ سایت دیگر |
| 🎯 **انتخاب کیفیت** | 1080p · 720p · 480p · 360p |
| 🔄 **سیستم Fallback** | اگه روش اصلی شکست بخوره، سیستم خودکار روش‌های جایگزین فعال میکنه |
| 📊 **Progress Bar** | نمایش مستقیم درصد دانلود + سرعت + زمان باقی‌مانده |
| 📋 **تاریخچه** | ذخیره‌سازی تمام دانلودها در پایگاه داده |
| 🔒 **کنترل دسترسی** | امکان فعال‌سازی حالت خصوصی و بلاک کاربران |
| ⏳ **Rate Limiting** | جلوگیری از سوء استفاده با محدودیت درخواست |
| 💾 **محدودیت حجم** | حد مجاز: 300 MB برای امنیت سرور |
| 🎨 **رابط فارسی** | تمام پیام‌ها و راهنما به فارسی |

---

## 🏗️ معماری

```
video_downloader/
├── bot.py                    # ربات تلگرام (اصلی)
├── fallback_extractor.py     # سیستم استخراج جایگزین
├── cookies.txt              # (اختیاری) کوکی‌های YouTube
├── bot_history.db           # پایگاه داده تاریخچه
└── README.md
```

---

## 🚀 نصب و راه‌اندازی

### ✅ پیش‌نیازها

```bash
# Python 3.10 یا بالاتر
python --version

# اگه روی Linux/Mac باشید:
sudo apt-get install ffmpeg
# یا اگه brew دارید:
brew install ffmpeg

# اگه Windows باشید:
# Download از: https://ffmpeg.org/download.html
```

### 📦 نصب کتابخانه‌ها

```bash
# Clone کنید
git clone https://github.com/PZhR07/video_downloader.git
cd video_downloader

# ایجاد Virtual Environment (اختیاری ولی توصیه‌شده)
python -m venv venv
source venv/bin/activate  # روی Linux/Mac
# یا
venv\Scripts\activate     # روی Windows

# نصب dependencies
pip install -r requirements.txt
```

### 🔑 دریافت Bot Token

1. درTelegram، به [@BotFather](https://t.me/botfather) بروید
2. دستور `/newbot` رو بفرستید
3. نام و username ربات رو وارد کنید
4. **Token** رو کپی کنید

### ⚙️ تنظیمات

`bot.py` رو باز کنید و **Token** رو جایگزین کنید:

```python
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
```

### 🍪 (اختیاری) کوکی‌های YouTube

اگه YouTube دانلود نشود، کوکی‌ها رو اینطوری بگیرید:

1. افزونه **"Get cookies.txt LOCALLY"** رو درChrome نصب کنید
2. وارد YouTube شوید
3. افزونه رو کلیک کنید → `cookies.txt` رو Export کنید
4. فایل رو درپوشه پروژه بذارید

---

## 🎮 استفاده

### شروع کار

```bash
python bot.py
```

### دستورات ربات

| دستور | توضیح |
|------|--------|
| `/start` | شروع + راهنمای سریع |
| `/help` | راهنمای کامل |
| `/history` | 10 دانلود آخر |
| `/check` | بررسی وضعیت سیستم |

### مثال استفاده

```
1️⃣ لینک ویدیو رو بفرست:
   https://youtube.com/watch?v=dQw4w9WgXcQ

2️⃣ روی کیفیت مورد نظر کلیک کن:
   🎬 1080p (Full HD)
   📹 720p (HD)
   📱 480p
   📺 360p

3️⃣ صبر کن - ویدیو MP4 برات ارسال میشه ✅
```

---

## 🔧 سیستم Fallback

اگه `yt-dlp` با مشکل مواجه شود، این مراحل به ترتیب فعال میشن:

```
1. HTML ساده (requests)
   └─ جستجوی <video>/<source> tags
   └─ meta og:video / twitter:stream
   └─ JSON/JS در <script> tags
   
2. iframe Extraction
   └─ بررسی iframe‌های embedded
   
3. Playwright (اختیاری)
   └─ رندر صفحه با Chrome headless
   └─ Capture شبکه + DOM
```

### 🛠️ نصب Playwright (برای سایت‌های پیچیده)

```bash
pip install playwright
playwright install chromium
```

---

## ⚙️ تنظیمات پیشرفته

درون `bot.py` این تنظیمات رو می‌تونید تغییر بدید:

```python
MAX_FILE_SIZE_MB = 300          # حد مجاز حجم فایل
MAX_CONCURRENT = 2              # دانلود‌های همزمان
RATE_LIMIT_COUNT = 3            # تعداد درخواست
RATE_LIMIT_SECS = 60            # در این مدت (ثانیه)
DOWNLOAD_TIMEOUT = 180          # timeout دانلود (ثانیه)
```

### 🔒 دسترسی محدود

```python
ALLOWED_USERS = {123456789}     # فقط این کاربران (خالی = همه)
BLOCKED_USERS = {987654321}     # بلاک‌شده‌ها
```

---

## 📊 پایگاه داده

تمام دانلودها در `bot_history.db` ذخیره میشن:

```sql
CREATE TABLE downloads (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    url       TEXT NOT NULL,
    quality   TEXT NOT NULL,
    title     TEXT,
    size_mb   REAL,
    status    TEXT,              -- 'ok', 'too_large', 'error', etc.
    ts        TEXT DEFAULT (datetime('now','localtime'))
)
```

---

## 🚨 مشکلات معمول و حل‌شان

### ❌ "ffmpeg: نصب نیست"

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# CentOS
sudo yum install ffmpeg

# macOS
brew install ffmpeg

# Windows
# Download: https://ffmpeg.org/download.html
# یا استفاده از Chocolatey:
choco install ffmpeg
```

### ❌ "YouTube ممکنه کار نکنه"

✅ **حل:** کوکی‌های YouTube رو نصب کنید (بالا رو ببینید)

### ❌ "Playwright نصب نیست"

```bash
pip install playwright
playwright install chromium
```

### ⏳ دانلود timeout میشه

- کیفیت پایین‌تری انتخاب کنید
- اتصال اینترنت رو بررسی کنید
- `DOWNLOAD_TIMEOUT` رو بیشتر کنید

---

## 📈 نمودار جریان

```
┌─────────────────┐
│  لینک دریافت    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ بررسی معتبر بودن│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  گرفتن metadata │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ نمایش کیفیت‌ها  │
└────────┬────────┘
         │
         ▼
    ┌────┴────┐
    │ انتخاب  │
    └────┬────┘
         │
         ▼
    ┌─────────────────────────────┐
    │ 1️⃣  yt-dlp دانلود         │
    └────┬──────────────┬─────────┘
         │ ✅           │ ❌
         │              ▼
         │         ┌──────────────────┐
         │         │ 2️⃣ Fallback     │
         │         │ Extractor       │
         │         └────┬─────────────┘
         │              │
         │         ┌────┴─────────────┐
         │         │ 3️⃣ Direct Download
         │         └────┬─────────────┘
         │              │
         └──────┬───────┘
                ▼
         ┌──────────────┐
         │ Validation  │
         └──────┬───────┘
                │
         ┌──────┴──────┐
         │ ✅  / ❌     │
         └──────┬───────┘
                │
         ┌──────┴──────┐
         │Upload to TG │
         └──────┬───────┘
                │
         ┌──────┴──────┐
         │ Save to DB  │
         └──────┬───────┘
                │
         ┌──────┴──────┐
         │ Clean up   │
         └─────────────┘
```

---

## 🔐 امنیت

- 🛡️ **Rate Limiting** — جلوگیری از DDoS
- 🔒 **محدودیت حجم** — جلوگیری از مصرف بیش از حد
- 📌 **کنترل دسترسی** — فقط کاربران مجاز
- 🗑️ **پاک‌سازی خودکار** — حذف فایل‌های موقت

---

## 📦 Dependencies

```
python-telegram-bot>=20.0
yt-dlp>=2024.01
aiohttp>=3.9
beautifulsoup4>=4.12
playwright>=1.40  (اختیاری)
```

دقیق‌تر برای `requirements.txt`:

```
python-telegram-bot==20.7
yt-dlp==2024.1.1
aiohttp==3.9.1
beautifulsoup4==4.12.2
playwright==1.40.0
```

---

## 🤝 مشارکت

اگه مشکلی پیدا کردید یا پیشنهادی دارید:

1. 🔀 Fork کنید
2. 🌿 Branch جدید بسازید: `git checkout -b feature/amazing-feature`
3. ✍️ Changes رو commit کنید: `git commit -m 'Add amazing feature'`
4. 📤 Push کنید: `git push origin feature/amazing-feature`
5. 🔔 Pull Request باز کنید

---

## 📝 لایسنس

این پروژه تحت لایسنس **MIT** منتشر شده است.

---

## ⚠️ Disclaimer

⚖️ **این ابزار صرفاً برای دانلود محتوایی که دارای اجازه دانلود هستند،** استفاده شود.
مسئولیت استفاده درست و قانونی با کاربر است.

---

## 📞 تماس و پشتیبانی

- 🐛 مشکلات: [Issues](https://github.com/PZhR07/video_downloader/issues)
- 💬 سؤالات: [Discussions](https://github.com/PZhR07/video_downloader/discussions)

---

## 🙏 تشکر

- 🎉 [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- 🤖 [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- 🧭 [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/)
- 🎭 [Playwright](https://playwright.dev/)

---

<div align="center">

**⭐ اگر دوست داشتید، یک ستاره بگذارید!**

![Stars](https://img.shields.io/github/stars/PZhR07/video_downloader?style=social)

</div>
