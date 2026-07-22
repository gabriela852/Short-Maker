"""Posts a finished short straight to Gabriela's YouTube channel - video,
thumbnail, and an auto-written title and description - so she never has to
download a clip and re-upload it by hand.

How the Google side works (why the UI says what it says):
- She does a ONE-TIME connect: her browser opens, she picks her Google account
  and grants access. We save a token in data/ so she never has to sign in again.
- YouTube LOCKS every video uploaded through an app to *private* until Google
  has audited the app (a one-time form). Before that audit, a posted short lands
  as a private draft on her channel and can't be flipped public in Studio - the
  only way to publish is to re-upload by hand OR pass the audit. After the audit,
  posts can go public directly. We always report the REAL status YouTube returns
  so the UI never over-promises.
- Custom thumbnails also need a phone-verified channel; if that fails we still
  keep the uploaded video (a thumbnail hiccup should never lose the short).

We ask for the narrowest scope that works: youtube.upload covers both the video
upload and setting its thumbnail.
"""
import glob
import json
import os

import anthropic
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The credentials file she downloads from Google Cloud goes in the project
# root. Google names it something long like
# "client_secret_1234-abcd.apps.googleusercontent.com.json", so we accept ANY
# file starting with "client_secret" - no renaming required on her part.
TOKEN_PATH = os.path.join(BASE_DIR, "data", "youtube_token.json")

# youtube.upload = upload videos and set their thumbnails. Nothing more.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

# A caption that reads like the user missed a setup step, not a stack trace.
CLIENT_SECRET_MISSING_MSG = (
    "YouTube isn't set up yet. Follow the one-time Google setup guide "
    "(YOUTUBE_SETUP.md in the app folder) to download your client_secret.json "
    "into the Shorts Maker folder, then connect your account."
)


class YouTubeError(Exception):
    """A message safe to show the user as-is."""


def _client_secret_path():
    # Prefer the tidy name if it's there, otherwise take whatever Google named it.
    exact = os.path.join(BASE_DIR, "client_secret.json")
    if os.path.isfile(exact):
        return exact
    matches = sorted(glob.glob(os.path.join(BASE_DIR, "client_secret*.json")))
    return matches[0] if matches else None


def has_client_secret():
    return _client_secret_path() is not None


def is_connected():
    return os.path.isfile(TOKEN_PATH)


def _load_credentials():
    """Loads the saved token, refreshing it if it has expired. Returns None if
    there's no usable token (she needs to connect). Never raises for the normal
    'not connected yet' case."""
    if not os.path.isfile(TOKEN_PATH):
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    except (ValueError, json.JSONDecodeError, OSError):
        return None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            return creds
        except Exception:
            # Refresh token was revoked or expired (e.g. consent screen still in
            # "Testing" mode, where tokens die after 7 days). Force a reconnect.
            return None
    return None


def _save_credentials(creds):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.replace(tmp, TOKEN_PATH)


def status():
    """What the UI needs to decide which button to show."""
    return {
        "has_client_secret": has_client_secret(),
        "connected": _load_credentials() is not None,
    }


def connect():
    """Runs the one-time sign-in. Opens her browser, she grants access, we save
    the token. Blocking - the caller runs this on a request thread. Returns the
    fresh status dict."""
    path = _client_secret_path()
    if not path:
        raise YouTubeError(CLIENT_SECRET_MISSING_MSG)
    try:
        flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
        # port=0 lets the OS pick a free port for the local callback.
        creds = flow.run_local_server(
            port=0,
            prompt="consent",  # ensures we get a refresh_token every time
            authorization_prompt_message="",
            success_message="Connected! You can close this tab and go back to Shorts Maker.",
        )
    except Exception as e:
        raise YouTubeError(f"Couldn't connect to YouTube: {e}")
    _save_credentials(creds)
    return {"has_client_secret": True, "connected": True}


def disconnect():
    """Forgets the saved token so she can reconnect (or connect a different
    account)."""
    if os.path.isfile(TOKEN_PATH):
        os.remove(TOKEN_PATH)


def _service():
    creds = _load_credentials()
    if creds is None:
        raise YouTubeError("Your YouTube account isn't connected. Click Connect YouTube first.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


# ---- Auto-written title & description -------------------------------------

WRITE_METADATA_TOOL = {
    "name": "write_metadata",
    "description": "Write the YouTube Shorts title and description for this clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A punchy YouTube Shorts title, at most 90 characters, no quotation marks. Written to make someone stop scrolling and tap.",
            },
            "description": {
                "type": "string",
                "description": "One or two sentences that tease the clip and make people want to watch. Do not use quotation marks. Do NOT include any hashtags.",
            },
        },
        "required": ["title", "description"],
    },
}

METADATA_SYSTEM = """You write titles and descriptions for YouTube Shorts. You are given a working title \
for a clip, a note on why the moment is engaging, and the name of the longer video it came from. Write a \
title and description that would make someone scrolling their feed stop and watch. Keep the title tight and \
curiosity-driving. Keep the description short and human - never clickbait that the clip doesn't deliver on. \
Use the write_metadata tool to report your answer."""


def _fallback_metadata(candidate_title, reason):
    title = (candidate_title or "Watch this").strip()[:100]
    description = (reason or "").strip()
    return title, description


def write_metadata(candidate_title, reason, source_title, api_key):
    """Returns (title, description). Uses Claude when a key is available, but
    always falls back to a plain template so posting never fails just because
    the write-up call did."""
    if not api_key:
        return _fallback_metadata(candidate_title, reason)
    try:
        client = anthropic.Anthropic(api_key=api_key)
        user = (
            f"Working title: {candidate_title}\n"
            f"Why this moment is engaging: {reason}\n"
            f"From the longer video: {source_title}"
        )
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=METADATA_SYSTEM,
            tools=[WRITE_METADATA_TOOL],
            tool_choice={"type": "tool", "name": "write_metadata"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "write_metadata":
                title = (block.input.get("title") or candidate_title or "Watch this").strip()[:100]
                description = (block.input.get("description") or "").strip()
                return title, description
    except Exception:
        pass
    return _fallback_metadata(candidate_title, reason)


# ---- Upload ----------------------------------------------------------------

def _studio_url(video_id):
    return f"https://studio.youtube.com/video/{video_id}/edit"


def _friendly_http_error(e):
    """Turns a Google HttpError into a sentence Gabriela can act on."""
    status_code = getattr(getattr(e, "resp", None), "status", None)
    detail = ""
    try:
        detail = json.loads(e.content.decode("utf-8"))["error"]["message"]
    except Exception:
        detail = str(e)
    if status_code == 403 and "quota" in detail.lower():
        return YouTubeError(
            "YouTube's daily upload limit for this app has been reached "
            "(about 6 posts a day). Try again tomorrow."
        )
    if status_code == 401:
        return YouTubeError("Your YouTube connection expired. Click Connect YouTube again.")
    return YouTubeError(f"YouTube rejected the upload: {detail}")


def upload_short(video_path, thumbnail_path, title, description, privacy="public"):
    """Uploads the short (and its thumbnail) to her channel. Returns a dict with
    the real privacy status YouTube assigned, a watch link, and a Studio link.
    Raises YouTubeError with a user-friendly message on failure."""
    if not os.path.isfile(video_path):
        raise YouTubeError("That short's video file is missing, so there's nothing to upload.")
    if privacy not in ("public", "unlisted", "private"):
        privacy = "public"

    youtube = _service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            # "People & Blogs" - a safe, broadly-allowed default category.
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    try:
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = req.execute()
    except HttpError as e:
        raise _friendly_http_error(e)
    except Exception as e:
        raise YouTubeError(f"Something went wrong uploading to YouTube: {e}")

    video_id = response["id"]
    actual_privacy = response.get("status", {}).get("privacyStatus", privacy)

    # Thumbnail is a nice-to-have: never let it fail the whole post. It can 403
    # if the channel isn't phone-verified (no custom thumbnails yet).
    thumbnail_set = False
    if thumbnail_path and os.path.isfile(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
            ).execute()
            thumbnail_set = True
        except Exception:
            thumbnail_set = False

    return {
        "video_id": video_id,
        "privacy_status": actual_privacy,
        "requested_privacy": privacy,
        "watch_url": f"https://youtu.be/{video_id}",
        "studio_url": _studio_url(video_id),
        "thumbnail_set": thumbnail_set,
    }
