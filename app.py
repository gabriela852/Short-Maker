import os
import traceback

from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, request, send_from_directory

from engine import download, rank, clip

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "outputs")

if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "a").close()
load_dotenv(ENV_PATH)

app = Flask(__name__, static_folder="static", template_folder="static")

# In-memory cache so /api/generate doesn't need to re-download/re-transcribe
# after /api/analyze already did the work in this session.
VIDEO_CACHE = {}


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
        return jsonify({"error": "Paste a YouTube link first."}), 400

    try:
        video = download.fetch_video(url)
        candidates = rank.find_best_moments(video["words"], api_key)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    VIDEO_CACHE[video["video_id"]] = video

    duration = video["words"][-1]["end"] if video["words"] else 0
    return jsonify(
        {
            "video_id": video["video_id"],
            "title": video["title"],
            "duration": duration,
            "candidates": candidates,
        }
    )


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    crop_x_pct = data.get("crop_x_pct", 0.5)
    caption_margin_v = data.get("caption_margin_v", 90)

    if video_id not in VIDEO_CACHE:
        return jsonify({"error": "That video isn't loaded anymore - click Analyze again first."}), 400
    if start is None or end is None or end <= start:
        return jsonify({"error": "Invalid clip times."}), 400

    video = VIDEO_CACHE[video_id]
    try:
        output_path = clip.make_short(
            video["video_path"],
            video["words"],
            float(start),
            float(end),
            crop_x_pct=float(crop_x_pct),
            caption_margin_v=float(caption_margin_v),
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    filename = os.path.basename(output_path)
    return jsonify({"filename": filename, "url": f"/api/file/{filename}"})


@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.get_json(force=True)
    video_id = data.get("video_id")
    start = data.get("start")
    end = data.get("end")
    timestamp = data.get("timestamp")
    crop_x_pct = data.get("crop_x_pct", 0.5)
    caption_margin_v = data.get("caption_margin_v", 90)

    if video_id not in VIDEO_CACHE:
        return jsonify({"error": "That video isn't loaded anymore - click Analyze again first."}), 400
    if start is None or end is None or end <= start:
        return jsonify({"error": "Invalid clip times."}), 400

    video = VIDEO_CACHE[video_id]
    try:
        output_path = clip.make_preview_frame(
            video["video_path"],
            video["words"],
            float(start),
            float(end),
            timestamp=float(timestamp) if timestamp is not None else None,
            crop_x_pct=float(crop_x_pct),
            caption_margin_v=float(caption_margin_v),
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    filename = os.path.basename(output_path)
    return jsonify({"url": f"/api/preview_file/{filename}"})


@app.route("/api/file/<path:filename>")
def get_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/api/preview_file/<path:filename>")
def get_preview_file(filename):
    return send_from_directory(clip.WORK_DIR, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
