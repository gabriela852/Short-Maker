"""Finds the ffmpeg/ffprobe/deno executables even if the app's PATH hasn't been
refreshed since they were installed (common right after a fresh winget install),
and makes sure their folders are on PATH for tools (like yt-dlp) that look them
up themselves."""
import shutil
import glob
import os

_FFMPEG_GLOB = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*-full_build\bin"
)
_DENO_GLOB = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\DenoLand.Deno*"
)


def _find(binary_name, extra_globs):
    on_path = shutil.which(binary_name)
    if on_path:
        return on_path
    for pattern in extra_globs:
        for bin_dir in glob.glob(pattern):
            candidate = os.path.join(bin_dir, binary_name + ".exe")
            if os.path.isfile(candidate):
                return candidate
    return None


FFMPEG = _find("ffmpeg", [_FFMPEG_GLOB])
FFPROBE = _find("ffprobe", [_FFMPEG_GLOB])
DENO = _find("deno", [_DENO_GLOB])

if not FFMPEG or not FFPROBE:
    raise FileNotFoundError(
        "Could not find ffmpeg/ffprobe. Install it with: winget install Gyan.FFmpeg"
    )

# Make sure subprocesses we spawn (and libraries like yt-dlp that do their own
# PATH lookups) can find these even if this Python process started before
# winget updated the system PATH.
for _exe in (FFMPEG, FFPROBE, DENO):
    if _exe:
        _dir = os.path.dirname(_exe)
        if _dir not in os.environ["PATH"]:
            os.environ["PATH"] = _dir + os.pathsep + os.environ["PATH"]
