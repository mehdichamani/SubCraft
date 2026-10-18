import asyncio
import html
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Depends, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from telegram import Update

from app.config import settings
from app.telegram_bot import create_telegram_application
from app.services.job_tracker import job_tracker
from app.services.cookie_manager import cookie_manager

# Configure Structured Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("clip_srt_bot")

ptb_app = None
effective_admin_password: Optional[str] = None
security = HTTPBasic(auto_error=False)


def get_effective_admin_password() -> str:
    """Returns configured ADMIN_PASSWORD or generates a session password printed to logs."""
    global effective_admin_password
    if effective_admin_password is None:
        if settings.admin_password:
            effective_admin_password = settings.admin_password
            logger.info("Using configured ADMIN_PASSWORD from environment for /dashboard access.")
        else:
            effective_admin_password = secrets.token_hex(4)  # Simple 8-char random password
            logger.info("==========================================================")
            logger.info("🔑 ADMIN_PASSWORD not set in environment.")
            logger.info(f"🔑 Generated session admin password for /dashboard access: {effective_admin_password}")
            logger.info(f"🔑 Access dashboard at: /dashboard?key={effective_admin_password}")
            logger.info("==========================================================")
    return effective_admin_password


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle Telegram Bot initialization & Webhook registration."""
    global ptb_app
    logger.info("Starting up SubCraft...")
    
    # Initialize / log effective admin password
    get_effective_admin_password()
    
    settings.validate_keys()

    # Initialize JobTracker database persistence if DATABASE_URL is configured
    job_tracker.init_db()
    # Initialize CookieManager database persistence if DATABASE_URL is configured
    cookie_manager.init_db()
    
    # Initialize Telegram Bot Application with retry resilience
    ptb_app = create_telegram_application()
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Initializing Telegram Application (attempt {attempt}/{max_retries})...")
            await ptb_app.initialize()
            await ptb_app.start()
            break
        except Exception as e:
            logger.error(f"Failed to initialize Telegram Application on attempt {attempt}: {e}")
            if attempt == max_retries:
                logger.critical("Maximum initialization retries reached. Raising exception.")
                raise
            await asyncio.sleep(2 * attempt)

    # Automatically set Telegram Webhook URL if RENDER_EXTERNAL_URL is provided
    if settings.render_external_url:
        base_url = settings.render_external_url.rstrip("/")
        webhook_url = f"{base_url}/webhook"
        logger.info(f"Setting Telegram webhook URL to: {webhook_url}")
        
        kwargs = {"url": webhook_url}
        if settings.webhook_secret:
            kwargs["secret_token"] = settings.webhook_secret
            
        for attempt in range(1, max_retries + 1):
            try:
                await ptb_app.bot.set_webhook(**kwargs)
                logger.info("Telegram webhook URL set successfully.")
                break
            except Exception as e:
                logger.warning(f"Attempt {attempt} to set Telegram webhook failed: {e}")
                if attempt == max_retries:
                    logger.error("Failed to set Telegram webhook after multiple attempts. Continuing startup.")
                else:
                    await asyncio.sleep(2 * attempt)
    else:
        logger.warning(
            "RENDER_EXTERNAL_URL is not configured. Telegram webhook URL was not auto-set. "
            "Please set RENDER_EXTERNAL_URL in your environment or set webhook manually."
        )

    yield

    # Clean shutdown
    logger.info("Shutting down Telegram Bot Application...")
    if ptb_app:
        try:
            await ptb_app.stop()
            await ptb_app.shutdown()
        except Exception as e:
            logger.error(f"Error during Telegram bot shutdown: {e}")
    logger.info("Shutdown complete.")


app = FastAPI(
    title="SubCraft",
    description="Cloud-native Telegram Bot for video subtitle generation and Persian translation",
    version=settings.app_version,
    lifespan=lifespan
)


@app.get("/translation-help", response_class=HTMLResponse)
async def get_translation_help():
    """Renders a visual comparison guide modal between Google Translate and AI Translation."""
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="fa" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>راهنمای انتخاب موتور ترجمه | SubCraft</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
    <style>
        body { font-family: 'Vazirmatn', sans-serif; }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 flex items-center justify-center min-h-screen p-4 sm:p-6">
    <div class="bg-slate-900/90 border border-slate-800 backdrop-blur-xl p-6 sm:p-8 rounded-3xl max-w-2xl w-full shadow-2xl space-y-6">
        <!-- Header -->
        <div class="text-center space-y-2">
            <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-2xl mb-2">
                🤖 VS 🌐
            </div>
            <h1 class="text-2xl font-bold text-white">مقایسه موتورهای ترجمه زیرنویس</h1>
            <p class="text-slate-400 text-sm">تفاوت بین گوگل ترنسلیت و هوش مصنوعی را بررسی کنید تا بهترین گزینه را انتخاب کنید.</p>
        </div>

        <!-- Comparison Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <!-- Google Translate Card -->
            <div class="bg-slate-950/70 border border-slate-800 p-5 rounded-2xl space-y-4 hover:border-sky-500/30 transition">
                <div class="flex items-center gap-3 border-b border-slate-800 pb-3">
                    <span class="text-2xl">🌐</span>
                    <div>
                        <h2 class="font-bold text-sky-400 text-lg">گوگل ترنسلیت</h2>
                        <span class="text-xs text-slate-400 font-mono">Google Translate API</span>
                    </div>
                </div>
                <ul class="text-xs space-y-2.5 text-slate-300">
                    <li class="flex items-start gap-2">
                        <span class="text-emerald-400 font-bold">✓</span>
                        <span><b>ترجمه ۱۰۰٪ کامل:</b> تمام خطوط بدون قطع شدن یا نیمه‌کاره ماندن ترجمه می‌شوند.</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="text-emerald-400 font-bold">✓</span>
                        <span><b>بدون توهم (Hallucination):</b> دقیقا همان متن ورودی ترجمه شده و کلمه‌ای اضافه یا کم نمی‌شود.</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="text-emerald-400 font-bold">✓</span>
                        <span><b>حفظ دقیق زمان‌بندی:</b> قالب SRT و زمان‌بندی‌ها ۱۰۰٪ دست‌نخورده باقی می‌مانند.</span>
                    </li>
                    <li class="flex items-start gap-2 text-slate-400">
                        <span class="text-amber-400 font-bold">!</span>
                        <span><b>لحن کلام:</b> ممکن است در برخی جملات عامیانه کمی کتابی باشد.</span>
                    </li>
                </ul>
                <div class="bg-sky-500/10 border border-sky-500/20 text-sky-300 text-xs p-2.5 rounded-xl text-center font-medium">
                    🎯 پیشنهادی برای کلیپ‌های طولانی و آموزشی
                </div>
            </div>

            <!-- AI Model Card -->
            <div class="bg-slate-950/70 border border-slate-800 p-5 rounded-2xl space-y-4 hover:border-purple-500/30 transition">
                <div class="flex items-center gap-3 border-b border-slate-800 pb-3">
                    <span class="text-2xl">🧠</span>
                    <div>
                        <h2 class="font-bold text-purple-400 text-lg">هوش مصنوعی (AI)</h2>
                        <span class="text-xs text-slate-400 font-mono">Groq / Gemini LLM</span>
                    </div>
                </div>
                <ul class="text-xs space-y-2.5 text-slate-300">
                    <li class="flex items-start gap-2">
                        <span class="text-emerald-400 font-bold">✓</span>
                        <span><b>ترجمه فوق‌العاده روان:</b> فهم اصطلاحات انگلیسی، شوخی‌ها و زبان عامیانه/کلاسی.</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="text-emerald-400 font-bold">✓</span>
                        <span><b>حس طبیعی:</b> مناسب‌تر برای دیالوگ‌های داستانی، فیلم و محتوای جذاب.</span>
                    </li>
                    <li class="flex items-start gap-2 text-slate-400">
                        <span class="text-rose-400 font-bold">✕</span>
                        <span><b>احتمال قطع شدن:</b> در متون بسیار طولانی ممکن است پاسخ نیمه‌کاره بماند.</span>
                    </li>
                    <li class="flex items-start gap-2 text-slate-400">
                        <span class="text-amber-400 font-bold">!</span>
                        <span><b>احتمال توهم:</b> گاهی توضیحات اضافی یا فرمت ناخواسته تولید می‌کند.</span>
                    </li>
                </ul>
                <div class="bg-purple-500/10 border border-purple-500/20 text-purple-300 text-xs p-2.5 rounded-xl text-center font-medium">
                    🎯 پیشنهادی برای کلیپ‌های کوتاه اینستاگرامی
                </div>
            </div>
        </div>

        <!-- Summary Footer -->
        <div class="text-center pt-2 border-t border-slate-800 text-xs text-slate-500">
            SubCraft • جهت بازگشت، این صفحه را ببندید و در تلگرام گزینه مورد نظر را انتخاب کنید.
        </div>
    </div>
</body>
</html>"""
    )


@app.get("/")
@app.head("/")
@app.get("/health")
@app.head("/health")
async def health_check():
    """Health check endpoint for Render service monitoring."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "service": "clip-srt-bot",
        "webhook_configured": bool(settings.render_external_url)
    }


@app.get("/about", response_class=HTMLResponse)
async def about_page():
    """Renders the About page displaying project version, tech stack, and developer information."""
    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html lang="fa" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>درباره پروژه | SubCraft v{settings.app_version}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
    <style>
        body {{ font-family: 'Vazirmatn', sans-serif; }}
    </style>
</head>
<body class="bg-slate-950 text-slate-100 flex items-center justify-center min-h-screen p-4 sm:p-6">
    <div class="bg-slate-900/90 border border-slate-800 backdrop-blur-xl p-6 sm:p-8 rounded-3xl max-w-2xl w-full shadow-2xl space-y-6">
        <!-- Header -->
        <div class="text-center space-y-2">
            <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-3xl mb-1">
                🎬
            </div>
            <h1 class="text-2xl font-bold text-white">SubCraft</h1>
            <div class="inline-block bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs font-semibold px-3 py-1 rounded-full">
                نسخه Beta v{settings.app_version}
            </div>
        </div>

        <!-- Info Grid -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div class="bg-slate-800/50 border border-slate-700/50 p-4 rounded-2xl space-y-2">
                <div class="text-xs text-slate-400 font-semibold">👨‍💻 توسعه‌دهنده</div>
                <div class="text-white font-medium">مهدی چمنی</div>
                <div class="text-xs text-slate-400">
                    ایمیل: <code class="text-indigo-300">mahdi.chamani20@gmail.com</code>
                </div>
                <div class="text-xs text-slate-400">
                    تلگرام: <a href="https://t.me/mehdichamanni" target="_blank" class="text-indigo-400 hover:underline">@mehdichamanni</a>
                </div>
            </div>

            <div class="bg-slate-800/50 border border-slate-700/50 p-4 rounded-2xl space-y-2">
                <div class="text-xs text-slate-400 font-semibold">📦 مخزن سورس‌کد</div>
                <div class="text-white font-medium">گیت‌هاب (GitHub)</div>
                <div class="text-xs">
                    <a href="https://github.com/mehdichamani/SubCraft" target="_blank" class="text-indigo-400 hover:underline break-all">
                        github.com/mehdichamani/SubCraft
                    </a>
                </div>
            </div>
        </div>

        <!-- Tech Stack -->
        <div class="bg-slate-800/30 border border-slate-800 p-4 rounded-2xl space-y-3 text-sm">
            <h2 class="font-bold text-slate-200">🛠 تکنولوژی‌ها و خدمات:</h2>
            <ul class="space-y-1.5 text-xs text-slate-300">
                <li>• <b>تبدیل گفتار به متن (STT):</b> Groq Whisper API (<code class="text-slate-400">whisper-large-v3</code>)</li>
                <li>• <b>ترجمه هوشمند:</b> Groq AI (<code class="text-slate-400">{settings.groq_translate_model}</code>) / Google Gemini</li>
                <li>• <b>پردازش مدیا:</b> FFmpeg (استخراج صدا ۱۶kHz و چسباندن زیرنویس سافت‌ساب) + Mutagen (ID3v2 Synced LRC)</li>
                <li>• <b>فریم‌ورک وب & وب‌هوک:</b> FastAPI + python-telegram-bot</li>
                <li>• <b>میزبانی سرور:</b> Render.com Free Tier</li>
            </ul>
        </div>

        <!-- Footer -->
        <div class="text-center pt-2 border-t border-slate-800 text-xs text-slate-500">
            SubCraft • v{settings.app_version} Beta
        </div>
    </div>
</body>
</html>"""
    )


@app.get("/api/about")
async def api_about():
    """JSON API endpoint for system about metadata."""
    return {
        "name": "SubCraft",
        "version": settings.app_version,
        "environment": "beta",
        "developer": "Mehdi Chamani",
        "repository": "https://github.com/mehdichamani/SubCraft",
        "hosting": "Render.com"
    }



@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None)
):
    """Explicit Telegram Webhook endpoint receiving HTTP POST updates."""
    if not ptb_app:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Application is not initialized."
        )

    # Webhook secret validation using constant-time comparison
    if settings.webhook_secret:
        if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
            x_telegram_bot_api_secret_token, settings.webhook_secret
        ):
            logger.warning("Received webhook request with invalid secret token.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid secret token"
            )

    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        # Process update asynchronously in background task to avoid Telegram webhook timeout
        asyncio.create_task(ptb_app.process_update(update))
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing Telegram webhook update: {e}", exc_info=True)
        return {"status": "error", "detail": str(e)}


from pydantic import BaseModel

class CookiePayload(BaseModel):
    mode: str = "general"
    general_cookies: Optional[str] = ""
    youtube_cookies: Optional[str] = ""
    instagram_cookies: Optional[str] = ""


def verify_admin_auth(
    key: Optional[str] = Query(None),
    password: Optional[str] = Query(None),
    credentials: Optional[HTTPBasicCredentials] = Depends(security)
) -> bool:
    """Helper to verify admin authentication across dashboard and API endpoints."""
    expected_password = get_effective_admin_password()
    provided_pass = key or password

    if provided_pass and secrets.compare_digest(provided_pass, expected_password):
        return True
    if credentials and credentials.password and secrets.compare_digest(credentials.password, expected_password):
        return True
    return False


@app.get("/api/cookies")
async def get_cookies_api(
    is_auth: bool = Depends(verify_admin_auth)
):
    """Returns stored cookies configuration and metadata (Admin only)."""
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required to access cookies."
        )
    data = cookie_manager.get_cookies_data()
    return {
        "mode": data.get("mode", "general"),
        "general_cookies": data.get("general_cookies", ""),
        "youtube_cookies": data.get("youtube_cookies", ""),
        "instagram_cookies": data.get("instagram_cookies", ""),
        "general_count": cookie_manager.count_cookies(data.get("general_cookies", "")),
        "youtube_count": cookie_manager.count_cookies(data.get("youtube_cookies", "")),
        "instagram_count": cookie_manager.count_cookies(data.get("instagram_cookies", "")),
        "updated_at": data.get("updated_at", 0)
    }


@app.post("/api/cookies")
async def save_cookies_api(
    payload: CookiePayload,
    is_auth: bool = Depends(verify_admin_auth)
):
    """Saves or updates cookie configuration (Admin only)."""
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required to update cookies."
        )
    updated = cookie_manager.save_cookies_data(
        mode=payload.mode,
        general_cookies=payload.general_cookies,
        youtube_cookies=payload.youtube_cookies,
        instagram_cookies=payload.instagram_cookies
    )
    return {
        "status": "success",
        "message": "کوکی‌ها با موفقیت ذخیره شدند.",
        "data": {
            "mode": updated.get("mode"),
            "general_count": cookie_manager.count_cookies(updated.get("general_cookies", "")),
            "youtube_count": cookie_manager.count_cookies(updated.get("youtube_cookies", "")),
            "instagram_count": cookie_manager.count_cookies(updated.get("instagram_cookies", "")),
            "updated_at": updated.get("updated_at")
        }
    }


@app.delete("/api/cookies/{target}")
async def clear_cookies_api(
    target: str,
    is_auth: bool = Depends(verify_admin_auth)
):
    """Clears cookies for a specific target: general, youtube, instagram, or all (Admin only)."""
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required to clear cookies."
        )
    if target not in ("general", "youtube", "instagram", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid clear target. Valid values: general, youtube, instagram, all"
        )
    updated = cookie_manager.clear_cookies(target=target)
    return {
        "status": "success",
        "message": f"کوکی‌های بخش {target} پاک‌سازی شدند.",
        "data": {
            "mode": updated.get("mode"),
            "general_count": cookie_manager.count_cookies(updated.get("general_cookies", "")),
            "youtube_count": cookie_manager.count_cookies(updated.get("youtube_cookies", "")),
            "instagram_count": cookie_manager.count_cookies(updated.get("instagram_cookies", "")),
            "updated_at": updated.get("updated_at")
        }
    }


@app.get("/admin")
async def admin_redirect(key: Optional[str] = Query(None), password: Optional[str] = Query(None)):
    """Redirect /admin to /dashboard preserving query authentication parameters."""
    target = "/dashboard"
    param = key or password
    if param:
        target += f"?key={param}"
    return RedirectResponse(url=target)


@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(
    key: Optional[str] = Query(None),
    password: Optional[str] = Query(None),
    credentials: Optional[HTTPBasicCredentials] = Depends(security)
):
    """Dashboard UI visualizing in-memory job operations, cookie management and status logs."""
    if not verify_admin_auth(key=key, password=password, credentials=credentials):
        return HTMLResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="Dashboard Access"'},
            content="""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <title>401 Unauthorized - Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 flex items-center justify-center min-h-screen">
    <div class="bg-slate-900 border border-slate-800 p-8 rounded-2xl max-w-md text-center shadow-2xl">
        <div class="text-4xl mb-4">🔒</div>
        <h1 class="text-2xl font-bold text-white mb-2">Access Denied</h1>
        <p class="text-slate-400 text-sm mb-6">You must provide a valid admin password or query parameter to access the dashboard.</p>
        <p class="text-xs text-slate-500 font-mono bg-slate-950 py-2 px-3 rounded-lg border border-slate-800">
            Use: /dashboard?key=YOUR_PASSWORD or HTTP Basic Auth
        </p>
    </div>
</body>
</html>"""
        )

    stats = job_tracker.get_stats()
    top_users = job_tracker.get_top_users(3)
    jobs = job_tracker.get_jobs()
    cookies_data = cookie_manager.get_cookies_data()

    # Cookie metadata
    cookie_mode = cookies_data.get("mode", "general")
    general_cookies_val = cookies_data.get("general_cookies", "")
    youtube_cookies_val = cookies_data.get("youtube_cookies", "")
    instagram_cookies_val = cookies_data.get("instagram_cookies", "")

    gen_count = cookie_manager.count_cookies(general_cookies_val)
    yt_count = cookie_manager.count_cookies(youtube_cookies_val)
    ig_count = cookie_manager.count_cookies(instagram_cookies_val)
    cookie_updated_ts = cookies_data.get("updated_at", 0)

    # Build Top 3 Users HTML
    top_users_html = []
    if not top_users:
        top_users_html.append('<div class="text-xs text-slate-500 italic py-2">هنوز کاربری ثبت نشده است / No users recorded yet</div>')
    else:
        rank_badges = [
            '<span class="w-6 h-6 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 flex items-center justify-center font-bold text-xs">🥇</span>',
            '<span class="w-6 h-6 rounded-full bg-slate-400/20 text-slate-300 border border-slate-400/30 flex items-center justify-center font-bold text-xs">🥈</span>',
            '<span class="w-6 h-6 rounded-full bg-amber-700/20 text-amber-400 border border-amber-700/30 flex items-center justify-center font-bold text-xs">🥉</span>'
        ]
        for idx, u in enumerate(top_users):
            badge = rank_badges[idx] if idx < len(rank_badges) else f'<span class="text-xs font-mono text-slate-500">#{idx+1}</span>'
            uname = html.escape(str(u.get("username", "Unknown")))
            uid = html.escape(str(u.get("user_id", "Unknown")))
            cnt = u.get("request_count", 0)
            top_users_html.append(f"""
                <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-slate-700 transition">
                    <div class="flex items-center gap-3">
                        {badge}
                        <div>
                            <div class="text-xs font-semibold text-slate-200">{uname}</div>
                            <div class="text-[10px] text-slate-500 font-mono">ID: {uid}</div>
                        </div>
                    </div>
                    <div class="inline-flex items-center gap-1 text-xs font-bold text-indigo-400 bg-indigo-500/10 px-2.5 py-1 rounded-lg border border-indigo-500/20">
                        <span>{cnt}</span>
                        <span class="text-[10px] text-indigo-300 font-normal">درخواست</span>
                    </div>
                </div>
            """)
    top_users_block = "".join(top_users_html)

    # Build Table Rows HTML
    table_rows = []
    if not jobs:
        table_rows.append("""
            <tr>
                <td colspan="7" class="px-6 py-10 text-center text-slate-500 italic">
                    هنوز هیچ درخواستی ثبت نشده است. ورودی‌های جدید به صورت زنده ظاهر خواهند شد.
                </td>
            </tr>
        """)
    else:
        for job in jobs:
            st = job.get("status", "pending")
            if st == "done":
                badge = '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Done</span>'
            elif st == "processing":
                badge = '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse">Processing</span>'
            elif st == "pending":
                badge = '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20">Pending</span>'
            elif st == "canceled":
                badge = '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-zinc-500/10 text-zinc-400 border border-zinc-500/20">Canceled</span>'
            else:
                badge = '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">Error</span>'

            job_id_esc = html.escape(str(job.get("job_id", "")))
            user_id_esc = html.escape(str(job.get("user_id", "")))
            username_esc = html.escape(str(job.get("username", "")))
            input_esc = html.escape(str(job.get("input_url_or_file", "")))
            subject_esc = html.escape(str(job.get("subject", "")))
            err_esc = html.escape(str(job.get("error_message", "")))
            ts = job.get("timestamp", 0)
            formatted_time = html.escape(str(job.get("formatted_time", "")))

            search_data = html.escape(f"{job_id_esc} {username_esc} {user_id_esc} {input_esc} {subject_esc} {st} {err_esc}".lower())

            js_input_copy = input_esc.replace("\\", "\\\\").replace("'", "\\'").replace('"', '&quot;')
            js_subject_copy = subject_esc.replace("\\", "\\\\").replace("'", "\\'").replace('"', '&quot;')

            subject_btn = f'<button onclick="copyText(\'{js_subject_copy}\', this)" class="shrink-0 px-2 py-0.5 text-[10px] bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded border border-slate-700 transition">کپی</button>' if subject_esc else ''
            input_btn = f'<button onclick="copyText(\'{js_input_copy}\', this)" class="shrink-0 px-2 py-0.5 text-[10px] bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded border border-slate-700 transition">کپی</button>' if input_esc and input_esc != 'N/A' else ''

            subject_display = f"""
                <div class="flex items-start justify-between gap-2">
                    <span class="text-xs text-slate-200 font-medium break-all">{subject_esc or '<span class="text-slate-600 italic">-</span>'}</span>
                    {subject_btn}
                </div>
            """

            input_display = f"""
                <div class="flex items-start justify-between gap-2">
                    <span class="text-xs text-slate-300 font-mono break-all">{input_esc}</span>
                    {input_btn}
                </div>
            """

            table_rows.append(f"""
                <tr class="job-row hover:bg-slate-800/40 transition-colors" data-search="{search_data}">
                    <td class="px-5 py-4 font-mono text-xs text-indigo-400 font-semibold align-top">{job_id_esc}</td>
                    <td class="px-5 py-4 align-top">
                        <div class="font-medium text-slate-200 text-xs">{username_esc}</div>
                        <div class="text-[11px] text-slate-500 font-mono">ID: {user_id_esc}</div>
                    </td>
                    <td class="px-5 py-4 align-top min-w-[200px]">{input_display}</td>
                    <td class="px-5 py-4 align-top min-w-[180px]">{subject_display}</td>
                    <td class="px-5 py-4 align-top whitespace-nowrap">{badge}</td>
                    <td class="px-5 py-4 align-top max-w-xs text-xs text-rose-400 font-mono break-all">{err_esc or '<span class="text-slate-600">-</span>'}</td>
                    <td class="px-5 py-4 align-top text-right text-xs text-slate-400 font-mono whitespace-nowrap">
                        <span class="shamsi-time" data-timestamp="{ts}">{formatted_time}</span>
                    </td>
                </tr>
            """)

    rows_html = "".join(table_rows)
    has_active_processing = stats["pending_processing"] > 0
    pulse_indicator = '<span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping inline-block ml-1"></span>' if has_active_processing else ''

    db_active = stats.get("db_active", False)
    storage_subtext = "پایگاه داده PostgreSQL" if db_active else "حافظه موقت (حداکثر ۱۰۰ کار)"
    storage_badge = '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 text-xs"><span class="w-2 h-2 rounded-full bg-indigo-400"></span>PostgreSQL DB</span>' if db_active else '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-800 text-slate-400 border border-slate-700 text-xs">In-Memory</span>'

    general_checked = 'checked' if cookie_mode == 'general' else ''
    platform_checked = 'checked' if cookie_mode == 'platform' else ''

    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>داشبورد مدیریت | SubCraft v{settings.app_version}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
    <style>
        body {{ font-family: 'Vazirmatn', sans-serif; }}
        .dropzone-active {{ border-color: #6366f1 !important; background-color: rgba(99, 102, 241, 0.1) !important; }}
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">
    <!-- Header -->
    <header class="border-b border-slate-800 bg-slate-900/70 backdrop-blur sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex flex-wrap items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400 text-xl font-bold">
                    🎬
                </div>
                <div>
                    <h1 class="text-lg sm:text-xl font-bold text-white tracking-tight">داشبورد مدیریت و پایش ربات</h1>
                    <p class="text-xs text-slate-400">SubCraft v{settings.app_version}</p>
                </div>
            </div>
            <div class="flex items-center gap-3 text-xs text-slate-400 flex-wrap">
                <span id="visitorTimezone" class="hidden sm:inline-flex items-center px-3 py-1 rounded-full bg-slate-900 border border-slate-800 text-slate-300 font-mono text-[11px]">...</span>
                {storage_badge}
                <button onclick="window.location.reload()" class="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition flex items-center gap-1.5 shadow-sm text-xs">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                    بروزرسانی
                </button>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8 space-y-6">
        <!-- Top Cards Grid (Combined Stats Card + Top Users Card) -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
            <!-- Card 1: Combined Summary Statistics Card -->
            <div class="bg-slate-900/80 border border-slate-800 rounded-3xl p-5 sm:p-6 shadow-xl shadow-black/20 space-y-4 flex flex-col justify-between">
                <div class="flex items-center justify-between border-b border-slate-800/80 pb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-xl">📊</span>
                        <h2 class="font-bold text-white text-base sm:text-lg">خلاصه آمار عملیات</h2>
                    </div>
                    <span class="text-[11px] text-slate-500 font-mono">{storage_subtext}</span>
                </div>

                <div class="grid grid-cols-2 gap-3 sm:gap-4">
                    <!-- Total -->
                    <div class="bg-slate-950/60 border border-slate-800/80 p-3.5 rounded-2xl">
                        <div class="text-[11px] text-slate-400 font-semibold mb-1">کل درخواست‌ها</div>
                        <div class="text-2xl sm:text-3xl font-extrabold text-white">{stats['total']}</div>
                    </div>
                    <!-- Completed -->
                    <div class="bg-slate-950/60 border border-slate-800/80 p-3.5 rounded-2xl">
                        <div class="text-[11px] text-slate-400 font-semibold mb-1">موفق (تکمیل شده)</div>
                        <div class="text-2xl sm:text-3xl font-extrabold text-emerald-400">{stats['completed']}</div>
                    </div>
                    <!-- Pending / Processing -->
                    <div class="bg-slate-950/60 border border-slate-800/80 p-3.5 rounded-2xl">
                        <div class="text-[11px] text-slate-400 font-semibold mb-1">در حال پردازش</div>
                        <div class="text-2xl sm:text-3xl font-extrabold text-amber-400 flex items-center">
                            {stats['pending_processing']} {pulse_indicator}
                        </div>
                    </div>
                    <!-- Failed / Canceled -->
                    <div class="bg-slate-950/60 border border-slate-800/80 p-3.5 rounded-2xl">
                        <div class="text-[11px] text-slate-400 font-semibold mb-1">ناموفق / لغو شده</div>
                        <div class="text-2xl sm:text-3xl font-extrabold text-rose-400">{stats['failed']}</div>
                    </div>
                </div>
            </div>

            <!-- Card 2: Top 3 Users Leaderboard Card -->
            <div class="bg-slate-900/80 border border-slate-800 rounded-3xl p-5 sm:p-6 shadow-xl shadow-black/20 space-y-4">
                <div class="flex items-center justify-between border-b border-slate-800/80 pb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-xl">🏆</span>
                        <h2 class="font-bold text-white text-base sm:text-lg">برترین کاربران (پردرخواست‌ترین‌ها)</h2>
                    </div>
                    <span class="text-[11px] bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 px-2.5 py-0.5 rounded-full font-mono">Top 3</span>
                </div>

                <div class="space-y-2.5">
                    {top_users_block}
                </div>
            </div>
        </div>

        <!-- Card 3: Cookie Management System Card -->
        <div class="bg-slate-900/80 border border-slate-800 rounded-3xl p-5 sm:p-6 shadow-xl shadow-black/20 space-y-5">
            <div class="flex flex-wrap items-center justify-between border-b border-slate-800/80 pb-4 gap-3">
                <div class="flex items-center gap-2.5">
                    <span class="text-2xl">🍪</span>
                    <div>
                        <h2 class="font-bold text-white text-base sm:text-lg">مدیریت کوکی‌ها (Cookie Management)</h2>
                        <p class="text-xs text-slate-400">تنظیم کوکی‌های Netscape جهت دانلود بدون محدودیت از یوتیوب و اینستاگرام</p>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    <span id="cookieLastUpdated" class="text-xs font-mono text-slate-400" data-timestamp="{cookie_updated_ts}">
                        آخرین بروزرسانی: <span class="shamsi-time" data-timestamp="{cookie_updated_ts}">-</span>
                    </span>
                </div>
            </div>

            <!-- Mode Selection Radio Buttons -->
            <div class="flex flex-wrap items-center gap-4 bg-slate-950/60 p-3.5 rounded-2xl border border-slate-800/80">
                <span class="text-xs font-semibold text-slate-300">شیوه تنظیم کوکی:</span>
                <label class="flex items-center gap-2 cursor-pointer text-xs font-medium text-slate-200">
                    <input type="radio" name="cookieMode" value="general" onchange="toggleCookieMode('general')" {general_checked} class="w-4 h-4 text-indigo-600 focus:ring-indigo-500 bg-slate-900 border-slate-700">
                    <span>حالت عمومی (یکپارچه / General)</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer text-xs font-medium text-slate-200">
                    <input type="radio" name="cookieMode" value="platform" onchange="toggleCookieMode('platform')" {platform_checked} class="w-4 h-4 text-indigo-600 focus:ring-indigo-500 bg-slate-900 border-slate-700">
                    <span>تفکیک بر اساس پلتفرم (YouTube و Instagram جداگانه)</span>
                </label>
            </div>

            <!-- General Mode Input -->
            <div id="generalCookieSection" class="space-y-3" style="display: {'block' if cookie_mode == 'general' else 'none'};">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="text-xs font-bold text-slate-300">متن کوکی عمومی (Netscape Cookies Format)</span>
                        <span id="generalBadge" class="text-[11px] px-2 py-0.5 rounded-full {'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' if gen_count > 0 else 'bg-slate-800 text-slate-400'} font-mono">
                            {gen_count} کوکی فعال
                        </span>
                    </div>
                    <div class="flex items-center gap-2">
                        <button onclick="clearCookieField('general')" class="text-xs text-rose-400 hover:text-rose-300 transition">پاک‌سازی</button>
                        <button onclick="exportCookieFile('general')" class="text-xs text-indigo-400 hover:text-indigo-300 transition">دانلود cookies.txt</button>
                    </div>
                </div>
                <div class="relative border-2 border-dashed border-slate-800 hover:border-slate-700 rounded-2xl p-2 transition dropzone" id="generalDropzone">
                    <textarea id="generalCookiesText" rows="6" placeholder="# Netscape HTTP Cookie File&#10;.youtube.com	TRUE	/	TRUE	1750000000	SID	..." class="w-full bg-slate-950 text-slate-200 text-xs font-mono p-3 rounded-xl focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-y leading-relaxed">{html.escape(general_cookies_val)}</textarea>
                    <div class="text-[10px] text-slate-500 mt-1 px-1 flex items-center justify-between">
                        <span>💡 می‌توانید فایل <code class="text-indigo-300">cookies.txt</code> را مستقیماً درون کادر بالا بکشید و رها کنید (Drag & Drop).</span>
                        <label class="cursor-pointer text-indigo-400 hover:underline">
                            انتخاب فایل
                            <input type="file" accept=".txt" class="hidden" onchange="handleFileSelect(event, 'generalCookiesText')">
                        </label>
                    </div>
                </div>
            </div>

            <!-- Platform-Specific Mode Input (YouTube & Instagram) -->
            <div id="platformCookieSection" class="grid grid-cols-1 md:grid-cols-2 gap-4" style="display: {'grid' if cookie_mode == 'platform' else 'none'};">
                <!-- YouTube Box -->
                <div class="space-y-2.5 bg-slate-950/40 p-4 rounded-2xl border border-slate-800/80">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="text-base">▶️</span>
                            <span class="text-xs font-bold text-slate-200">کوکی‌های YouTube</span>
                            <span id="youtubeBadge" class="text-[10px] px-2 py-0.5 rounded-full {'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' if yt_count > 0 else 'bg-slate-800 text-slate-400'} font-mono">
                                {yt_count} کوکی
                            </span>
                        </div>
                        <div class="flex items-center gap-2">
                            <button onclick="clearCookieField('youtube')" class="text-xs text-rose-400 hover:text-rose-300">پاک‌سازی</button>
                            <button onclick="exportCookieFile('youtube')" class="text-xs text-indigo-400 hover:text-indigo-300">دانلود</button>
                        </div>
                    </div>
                    <div class="border-2 border-dashed border-slate-800 hover:border-slate-700 rounded-xl p-1.5 transition dropzone" id="youtubeDropzone">
                        <textarea id="youtubeCookiesText" rows="6" placeholder="# YouTube Cookies&#10;.youtube.com	TRUE	/	..." class="w-full bg-slate-950 text-slate-200 text-xs font-mono p-2.5 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-y leading-relaxed">{html.escape(youtube_cookies_val)}</textarea>
                        <div class="text-[10px] text-slate-500 mt-1 px-1 flex items-center justify-between">
                            <span>Drop <code class="text-indigo-300">youtube_cookies.txt</code></span>
                            <label class="cursor-pointer text-indigo-400 hover:underline">
                                انتخاب فایل
                                <input type="file" accept=".txt" class="hidden" onchange="handleFileSelect(event, 'youtubeCookiesText')">
                            </label>
                        </div>
                    </div>
                </div>

                <!-- Instagram Box -->
                <div class="space-y-2.5 bg-slate-950/40 p-4 rounded-2xl border border-slate-800/80">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="text-base">📸</span>
                            <span class="text-xs font-bold text-slate-200">کوکی‌های Instagram</span>
                            <span id="instagramBadge" class="text-[10px] px-2 py-0.5 rounded-full {'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' if ig_count > 0 else 'bg-slate-800 text-slate-400'} font-mono">
                                {ig_count} کوکی
                            </span>
                        </div>
                        <div class="flex items-center gap-2">
                            <button onclick="clearCookieField('instagram')" class="text-xs text-rose-400 hover:text-rose-300">پاک‌سازی</button>
                            <button onclick="exportCookieFile('instagram')" class="text-xs text-indigo-400 hover:text-indigo-300">دانلود</button>
                        </div>
                    </div>
                    <div class="border-2 border-dashed border-slate-800 hover:border-slate-700 rounded-xl p-1.5 transition dropzone" id="instagramDropzone">
                        <textarea id="instagramCookiesText" rows="6" placeholder="# Instagram Cookies&#10;.instagram.com	TRUE	/	..." class="w-full bg-slate-950 text-slate-200 text-xs font-mono p-2.5 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-y leading-relaxed">{html.escape(instagram_cookies_val)}</textarea>
                        <div class="text-[10px] text-slate-500 mt-1 px-1 flex items-center justify-between">
                            <span>Drop <code class="text-indigo-300">instagram_cookies.txt</code></span>
                            <label class="cursor-pointer text-indigo-400 hover:underline">
                                انتخاب فایل
                                <input type="file" accept=".txt" class="hidden" onchange="handleFileSelect(event, 'instagramCookiesText')">
                            </label>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Cookie Action Buttons & Feedback Toast -->
            <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-800/80">
                <div id="cookieStatusMsg" class="text-xs font-medium transition duration-300"></div>
                <div class="flex items-center gap-3">
                    <button onclick="saveCookiesForm()" id="saveCookieBtn" class="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-bold transition flex items-center gap-2 shadow-lg shadow-indigo-600/20">
                        <span>💾</span>
                        <span>ذخیره تغییرات کوکی</span>
                    </button>
                </div>
            </div>
        </div>

        <!-- Search & Request Table Section -->
        <div class="bg-slate-900/80 border border-slate-800 rounded-3xl overflow-hidden shadow-2xl shadow-black/30 space-y-0">
            <!-- Search Header Bar -->
            <div class="p-4 sm:p-5 border-b border-slate-800 bg-slate-900/40 flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-center gap-2">
                    <span class="text-lg">📋</span>
                    <h2 class="text-base font-bold text-white">جدول درخواست‌ها و وضعیت</h2>
                </div>
                <div class="flex items-center gap-3 w-full sm:w-auto">
                    <div class="relative w-full sm:w-80">
                        <input type="text" id="searchInput" oninput="filterJobs()" placeholder="🔍 جستجو در شناسه، کاربر، لینک، موضوع یا خطا..." class="w-full bg-slate-950 border border-slate-700/80 text-xs rounded-xl px-3.5 py-2.5 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition shadow-inner" />
                    </div>
                    <span id="searchCount" class="text-xs text-slate-400 font-mono whitespace-nowrap shrink-0">{len(jobs)} مورد</span>
                </div>
            </div>

            <!-- Responsive Table Container -->
            <div class="overflow-x-auto">
                <table class="w-full text-right text-xs text-slate-300">
                    <thead class="bg-slate-950/90 text-xs font-semibold text-slate-400 border-b border-slate-800">
                        <tr>
                            <th scope="col" class="px-5 py-3.5">شناسه کار (Job ID)</th>
                            <th scope="col" class="px-5 py-3.5">کاربر</th>
                            <th scope="col" class="px-5 py-3.5">ورودی / رسانه</th>
                            <th scope="col" class="px-5 py-3.5">موضوع کلیپ</th>
                            <th scope="col" class="px-5 py-3.5">وضعیت</th>
                            <th scope="col" class="px-5 py-3.5">جزئیات / خطا</th>
                            <th scope="col" class="px-5 py-3.5 text-left">زمان (شمسی)</th>
                        </tr>
                    </thead>
                    <tbody id="jobsTableBody" class="divide-y divide-slate-800/60 bg-slate-900/20">
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- Client-side JavaScript for Cookie Management, Copy, Real-Time Search, Timezone detection & Shamsi date formatting -->
    <script>
        function getAuthParams() {{
            const urlParams = new URLSearchParams(window.location.search);
            const key = urlParams.get('key') || urlParams.get('password');
            return key ? `?key=${{encodeURIComponent(key)}}` : '';
        }}

        function toggleCookieMode(mode) {{
            const genSec = document.getElementById('generalCookieSection');
            const platSec = document.getElementById('platformCookieSection');
            if (mode === 'general') {{
                genSec.style.display = 'block';
                platSec.style.display = 'none';
            }} else {{
                genSec.style.display = 'none';
                platSec.style.display = 'grid';
            }}
        }}

        function setupDropzone(dropzoneId, textareaId) {{
            const dropzone = document.getElementById(dropzoneId);
            const textarea = document.getElementById(textareaId);
            if (!dropzone || !textarea) return;

            ['dragenter', 'dragover'].forEach(name => {{
                dropzone.addEventListener(name, (e) => {{
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.add('dropzone-active');
                }}, false);
            }});

            ['dragleave', 'drop'].forEach(name => {{
                dropzone.addEventListener(name, (e) => {{
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.remove('dropzone-active');
                }}, false);
            }});

            dropzone.addEventListener('drop', (e) => {{
                const files = e.dataTransfer.files;
                if (files.length > 0) {{
                    const file = files[0];
                    const reader = new FileReader();
                    reader.onload = function(evt) {{
                        textarea.value = evt.target.result;
                        showCookieMsg("فایل کوکی با موفقیت بارگذاری شد.", "text-indigo-400");
                    }};
                    reader.readAsText(file, "UTF-8");
                }}
            }});
        }}

        function handleFileSelect(event, textareaId) {{
            const input = event.target;
            if (input.files && input.files[0]) {{
                const reader = new FileReader();
                reader.onload = function(e) {{
                    document.getElementById(textareaId).value = e.target.result;
                    showCookieMsg("فایل کوکی با موفقیت وارد شد.", "text-indigo-400");
                }};
                reader.readAsText(input.files[0], "UTF-8");
            }}
        }}

        function showCookieMsg(msg, colorClass) {{
            const el = document.getElementById('cookieStatusMsg');
            el.className = `text-xs font-semibold ${{colorClass}}`;
            el.innerText = msg;
            setTimeout(() => {{
                el.innerText = '';
            }}, 4000);
        }}

        function clearCookieField(target) {{
            if (!confirm(`آیا از پاک‌سازی کوکی‌های بخش ${{target}} اطمینان دارید؟`)) return;
            if (target === 'general') document.getElementById('generalCookiesText').value = '';
            if (target === 'youtube') document.getElementById('youtubeCookiesText').value = '';
            if (target === 'instagram') document.getElementById('instagramCookiesText').value = '';

            fetch(`/api/cookies/${{target}}${{getAuthParams()}}`, {{ method: 'DELETE' }})
                .then(res => res.json())
                .then(data => {{
                    showCookieMsg(`کوکی‌های ${{target}} پاک‌سازی شدند.`, "text-amber-400");
                }})
                .catch(err => {{
                    showCookieMsg("خطا در برقراری ارتباط با سرور", "text-rose-400");
                }});
        }}

        function exportCookieFile(target) {{
            let content = '';
            let filename = 'cookies.txt';
            if (target === 'general') {{
                content = document.getElementById('generalCookiesText').value;
                filename = 'cookies.txt';
            }} else if (target === 'youtube') {{
                content = document.getElementById('youtubeCookiesText').value;
                filename = 'youtube_cookies.txt';
            }} else if (target === 'instagram') {{
                content = document.getElementById('instagramCookiesText').value;
                filename = 'instagram_cookies.txt';
            }}
            if (!content) {{
                alert('محتوای کوکی خالی است.');
                return;
            }}
            const blob = new Blob([content], {{ type: 'text/plain;charset=utf-8' }});
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            a.click();
            URL.revokeObjectURL(a.href);
        }}

        function saveCookiesForm() {{
            const mode = document.querySelector('input[name="cookieMode"]:checked').value;
            const general = document.getElementById('generalCookiesText').value;
            const youtube = document.getElementById('youtubeCookiesText').value;
            const instagram = document.getElementById('instagramCookiesText').value;

            const btn = document.getElementById('saveCookieBtn');
            const originalBtn = btn.innerHTML;
            btn.innerHTML = '<span>⏳</span><span>در حال ذخیره...</span>';
            btn.disabled = true;

            fetch(`/api/cookies${{getAuthParams()}}`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    mode: mode,
                    general_cookies: general,
                    youtube_cookies: youtube,
                    instagram_cookies: instagram
                }})
            }})
            .then(res => {{
                if (!res.ok) throw new Error("HTTP error " + res.status);
                return res.json();
            }})
            .then(res => {{
                btn.innerHTML = originalBtn;
                btn.disabled = false;
                showCookieMsg("✓ " + (res.message || "تنظیمات کوکی با موفقیت ذخیره شد."), "text-emerald-400");
                if (res.data) {{
                    document.getElementById('generalBadge').innerText = `${{res.data.general_count}} کوکی فعال`;
                    document.getElementById('youtubeBadge').innerText = `${{res.data.youtube_count}} کوکی`;
                    document.getElementById('instagramBadge').innerText = `${{res.data.instagram_count}} کوکی`;
                }}
            }})
            .catch(err => {{
                btn.innerHTML = originalBtn;
                btn.disabled = false;
                showCookieMsg("✕ خطا در ذخیره کوکی‌ها. لطفا مجدد تلاش کنید.", "text-rose-400");
            }});
        }}

        function copyText(text, btn) {{
            if (!text) return;
            navigator.clipboard.writeText(text).then(() => {{
                const oldText = btn.innerText;
                btn.innerText = "✓ کپی شد";
                btn.classList.add("bg-emerald-600", "text-white");
                setTimeout(() => {{
                    btn.innerText = oldText;
                    btn.classList.remove("bg-emerald-600", "text-white");
                }}, 1800);
            }}).catch(err => {{
                console.error("Failed to copy text: ", err);
            }});
        }}

        function filterJobs() {{
            const input = document.getElementById("searchInput").value.toLowerCase().trim();
            const rows = document.querySelectorAll(".job-row");
            let count = 0;

            rows.forEach(row => {{
                const searchStr = row.getAttribute("data-search") || "";
                if (!input || searchStr.includes(input)) {{
                    row.style.display = "";
                    count++;
                }} else {{
                    row.style.display = "none";
                }}
            }});

            const countElem = document.getElementById("searchCount");
            if (countElem) {{
                countElem.innerText = count + " مورد یافت شد";
            }}
        }}

        function formatShamsiDate(ts) {{
            if (!ts || ts <= 0) return "-";
            const date = new Date(ts * 1000);
            try {{
                const formatter = new Intl.DateTimeFormat('fa-IR-u-ca-persian-u-nu-latn', {{
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false
                }});
                const parts = formatter.formatToParts(date);
                let year='', month='', day='', hour='', minute='', second='';
                for (const p of parts) {{
                    if (p.type === 'year') year = p.value;
                    if (p.type === 'month') month = p.value;
                    if (p.type === 'day') day = p.value;
                    if (p.type === 'hour') hour = p.value;
                    if (p.type === 'minute') minute = p.value;
                    if (p.type === 'second') second = p.value;
                }}
                return `${{year}}-${{month}}-${{day}} ${{hour}}:${{minute}}:${{second}}`;
            }} catch (e) {{
                return date.toLocaleString('fa-IR');
            }}
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            try {{
                const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "Local";
                const tzElem = document.getElementById("visitorTimezone");
                if (tzElem) {{
                    tzElem.innerText = "🌐 " + userTz;
                }}
            }} catch (e) {{}}

            setupDropzone("generalDropzone", "generalCookiesText");
            setupDropzone("youtubeDropzone", "youtubeCookiesText");
            setupDropzone("instagramDropzone", "instagramCookiesText");

            document.querySelectorAll(".shamsi-time").forEach(elem => {{
                const ts = parseFloat(elem.getAttribute("data-timestamp"));
                if (ts) {{
                    elem.innerText = formatShamsiDate(ts);
                }}
            }});
        }});
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)

