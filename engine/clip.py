"""Cuts a segment out of the source video, reframes it to vertical 9:16, and
burns in styled captions - the CapCut/Descript-style finishing pass."""
import glob
import os
import subprocess
import time
import uuid

from .ffmpeg_util import FFMPEG, SUBPROCESS_FLAGS

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "outputs")
WORK_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "work")

TARGET_W = 1080
TARGET_H = 1920
WORDS_PER_CAPTION = 5
PREVIEW_MAX_AGE_SECONDS = 60

# Title banner (the optional headline burned across the TOP of the short, above
# her head). Rendered from a hand-written ASS file that declares the real
# 1080x1920 canvas, so - unlike the caption SRT (see FFMPEG_ASS_DEFAULT_PLAYRES_Y)
# - every size below is in true output pixels with no libass rescaling guesswork.
TITLE_FONT = "Arial"
TITLE_FONTSIZE = 88          # ~ same visual size as the burned captions
TITLE_OUTLINE = 5            # thick black outline so white text reads on any background
TITLE_SHADOW = 2
TITLE_MARGIN_LR = 70         # left/right padding -> text wraps within ~940px
TITLE_MARGIN_V = 80          # distance down from the top edge


def _srt_timestamp(seconds):
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _chunk_words(words, clip_start, clip_end):
    """Groups the clip's words into caption chunks (the running on-screen lines)."""
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
    return entries


def _build_srt(words, clip_start, clip_end, srt_path, time_origin=None):
    """Writes the caption SRT for the words inside [clip_start, clip_end].
    `time_origin` is the timestamp that ffmpeg's subtitles filter treats as 0 -
    which is the *input-seek* point, NOT the clip start. When ffmpeg does a fast
    pre-roll seek before the clip (see make_short), captions must be timed from
    that pre-roll point or they render early and drift out of sync with the
    voice. Defaults to clip_start for callers that don't pre-roll."""
    if time_origin is None:
        time_origin = clip_start
    entries = _chunk_words(words, clip_start, clip_end)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(entries, start=1):
            start = max(0.0, chunk[0]["start"] - time_origin)
            end = max(0.0, chunk[-1]["end"] - time_origin)
            text = " ".join(w["text"].strip() for w in chunk)
            f.write(f"{i}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n\n")


def _caption_at(words, clip_start, clip_end, t):
    """The on-screen caption line (same chunking as the running captions) that
    is showing at absolute time `t` - used to put the right line on the
    thumbnail. Falls back to the nearest chunk if `t` lands in a gap."""
    entries = _chunk_words(words, clip_start, clip_end)
    if not entries:
        return ""

    def text_of(chunk):
        return " ".join(w["text"].strip() for w in chunk)

    for chunk in entries:
        if chunk[0]["start"] <= t <= chunk[-1]["end"]:
            return text_of(chunk)
    # gap: pick the chunk whose midpoint is closest to t
    nearest = min(entries, key=lambda c: abs(((c[0]["start"] + c[-1]["end"]) / 2) - t))
    return text_of(nearest)


def _escape_for_filter(path):
    """ffmpeg filter args treat ':' and '\\' specially - this is the standard
    Windows-path escaping needed inside a subtitles= filter argument."""
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    return path


FFMPEG_ASS_DEFAULT_PLAYRES_Y = 288
"""ffmpeg's SRT->ASS conversion doesn't declare a PlayResY, so libass falls back
to this legacy SSA default and scales every ASS style value (MarginV, FontSize,
Outline, ...) by TARGET_H/288 when rendering onto our actual 1080x1920 canvas.
FontSize/Outline below happen to already look right because they were tuned by
eye under this same scaling - but MarginV values were computed as real target
pixels (e.g. by engine.framing's geometry), so only MarginV needs compensating
back down by the inverse ratio before libass scales it back up."""


def _ass_style(margin_v):
    compensated_margin_v = int(round(margin_v * FFMPEG_ASS_DEFAULT_PLAYRES_Y / TARGET_H))
    return (
        "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        f"Alignment=2,MarginV={compensated_margin_v}"
    )


def _build_vf(srt_path, crop_x_pct=0.5, caption_margin_v=440):
    """Shared scale+crop+subtitles filter chain for both the full render and
    the single-frame preview. Scaling with force_original_aspect_ratio=increase
    guarantees the frame always covers the target 1080x1920, so the crop
    always has valid non-negative slack regardless of the source aspect ratio.
    Used for the manual/Advanced fallback path (no face-detected framing)."""
    crop_x_pct = max(0.0, min(1.0, crop_x_pct))
    subtitles_arg = f"subtitles='{_escape_for_filter(srt_path)}':force_style='{_ass_style(caption_margin_v)}'"
    crop_expr = f"crop={TARGET_W}:{TARGET_H}:x='(in_w-out_w)*{crop_x_pct:.4f}'"
    return (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"{crop_expr},{subtitles_arg}"
    )


def _build_filter(srt_path, framing, crop_x_pct, caption_margin_v):
    """Builds the -vf filter chain. When `framing` is a face-detected crop
    (from engine.framing.detect_face_framing) it scales to fill the frame and
    crops the full-screen 9:16 window centered on the speaker's face. When
    `framing` is None it uses the manual/Advanced crop_x_pct/caption_margin_v
    values instead."""
    if framing is None:
        return _build_vf(srt_path, crop_x_pct, caption_margin_v)

    subtitles_arg = f"subtitles='{_escape_for_filter(srt_path)}':force_style='{_ass_style(framing['margin_v'])}'"
    crop_expr = f"crop={TARGET_W}:{TARGET_H}:x={framing['crop_x']}:y={framing['crop_y']}"
    return f"scale={framing['scaled_w']}:{framing['scaled_h']},{crop_expr},{subtitles_arg}"


def _ass_escape(text):
    """Makes user title text safe to drop into an ASS Dialogue line. Braces open
    ASS override tags and backslashes are control chars, so neutralize both;
    real line breaks are folded to spaces (the banner wraps on its own)."""
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    text = text.replace("\r", " ").replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def _build_title_ass(title_text, ass_path):
    """Writes a one-line ASS file for the top title banner. Declaring PlayResX/Y
    at the true 1080x1920 means libass renders at real pixels (no PlayResY=288
    rescaling), and Alignment=8 pins it top-center. The single event spans the
    whole clip, so the banner is simply always on - no per-frame timing to keep
    in sync with the pre-roll seek the way the captions need."""
    text = _ass_escape(title_text)
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {TARGET_W}\n"
        f"PlayResY: {TARGET_H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Title,{TITLE_FONT},{TITLE_FONTSIZE},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{TITLE_OUTLINE},"
        f"{TITLE_SHADOW},8,{TITLE_MARGIN_LR},{TITLE_MARGIN_LR},{TITLE_MARGIN_V},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,9:59:59.99,Title,,0,0,0,,{text}\n"
    )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(content)


def _append_title(vf, title_text, ass_path):
    """Appends the top-title subtitles filter onto an existing filter chain when
    a non-empty title is given (otherwise returns the chain unchanged, so shorts
    without a title render exactly as before). Writes the ASS file as a side
    effect. Returns (vf, ass_path_or_None) so callers can clean up temp files."""
    if not title_text or not title_text.strip():
        return vf, None
    _build_title_ass(title_text, ass_path)
    return f"{vf},subtitles='{_escape_for_filter(ass_path)}'", ass_path


# Video-encoder choices for the full render. Quick Sync (h264_qsv) is Intel's
# built-in hardware encoder - on the Iris Xe laptop it renders a clip ~3-4x
# faster than grinding every frame on the CPU with libx264. We try it first and
# transparently fall back to libx264 if the hardware path fails for any reason
# (driver hiccup, an odd source, ffmpeg build without QSV), so a short is always
# produced. -global_quality is QSV's quality knob; 22 matches libx264 -crf 20's
# file size and look in testing.
_HW_VCODEC = ["-c:v", "h264_qsv", "-global_quality", "22"]
_CPU_VCODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _encode_clip(pre_input, post_input, vf, output_path):
    """Encodes one clip, hardware-accelerated when possible. `pre_input` is the
    ffmpeg args before -i (the pre-roll seek + input), `post_input` the args
    after it (remainder seek + duration). Tries Quick Sync first and falls back
    to libx264 if it fails, so a short is always produced. Raises RuntimeError
    only if the CPU fallback also fails."""
    def run(vcodec):
        cmd = [
            FFMPEG, "-y",
            *pre_input,
            *post_input,
            "-vf", vf,
            *vcodec,
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS)

    result = run(_HW_VCODEC)
    if result.returncode == 0:
        return
    # Hardware path failed - retry on the CPU so the user still gets their short.
    hw_err = result.stderr[-1500:]
    result = run(_CPU_VCODEC)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed (hardware and CPU encoders both errored).\n"
            f"Hardware error:\n{hw_err}\n\nCPU error:\n{result.stderr[-1500:]}"
        )


def _sweep_old_previews():
    cutoff = time.time() - PREVIEW_MAX_AGE_SECONDS
    for path in glob.glob(os.path.join(WORK_DIR, "preview_*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def make_short(video_path, words, start, end, output_name=None, framing=None, crop_x_pct=0.5, caption_margin_v=440, title_text=""):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    srt_path = os.path.join(WORK_DIR, f"{job_id}.srt")

    output_name = output_name or f"short_{job_id}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    duration = end - start
    coarse_seek = max(0.0, start - 5)
    remainder_seek = start - coarse_seek

    # Captions must be timed from the pre-roll seek point (coarse_seek), not the
    # clip start, so the subtitles filter renders them in sync with the voice.
    _build_srt(words, start, end, srt_path, time_origin=coarse_seek)

    vf = _build_filter(srt_path, framing, crop_x_pct, caption_margin_v)
    vf, title_ass = _append_title(vf, title_text, os.path.join(WORK_DIR, f"{job_id}_title.ass"))

    try:
        _encode_clip(
            pre_input=["-ss", str(coarse_seek), "-i", video_path],
            post_input=["-ss", str(remainder_seek), "-t", str(duration)],
            vf=vf,
            output_path=output_path,
        )
    finally:
        # Tidy the per-job temp files so WORK_DIR doesn't slowly accumulate them.
        for tmp in (srt_path, title_ass):
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    return output_path


def make_preview_frame(video_path, words, start, end, timestamp=None, framing=None, crop_x_pct=0.5, caption_margin_v=440, title_text=""):
    """Extracts one JPEG frame with the same crop+caption filter chain used by
    make_short, so the user can see the result before committing to a full render."""
    os.makedirs(WORK_DIR, exist_ok=True)
    _sweep_old_previews()

    if timestamp is None:
        timestamp = (start + end) / 2
    timestamp = max(start, min(end, timestamp))

    job_id = uuid.uuid4().hex[:8]
    srt_path = os.path.join(WORK_DIR, f"preview_{job_id}.srt")
    # Single input-seek to `timestamp`, so time the captions from there - shows
    # the caption that actually belongs to the previewed frame.
    _build_srt(words, start, end, srt_path, time_origin=timestamp)

    output_path = os.path.join(WORK_DIR, f"preview_{job_id}.jpg")
    vf = _build_filter(srt_path, framing, crop_x_pct, caption_margin_v)
    vf, title_ass = _append_title(vf, title_text, os.path.join(WORK_DIR, f"preview_{job_id}_title.ass"))

    cmd = [
        FFMPEG, "-y",
        "-ss", str(max(0.0, timestamp)), "-i", video_path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "3", output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS)
    for tmp in (srt_path, title_ass):
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg preview failed:\n{result.stderr[-2000:]}")

    return output_path


def _best_face_time(video_path, t, lo, hi, window=0.4, samples=5):
    """Nudges the thumbnail timestamp to a nearby frame where a face is most
    clearly detected (largest frontal face) - avoids mid-blinks and awkward
    mid-word mouth shapes. Falls back to `t` if nothing is detected."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        best_t, best_area = None, 0
        for i in range(samples):
            frac = 0.0 if samples == 1 else i / (samples - 1)
            ct = max(lo, min(hi, (t - window) + (2 * window) * frac))
            cap.set(cv2.CAP_PROP_POS_MSEC, ct * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            sd = 640.0 / w if w else 1.0
            gray = cv2.cvtColor(cv2.resize(frame, (0, 0), fx=sd, fy=sd), cv2.COLOR_BGR2GRAY)
            faces = casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces):
                area = max(f[2] * f[3] for f in faces)
                if area > best_area:
                    best_area, best_t = area, ct
        cap.release()
        return best_t if best_t is not None else t
    except Exception:
        return t


def make_thumbnail(video_path, words, start, end, thumb_time, framing=None,
                   crop_x_pct=0.5, caption_margin_v=440, output_name=None, title_text=""):
    """Saves a ready-to-upload 1080x1920 thumbnail JPEG: the most
    attention-grabbing caption line of the clip, on a frame where the speaker's
    face looks good. Same crop/caption styling as the short itself."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    thumb_time = max(start, min(end, thumb_time))
    caption = _caption_at(words, start, end, thumb_time)
    frame_t = _best_face_time(video_path, thumb_time, start, end)

    job_id = uuid.uuid4().hex[:8]
    srt_path = os.path.join(WORK_DIR, f"thumb_{job_id}.srt")
    # One caption spanning the whole (tiny) clip timeline - because we extract
    # with an input seek, the frame's timeline starts at ~0, so the line must
    # be placed at 00:00 to appear on the still.
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(f"1\n{_srt_timestamp(0)} --> {_srt_timestamp(10)}\n{caption}\n\n")

    vf = _build_filter(srt_path, framing, crop_x_pct, caption_margin_v)
    vf, title_ass = _append_title(vf, title_text, os.path.join(WORK_DIR, f"thumb_{job_id}_title.ass"))

    output_name = output_name or f"thumb_{job_id}.jpg"
    output_path = os.path.join(OUTPUT_DIR, output_name)
    cmd = [
        FFMPEG, "-y",
        "-ss", str(max(0.0, frame_t)), "-i", video_path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "2", output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS)
    for tmp in (srt_path, title_ass):
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg thumbnail failed:\n{result.stderr[-2000:]}")

    return output_path
