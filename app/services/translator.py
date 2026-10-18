import asyncio
import logging
import threading
from typing import Optional
from groq import Groq

try:
    from google import genai
except ImportError:
    genai = None

from deep_translator import GoogleTranslator

from app.config import settings
from app.utils.srt import clean_srt_response, parse_srt_blocks

logger = logging.getLogger("clip_srt_bot")


def mask_key(key: str) -> str:
    """Masks an API key for safe logging output."""
    if not key:
        return "None"
    if len(key) <= 10:
        return key[:2] + "..." + key[-2:]
    return key[:6] + "..." + key[-4:]


class TranslationService:
    """Service for translating SRT subtitles into Persian using Google Translate or AI models (Groq/Gemini)."""

    _counter_lock = threading.Lock()
    _key_index = 0

    def __init__(self, api_key: Optional[str] = None):
        self.explicit_key = api_key

    @classmethod
    def get_next_gemini_api_key(cls) -> Optional[str]:
        """Gets the next API key in round-robin order from configured Gemini keys."""
        keys = settings.get_gemini_api_keys()
        if not keys:
            return None
        with cls._counter_lock:
            key = keys[cls._key_index % len(keys)]
            cls._key_index = (cls._key_index + 1) % len(keys)
            return key

    async def translate_to_persian(self, srt_content: str, engine: str = "ai") -> str:
        """
        Translates input SRT content into Persian (Farsi).
        
        Args:
            srt_content: Raw SRT subtitle content.
            engine: "google" for Google Translate, or "ai" for Groq AI (with Gemini fallback).
        """
        if not srt_content.strip():
            raise ValueError("Input SRT content is empty.")

        if engine == "google":
            return await self.translate_with_google(srt_content)
        else:
            return await self.translate_with_ai(srt_content)

    async def translate_with_google(self, srt_content: str) -> str:
        """
        Translates SRT content to Persian block-by-block using Google Translate (deep-translator).
        Preserves exact timestamps and indices while preventing hallucinations/truncation.
        """
        blocks = parse_srt_blocks(srt_content)
        if not blocks:
            raise ValueError("Failed to parse SRT blocks for Google Translation.")

        logger.info(f"Translating {len(blocks)} subtitle blocks using Google Translate API...")

        def _do_google_translate(blocks_list):
            import time

            translator = GoogleTranslator(source="auto", target="fa")
            delimiter = "\n⟦§⟧\n"
            max_char_limit = 4000

            # Group blocks into chunks of at most 4000 characters
            chunks = []
            current_chunk = []
            current_length = 0

            for b in blocks_list:
                clean_text = b["text"].replace(delimiter.strip(), " ").strip()
                if not clean_text:
                    clean_text = "..."
                
                # Length of text plus delimiter length
                added_length = len(clean_text) + len(delimiter)
                if current_chunk and (current_length + added_length > max_char_limit):
                    chunks.append(current_chunk)
                    current_chunk = [clean_text]
                    current_length = len(clean_text)
                else:
                    current_chunk.append(clean_text)
                    current_length += added_length

            if current_chunk:
                chunks.append(current_chunk)

            logger.info(f"Split {len(blocks_list)} blocks into {len(chunks)} Google Translate chunk(s) (<= {max_char_limit} chars each).")

            translated_texts = []
            for idx, chunk in enumerate(chunks):
                if idx > 0:
                    time.sleep(1.0)  # Delay between chunks to prevent rate limiting

                joined_text = delimiter.join(chunk)
                try:
                    res = translator.translate(joined_text)
                except Exception as ex:
                    err_msg = str(ex).lower()
                    if "too many requests" in err_msg or "429" in err_msg:
                        raise RuntimeError(
                            "متن زیرنویس طولانی است و به سقف درخواست‌های مترجم گوگل رسید. "
                            "لطفاً از موتور هوش مصنوعی (AI / Groq) برای ترجمه استفاده کنید."
                        )
                    raise

                if not res:
                    translated_texts.extend(chunk)
                    continue

                parts = res.split("⟦§⟧")
                if len(parts) == len(chunk):
                    translated_texts.extend([p.strip() for p in parts])
                else:
                    # In case Google altered delimiters, fall back to line splitting or chunk
                    lines = [line.strip() for line in res.splitlines() if line.strip()]
                    if len(lines) == len(chunk):
                        translated_texts.extend(lines)
                    else:
                        translated_texts.extend([parts[0].strip() if parts else ""] * len(chunk))

            translated_blocks = []
            for idx, b in enumerate(blocks_list):
                trans_text = translated_texts[idx] if idx < len(translated_texts) else b["text"]
                translated_blocks.append(
                    f"{b['index']}\n{b['time']}\n{trans_text}\n"
                )
            return "\n".join(translated_blocks)

        try:
            result = await asyncio.to_thread(_do_google_translate, blocks)
            logger.info("Persian subtitle translation completed successfully via Google Translate.")
            return result
        except Exception as e:
            logger.error(f"Google Translate failed: {e}")
            err_str = str(e)
            if "لطفاً از موتور" in err_str:
                raise RuntimeError(err_str)
            if "too many requests" in err_str.lower() or "429" in err_str:
                raise RuntimeError(
                    "متن زیرنویس طولانی است و سرور گوگل پاسخ نداد. "
                    "لطفاً از موتور ترجمه هوش مصنوعی (AI / Groq) استفاده کنید."
                )
            raise RuntimeError(f"Google Translate error: {e}")

    async def translate_with_ai(self, srt_content: str) -> str:
        """
        Translates input SRT content into fluent Persian (Farsi) using Groq API (openai/gpt-oss-120b).
        Preserves exact timestamps and line numbering.
        Falls back to Gemini API if configured and Groq fails.
        """
        system_prompt = (
            "You are a professional subtitle translator.\n"
            "Translate the text in the SRT subtitle file into fluent, natural, and modern Persian (Farsi).\n\n"
            "STRICT INSTRUCTIONS:\n"
            "1. Keep exact same numeric sequence and timestamp ranges (`HH:MM:SS,mmm --> HH:MM:SS,mmm`).\n"
            "2. Translate only the subtitle text lines into Persian.\n"
            "3. Do NOT alter timestamps or subtitle block structure.\n"
            "4. Output ONLY valid SRT content without any markdown intro/outro or explanations."
        )

        user_prompt = f"INPUT SRT:\n{srt_content}"

        # 1. Primary: Try Groq API
        groq_key = self.explicit_key or settings.groq_api_key
        model_name = settings.groq_translate_model or "openai/gpt-oss-120b"
        if groq_key:
            masked = mask_key(groq_key)
            logger.info(f"Sending SRT content to Groq API ({model_name}) using key [{masked}]...")

            def _do_groq_translate(key: str):
                client = Groq(api_key=key)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                )
                return response.choices[0].message.content

            try:
                raw_response = await asyncio.to_thread(_do_groq_translate, groq_key)
                if raw_response:
                    clean_srt = clean_srt_response(raw_response)
                    logger.info("Persian subtitle translation completed successfully via Groq AI.")
                    return clean_srt
            except Exception as e:
                logger.warning(f"Groq API translation failed with key [{masked}]: {e}")
                if not settings.get_gemini_api_keys():
                    raise RuntimeError(f"Groq translation error: {e}")

        # 2. Secondary: Fallback to Gemini API if keys are available
        gemini_keys = settings.get_gemini_api_keys()
        if gemini_keys and genai is not None:
            max_attempts = len(gemini_keys)
            last_exception = None
            for attempt in range(max_attempts):
                api_key = self.get_next_gemini_api_key()
                if not api_key:
                    break

                masked = mask_key(api_key)
                logger.info(f"Fallback: Sending SRT content to Gemini using key [{masked}] (attempt {attempt + 1}/{max_attempts})...")

                def _do_gemini_translate(key: str):
                    client = genai.Client(api_key=key)
                    full_prompt = f"{system_prompt}\n\n{user_prompt}"
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=full_prompt,
                    )
                    return response.text

                try:
                    raw_response = await asyncio.to_thread(_do_gemini_translate, api_key)
                    if raw_response:
                        clean_srt = clean_srt_response(raw_response)
                        logger.info(f"Persian subtitle translation completed successfully via Gemini fallback with key [{masked}].")
                        return clean_srt
                except Exception as e:
                    logger.warning(f"Gemini API fallback call failed with key [{masked}]: {e}")
                    last_exception = e

            raise RuntimeError(f"Translation failed on both Groq and Gemini. Last Gemini error: {last_exception}")

        raise RuntimeError("No valid API Key (GROQ_API_KEY) configured for translation.")

    async def generate_persian_subject(self, srt_content: str) -> str:
        """
        Analyzes the first 10 text blocks of input SRT content and generates a short Persian subject/headline.
        Uses Groq AI with fallback to Gemini.
        """
        if not srt_content.strip():
            return "خلاصه ویدیو"

        blocks = parse_srt_blocks(srt_content)
        if not blocks:
            return "خلاصه ویدیو"

        first_10_texts = [b["text"].strip() for b in blocks[:10] if b.get("text")]
        if not first_10_texts:
            return "خلاصه ویدیو"

        sample_text = "\n".join(first_10_texts)

        system_prompt = (
            "You are an expert Persian copywriter and content strategist.\n"
            "Analyze the following transcript excerpt (the first spoken lines) and summarize the core subject/topic into a short, attractive, engaging headline in Persian (Farsi).\n\n"
            "STRICT INSTRUCTIONS:\n"
            "1. Output ONLY the Persian title (3 to 7 words max).\n"
            "2. Do NOT use markdown, emojis, quotes, prefixes, intros, or explanations.\n"
            "3. Ensure the subject is fluent and grammatically correct Persian."
        )
        user_prompt = f"TRANSCRIPT EXCERPT:\n{sample_text}"

        groq_key = self.explicit_key or settings.groq_api_key
        model_name = settings.groq_translate_model or "openai/gpt-oss-120b"

        def _clean_subject(text: str) -> str:
            cleaned = text.strip().strip('"\'`').replace('\n', ' ')
            if cleaned.startswith("موضوع:"):
                cleaned = cleaned[6:].strip()
            elif cleaned.startswith("عنوان:"):
                cleaned = cleaned[6:].strip()
            return cleaned or "خلاصه ویدیو"

        if groq_key:
            masked = mask_key(groq_key)
            logger.info(f"Generating Persian subject via Groq API ({model_name})...")

            def _do_groq_subject(key: str):
                client = Groq(api_key=key)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                )
                return response.choices[0].message.content

            try:
                raw_response = await asyncio.to_thread(_do_groq_subject, groq_key)
                if raw_response:
                    subj = _clean_subject(raw_response)
                    logger.info(f"Generated Persian subject via Groq: {subj}")
                    return subj
            except Exception as e:
                logger.warning(f"Groq subject generation failed: {e}")

        gemini_keys = settings.get_gemini_api_keys()
        if gemini_keys and genai is not None:
            max_attempts = len(gemini_keys)
            for attempt in range(max_attempts):
                api_key = self.get_next_gemini_api_key()
                if not api_key:
                    break

                def _do_gemini_subject(key: str):
                    client = genai.Client(api_key=key)
                    full_prompt = f"{system_prompt}\n\n{user_prompt}"
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=full_prompt,
                    )
                    return response.text

                try:
                    raw_response = await asyncio.to_thread(_do_gemini_subject, api_key)
                    if raw_response:
                        subj = _clean_subject(raw_response)
                        logger.info(f"Generated Persian subject via Gemini fallback: {subj}")
                        return subj
                except Exception as e:
                    logger.warning(f"Gemini subject generation failed: {e}")

        return "خلاصه ویدیو"


