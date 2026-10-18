import asyncio
import html
import logging
import os
import shutil
import tempfile
import uuid
import time
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from app.config import settings
from app.services.downloader import DownloaderService
from app.services.media_processor import MediaProcessor, NoAudioTrackError
from app.services.stt_service import STTService
from app.services.translator import TranslationService
from app.services.job_tracker import job_tracker
from app.services.telegraph import TelegraphService
from app.services.telegram_rich import TelegramRichService
from app.utils.srt import merge_bilingual_srt, srt_to_alternating_text, srt_to_lrc

logger = logging.getLogger("clip_srt_bot")

def extract_input_desc(message) -> str:
    """Extracts a short human-readable description of the user's input."""
    if not message:
        return "Unknown"
    if message.video:
        size_mb = (message.video.file_size or 0) / (1024 * 1024)
        return f"Video ({size_mb:.1f}MB)" if size_mb else "Video"
    elif message.document:
        name = message.document.file_name or "Document"
        size_mb = (message.document.file_size or 0) / (1024 * 1024)
        return f"Doc: {name} ({size_mb:.1f}MB)" if size_mb else f"Doc: {name}"
    elif message.audio or message.voice:
        audio_obj = message.audio or message.voice
        size_mb = (audio_obj.file_size or 0) / (1024 * 1024)
        return f"Audio ({size_mb:.1f}MB)" if message.audio else "Voice Message"
    elif message.text:
        url = DownloaderService.extract_url(message.text)
        return url if url else message.text[:60]
    return "Unknown Input"


def get_plain_footer(channel: str = "", translate_method: str = "هوش مصنوعی", bot_username: str = "instazirnevisbot") -> str:
    safe_channel = (channel or "").strip()
    if not safe_channel or safe_channel == "Unknown Channel":
        channel_line = "از پیج نامشخص"
    elif safe_channel.startswith("@"):
        channel_line = f"از پیج {safe_channel}"
    else:
        if " " not in safe_channel:
            channel_line = f"از پیج @{safe_channel}"
        else:
            channel_line = f"از پیج {safe_channel}"

    return (
        f"{channel_line}\n"
        f"ترجمه شده با {translate_method} توسط ربات @{bot_username}"
    )


async def get_message_footer(
    context: ContextTypes.DEFAULT_TYPE,
    channel: str = "",
    translate_method: str = "هوش مصنوعی"
) -> str:
    """Generates the standardized Persian caption/message footer."""
    bot_username = context.bot.username
    if not bot_username:
        try:
            bot_info = await context.bot.get_me()
            bot_username = bot_info.username or "instazirnevisbot"
        except Exception:
            bot_username = "instazirnevisbot"

    safe_channel = (channel or "").strip()
    if not safe_channel or safe_channel == "Unknown Channel":
        channel_line = "از پیج نامشخص"
    elif safe_channel.startswith("@"):
        channel_line = f"از پیج {html.escape(safe_channel)}"
    else:
        if " " not in safe_channel:
            channel_line = f"از پیج @{html.escape(safe_channel)}"
        else:
            channel_line = f"از پیج {html.escape(safe_channel)}"

    return (
        f"{channel_line}\n"
        f"ترجمه شده با {translate_method} توسط ربات @{bot_username}"
    )


# Track active jobs to handle callback responses and retries
active_jobs = {}
MAX_TG_FILE_SIZE = 20 * 1024 * 1024  # 20 MB Telegram Bot API limit

async def safe_update_status(message, text: str, **kwargs):
    """Safely updates status text or photo caption of a Telegram message, suppressing 'Message is not modified' error."""
    try:
        if message.photo:
            return await message.edit_caption(caption=text, **kwargs)
        else:
            return await message.edit_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return message
        raise e


async def safe_edit_text(message, text: str, **kwargs):
    """Backwards-compatible wrapper for safe_update_status."""
    return await safe_update_status(message, text, **kwargs)


def get_cancel_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Creates inline keyboard with a Cancel Process button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو عملیات", callback_data=f"cancel_{job_id}")]
    ])


def clean_old_jobs():
    """Removes temporary directories and cancels active tasks for jobs older than 1 hour."""
    now = time.time()
    expired = []
    for job_id, job_info in active_jobs.items():
        if now - job_info.get('timestamp', 0) > 3600:
            expired.append(job_id)
    for job_id in expired:
        job_info = active_jobs.pop(job_id, None)
        if job_info:
            task = job_info.get('task')
            if task and not task.done():
                task.cancel()
            if 'work_dir' in job_info:
                logger.info(f"Cleaning up expired job {job_id} at {job_info['work_dir']}")
                shutil.rmtree(job_info['work_dir'], ignore_errors=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    welcome_text = (
        "👋 <b>به ربات زیرنویس‌ساز خوش آمدید!</b>\n\n"
        "لطفاً یک <b>ویدیو کوتاه</b>، <b>فایل صوتی</b>، <b>پیام صوتی</b> یا یک <b>لینک ویدیو</b> (اینستاگرام، یوتیوب، تیک‌تاک، توییتر و ...) ارسال کنید.\n\n"
        "✨ <b>امکانات ربات:</b>\n"
        "۱. استخراج صدا و تبدیل گفتار به متن با <b>Groq Whisper (whisper-large-v3)</b>.\n"
        f"۲. ترجمه زیرنویس به فارسی روان با <b>Groq AI ({settings.groq_translate_model})</b> به صورت خط به خط متناوب.\n"
        "۳. امکان انتخاب دریافت ویدیو به صورت فایل با زیرنویس سافت‌ساب یا فقط متن ترجمه شده.\n\n"
        "📖 جهت مشاهده راهنما و فرمت‌های پشتیبانی شده: /help\n"
        "ℹ️ درباره توسعه‌دهنده و پروژه: /about"
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command with detailed input documentation."""
    help_text = (
        "📖 <b>راهنمای جامع استفاده از ربات زیرنویس‌ساز</b>\n\n"
        "📥 <b>ورودی‌های پشتیبانی شده:</b>\n\n"
        "۱. <b>ارسال مستقیم فایل رسانه:</b>\n"
        "• <b>ویدیوها:</b> MP4, MKV, MOV, AVI, WEBM, FLV, M4V (حداکثر ۲۰ مگابایت)\n"
        "• <b>صوت و ویس:</b> MP3, WAV, AAC, M4A, FLAC, OGG, OPUS و ویس‌های تلگرام\n\n"
        "۲. <b>ارسال لینک رسانه (بدون محدودیت حجم):</b>\n"
        "• <b>اینستاگرام:</b> ریلمز (Reels)، پست‌ها و کلیپ‌های IGTV\n"
        "• <b>یوتیوب:</b> ویدیوهای اصلی و یوتیوب شورتس (Shorts)\n"
        "• <b>تیک‌تاک & توییتر (X):</b> کلیپ‌ها و ویدیوهای توییت شده\n"
        "• <b>لینک مستقیم دانلود:</b> فایل‌های مستقیم .mp4 و .mp3\n\n"
        "⚙️ <b>دستورات ربات:</b>\n"
        "• /start - شروع به کار و معرفی اولیه\n"
        "• /help - راهنمای جامع و فرمت‌های پشتیبانی شده\n"
        "• /about - شناسنامه سازنده، سورس‌کد و تکنولوژی‌ها\n\n"
        "🔄 <b>مراحل پردازش:</b>\n"
        "۱. ارسال فایل یا لینک ⬅️ ۲. استخراج صدا و تبدیل گفتار به متن ⬅️ ۳. ترجمه فارسی خط به خط ⬅️ ۴. انتخاب خروجی (ویدیو زیرنویس‌دار، صوت MP3 با لیریکس همگام Musicolet، یا متن ترجمه)."
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode="HTML")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /about command for developer branding and system specs."""
    about_text = (
        f"👤 <b>درباره ربات و شناسنامه توسعه‌دهنده (نسخه v{settings.app_version})</b>\n\n"
        f"📌 <b>نسخه فعلی:</b> <code>v{settings.app_version}</code> (Beta)\n"
        "👨‍💻 <b>توسعه‌دهنده:</b> مهدی چمنی\n"
        "📧 <b>ایمیل:</b> <code>mahdi.chamani20@gmail.com</code>\n"
        "💬 <b>تلگرام:</b> <a href=\"https://t.me/mehdichamanni\">@mehdichamanni</a>\n\n"
        "📦 <b>مخزن گیت‌هاب (GitHub):</b>\n"
        "🔗 <a href=\"https://github.com/mehdichamani/SubCraft\">github.com/mehdichamani/SubCraft</a>\n\n"
        "🛠 <b>تکنولوژی‌های استفاده شده (Tech Stack):</b>\n"
        "• <b>دانلود رسانه:</b> yt-dlp & PTB API\n"
        "• <b>پردازش رسانه:</b> FFmpeg (16kHz Audio Extraction & Soft Subtitle Remuxing) + Mutagen (ID3 Synced LRC)\n"
        "• <b>تبدیل گفتار به متن (STT):</b> Groq Whisper API (whisper-large-v3) / OpenAI\n"
        f"• <b>ترجمه هوشمند:</b> Groq AI ({settings.groq_translate_model}) / Gemini / OpenAI\n"
        "• <b>سرور & وب‌هوک:</b> FastAPI + Uvicorn + python-telegram-bot\n\n"
        "☁️ <b>میزبانی:</b> hosted by <a href=\"https://render.com\">render.com</a>"
    )
    if update.message:
        await update.message.reply_text(about_text, parse_mode="HTML", disable_web_page_preview=True)


async def process_media_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: Optional[str] = None) -> None:
    """Core processor for video/audio attachments or media URLs."""
    if not update.message:
        return

    # Periodically clean expired temporary jobs
    clean_old_jobs()

    # Check API Key configuration
    if not settings.groq_api_key:
        await update.message.reply_text(
            "⚠️ **خطای پیکربندی:** کلید API برای Groq در سرور تنظیم نشده است. لطفاً متغیرهای محیطی را بررسی کنید."
        )
        return

    message = update.message
    
    if not job_id:
        job_id = str(uuid.uuid4())[:8]

    user = update.effective_user
    user_id = user.id if user else None
    username = f"@{user.username}" if (user and user.username) else (user.full_name if user else "Unknown")
    input_desc = extract_input_desc(message)
    
    job_tracker.add_job(
        job_id=job_id,
        user_id=user_id,
        username=username,
        input_url_or_file=input_desc,
        status="pending"
    )
        
    work_dir = os.path.join(tempfile.gettempdir(), f"clip_srt_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    cancel_kb = get_cancel_keyboard(job_id)
    status_msg = await message.reply_text("⏳ در حال پردازش درخواست شما...", reply_markup=cancel_kb)

    # Save initial job info with update object and task reference for cancellation
    active_jobs[job_id] = {
        'update': update,
        'work_dir': work_dir,
        'task': asyncio.current_task(),
        'timestamp': time.time()
    }

    input_path: Optional[str] = None
    is_video = False
    poster_path = os.path.join(work_dir, "poster.jpg")
    title: str = "Video"
    channel: str = "Unknown Channel"

    async def _send_poster_if_available(caption_text: str):
        nonlocal status_msg
        if os.path.exists(poster_path) and os.path.getsize(poster_path) > 0:
            try:
                with open(poster_path, "rb") as p_file:
                    poster_msg = await message.reply_photo(
                        photo=p_file,
                        caption=caption_text,
                        reply_markup=cancel_kb,
                        parse_mode="HTML"
                    )
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                status_msg = poster_msg
            except Exception as pe:
                logger.warning(f"Could not send poster photo message: {pe}")

    try:
        job_tracker.update_job(job_id, status="processing")
        # 1. Determine input source (Telegram File or Web URL)
        if message.video:
            if message.video.file_size and message.video.file_size > MAX_TG_FILE_SIZE:
                job_tracker.update_job(job_id, status="error", error_message="File size exceeds 20MB limit")
                await safe_update_status(
                    status_msg,
                    "⚠️ <b>حجم فایل بیش از ۲۰ مگابایت است:</b>\n\n"
                    "به دلیل محدودیت‌های تلگرام، امکان دانلود مستقیم فایل‌های بالای ۲۰ مگابایت توسط ربات‌ها وجود ندارد.\n\n"
                    "💡 <b>راهکار:</b> لطفاً <b>لینک مستقیم ویدیو</b> (از یوتیوب، اینستاگرام، تیک‌تاک و ...) را ارسال کنید تا بدون محدودیت پردازش شود.",
                    parse_mode="HTML"
                )
                active_jobs.pop(job_id, None)
                if os.path.exists(work_dir):
                    shutil.rmtree(work_dir, ignore_errors=True)
                return

            is_video = True
            ext = ".mp4"
            input_path = os.path.join(work_dir, f"input{ext}")
            title = os.path.splitext(message.video.file_name)[0] if message.video.file_name else "Video"
            channel = "Unknown Channel"

            # Extract native Telegram video thumbnail prior to main file download
            if message.video.thumbnail:
                try:
                    await DownloaderService.download_telegram_file(context.bot, message.video.thumbnail.file_id, poster_path)
                    await _send_poster_if_available("🖼️ <b>پوستر رسانه دریافت شد.</b>\n📥 در حال دانلود ویدیو از تلگرام...")
                except Exception as te:
                    logger.warning(f"Failed to download Telegram video thumbnail: {te}")

            if not os.path.exists(poster_path):
                await safe_update_status(status_msg, "📥 در حال دانلود ویدیو از تلگرام...", reply_markup=cancel_kb)
            await DownloaderService.download_telegram_file(context.bot, message.video.file_id, input_path)

        elif message.document and (message.document.mime_type or "").startswith(("video/", "audio/")):
            if message.document.file_size and message.document.file_size > MAX_TG_FILE_SIZE:
                job_tracker.update_job(job_id, status="error", error_message="File size exceeds 20MB limit")
                await safe_update_status(
                    status_msg,
                    "⚠️ <b>حجم فایل بیش از ۲۰ مگابایت است:</b>\n\n"
                    "به دلیل محدودیت‌های تلگرام، امکان دانلود مستقیم فایل‌های بالای ۲۰ مگابایت توسط ربات‌ها وجود ندارد.\n\n"
                    "💡 <b>راهکار:</b> لطفاً <b>لینک مستقیم ویدیو</b> را ارسال کنید تا بدون محدودیت پردازش شود.",
                    parse_mode="HTML"
                )
                active_jobs.pop(job_id, None)
                if os.path.exists(work_dir):
                    shutil.rmtree(work_dir, ignore_errors=True)
                return

            is_video = (message.document.mime_type or "").startswith("video/")
            ext = os.path.splitext(message.document.file_name or "media")[1] or (".mp4" if is_video else ".mp3")
            input_path = os.path.join(work_dir, f"input{ext}")
            title = os.path.splitext(message.document.file_name)[0] if message.document.file_name else "Video"
            channel = "Unknown Channel"

            # Extract document thumbnail if available prior to main file download
            if message.document.thumbnail:
                try:
                    await DownloaderService.download_telegram_file(context.bot, message.document.thumbnail.file_id, poster_path)
                    await _send_poster_if_available("🖼️ <b>پوستر رسانه دریافت شد.</b>\n📥 در حال دانلود فایل از تلگرام...")
                except Exception as te:
                    logger.warning(f"Failed to download Telegram document thumbnail: {te}")

            if not os.path.exists(poster_path):
                await safe_update_status(status_msg, "📥 در حال دانلود فایل رسانه از تلگرام...", reply_markup=cancel_kb)
            await DownloaderService.download_telegram_file(context.bot, message.document.file_id, input_path)

        elif message.audio or message.voice:
            audio_obj = message.audio or message.voice
            if audio_obj and audio_obj.file_size and audio_obj.file_size > MAX_TG_FILE_SIZE:
                job_tracker.update_job(job_id, status="error", error_message="Audio size exceeds 20MB limit")
                await safe_update_status(
                    status_msg,
                    "⚠️ <b>حجم فایل صوتی بیش از ۲۰ مگابایت است:</b>\nلطفاً لینک مستقیم فایل صوتی یا ویدیو را ارسال کنید.",
                    parse_mode="HTML"
                )
                active_jobs.pop(job_id, None)
                if os.path.exists(work_dir):
                    shutil.rmtree(work_dir, ignore_errors=True)
                return

            is_video = False
            file_id = message.audio.file_id if message.audio else message.voice.file_id
            input_path = os.path.join(work_dir, "input.m4a" if message.voice else "input.mp3")
            if message.audio:
                title = message.audio.title if message.audio.title else "Audio"
                channel = message.audio.performer if message.audio.performer else "Unknown Channel"
            else:
                title = "Voice Message"
                channel = "Unknown Channel"

            await safe_update_status(status_msg, "📥 در حال دانلود صدا از تلگرام...", reply_markup=cancel_kb)
            await DownloaderService.download_telegram_file(context.bot, file_id, input_path)

        elif message.text:
            url = DownloaderService.extract_url(message.text)
            if not url:
                job_tracker.update_job(job_id, status="error", error_message="No valid media link or input")
                await safe_update_status(status_msg, "💡 لطفاً یک ویدیو، فایل صوتی یا لینک معتبر ارسال کنید.")
                active_jobs.pop(job_id, None)
                if os.path.exists(work_dir):
                    shutil.rmtree(work_dir, ignore_errors=True)
                return

            # Fetch web thumbnail prior to downloading main video file
            has_web_thumb = await DownloaderService.fetch_web_thumbnail(url, poster_path)
            if has_web_thumb:
                await _send_poster_if_available(f"🖼️ <b>پوستر رسانه دریافت شد.</b>\n🌐 در حال دانلود رسانه از لینک:\n<code>{url}</code>...")
            else:
                await safe_update_status(status_msg, f"🌐 در حال دانلود رسانه از لینک:\n`{url}`...", parse_mode="Markdown", reply_markup=cancel_kb)

            input_path, title, channel = await DownloaderService.download_web_media(url, work_dir)
            is_video = input_path.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi"))

        else:
            job_tracker.update_job(job_id, status="error", error_message="Unsupported media format")
            await safe_update_status(status_msg, "❓ فرمت فایل پشتیبانی نمی‌شود. لطفاً ویدیو، صدا یا لینک ویدیو بفرستید.")
            active_jobs.pop(job_id, None)
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
            return

        # 2. Extract Audio (16kHz mono MP3)
        audio_path = os.path.join(work_dir, "extracted_audio.mp3")
        await safe_update_status(status_msg, "🎵 در حال استخراج صدا...", reply_markup=cancel_kb, parse_mode="HTML")
        await MediaProcessor.extract_audio(input_path, audio_path)

        # 3. Speech-to-Text via Groq Whisper-large-v3
        await safe_update_status(status_msg, "🎙️ در حال تبدیل گفتار به متن با Groq Whisper...", reply_markup=cancel_kb, parse_mode="HTML")
        stt_service = STTService()
        english_srt = await stt_service.transcribe(audio_path)

        # 4. Generate Persian subject headline from first 10 text rows using AI
        translation_service = TranslationService()
        subject = await translation_service.generate_persian_subject(english_srt)
        job_tracker.update_job(job_id, subject=subject)

        # Store job information for the callback query handler (waiting for user choice)
        active_jobs[job_id].update({
            'is_video': is_video,
            'english_srt': english_srt,
            'input_path': input_path,
            'title': title,
            'channel': channel,
            'subject': subject,
            'timestamp': time.time()
        })

        help_url = f"{settings.render_external_url.rstrip('/')}/translation-help" if settings.render_external_url else "https://github.com/mehdichamani/SubCraft"

        # Send completion options with 6 format/engine choices, Help link, and Cancel button
        keyboard = [
            [InlineKeyboardButton("🎬 ویدیو با زیرنویس (گوگل ترنسلیت)", callback_data=f"opt_vid_gt_{job_id}")],
            [InlineKeyboardButton("🎬 ویدیو با زیرنویس (هوش مصنوعی / AI)", callback_data=f"opt_vid_ai_{job_id}")],
            [InlineKeyboardButton("🎵 آهنگ با متن (گوگل ترنسلیت)", callback_data=f"opt_mp3_gt_{job_id}")],
            [InlineKeyboardButton("🎵 آهنگ با متن (هوش مصنوعی / AI)", callback_data=f"opt_mp3_ai_{job_id}")],
            [InlineKeyboardButton("📄 فقط متن ترجمه (گوگل ترنسلیت)", callback_data=f"opt_txt_gt_{job_id}")],
            [InlineKeyboardButton("📄 فقط متن ترجمه (هوش مصنوعی / AI)", callback_data=f"opt_txt_ai_{job_id}")],
            [InlineKeyboardButton("❓ راهنما و مقایسه تفاوت موتورها", url=help_url)],
            [InlineKeyboardButton("❌ لغو / حذف", callback_data=f"cancel_{job_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_update_status(
            status_msg,
            "🎙️ <b>تبدیل گفتار به متن با موفقیت انجام شد.</b>\n"
            "لطفاً <b>فرمت خروجی</b> و <b>موتور ترجمه</b> مورد نظر خود را انتخاب کنید:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    except asyncio.CancelledError:
        logger.info(f"Job {job_id} was cancelled by user.")
        job_tracker.update_job(job_id, status="canceled", error_message="Canceled by user")
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        active_jobs.pop(job_id, None)
        raise

    except BadRequest as br_err:
        err_msg = str(br_err)
        if "message is not modified" in err_msg.lower():
            logger.debug(f"Ignored 'Message is not modified' error: {br_err}")
            return
        logger.error(f"Telegram BadRequest in process_media_job: {br_err}")
        job_tracker.update_job(job_id, status="error", error_message=err_msg)
        if "file is too big" in err_msg.lower():
            user_msg = (
                "⚠️ <b>حجم فایل بیش از حد مجاز تلگرام است:</b>\n\n"
                "تلگرام اجازه دانلود فایل‌های بالای ۲۰ مگابایت را به ربات‌ها نمی‌دهد.\n"
                "💡 <b>راهکار:</b> لطفاً <b>لینک مستقیم ویدیو</b> را ارسال کنید."
            )
        else:
            user_msg = f"❌ <b>خطای درخواست تلگرام:</b>\n<code>{html.escape(err_msg[:300])}</code>"
        
        await message.reply_text(user_msg, parse_mode="HTML")
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    except NoAudioTrackError as audio_err:
        logger.warning(f"Job {job_id} failed due to missing audio track: {audio_err}")
        job_tracker.update_job(job_id, status="error", error_message="No audio track found")
        no_audio_msg = (
            "⚠️ <b>ویدیوی ارسالی فاقد ترک صوتی (صدا) است:</b>\n\n"
            "برای تبدیل گفتار به متن و ایجاد زیرنویس، فایل ارسالی باید دارای صدای قابل استخراج باشد.\n\n"
            "💡 <b>نکات راهنما:</b>\n"
            "• اگر ویدیو صامت، گیف، یا بدون ترک صداست، امکان رونویسی آن وجود ندارد.\n"
            "• در صورت ارسال لینک آنلاین (اینستاگرام، یوتیوب، تیک‌تاک و ...)، مطمئن شوید ویدیو در مبدأ صدادار است و دسترسی به آن به دلیل کوکی یا محدودیت منطقه‌ای مسدود نشده باشد."
        )
        retry_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"retry_{job_id}")]
        ])
        await message.reply_text(no_audio_msg, reply_markup=retry_keyboard, parse_mode="HTML")
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        job_tracker.update_job(job_id, status="error", error_message=str(e))
        # Send a NEW message with HTML-escaped error details and Retry button
        retry_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"retry_{job_id}")]
        ])
        safe_err = html.escape(str(e)[:300])
        await message.reply_text(
            f"❌ <b>عملیات با خطا مواجه شد:</b>\n<code>{safe_err}</code>\n\nمی‌توانید با دکمه زیر مجدداً تلاش کنید:",
            reply_markup=retry_keyboard,
            parse_mode="HTML"
        )
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callback buttons: Retry, Cancel, and output format/translation engine selection."""
    query = update.callback_query
    await query.answer()

    data = query.data
    valid_prefixes = (
        "opt_vid_gt_", "opt_vid_ai_",
        "opt_mp3_gt_", "opt_mp3_ai_",
        "opt_txt_gt_", "opt_txt_ai_",
        "softsub_", "text_", "retry_", "cancel_"
    )
    if not any(data.startswith(prefix) for prefix in valid_prefixes):
        return

    # Handle Cancel action
    if data.startswith("cancel_"):
        _, job_id = data.split("_", 1)
        job_tracker.update_job(job_id, status="canceled", error_message="Canceled by user")
        job_info = active_jobs.pop(job_id, None)
        if job_info:
            task = job_info.get('task')
            if task and not task.done():
                task.cancel()
            work_dir = job_info.get('work_dir')
            if work_dir and os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
            
            await safe_update_status(
                query.message,
                "❌ <b>عملیات توسط کاربر لغو شد.</b>",
                parse_mode="HTML",
                reply_markup=None
            )
            await query.answer("عملیات لغو شد.")
        else:
            try:
                await safe_update_status(
                    query.message,
                    "❌ <b>این عملیات لغو شده یا منقضی شده است.</b>",
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception:
                pass
            await query.answer("عملیات منقضی شده است.")
        return

    # Handle Retry action
    if data.startswith("retry_"):
        _, job_id = data.split("_", 1)
        job_info = active_jobs.get(job_id)
        if not job_info or 'update' not in job_info:
            await query.message.reply_text("❌ خطا: اطلاعات این درخواست دیگر در دسترس نیست (احتمالاً منقضی شده است). لطفاً فایل یا لینک را دوباره ارسال کنید.")
            return
        
        orig_update = job_info['update']
        if 'work_dir' in job_info and os.path.exists(job_info['work_dir']):
            shutil.rmtree(job_info['work_dir'], ignore_errors=True)
            
        await query.message.reply_text("🔄 در حال تلاش مجدد برای پردازش...")
        await process_media_job(orig_update, context, job_id=job_id)
        return

    # Parse action and job_id
    if data.startswith("softsub_"):
        action, job_id = "opt_vid_ai", data[8:]
    elif data.startswith("text_"):
        action, job_id = "opt_txt_ai", data[5:]
    elif data.startswith("opt_vid_gt_"):
        action, job_id = "opt_vid_gt", data[11:]
    elif data.startswith("opt_vid_ai_"):
        action, job_id = "opt_vid_ai", data[11:]
    elif data.startswith("opt_mp3_gt_"):
        action, job_id = "opt_mp3_gt", data[11:]
    elif data.startswith("opt_mp3_ai_"):
        action, job_id = "opt_mp3_ai", data[11:]
    elif data.startswith("opt_txt_gt_"):
        action, job_id = "opt_txt_gt", data[11:]
    elif data.startswith("opt_txt_ai_"):
        action, job_id = "opt_txt_ai", data[11:]
    else:
        return

    job_info = active_jobs.get(job_id)

    if not job_info or 'english_srt' not in job_info:
        await query.message.reply_text("❌ خطا: اطلاعات این درخواست دیگر در دسترس نیست (احتمالاً منقضی شده است). لطفاً فایل را دوباره ارسال کنید.")
        return

    work_dir = job_info['work_dir']
    english_srt = job_info['english_srt']
    input_path = job_info['input_path']
    is_video = job_info['is_video']
    title = job_info.get('title', 'Video')
    channel = job_info.get('channel', 'Unknown Channel')
    subject = job_info.get('subject', 'خلاصه ویدیو')

    try:
        job_tracker.update_job(job_id, status="processing")

        is_google = action in ("opt_vid_gt", "opt_mp3_gt", "opt_txt_gt")
        is_video_mode = action in ("opt_vid_gt", "opt_vid_ai")
        is_mp3_mode = action in ("opt_mp3_gt", "opt_mp3_ai")
        engine = "google" if is_google else "ai"
        translate_method = "مترجم گوگل" if is_google else "هوش مصنوعی"

        if is_video_mode and not is_video:
            await query.message.reply_text("⚠️ این فایل ویدیو نیست و امکان قرار دادن زیرنویس روی آن وجود ندارد. لطفاً یکی از گزینه‌های صوت MP3 یا متن را انتخاب کنید.")
            return

        engine_name = "گوگل ترنسلیت" if is_google else f"هوش مصنوعی ({settings.groq_translate_model})"
        status_msg = await query.message.reply_text(f"🌐 در حال ترجمه زیرنویس به فارسی با {engine_name}...")

        # Perform translation with selected engine
        translation_service = TranslationService()
        persian_srt = await translation_service.translate_to_persian(english_srt, engine=engine)

        # Merge into line-by-line alternating SRT
        bilingual_srt = merge_bilingual_srt(english_srt, persian_srt)
        srt_file_path = os.path.join(work_dir, "subtitles_bilingual.srt")
        with open(srt_file_path, "w", encoding="utf-8") as f:
            f.write(bilingual_srt)

        footer = await get_message_footer(context, channel=channel, translate_method=translate_method)
        safe_subject = html.escape(subject) if subject else "خلاصه ویدیو"

        if is_video_mode:
            await safe_update_status(status_msg, "🎬 در حال ریماکس کردن زیرنویس سافت‌ساب روی ویدیو...")
            sanitized_filename = DownloaderService.sanitize_filename(channel=channel, title=subject, ext=".mkv")
            output_video_path = os.path.join(work_dir, sanitized_filename)
            await MediaProcessor.embed_subtitles_soft(input_path, srt_file_path, output_video_path)

            poster_path = os.path.join(work_dir, "poster.jpg")
            thumb_file = open(poster_path, "rb") if os.path.exists(poster_path) else None

            caption = f"<b>{safe_subject}</b>\n\n{footer}"

            try:
                # Always send clip output explicitly as document attachment (send_document / reply_document)
                with open(output_video_path, "rb") as vid_file:
                    await query.message.reply_document(
                        document=vid_file,
                        filename=sanitized_filename,
                        caption=caption,
                        thumbnail=thumb_file,
                        parse_mode="HTML"
                    )
                logger.info(f"Sent subtitled video clip as document attachment ({output_video_path})")
            finally:
                if thumb_file:
                    thumb_file.close()

            try:
                await status_msg.delete()
            except Exception:
                pass

        elif is_mp3_mode:
            await safe_update_status(status_msg, "🎵 در حال آماده‌سازی صوت MP3 با لیریکس همگام Musicolet...")
            sanitized_mp3_filename = DownloaderService.sanitize_filename(channel=channel, title=subject, ext=".mp3")
            output_mp3_path = os.path.join(work_dir, sanitized_mp3_filename)
            raw_audio_path = os.path.join(work_dir, "extracted_audio.mp3")

            # Generate Musicolet-compatible timestamped LRC text
            lrc_content = srt_to_lrc(bilingual_srt, title=subject or "Clip Audio", artist=channel or "SubCraft")
            
            poster_path = os.path.join(work_dir, "poster.jpg")
            cover_path = poster_path if os.path.exists(poster_path) else None

            await MediaProcessor.embed_lyrics_mp3(
                audio_path=raw_audio_path,
                lrc_content=lrc_content,
                output_mp3_path=output_mp3_path,
                title=subject or "Clip Audio",
                artist=channel or "SubCraft",
                cover_path=cover_path
            )

            thumb_file = open(cover_path, "rb") if cover_path else None
            caption = f"🎵 <b>{safe_subject}</b>\n\n📝 <i>دارای متن ترانه همگام (Synced LRC Lyrics) سازگار با Musicolet</i>\n\n{footer}"

            try:
                with open(output_mp3_path, "rb") as mp3_file:
                    await query.message.reply_audio(
                        audio=mp3_file,
                        filename=sanitized_mp3_filename,
                        title=subject or "Clip Audio",
                        performer=channel or "SubCraft",
                        caption=caption,
                        thumbnail=thumb_file,
                        parse_mode="HTML"
                    )
                logger.info(f"Sent MP3 with Musicolet synced LRC lyrics ({output_mp3_path})")
            finally:
                if thumb_file:
                    thumb_file.close()

            try:
                await status_msg.delete()
            except Exception:
                pass

        else:
            translated_text = srt_to_alternating_text(bilingual_srt)
            full_text_message = (
                f"<b>{safe_subject}</b>\n\n"
                f"{translated_text}\n\n"
                f"{footer}"
            )

            bot_username = context.bot.username or "instazirnevisbot"
            plain_footer = get_plain_footer(channel=channel, translate_method=translate_method, bot_username=bot_username)

            if len(full_text_message) < 4000:
                await query.message.reply_text(
                    full_text_message,
                    parse_mode="HTML"
                )
            else:
                rich_sent = False
                try:
                    # Attempt modern Telegram Rich Message (Bot API 10.1+)
                    rich_blocks = TelegramRichService.build_bilingual_blocks(
                        bilingual_srt=bilingual_srt,
                        subject=subject or "خلاصه و متن ترجمه",
                        footer_text=plain_footer
                    )
                    await TelegramRichService.send_rich_message(
                        chat_id=query.message.chat_id,
                        blocks=rich_blocks
                    )
                    rich_sent = True
                    logger.info(f"Sent long bilingual translation via Telegram Rich Messages to chat {query.message.chat_id}")
                except Exception as rich_err:
                    logger.warning(f"Failed to send Telegram Rich Message, falling back: {rich_err}")

                if not rich_sent:
                    try:
                        telegraph_url = await TelegraphService.create_page(
                            title=subject or "خلاصه و زیرنویس",
                            text_content=translated_text,
                            author_name=f"@{bot_username}",
                            footer_text=plain_footer
                        )
                        telegraph_msg = (
                            f"<b>{safe_subject}</b>\n\n"
                            f"📖 <b>متن کامل به صورت مقاله در تلگراف ایجاد شد:</b>\n"
                            f"🔗 <a href=\"{telegraph_url}\">مشاهده مقاله در تلگراف (Instant View)</a>\n\n"
                            f"{footer}"
                        )
                        await query.message.reply_text(
                            telegraph_msg,
                            parse_mode="HTML"
                        )
                        logger.info(f"Sent long text output via Telegraph ({telegraph_url})")
                    except Exception as te_err:
                        logger.warning(f"Failed to create Telegraph page, falling back to TXT document: {te_err}")
                        txt_filename = DownloaderService.sanitize_filename(channel=channel, title=subject, ext=".txt")
                        txt_file_path = os.path.join(work_dir, txt_filename)
                        plain_header = f"{subject}\n\n" if subject else "خلاصه ویدیو\n\n"
                        doc_caption = f"<b>{safe_subject}</b>\n\n{footer}"
                        with open(txt_file_path, "w", encoding="utf-8") as tf:
                            tf.write(plain_header + translated_text + "\n\n" + plain_footer)
                        with open(txt_file_path, "rb") as tf:
                            await query.message.reply_document(
                                document=tf,
                                filename=txt_filename,
                                caption=doc_caption,
                                parse_mode="HTML"
                            )


        # Update status to done and clean up job directory
        job_tracker.update_job(job_id, status="done")
        active_jobs.pop(job_id, None)
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"Error handling callback choice {action}: {e}", exc_info=True)
        job_tracker.update_job(job_id, status="error", error_message=str(e))
        retry_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"retry_{job_id}")]
        ])
        safe_err = html.escape(str(e)[:300])
        await query.message.reply_text(
            f"❌ <b>خطایی در پردازش و ارسال فایل رخ داد:</b>\n<code>{safe_err}</code>\n\nمی‌توانید مجدداً تلاش کنید:",
            reply_markup=retry_keyboard,
            parse_mode="HTML"
        )

def create_telegram_application() -> Application:
    """Builds and configures python-telegram-bot Application instance."""
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is missing.")

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Register Command, Message & Callback Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL | filters.TEXT,
        process_media_job
    ))

    return app


