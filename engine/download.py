"""Downloads a YouTube video and its captions using yt-dlp."""
import json
import os
import re
import glob
import yt_dlp

from .ffmpeg_util import FFMPEG

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "downloads")


def _safe_id(url_or_id):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", url_or_id)[:64]


def _resolve_local_files(video_id):
    """Finds the already-downloaded video file and its caption file for a
    video_id, purely by looking at what's on disk in DOWNLOAD_DIR. Returns
    (video_path, caption_path) - either may be None if missing. Uses sorted()
    so caption selection is deterministic even when multiple lang variants
    exist (e.g. "<id>.en.json3" and "<id>.en-orig.json3") - directory
    enumeration order isn't guaranteed, especially on Windows/NTFS."""
    video_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    if not os.path.isfile(video_path):
        candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*")))
        video_candidates = [c for c in candidates if not c.endswith(".json3")]
        video_path = video_candidates[0] if video_candidates else None

    caption_path = None
    caption_candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*.json3")))
    if caption_candidates:
        caption_path = caption_candidates[0]

    return video_path, caption_path


def load_video(video_id):
    """Reconstructs {video_path, words, video_id} purely from files already
    on disk (no network) - used to bring a previously-analyzed video back
    after a server restart or page refresh. Returns None if the video file
    or every caption variant is missing."""
    video_path, caption_path = _resolve_local_files(video_id)
    if not video_path or not caption_path:
        return None

    words = _parse_json3_captions(caption_path)
    if not words:
        return None

    return {"video_path": video_path, "words": words, "video_id": video_id}


def fetch_video(url, progress_hook=None):
    """Downloads the video (<=1080p mp4) and its captions (auto or manual).

    Returns dict: { video_path, words: [{start, end, text}], title, video_id }
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    ydl_opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4][height<=1080]/best",
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "json3",
        "subtitleslangs": ["en", "en-US", "en-orig"],
        "ffmpeg_location": os.path.dirname(FFMPEG),
        "quiet": True,
        "no_warnings": True,
    }
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info["id"]
    title = info.get("title", video_id)

    video_path, caption_path = _resolve_local_files(video_id)
    if not video_path:
        raise RuntimeError("Download finished but no video file was found.")

    words = _parse_json3_captions(caption_path) if caption_path else []

    if not words:
        raise RuntimeError(
            "This video has no captions (auto-generated or manual) available. "
            "The short-finder needs a transcript to work, so try a video that has captions on YouTube."
        )

    return {
        "video_path": video_path,
        "words": words,
        "title": title,
        "video_id": video_id,
    }


def _parse_json3_captions(path):
    """Parses YouTube's json3 caption format into a flat list of {start, end, text} words."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    words = []
    for event in data.get("events", []):
        if "segs" not in event or "tStartMs" not in event:
            continue
        base_ms = event["tStartMs"]
        offset_ms = 0
        for seg in event["segs"]:
            text = seg.get("utf8", "")
            seg_offset = seg.get("tOffsetMs", offset_ms)
            start_ms = base_ms + seg_offset
            if text.strip() == "":
                continue
            words.append({"start": start_ms / 1000.0, "text": text})
            offset_ms = seg_offset

    # json3 doesn't give per-word end times, so derive end = next word's start
    for i in range(len(words)):
        if i + 1 < len(words):
            words[i]["end"] = words[i + 1]["start"]
        else:
            words[i]["end"] = words[i]["start"] + 0.5

    # collapse into clean word tokens (json3 often splits mid-word with leading spaces)
    cleaned = []
    for w in words:
        text = w["text"].replace("\n", " ")
        if text.strip() == "":
            continue
        cleaned.append({"start": w["start"], "end": w["end"], "text": text})
    return cleaned
