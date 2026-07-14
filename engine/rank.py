"""Asks Claude to read a video's transcript and pick the most engaging moments
for a short, the way a human editor would (hook, payoff, self-contained story) -
rather than scoring volume peaks or keywords.

Claude picks by SEGMENT INDEX, not by raw seconds. We hand it the transcript
already split into numbered, sentence-like segments (see engine/segment.py) and
it returns the first/last segment to include. That means its picks can never
land mid-sentence, and the exact same segments power the manual trim controls in
the UI - one source of truth for where a clip can begin and end."""
import os
import anthropic

from engine import segment

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

PICK_SHORTS_TOOL = {
    "name": "pick_shorts",
    "description": "Report the best candidate short-form clips found in the transcript, as ranges of segment indices.",
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "start_index": {
                            "type": "integer",
                            "description": "Index of the FIRST segment to include in the clip. The clip opens here, so this MUST be the single most shocking, beautiful, surprising, or high-energy moment you can start on - the hook that lands in the very first second. Never open on setup, context, or preamble.",
                        },
                        "end_index": {
                            "type": "integer",
                            "description": "Index of the LAST segment to include (inclusive). The clip must end here on a complete thought or payoff.",
                        },
                        "title": {
                            "type": "string",
                            "description": "A short, punchy working title for this clip, as if it were the on-screen hook text.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One or two sentences on why this moment is the most engaging - what the hook, payoff, or emotional beat is.",
                        },
                        "thumbnail_index": {
                            "type": "integer",
                            "description": "Index of the single segment (between start_index and end_index) whose line is the most scroll-stopping - a bold claim, a surprising reveal, or an emotional peak - to show as caption text on the thumbnail. Not the very first or last segment.",
                        },
                    },
                    "required": ["start_index", "end_index", "title", "reason", "thumbnail_index"],
                },
            }
        },
        "required": ["candidates"],
    },
}

SYSTEM_PROMPT = """You are an expert short-form video editor, in the style of a senior editor at CapCut or Descript who \
specializes in finding viral moments in long-form YouTube videos to turn into 30-60 second Shorts/Reels/TikToks.

You are given a transcript that has already been split into numbered segments, one per line, like:
[0] (00:00) Hey, how's it going?
[1] (00:03) I just won a hackathon and I'm not a software engineer.

Find the 3 best possible standalone clips. A great clip:
- OPENS ON THE MOST SCROLL-STOPPING MOMENT of the clip - the single most shocking, beautiful, surprising, or \
high-energy line you can find. This is the hook, and it MUST land in the very first second: viewers decide in 1-2 \
seconds whether to keep watching, so never open on setup, context, or slow build-up. Start exactly where the energy \
peaks and let the clip play forward from there. Only include a lead-in segment before that peak if the moment is \
genuinely impossible to follow without it, and keep any such lead-in to a single short segment.
- Is self-contained: a viewer who has never seen the full video can follow it without missing context.
- Has a clear payoff, punchline, emotional peak, or "aha" moment, and ENDS on the segment that completes that thought.
- Is roughly 25 to 60 seconds long (prefer 30-45s) - add up the segment durations to judge length.

Report each clip as a range of segment indices: start_index (the first segment to include) through end_index (the last \
segment to include, inclusive). Because you pick whole segments, clips will never start or end mid-sentence.

Pick moments that are genuinely different from each other (don't pick three variations of the same beat). Use the \
pick_shorts tool to report your answer."""


def _format_timestamp(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def find_best_moments(words, api_key):
    """Returns (candidates, segments). Each candidate carries both segment
    indices (start_index/end_index, for the trim UI) and the seconds they map
    to (start_seconds/end_seconds/thumbnail_seconds, for cutting)."""
    client = anthropic.Anthropic(api_key=api_key)

    segments = segment.split_segments(words)
    if not segments:
        raise RuntimeError("This video's transcript came through empty, so there's nothing to analyze.")
    n = len(segments)

    transcript_text = "\n".join(
        f"[{i}] ({_format_timestamp(s['start'])}) {s['text']}" for i, s in enumerate(segments)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=[PICK_SHORTS_TOOL],
        tool_choice={"type": "tool", "name": "pick_shorts"},
        messages=[
            {
                "role": "user",
                "content": f"Here is the segmented transcript:\n\n{transcript_text}",
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "pick_shorts":
            candidates = []
            for c in block.input["candidates"]:
                i0 = max(0, min(int(c["start_index"]), n - 1))
                i1 = max(0, min(int(c["end_index"]), n - 1))
                if i1 < i0:
                    i0, i1 = i1, i0
                start = segments[i0]["start"]
                end = segments[i1]["end"]
                # Thumbnail: middle of the chosen segment, kept inside the clip.
                t_idx = max(i0, min(int(c.get("thumbnail_index", i0)), i1))
                thumb = (segments[t_idx]["start"] + segments[t_idx]["end"]) / 2
                candidates.append({
                    "title": c["title"],
                    "reason": c["reason"],
                    "start_index": i0,
                    "end_index": i1,
                    "start_seconds": round(start, 2),
                    "end_seconds": round(end, 2),
                    "thumbnail_seconds": round(thumb, 2),
                })
            return candidates, segments

    raise RuntimeError("Claude didn't return a structured answer. Try again.")
