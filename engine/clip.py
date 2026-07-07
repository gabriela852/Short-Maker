"""Cuts a segment out of the source video, reframes it to vertical 9:16, and
burns in styled captions - the CapCut/Descript-style finishing pass."""
import glob
import os
import subprocess
import time
import uuid

from .ffmpeg_util import FFMPEG

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "outputs")
WORK_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "work")

TARGET_W = 1080
TARGET_H = 1920
WORDS_PER_CAPTION = 5
PREVIEW_MAX_AGE_SECONDS = 60


def _srt_timestamp(seconds):
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(words, clip_start, clip_end, srt_path):
    segment_words = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]

    entries = []
    chunk = []
    for w in segment_words:
        chunk.append(w)
        if len(chunk) >= WORDS_PER_CAPTION:
            entries.append(chunk)
            chunk = []
    if chunk:
        entries.append(chunk)

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(entries, start=1):
            start = max(0.0, chunk[0]["start"] - clip_start)
            end = max(0.0, chunk[-1]["end"] - clip_start)
            text = " ".join(w["text"].strip() for w in chunk)
            f.write(f"{i}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n\n")


def _escape_for_filter(path):
    """ffmpeg filter args treat ':' and '\\' specially - this is the standard
    Windows-path escaping needed inside a subtitles= filter argument."""
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    return path


def _build_vf(srt_path, crop_x_pct=0.5, caption_margin_v=90):
    """Shared scale+crop+subtitles filter chain for both the full render and
    the single-frame preview. Scaling with force_original_aspect_ratio=increase
    guarantees the frame always covers the target 1080x1920, so the crop
    always has valid non-negative slack regardless of the source aspect ratio."""
    crop_x_pct = max(0.0, min(1.0, crop_x_pct))
    style = (
        "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        f"Alignment=2,MarginV={int(caption_margin_v)}"
    )
    subtitles_arg = f"subtitles='{_escape_for_filter(srt_path)}':force_style='{style}'"
    crop_expr = f"crop={TARGET_W}:{TARGET_H}:x='(in_w-out_w)*{crop_x_pct:.4f}'"
    return (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"{crop_expr},{subtitles_arg}"
    )


def _sweep_old_previews():
    cutoff = time.time() - PREVIEW_MAX_AGE_SECONDS
    for path in glob.glob(os.path.join(WORK_DIR, "preview_*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def make_short(video_path, words, start, end, output_name=None, crop_x_pct=0.5, caption_margin_v=90):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    srt_path = os.path.join(WORK_DIR, f"{job_id}.srt")
    _build_srt(words, start, end, srt_path)

    output_name = output_name or f"short_{job_id}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    duration = end - start
    coarse_seek = max(0.0, start - 5)
    remainder_seek = start - coarse_seek

    vf = _build_vf(srt_path, crop_x_pct, caption_margin_v)

    cmd = [
        FFMPEG,
        "-y",
        "-ss", str(coarse_seek),
        "-i", video_path,
        "-ss", str(remainder_seek),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-3000:]}")

    return output_path


def make_preview_frame(video_path, words, start, end, timestamp=None, crop_x_pct=0.5, caption_margin_v=90):
    """Extracts one JPEG frame with the same crop+caption filter chain used by
    make_short, so the user can see the result before committing to a full render."""
    os.makedirs(WORK_DIR, exist_ok=True)
    _sweep_old_previews()

    if timestamp is None:
        timestamp = (start + end) / 2
    timestamp = max(start, min(end, timestamp))

    job_id = uuid.uuid4().hex[:8]
    srt_path = os.path.join(WORK_DIR, f"preview_{job_id}.srt")
    _build_srt(words, start, end, srt_path)

    output_path = os.path.join(WORK_DIR, f"preview_{job_id}.jpg")
    vf = _build_vf(srt_path, crop_x_pct, caption_margin_v)

    cmd = [
        FFMPEG,
        "-y",
        "-ss", str(max(0.0, timestamp)),
        "-i", video_path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "3",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        os.remove(srt_path)
    except OSError:
        pass

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg preview failed:\n{result.stderr[-2000:]}")

    return output_path
