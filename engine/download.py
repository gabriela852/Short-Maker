"""Downloads a video and its word-timed transcript, from either a YouTube link
(via yt-dlp + auto-captions) or a Descript share link (via Descript's public
share API). Both paths return the same shape: {video_path, words, title, video_id}."""
import json
import os
import re
import glob
import shutil
import subprocess
import urllib.error
import urllib.request

import yt_dlp

from .ffmpeg_util import FFMPEG, SUBPROCESS_FLAGS

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "downloads")

_DESCRIPT_SLUG_RE = re.compile(r"descript\.com/(?:view|embed)/([A-Za-z0-9_-]+)")


def _safe_id(url_or_id):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", url_or_id)[:64]


def fetch_source(url, progress_hook=None):
    """Routes to the right downloader based on the link the user pasted -
    a Descript share link or (default) a YouTube/other yt-dlp-supported URL."""
    if "descript.com" in url:
        return fetch_descript_video(url, progress_hook=progress_hook)
    return fetch_video(url, progress_hook=progress_hook)


def _caption_token_count(path):
    """Rough count of separately-timed text segments in a json3 caption file.
    A high count means the track has real per-word timings (auto-generated
    captions); a low count means each cue is a whole sentence or line (a
    manual/uploaded track). We use this to prefer the finer-grained track."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    count = 0
    for event in data.get("events", []):
        for seg in event.get("segs", []):
            if seg.get("utf8", "").strip():
                count += 1
    return count


def _resolve_local_files(video_id):
    """Finds the already-downloaded video file and its caption file for a
    video_id, purely by looking at what's on disk in DOWNLOAD_DIR. Returns
    (video_path, caption_path) - either may be None if missing. When several
    caption tracks exist (e.g. "<id>.en.json3", "<id>.en-orig.json3",
    "<id>.en-US.json3"), we pick the one with the finest (word-level) timing
    rather than the alphabetically-first one - a manual "en-US" track often
    sorts first but carries only whole-sentence cues, which makes captions
    render as huge multi-line blocks instead of a few words at a time."""
    video_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    if not os.path.isfile(video_path):
        candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*")))
        video_candidates = [
            c for c in candidates
            if not c.endswith(".json3") and not c.endswith(".words.json")
        ]
        video_path = video_candidates[0] if video_candidates else None

    caption_path = None
    caption_candidates = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*.json3")))
    if caption_candidates:
        # Prefer the word-level track (most separately-timed segments). Ties keep
        # the alphabetically-first file, so selection stays deterministic.
        caption_path = max(caption_candidates, key=_caption_token_count)

    return video_path, caption_path


def load_video(video_id):
    """Reconstructs {video_path, words, video_id} purely from files already
    on disk (no network) - used to bring a previously-analyzed video back
    after a server restart or page refresh. Reads a normalized ".words.json"
    sidecar if present (Descript videos have no json3 file), otherwise parses
    the YouTube json3 captions. Returns None if the video or transcript is missing."""
    video_path, caption_path = _resolve_local_files(video_id)
    if not video_path:
        return None

    sidecar = os.path.join(DOWNLOAD_DIR, f"{video_id}.words.json")
    if os.path.isfile(sidecar):
        with open(sidecar, "r", encoding="utf-8") as f:
            words = json.load(f)
    elif caption_path:
        words = _parse_json3_captions(caption_path)
    else:
        return None

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


def _descript_slug(url):
    m = _DESCRIPT_SLUG_RE.search(url)
    if not m:
        raise RuntimeError(
            "That doesn't look like a Descript share link. Use a link like "
            "https://share.descript.com/view/XXXXXXXX"
        )
    return m.group(1)


def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _media_url(entry):
    """Descript media entries are objects like {url, cdn_url, ...}; pull the
    plain URL string out (None if the entry is missing)."""
    if isinstance(entry, dict):
        return entry.get("url") or entry.get("cdn_url")
    return entry


def _parse_descript_transcript(data):
    """Turns Descript's transcript.json (one word per segment, with real start
    and end times) into the pipeline's flat [{start, end, text}] word list."""
    words = []
    for seg in data.get("segments", []):
        text = (seg.get("body") or "").replace("\n", " ").strip()
        if not text:
            continue
        start, end = seg.get("startTime"), seg.get("endTime")
        if start is None or end is None:
            continue
        words.append({"start": float(start), "end": float(end), "text": text})
    return words


def fetch_descript_video(url, progress_hook=None):
    """Downloads a Descript share link's video + word-timed transcript via
    Descript's public share API. Returns {video_path, words, title, video_id}."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    slug = _descript_slug(url)
    video_id = _safe_id(f"descript_{slug}")

    api_url = f"https://share.descript.com/v2/published_projects/slugs/{slug}"
    try:
        project = _http_get_json(api_url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise RuntimeError(
                "This Descript link isn't publicly accessible (it may be private, "
                "password-protected, or expired). In Descript, open the share settings "
                "and make sure anyone with the link can view it, then paste it again."
            )
        raise RuntimeError(f"Couldn't reach Descript (error {e.code}). Try again in a moment.")
    except urllib.error.URLError:
        raise RuntimeError("Couldn't reach Descript - check your internet connection and try again.")

    contents = project.get("contents") or {}
    media = contents.get("media") or {}
    title = project.get("name") or video_id

    transcript_ref = contents.get("transcript") or {}
    transcript_url = transcript_ref.get("url")
    if not transcript_url:
        raise RuntimeError(
            "This Descript project has no transcript. The short-finder needs a transcript "
            "to work - turn on transcription for this project in Descript, then try again."
        )
    try:
        words = _parse_descript_transcript(_http_get_json(transcript_url))
    except (urllib.error.HTTPError, urllib.error.URLError):
        raise RuntimeError(
            "This Descript link's transcript has expired. Reopen the share page in Descript "
            "to refresh it, then paste the link again."
        )
    if not words:
        raise RuntimeError(
            "This Descript project's transcript is empty. The short-finder needs spoken words "
            "with timing to work."
        )

    video_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")
    original_url = _media_url(media.get("original"))
    stream_url = _media_url(media.get("stream"))
    already_downloaded = os.path.isfile(video_path) and os.path.getsize(video_path) > 0
    if already_downloaded:
        pass  # reuse the existing download (these files are large) - mirrors yt-dlp's skip-if-present
    elif original_url:
        req = urllib.request.Request(original_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, open(video_path, "wb") as out:
                shutil.copyfileobj(resp, out)
        except (urllib.error.HTTPError, urllib.error.URLError):
            raise RuntimeError(
                "This Descript link's video has expired. Reopen the share page in Descript "
                "to refresh it, then paste the link again."
            )
    elif stream_url:
        result = subprocess.run(
            [FFMPEG, "-y", "-i", stream_url, "-c", "copy", video_path],
            capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Couldn't download the Descript video:\n{result.stderr[-1500:]}")
    else:
        raise RuntimeError(
            "This Descript link doesn't expose a downloadable video. Make sure the share page "
            "shows the video and is set to public."
        )

    if not os.path.isfile(video_path) or os.path.getsize(video_path) == 0:
        raise RuntimeError("The Descript video download finished but produced an empty file.")

    with open(os.path.join(DOWNLOAD_DIR, f"{video_id}.words.json"), "w", encoding="utf-8") as f:
        json.dump(words, f)

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

    # Safety net for manual/uploaded caption tracks that time a whole sentence as
    # one segment: split any multi-word segment into individual words with evenly
    # interpolated timing, so captions still show a few words at a time instead of
    # dumping a full sentence (or several) on screen at once. Word-level tracks are
    # already one word per segment, so this leaves them untouched.
    expanded = []
    for w in cleaned:
        parts = w["text"].split()
        if len(parts) <= 1:
            expanded.append(w)
            continue
        span = max(0.0, w["end"] - w["start"])
        per = span / len(parts) if span > 0 else 0.0
        for i, part in enumerate(parts):
            ws = w["start"] + per * i
            we = w["start"] + per * (i + 1) if per > 0 else w["end"]
            expanded.append({"start": ws, "end": we, "text": part})
    return expanded
