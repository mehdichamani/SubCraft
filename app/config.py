import logging
import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("clip_srt_bot")

class Settings(BaseSettings):
    app_name: str = "SubCraft"
    app_version: str = "0.9.0"
    telegram_bot_token: str = ""
    groq_api_key: str = ""
    groq_translate_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    render_external_url: str = ""
    webhook_secret: Optional[str] = None
    port: int = 8000
    instagram_cookies: Optional[str] = None
    youtube_cookies: Optional[str] = None
    cookies: Optional[str] = None
    admin_password: Optional[str] = None
    database_url: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_gemini_api_keys(self) -> list[str]:
        """Returns a list of parsed Gemini API keys from gemini_api_keys or gemini_api_key."""
        raw_keys = []
        if self.gemini_api_keys:
            raw_keys.extend(self.gemini_api_keys.split(","))
        if self.gemini_api_key:
            raw_keys.extend(self.gemini_api_key.split(","))
        
        keys = []
        for k in raw_keys:
            cleaned = k.strip()
            if cleaned and cleaned not in keys:
                keys.append(cleaned)
        return keys

    def validate_keys(self) -> bool:
        """Validates that all required API keys are configured."""
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        
        if missing:
            logger.error(f"Missing required environment variables: {', '.join(missing)}")
            return False
        return True

settings = Settings()
