"""
Web Processor Service for SubCraft Web Studio.
Manages asynchronous media processing jobs, tracking statuses,
and orchestrating download, transcription, translation, and media packaging.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, Any, Optional

from app.config import settings
from app.services.downloader import DownloaderService
from app.services.media_processor import MediaProcessor, NoAudioTrackError
from app.services.stt_service import STTService
from app.services.translator import TranslationService
from app.services.storage_cleaner import OutputStorageManager
from app.services.job_tracker import job_tracker
from app.utils.srt import (
    format_srt_by_language_mode,
    srt_to_plain_text,
    srt_to_lrc
)

logger = logging.getLogger("clip_srt_bot")

class WebProcessorService:
    def __init__(self):
        # In-memory dictionary of running/completed web jobs
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        url: str,
        output_format: str = "mkv",
        language_mode: str = "bilingual",
        engine: str = "ai"
    ) -> str:
        """
        Registers a new background web studio job and initiates processing.
        """
        task_id = str(uuid.uuid4())[:8]
        clean_url = DownloaderService.extract_url(url)
        if not clean_url:
            raise ValueError("لینک وارد شده نامعتبر است.")

        # Normalize options
        valid_formats = ("mkv", "mp3", "srt", "txt")
        fmt = output_format.lower().strip() if output_format else "mkv"
        if fmt not in valid_formats:
            fmt = "mkv"

        valid_modes = ("bilingual", "translation_only", "original_only")
        mode = language_mode.lower().strip() if language_mode else "bilingual"
        if mode not in valid_modes:
            mode = "bilingual"

        eng = "google" if engine == "google" else "ai"

        job_info = {
            "task_id": task_id,
            "url": clean_url,
            "format": fmt,
            "language_mode": mode,
            "engine": eng,
            "status": "queued",
            "progress_percent": 5,
            "progress_text": "در صف پردازش...",
            "error": None,
            "title": "در حال دریافت...",
            "channel": "",
            "download_url": None,
            "created_at": time.time(),
            "completed_at": None,
        }

        async with self._lock:
            self._jobs[task_id] = job_info

        # Record in persistent job tracker as well
        job_tracker.add_job(
            job_id=f"web_{task_id}",
            user_id=0,
            username="Web Studio User",
            input_url_or_file=clean_url,
            status="pending"
        )

        # Launch background processing
        asyncio.create_task(self._run_job(task_id))
        return task_id

    def get_job_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns status dictionary of the job.
        """
        return self._jobs.get(task_id)

    async def _update_job(self, task_id: str, **kwargs):
        async with self._lock:
            if task_id in self._jobs:
                self._jobs[task_id].update(kwargs)

    async def _run_job(self, task_id: str):
        job = self.get_job_status(task_id)
        if not job:
            return

        work_dir = os.path.join(tempfile.gettempdir(), f"web_studio_{task_id}")
        os.makedirs(work_dir, exist_ok=True)

        url = job["url"]
        fmt = job["format"]
        language_mode = job["language_mode"]
        engine = job["engine"]

        try:
            # 1. Download Media
            await self._update_job(task_id, status="downloading", progress_percent=15, progress_text="در حال دانلود رسانه...")
            job_tracker.update_job(f"web_{task_id}", status="processing")

            input_path, title, channel = await DownloaderService.download_web_media(url, work_dir)
            await self._update_job(task_id, title=title, channel=channel)
            is_video = input_path.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi"))

            # 2. Extract Audio
            await self._update_job(task_id, status="extracting", progress_percent=35, progress_text="در حال استخراج صوت...")
            audio_path = os.path.join(work_dir, "extracted_audio.mp3")
            await MediaProcessor.extract_audio(input_path, audio_path)

            # 3. Speech-to-Text via Groq Whisper
            await self._update_job(task_id, status="transcribing", progress_percent=55, progress_text="تبدیل گفتار به متن با هوش مصنوعی...")
            stt_service = STTService()
            original_srt = await stt_service.transcribe(audio_path)

            # 4. Translation (if needed)
            translated_srt = None
            if language_mode != "original_only":
                eng_name = "مترجم گوگل" if engine == "google" else "هوش مصنوعی"
                await self._update_job(
                    task_id,
                    status="translating",
                    progress_percent=75,
                    progress_text=f"ترجمه زیرنویس به فارسی با {eng_name}..."
                )
                translation_service = TranslationService()
                translated_srt = await translation_service.translate_to_persian(original_srt, engine=engine)

            final_srt = format_srt_by_language_mode(original_srt, translated_srt, language_mode=language_mode)

            # 5. Packaging final output
            await self._update_job(task_id, status="packaging", progress_percent=90, progress_text="تولید فایل نهایی خروجی...")

            final_output_path = None
            safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()[:40] or "media"

            if fmt == "mkv":
                if not is_video:
                    # If input was not video, package as MP3 with LRC
                    fmt = "mp3"
                else:
                    srt_file = os.path.join(work_dir, "subs.srt")
                    with open(srt_file, "w", encoding="utf-8") as f:
                        f.write(final_srt)
                    out_video = os.path.join(work_dir, f"{safe_title}.mkv")
                    await MediaProcessor.embed_subtitles_soft(input_path, srt_file, out_video)
                    final_output_path = out_video

            if fmt == "mp3":
                lrc_text = srt_to_lrc(final_srt, title=title, artist=channel)
                out_mp3 = os.path.join(work_dir, f"{safe_title}.mp3")
                await MediaProcessor.embed_lyrics_mp3(
                    audio_path,
                    lrc_text,
                    out_mp3,
                    title=title,
                    artist=channel
                )
                final_output_path = out_mp3

            elif fmt == "srt":
                out_srt = os.path.join(work_dir, f"{safe_title}.srt")
                with open(out_srt, "w", encoding="utf-8") as f:
                    f.write(final_srt)
                final_output_path = out_srt

            elif fmt == "txt":
                out_txt = os.path.join(work_dir, f"{safe_title}.txt")
                with open(out_txt, "w", encoding="utf-8") as f:
                    f.write(srt_to_plain_text(final_srt))
                final_output_path = out_txt

            # Register with OutputStorageManager (FIFO buffer retention)
            if final_output_path and os.path.exists(final_output_path):
                registered_path = OutputStorageManager.register_output(final_output_path, task_id, title)
                download_url = f"/api/web-download/{task_id}"

                await self._update_job(
                    task_id,
                    status="ready",
                    progress_percent=100,
                    progress_text="پردازش با موفقیت تکمیل شد!",
                    download_url=download_url,
                    completed_at=time.time()
                )
                job_tracker.update_job(f"web_{task_id}", status="completed")
            else:
                raise RuntimeError("فایل خروجی یافت نشد.")

        except NoAudioTrackError as audio_err:
            logger.warning(f"Web task {task_id} failed: {audio_err}")
            await self._update_job(
                task_id,
                status="failed",
                error="ویدیوی ارسالی فاقد ترک صوتی (صدا) است."
            )
            job_tracker.update_job(f"web_{task_id}", status="error", error_message="No audio track")

        except Exception as e:
            logger.error(f"Web task {task_id} error: {e}", exc_info=True)
            await self._update_job(
                task_id,
                status="failed",
                error=str(e)[:250]
            )
            job_tracker.update_job(f"web_{task_id}", status="error", error_message=str(e))

        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)


web_processor = WebProcessorService()
