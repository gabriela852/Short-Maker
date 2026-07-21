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


def _analysis_path(video_id):
    return os.path.join(ANALYSES_DIR, f"{video_id}.json")


def _save_analysis(video_id, title, duration, candidates, segments):
    data = {
        "video_id": video_id,
        "title": title,
        "duration": duration,
        "candidates": candidates,
        "segments": segments,
        "analyzed_at": datetime.datetime.now().isoformat(),
    }
    path = _analysis_path(video_id)
    tmp_path = path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _load_analysis(video_id):
    path = _analysis_path(video_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
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

    analysis = _load_analysis(video_id)
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
    if not url:
        return jsonify({"error": "Paste a video link first."}), 400

    try:
        video = download.fetch_source(url)
        candidates, segments = rank.find_best_moments(video["words"], api_key)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    VIDEO_CACHE[video["video_id"]] = video

    duration = video["words"][-1]["end"] if video["words"] else 0
    _save_analysis(video["video_id"], video["title"], duration, candidates, segments)

    return jsonify(
        {
            "video_id": video["video_id"],
            "title": video["title"],
            "duration": duration,
            "candidates": candidates,
            "segments": segments,
        }
    )


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    auto = data.get("auto", True)
    crop_x_pct = data.get("crop_x_pct", 0.5)
    caption_margin_v = data.get("caption_margin_v", 90)
    candidate_title = data.get("candidate_title", "")
    reason = data.get("reason", "")
    thumbnail_seconds = data.get("thumbnail_seconds")

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
            output_name=os.path.splitext(filename)[0] + ".jpg",
        )
        thumbnail_filename = os.path.basename(thumb_path)
    except Exception:
        traceback.print_exc()

    _save_generated_sidecar(
        filename, video_id, video.get("title", video_id), candidate_title, reason,
        float(start), float(end), float(crop_x_pct), float(caption_margin_v), thumbnail_filename,
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
    caption_margin_v = data.get("caption_margin_v", 90)

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
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    filename = os.path.basename(output_path)
    return jsonify({"url": f"/api/preview_file/{filename}", "auto_used": frame_info is not None})


def _save_generated_sidecar(filename, video_id, source_title, candidate_title, reason, start, end, crop_x_pct, caption_margin_v, thumbnail_filename=None):
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
        "thumbnail_filename": thumbnail_filename,
        "generated_at": datetime.datetime.now().isoformat(),
    }
    sidecar_path = os.path.join(OUTPUT_DIR, os.path.splitext(filename)[0] + ".json")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


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
    title, description = youtube.write_metadata(
        sidecar.get("candidate_title", ""),
        sidecar.get("reason", ""),
        sidecar.get("source_title", ""),
        api_key,
    )

    try:
        result = youtube.upload_short(video_path, thumbnail_path, title, description, privacy)
    except youtube.YouTubeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    result["title"] = title
    result["description"] = description
    return jsonify(result)


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
