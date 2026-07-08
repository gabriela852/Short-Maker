"""Detects the subject's face in a candidate clip and computes a full-screen,
face-centered vertical (9:16) crop - the automatic framing that keeps the
speaker centered without bars or blur. Returns None when no face is found in
any sampled frame, so the caller falls back to a plain centered crop."""
import statistics

import cv2

TARGET_W = 1080
TARGET_H = 1920
SAMPLE_COUNT = 9
DETECT_WIDTH = 640  # downscale for detection speed; coordinates are scaled back up
DEFAULT_MARGIN_V = 90  # caption distance from the bottom (real target pixels)

_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _even(n):
    return int(n) // 2 * 2


def _clamp_even(n, lo, hi):
    return max(lo, min(hi, n)) // 2 * 2


def _detect_faces(video_path, start, end):
    """Samples frames evenly across the clip and returns (orig_w, orig_h,
    detections), where detections is a list of (center_x, center_y) in
    original-video pixels - one per frame that had a detectable face (the
    largest face wins when several are found)."""
    cap = cv2.VideoCapture(video_path)
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    detections = []
    if orig_w and orig_h:
        scale_down = DETECT_WIDTH / orig_w
        for i in range(SAMPLE_COUNT):
            t = start + (end - start) * (i + 1) / (SAMPLE_COUNT + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, (0, 0), fx=scale_down, fy=scale_down)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = _cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) == 0:
                continue
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            detections.append(((fx + fw / 2) / scale_down, (fy + fh / 2) / scale_down))

    cap.release()
    return orig_w, orig_h, detections


def detect_face_framing(video_path, start, end):
    """Returns a full-screen 9:16 crop centered on the speaker's face, using
    the median face position across the clip so brief leans/gestures don't
    throw off the centering. None if no face was found (caller falls back)."""
    orig_w, orig_h, detections = _detect_faces(video_path, start, end)
    if not detections or not orig_w or not orig_h:
        return None

    center_x = statistics.median(d[0] for d in detections)
    center_y = statistics.median(d[1] for d in detections)

    # Scale so the frame just covers the target on both axes (no bars), then
    # crop the 1080x1920 window centered on the face.
    scale = max(TARGET_H / orig_h, TARGET_W / orig_w)
    scaled_w = _even(orig_w * scale)
    scaled_h = _even(orig_h * scale)
    crop_x = _clamp_even(int(center_x * scale - TARGET_W / 2), 0, max(scaled_w - TARGET_W, 0))
    crop_y = _clamp_even(int(center_y * scale - TARGET_H / 2), 0, max(scaled_h - TARGET_H, 0))

    return {
        "mode": "crop",
        "scaled_w": scaled_w,
        "scaled_h": scaled_h,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "margin_v": DEFAULT_MARGIN_V,
    }
