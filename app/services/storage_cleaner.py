"""
Output Storage Manager with FIFO retention buffer for SubCraft.
Ensures at most MAX_RECENT_FILES (10) are retained in data/recent_outputs.
Older files are automatically deleted upon adding the 11th file.
"""
import os
import shutil
import time
from pathlib import Path
from typing import List, Dict, Optional

OUTPUT_DIR = Path("data/recent_outputs")
MAX_RECENT_FILES = 10


class OutputStorageManager:
    @classmethod
    def get_output_dir(cls) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return OUTPUT_DIR

    @classmethod
    def register_output(cls, source_file_path: str, task_id: str, title: Optional[str] = None) -> str:
        """
        Moves processed media file to OUTPUT_DIR, prefixes with task_id, and enforces FIFO retention.
        Returns the absolute path of the registered file.
        """
        out_dir = cls.get_output_dir()
        src = Path(source_file_path)
        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {source_file_path}")

        # Clean filename while preserving extension
        clean_name = src.name.replace(" ", "_")
        dest_filename = f"{task_id}_{clean_name}"
        dest_path = out_dir / dest_filename

        shutil.move(str(src), str(dest_path))
        # Update mtime to current time for reliable FIFO sorting
        try:
            os.utime(str(dest_path), None)
        except Exception:
            pass

        cls.enforce_retention_limit()
        return str(dest_path)

    @classmethod
    def enforce_retention_limit(cls):
        """
        Enforces FIFO retention: removes oldest files if count exceeds MAX_RECENT_FILES.
        """
        out_dir = cls.get_output_dir()
        files = [f for f in out_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
        # Sort by last modification time (oldest first)
        files.sort(key=lambda f: f.stat().st_mtime)

        while len(files) > MAX_RECENT_FILES:
            oldest = files.pop(0)
            try:
                oldest.unlink(missing_ok=True)
            except Exception:
                pass

    @classmethod
    def list_recent_outputs(cls) -> List[Dict]:
        """
        Returns metadata list of recent output files, sorted newest to oldest.
        """
        out_dir = cls.get_output_dir()
        files = [f for f in out_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
        # Sort newest first
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        results = []
        for f in files:
            stat = f.stat()
            # Extract task_id and original filename
            parts = f.name.split("_", 1)
            task_id = parts[0] if len(parts) > 1 else f.stem
            display_name = parts[1] if len(parts) > 1 else f.name

            # Determine human readable format
            ext = f.suffix.lower()
            format_badge = "MKV" if ext == ".mkv" else ("MP3" if ext == ".mp3" else ("SRT" if ext == ".srt" else ext.replace(".", "").upper()))

            results.append({
                "task_id": task_id,
                "filename": f.name,
                "display_name": display_name,
                "format": format_badge,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "created_at": stat.st_mtime,
                "download_url": f"/api/web-download/{task_id}"
            })
        return results

    @classmethod
    def get_file_for_task(cls, task_id: str) -> Optional[Path]:
        """
        Retrieves the Path of the file corresponding to task_id if present.
        """
        out_dir = cls.get_output_dir()
        for f in out_dir.iterdir():
            if f.is_file() and f.name.startswith(f"{task_id}_"):
                return f
        return None
