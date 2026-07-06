"""Asks Claude to read a video's transcript and pick the most engaging moments
for a short, the way a human editor would (hook, payoff, self-contained story) -
rather than scoring volume peaks or keywords."""
import os
import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

PICK_SHORTS_TOOL = {
    "name": "pick_shorts",
    "description": "Report the best candidate short-form clips found in the transcript.",
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
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                        "title": {
                            "type": "string",
                            "description": "A short, punchy working title for this clip, as if it were the on-screen hook text.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One or two sentences on why this moment is the most engaging - what the hook, payoff, or emotional beat is.",
                        },
                    },
                    "required": ["start_seconds", "end_seconds", "title", "reason"],
                },
            }
        },
        "required": ["candidates"],
    },
}

SYSTEM_PROMPT = """You are an expert short-form video editor, in the style of a senior editor at CapCut or Descript who \
specializes in finding viral moments in long-form YouTube videos to turn into 30-60 second Shorts/Reels/TikToks.

Given a timestamped transcript, find the 3 best possible standalone clips. A great clip:
- Opens with an immediate hook in its first sentence (a bold claim, a question, a surprising statement) - viewers decide in \
1-2 seconds whether to keep watching.
- Is self-contained: a viewer who has never seen the full video can follow it without missing context.
- Has a clear payoff, punchline, emotional peak, or "aha" moment within the clip.
- Is between 25 and 60 seconds long (prefer 30-45s).
- Does NOT start or end mid-sentence or mid-thought.

Pick moments that are genuinely different from each other (don't pick three variations of the same beat).
Use the pick_shorts tool to report your answer. start_seconds/end_seconds must be based on the timestamps given in the transcript."""


def _build_transcript_lines(words, gap_threshold=0.6, max_words_per_line=14):
    lines = []
    current = []
    current_start = None
    last_end = None
    for w in words:
        if current and (w["start"] - last_end > gap_threshold or len(current) >= max_words_per_line):
            lines.append((current_start, " ".join(current)))
            current = []
        if not current:
            current_start = w["start"]
        current.append(w["text"].strip())
        last_end = w["end"]
    if current:
        lines.append((current_start, " ".join(current)))
    return lines


def _format_timestamp(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def _snap_to_words(start, end, words):
    """Avoids mid-word cuts by snapping to the nearest word boundaries."""
    starts = [w["start"] for w in words]
    ends = [w["end"] for w in words]

    snapped_start = start
    for s in starts:
        if s <= start:
            snapped_start = s
        else:
            break

    snapped_end = end
    for e in ends:
        if e >= end:
            snapped_end = e
            break
    else:
        snapped_end = ends[-1] if ends else end

    return snapped_start, snapped_end


def find_best_moments(words, api_key):
    client = anthropic.Anthropic(api_key=api_key)

    lines = _build_transcript_lines(words)
    transcript_text = "\n".join(f"[{_format_timestamp(t)}] {text}" for t, text in lines)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=[PICK_SHORTS_TOOL],
        tool_choice={"type": "tool", "name": "pick_shorts"},
        messages=[
            {
                "role": "user",
                "content": f"Here is the transcript:\n\n{transcript_text}",
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "pick_shorts":
            candidates = block.input["candidates"]
            for c in candidates:
                snapped_start, snapped_end = _snap_to_words(c["start_seconds"], c["end_seconds"], words)
                c["start_seconds"] = round(snapped_start, 2)
                c["end_seconds"] = round(snapped_end, 2)
            return candidates

    raise RuntimeError("Claude didn't return a structured answer. Try again.")
