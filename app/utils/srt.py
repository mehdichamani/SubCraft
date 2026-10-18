import re
from typing import Any, Dict, List, Optional, Union

def seconds_to_srt_time(seconds: float) -> str:
    """Converts seconds (float) to SRT timestamp format HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def format_segments_to_srt(segments: List[Union[Dict[str, Any], Any]]) -> str:
    """
    Formats Groq Whisper verbose_json segments into a standard SRT string.
    Each segment should contain 'start', 'end', and 'text'.
    """
    srt_blocks = []
    index = 1
    
    for seg in segments:
        if isinstance(seg, dict):
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            text = seg.get("text", "").strip()
        else:
            start = getattr(seg, "start", 0.0)
            end = getattr(seg, "end", 0.0)
            text = getattr(seg, "text", "").strip()
            
        if not text:
            continue
            
        start_str = seconds_to_srt_time(float(start))
        end_str = seconds_to_srt_time(float(end))
        
        block = f"{index}\n{start_str} --> {end_str}\n{text}\n"
        srt_blocks.append(block)
        index += 1
        
    return "\n".join(srt_blocks)

def clean_srt_response(raw_text: str) -> str:
    """
    Removes markdown code fences (e.g. ```srt ... ``` or ``` ...) from raw LLM output
    to return pure SRT text. Normalizes line endings to LF.
    """
    text = raw_text.replace('\r\n', '\n').strip()
    # Match markdown code fences
    pattern = r"^```(?:srt|subtitles)?\s*\n?(.*?)\n?```$"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Strip any stray backticks at top/bottom
    text = re.sub(r"^```(?:srt)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text

def parse_srt_blocks(srt_content: str) -> List[Dict[str, str]]:
    """
    Parses an SRT format string into a list of block dicts:
    [{'index': '1', 'time': '00:00:01,000 --> 00:00:04,000', 'text': '...'}]
    """
    blocks = []
    normalized_content = srt_content.replace('\r\n', '\n').strip()
    raw_blocks = re.split(r'\n\s*\n', normalized_content)
    for b in raw_blocks:
        lines = [line.strip() for line in b.strip().splitlines() if line.strip()]
        if not lines:
            continue
        # Find time line with '-->'
        time_index = -1
        for idx, line in enumerate(lines):
            if "-->" in line:
                time_index = idx
                break
        if time_index == -1:
            continue
        
        index_str = lines[0] if time_index > 0 else str(len(blocks) + 1)
        time_str = lines[time_index]
        text_lines = lines[time_index + 1:]
        text_str = "\n".join(text_lines)
        blocks.append({
            'index': index_str,
            'time': time_str,
            'text': text_str
        })
    return blocks

def merge_bilingual_srt(original_srt: str, translated_srt: str) -> str:
    """
    Merges original language SRT and Persian translated SRT into alternating lines:
    Line 1: Original Language
    Line 2: Persian Translation
    (Alternating throughout the entire SRT subtitle file).
    """
    orig_blocks = parse_srt_blocks(original_srt)
    trans_blocks = parse_srt_blocks(translated_srt)
    
    merged_srt_blocks = []
    max_len = max(len(orig_blocks), len(trans_blocks))
    
    for i in range(max_len):
        orig = orig_blocks[i] if i < len(orig_blocks) else None
        trans = trans_blocks[i] if i < len(trans_blocks) else None
        
        time_str = (orig and orig['time']) or (trans and trans['time']) or ""
        orig_text = (orig and orig['text']) or ""
        trans_text = (trans and trans['text']) or ""
        
        # Format text strictly: Line 1 Original, Line 2 Persian
        combined_text = f"{orig_text}\n{trans_text}".strip()
        
        block = f"{i + 1}\n{time_str}\n{combined_text}\n"
        merged_srt_blocks.append(block)
        
    return "\n".join(merged_srt_blocks)

def srt_to_alternating_text(srt_content: str) -> str:
    """
    Extracts subtitle text lines from an SRT string into an alternating line-by-line format:
    Line 1: Original Language
    Line 2: Persian Translation
    Line 3: Original Language
    Line 4: Persian Translation...
    """
    blocks = parse_srt_blocks(srt_content)
    text_lines = []
    for b in blocks:
        lines = [line.strip() for line in b['text'].splitlines() if line.strip()]
        for line in lines:
            text_lines.append(line)
    return "\n".join(text_lines)


def srt_to_plain_text(srt_content: str) -> str:
    """
    Extracts pure text lines without index or timestamps.
    """
    blocks = parse_srt_blocks(srt_content)
    text_lines = []
    for b in blocks:
        lines = [line.strip() for line in b['text'].splitlines() if line.strip()]
        for line in lines:
            text_lines.append(line)
    return "\n".join(text_lines)


def format_srt_by_language_mode(original_srt: str, translated_srt: Optional[str], language_mode: str = "bilingual") -> str:
    """
    Produces final SRT content according to the requested language mode:
    - 'bilingual': Alternating original and Persian text.
    - 'translation_only': Persian translation only.
    - 'original_only': Original transcribed speech only.
    """
    mode = (language_mode or "bilingual").lower().strip()
    if mode == "original_only" or not translated_srt:
        return original_srt

    if mode == "translation_only":
        return translated_srt

    # Default: bilingual
    return merge_bilingual_srt(original_srt, translated_srt)


def srt_time_to_lrc_time(srt_time: str) -> str:
    """
    Converts an SRT timestamp HH:MM:SS,mmm into standard LRC timestamp format [mm:ss.xx].
    e.g. '00:01:23,456' -> '[01:23.46]'
    """
    srt_time = srt_time.strip()
    match = re.search(r'(\d+):(\d+):(\d+)[,.](\d+)', srt_time)
    if not match:
        return "[00:00.00]"
    hrs, mins, secs, ms_str = match.groups()
    total_minutes = int(hrs) * 60 + int(mins)
    seconds = int(secs)
    # Convert milliseconds (up to 3 digits) to hundredths of a second (2 digits)
    ms = int(ms_str[:3].ljust(3, '0'))
    hundredths = int(round(ms / 10.0))
    if hundredths >= 100:
        hundredths = 99
    return f"[{total_minutes:02d}:{seconds:02d}.{hundredths:02d}]"


def srt_to_lrc(srt_content: str, title: str = "", artist: str = "") -> str:
    """
    Converts an SRT format string into standard timestamped LRC string compatible with
    media players like Musicolet.
    Each block start timestamp is formatted as [mm:ss.xx].
    """
    blocks = parse_srt_blocks(srt_content)
    lrc_lines = []

    if title:
        lrc_lines.append(f"[ti:{title}]")
    if artist:
        lrc_lines.append(f"[ar:{artist}]")
    lrc_lines.append("[by:SubCraft]")
    lrc_lines.append("")

    for b in blocks:
        time_parts = b['time'].split('-->')
        if not time_parts:
            continue
        start_srt_time = time_parts[0].strip()
        lrc_tag = srt_time_to_lrc_time(start_srt_time)
        
        # In case of multiple lines (e.g. bilingual), keep them cleanly formatted
        text_lines = [line.strip() for line in b['text'].splitlines() if line.strip()]
        if not text_lines:
            continue
        for line in text_lines:
            lrc_lines.append(f"{lrc_tag} {line}")

    return "\n".join(lrc_lines).strip()


