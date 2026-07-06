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

    video_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    if not os.path.isfile(video_path):
        candidates = glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*"))
        video_candidates = [c for c in candidates if not c.endswith(".json3")]
        if not video_candidates:
            raise RuntimeError("Download finished but no video file was found.")
        video_path = video_candidates[0]

    caption_path = None
    for path in glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*.json3")):
        caption_path = path
        break

    words = []
    if caption_path:
        words = _parse_json3_captions(caption_path)

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
