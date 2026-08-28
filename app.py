import datetime
import glob
import json
import os
import traceback
import uuid

from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, request, send_from_directory
from send2trash import send2trash

from engine import download, rank, clip, framing, youtube

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "outputs")
ANALYSES_DIR = os.path.join(BASE_DIR, "data", "analyses")

if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "a").close()
load_dotenv(ENV_PATH)
os.makedirs(ANALYSES_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="static")

# In-memory cache so /api/generate doesn't need to re-download/re-transcribe
# after /api/analyze already did the work in this session. Lazily rebuilt
# (see _get_video) from files already on disk if a video isn't cached yet -
# e.g. after a server restart - rather than eagerly reloading everything
# at startup.
VIDEO_CACHE = {}

# Face-detection results are a pure function of (video, start, end), so cache
# them the same way - avoids re-running OpenCV every time a preview debounces
# or the user clicks between preview and generate for the same candidate.
FRAMING_CACHE = {}


def _get_framing(video, start, end):
    key = (video["video_id"], round(start, 2), round(end, 2))
    if key not in FRAMING_CACHE:
        FRAMING_CACHE[key] = framing.detect_face_framing(video["video_path"], start, end)
    return FRAMING_CACHE[key]


def _analysis_path(video_id, version="A"):
    # Version A keeps the original bare filename (backward compatible with every
    # analysis saved before versions existed); other versions get a suffix, so
    # analyzing the same link under A and then B never overwrites the other's
    # saved clips.
    suffix = "" if version == "A" else f".{version}"
    return os.path.join(ANALYSES_DIR, f"{video_id}{suffix}.json")


def _save_analysis(video_id, title, duration, candidates, segments, version="A"):
    data = {
        "video_id": video_id,
        "version": version,
        "title": title,
        "duration": duration,
        "candidates": candidates,
        "segments": segments,
        "analyzed_at": datetime.datetime.now().isoformat(),
    }
    path = _analysis_path(video_id, version)
    tmp_path = path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _load_analysis(video_id, version="A"):
    path = _analysis_path(video_id, version)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _any_analysis(video_id):
    """Any saved analysis for this video, whatever version - used only to
    recover the video's title after a restart (the title is the same for A and
    B). Checks the A file first, then any versioned one."""
    a = _load_analysis(video_id, "A")
    if a:
        return a
    for path in sorted(glob.glob(os.path.join(ANALYSES_DIR, f"{video_id}.*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _get_video(video_id):
    """Returns {video_path, words, video_id, title}, using the in-memory
    cache if present, otherwise reconstructing it from files still on disk
    (no network). Returns None if the video isn't cached and can't be
    reconstructed (e.g. its source files were deleted)."""
    if video_id in VIDEO_CACHE:
        return VIDEO_CACHE[video_id]

    video = download.load_video(video_id)
    if video is None:
        return None

    analysis = _any_analysis(video_id)
    video["title"] = analysis["title"] if analysis else video_id
    VIDEO_CACHE[video_id] = video
    return video


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/settings", methods=["GET"])
def get_settings():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return jsonify({"has_key": bool(key)})


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.get_json(force=True)
    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "Please paste a valid API key."}), 400
    set_key(ENV_PATH, "ANTHROPIC_API_KEY", api_key)
    os.environ["ANTHROPIC_API_KEY"] = api_key
    return jsonify({"ok": True})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Add your Anthropic API key first (see the Settings box)."}), 400

    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    version = (data.get("version") or "A").strip().upper()
    if version not in ("A", "B", "C"):
        version = "A"
    if not url:
        return jsonify({"error": "Paste a video link first."}), 400

    try:
        video = download.fetch_source(url)
        # If she already analyzed this exact video in this style before, reuse the
        # clips we saved then instead of asking Claude for a fresh (and possibly
        # different) set. That way going back to a video keeps the same moments,
        # comes back instantly, and costs nothing. Brand-new videos analyze normally.
        cached = _load_analysis(video["video_id"], version)
        cached_usable = (
            cached
            and cached.get("candidates")
            and cached.get("segments")
            and cached["candidates"][0].get("start_index") is not None
        )
        if cached_usable:
            candidates = cached["candidates"]
            segments = cached["segments"]
        else:
            candidates, segments = rank.find_best_moments(video["words"], api_key, version)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    VIDEO_CACHE[video["video_id"]] = video

    duration = video["words"][-1]["end"] if video["words"] else 0
    _save_analysis(video["video_id"], video["title"], duration, candidates, segments, version)

    return jsonify(
        {
            "video_id": video["video_id"],
            "title": video["title"],
            "duration": duration,
            "version": version,
            "candidates": candidates,
            "segments": segments,
        }
    )


def _clip_transcript(words, start, end):
    """The spoken words inside [start, end], joined into plain text - what the
    headline writer reads to capture the clip's hook."""
    return " ".join(
        w["text"].strip() for w in words if w["end"] > start and w["start"] < end
    ).strip()


@app.route("/api/headline", methods=["POST"])
def headline():
    """Writes a short on-screen title-banner headline for one clip. Used by the
    title switch (auto-fills when turned on) and the Suggest another button."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Add your Anthropic API key first (see the Settings box)."}), 400

    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    candidate_title = data.get("candidate_title", "")
    reason = data.get("reason", "")
    avoid = (data.get("avoid") or "").strip()
    version = (data.get("version") or "A").strip().upper()
    if version not in ("A", "B", "C"):
        version = "A"

    video = _get_video(video_id)
    if video is None:
        return jsonify({"error": "That video isn't loaded anymore - click Analyze again first."}), 400
    if start is None or end is None or end <= start:
        return jsonify({"error": "Invalid clip times."}), 400

    clip_text = _clip_transcript(video["words"], float(start), float(end))
    try:
        headline_text = rank.write_headline(
            clip_text, candidate_title, reason, api_key, avoid=avoid or None, version=version
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    return jsonify({"headline": headline_text})


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    auto = data.get("auto", True)
    crop_x_pct = data.get("crop_x_pct", 0.5)
    caption_margin_v = data.get("caption_margin_v", 440)
    candidate_title = data.get("candidate_title", "")
    reason = data.get("reason", "")
    thumbnail_seconds = data.get("thumbnail_seconds")
    title_text = (data.get("title_text") or "").strip()
    version = (data.get("version") or "A").strip().upper()
    if version not in ("A", "B", "C"):
        version = "A"

    video = _get_video(video_id)
    if video is None:
        return jsonify({"error": "That video isn't loaded anymore - click Analyze again first."}), 400
    if start is None or end is None or end <= start:
        return jsonify({"error": "Invalid clip times."}), 400

    frame_info = _get_framing(video, float(start), float(end)) if auto else None

    try:
        output_path = clip.make_short(
            video["video_path"],
            video["words"],
            float(start),
            float(end),
            framing=frame_info,
            crop_x_pct=float(crop_x_pct),
            caption_margin_v=float(caption_margin_v),
            title_text=title_text,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    filename = os.path.basename(output_path)

    # A ready-to-upload thumbnail: the punchiest caption line on a good frame.
    # Never let a thumbnail hiccup fail the whole short - the short is the point.
    thumb_time = float(thumbnail_seconds) if thumbnail_seconds is not None else float(start) + (float(end) - float(start)) * 0.33
    thumbnail_filename = None
    try:
        thumb_path = clip.make_thumbnail(
            video["video_path"], video["words"], float(start), float(end), thumb_time,
            framing=frame_info, crop_x_pct=float(crop_x_pct), caption_margin_v=float(caption_margin_v),
            output_name=os.path.splitext(filename)[0] + ".jpg", title_text=title_text,
        )
        thumbnail_filename = os.path.basename(thumb_path)
    except Exception:
        traceback.print_exc()

    _save_generated_sidecar(
        filename, video_id, video.get("title", video_id), candidate_title, reason,
        float(start), float(end), float(crop_x_pct), float(caption_margin_v), thumbnail_filename,
        title_text, version,
    )
    resp = {"filename": filename, "url": f"/api/file/{filename}"}
    if thumbnail_filename:
        resp["thumbnail_url"] = f"/api/file/{thumbnail_filename}"
    return jsonify(resp)


@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    timestamp = data.get("timestamp")
    auto = data.get("auto", True)
    crop_x_pct = data.get("crop_x_pct", 0.5)
    caption_margin_v = data.get("caption_margin_v", 440)
    title_text = (data.get("title_text") or "").strip()

    video = _get_video(video_id)
    if video is None:
        return jsonify({"error": "That video isn't loaded anymore - click Analyze again first."}), 400
    if start is None or end is None or end <= start:
        return jsonify({"error": "Invalid clip times."}), 400

    frame_info = _get_framing(video, float(start), float(end)) if auto else None

    try:
        output_path = clip.make_preview_frame(
            video["video_path"],
            video["words"],
            float(start),
            float(end),
            timestamp=float(timestamp) if timestamp is not None else None,
            framing=frame_info,
            crop_x_pct=float(crop_x_pct),
            caption_margin_v=float(caption_margin_v),
            title_text=title_text,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    filename = os.path.basename(output_path)
    return jsonify({"url": f"/api/preview_file/{filename}", "auto_used": frame_info is not None})


def _save_generated_sidecar(filename, video_id, source_title, candidate_title, reason, start, end, crop_x_pct, caption_margin_v, thumbnail_filename=None, title_text="", version="A"):
    data = {
        "filename": filename,
        "video_id": video_id,
        "source_title": source_title,
        "candidate_title": candidate_title,
        "reason": reason,
        "start": start,
        "end": end,
        "crop_x_pct": crop_x_pct,
        "caption_margin_v": caption_margin_v,
        "title_text": title_text,
        "version": version,
        "thumbnail_filename": thumbnail_filename,
        "generated_at": datetime.datetime.now().isoformat(),
    }
    sidecar_path = os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _record_post(filename, result, title):
    """Stamps a successful YouTube post onto the short's sidecar so History can
    show what's already been posted (and stop her posting the same clip twice).
    Read-modify-write so it never clobbers the clip's existing metadata, written
    atomically like _save_analysis. Best-effort: the video is already live, so a
    sidecar hiccup must never turn a successful post into a failure."""
    sidecar_path = os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json")
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {"filename": filename}

    data["posted_at"] = datetime.datetime.now().isoformat()
    data["youtube_video_id"] = result.get("video_id")
    data["youtube_url"] = result.get("watch_url")
    data["posted_privacy"] = result.get("privacy_status")
    data["posted_title"] = title
    data["post_count"] = int(data.get("post_count", 0)) + 1
    # A real post supersedes any earlier hand-mark, so the card shows the real
    # date and Watch link instead of "marked by you".
    data.pop("posted_manual", None)

    tmp_path = sidecar_path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, sidecar_path)


@app.route("/api/history")
def history():
    analyses = []
    for path in glob.glob(os.path.join(ANALYSES_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                analyses.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    analyses.sort(key=lambda a: a.get("analyzed_at", ""), reverse=True)

    generated = []
    for path in glob.glob(os.path.join(OUTPUT_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not os.path.isfile(os.path.join(OUTPUT_DIR, entry.get("filename", ""))):
            continue
        entry["url"] = f"/api/file/{entry['filename']}"
        thumb = entry.get("thumbnail_filename")
        if thumb and os.path.isfile(os.path.join(OUTPUT_DIR, thumb)):
            entry["thumbnail_url"] = f"/api/file/{thumb}"
        generated.append(entry)
    generated.sort(key=lambda g: g.get("generated_at", ""), reverse=True)

    return jsonify({"analyses": analyses, "generated": generated})


@app.route("/api/delete", methods=["POST"])
def delete_short():
    """Removes a generated short from History. Sends the video, its thumbnail,
    and its sidecar to the Recycle Bin (recoverable) rather than deleting them
    outright, so a misclick is never permanent."""
    data = request.get_json(force=True)
    filename = os.path.basename((data.get("filename") or "").strip())
    if not filename:
        return jsonify({"error": "No file specified."}), 400

    # Path safety: the resolved target must stay inside OUTPUT_DIR.
    target = os.path.abspath(os.path.join(OUTPUT_DIR, filename))
    if os.path.commonpath([target, os.path.abspath(OUTPUT_DIR)]) != os.path.abspath(OUTPUT_DIR):
        return jsonify({"error": "Invalid file path."}), 400

    stem = os.path.splitext(filename)[0]
    to_trash = [filename, stem + ".json"]

    # The sidecar knows the real thumbnail name; fall back to the stem's .jpg.
    sidecar_path = os.path.join(OUTPUT_DIR, stem + ".json")
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            thumb = json.load(f).get("thumbnail_filename")
        if thumb:
            to_trash.append(os.path.basename(thumb))
    except (OSError, json.JSONDecodeError):
        to_trash.append(stem + ".jpg")

    # Trash each piece if it's actually there; a partial cleanup should still succeed.
    for name in to_trash:
        path = os.path.join(OUTPUT_DIR, name)
        if os.path.isfile(path):
            try:
                send2trash(path)
            except OSError:
                traceback.print_exc()

    return jsonify({"ok": True})


@app.route("/api/youtube/status", methods=["GET"])
def youtube_status():
    """Tells the UI whether the one-time Google setup is done and whether her
    account is connected, so it can show the right button."""
    return jsonify(youtube.status())


@app.route("/api/youtube/connect", methods=["POST"])
def youtube_connect():
    """Runs the one-time sign-in. This opens her browser and blocks until she
    grants access, so the server must be threaded (see app.run below)."""
    try:
        return jsonify(youtube.connect())
    except youtube.YouTubeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/youtube/disconnect", methods=["POST"])
def youtube_disconnect():
    youtube.disconnect()
    return jsonify(youtube.status())


@app.route("/api/youtube/post", methods=["POST"])
def youtube_post():
    """Posts an already-made short to her channel: writes a title/description,
    then uploads the video and its thumbnail."""
    data = request.get_json(force=True)
    filename = os.path.basename((data.get("filename") or "").strip())
    privacy = data.get("privacy", "public")
    if not filename:
        return jsonify({"error": "No short specified."}), 400

    # Path safety: the target must stay inside OUTPUT_DIR.
    video_path = os.path.abspath(os.path.join(OUTPUT_DIR, filename))
    if os.path.commonpath([video_path, os.path.abspath(OUTPUT_DIR)]) != os.path.abspath(OUTPUT_DIR):
        return jsonify({"error": "Invalid file path."}), 400
    if not os.path.isfile(video_path):
        return jsonify({"error": "That short isn't on this computer anymore."}), 400

    # The sidecar holds the title/reason/source and the real thumbnail name.
    sidecar = {}
    sidecar_path = os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json")
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            sidecar = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    thumb_name = sidecar.get("thumbnail_filename")
    thumbnail_path = os.path.join(OUTPUT_DIR, thumb_name) if thumb_name else None
    if thumbnail_path and not os.path.isfile(thumbnail_path):
        thumbnail_path = None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    # Post the exact title she sees on the History card (candidate_title) so
    # what's on YouTube always matches what she saw here; Claude still writes
    # the description.
    title, description = youtube.write_metadata(
        sidecar.get("candidate_title", ""),
        sidecar.get("reason", ""),
        sidecar.get("source_title", ""),
        api_key,
        title_override=sidecar.get("candidate_title", ""),
    )

    try:
        result = youtube.upload_short(video_path, thumbnail_path, title, description, privacy)
    except youtube.YouTubeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    # Remember that this short went out, so History can mark it as posted and
    # she never accidentally posts the same clip twice again. The upload already
    # succeeded, so never let a bookkeeping error surface as a failed post.
    try:
        _record_post(filename, result, title)
    except Exception:
        traceback.print_exc()

    result["title"] = title
    result["description"] = description
    return jsonify(result)


@app.route("/api/youtube/mark", methods=["POST"])
def youtube_mark():
    """Lets her hand-mark a short as already posted (or undo that) - for shorts
    she posted before automatic tracking existed, or posted by hand outside the
    app. A manual mark carries no watch link or count, just the fact that it's
    up, so History can show it apart from the not-yet-posted ones."""
    data = request.get_json(force=True)
    filename = os.path.basename((data.get("filename") or "").strip())
    posted = bool(data.get("posted"))
    if not filename:
        return jsonify({"error": "No short specified."}), 400

    # Path safety: the sidecar must stay inside OUTPUT_DIR (same guard as /delete).
    sidecar_path = os.path.abspath(os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json"))
    if os.path.commonpath([sidecar_path, os.path.abspath(OUTPUT_DIR)]) != os.path.abspath(OUTPUT_DIR):
        return jsonify({"error": "Invalid file path."}), 400

    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            sc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "That short's details aren't on this computer anymore."}), 400

    if posted:
        sc["posted_at"] = datetime.datetime.now().isoformat()
        sc["posted_manual"] = True
    else:
        # Undo cleanly so the card reverts to the plain not-posted look.
        for k in ("posted_at", "posted_manual", "post_count", "youtube_url",
                  "posted_privacy", "youtube_video_id", "posted_title"):
            sc.pop(k, None)

    tmp_path = sidecar_path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sc, f)
    os.replace(tmp_path, sidecar_path)

    return jsonify({"ok": True, "posted": posted})


@app.route("/api/file/<path:filename>")
def get_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/api/preview_file/<path:filename>")
def get_preview_file(filename):
    return send_from_directory(clip.WORK_DIR, filename)


if __name__ == "__main__":
    # threaded=True so the blocking "Connect YouTube" sign-in doesn't freeze
    # the rest of the app while her browser is open.
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
