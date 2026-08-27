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

from engine import creator_profile, segment

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
                "maxItems": 15,
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

def _system_prompt(max_clips):
    return f"""You are an expert short-form video editor, in the style of a senior editor at CapCut or Descript who \
specializes in finding viral moments in long-form YouTube videos to turn into 30-60 second Shorts/Reels/TikToks.

You are given a transcript that has already been split into numbered segments, one per line, like:
[0] (00:00) Hey, how's it going?
[1] (00:03) I just won a hackathon and I'm not a software engineer.

Find the {max_clips} best possible standalone clips. A great clip:
- OPENS ON THE MOST SCROLL-STOPPING MOMENT of the clip - the single most shocking, beautiful, surprising, or \
high-energy line you can find. This is the hook, and it MUST land in the very first second: viewers decide in 1-2 \
seconds whether to keep watching, so never open on setup, context, or slow build-up. Start exactly where the energy \
peaks and let the clip play forward from there. Only include a lead-in segment before that peak if the moment is \
genuinely impossible to follow without it, and keep any such lead-in to a single short segment.
- STARTS AT THE BEGINNING OF A COMPLETE SENTENCE. The start_index segment must be the first word of a sentence, \
never the middle of one. Read the segment's text: if it begins lowercase or picks up mid-thought (e.g. "allowed me \
to make that move..."), it is NOT a valid opening - move the start to the segment where that sentence actually begins.
- The opening sentence must be PUNCHY and stand on its own. It must NOT begin with a filler or connector word such \
as "And", "But", "So", "Or", "Because", "Plus", or "Also" - those signal you have started mid-flow. Pick a segment \
whose first word carries weight.
- Is self-contained: a viewer who has never seen the full video can follow it without missing context.
- Has a clear payoff, punchline, emotional peak, or "aha" moment. The end_index segment must FINISH that thought - \
its text must end with a period, question mark, or exclamation. The last line must complete the idea; never end on a \
segment that trails off or starts a brand-new thought that the clip won't finish.
- Is roughly 25 to 60 seconds long (prefer 30-45s) - add up the segment durations to judge length.

Report each clip as a range of segment indices: start_index (the first segment to include) through end_index (the last \
segment to include, inclusive). Choose start_index so the clip opens on a full sentence, and end_index so it closes on \
a finished thought.

Return {max_clips} clips whenever the video can genuinely support that many. They MUST be distinct:
- Their segment ranges must NOT overlap - no clip may share a segment with another clip.
- Each must come from a different part or topic of the video. Never pick two variations of the same beat, the \
same story, or the same line said twice.
- Spread them across the whole video (beginning, middle, and end), not clustered in one stretch.

Only return fewer than {max_clips} if the video is genuinely too short or too repetitive to yield {max_clips} strong, non-overlapping \
clips - in that case return as many genuinely distinct ones as it truly supports, rather than padding with weak or \
overlapping picks.

List the clips in order of quality, your single strongest clip FIRST and weakest last (even though the clips \
themselves come from different points across the video). Use the pick_shorts tool to report your answer."""


def _system_prompt_b(max_clips):
    """Version B: ultra-tight 20-30s clips. Immediate hook, one self-contained
    idea, high density, no fluff. (Physically cutting internal pauses - true
    'remove dead air' - is a separate render-time feature; here we steer the
    model toward dense passages and enforce the 20-30s length below.)"""
    return f"""You are an expert short-form video editor who makes ULTRA-TIGHT, high-retention \
Shorts/Reels/TikToks - the punchy 20 to 30 second kind with zero wasted words.

You are given a transcript that has already been split into numbered segments, one per line, like:
[0] (00:00) Hey, how's it going?
[1] (00:03) I just won a hackathon and I'm not a software engineer.

Find the {max_clips} best possible standalone clips. EVERY clip MUST obey ALL of these rules:
- IMMEDIATE HOOK: the clip starts DIRECTLY on a bold claim, a strong statement, or an intriguing \
question. No setup, no context, no warm-up, no preamble. The very first sentence must grab attention \
entirely on its own.
- SELF-CONTAINED: it delivers ONE full insight or ONE complete story that makes complete sense without \
any other part of the video. A viewer who never saw the rest can follow it completely.
- HIGH INFORMATION DENSITY: zero fluff, zero conversational filler, no rambling, no dead air. Every \
sentence must earn its place. Avoid passages where she trails off, repeats herself, or pauses to think.
- LENGTH 20 TO 30 SECONDS. Add up the segment durations as you choose the range and keep each clip inside \
this window. Aim for around 25 seconds. This is a hard requirement, not a suggestion.
- CLEAN SENTENCE BOUNDARIES: start_index must be the FIRST word of a sentence (never mid-thought, never a \
filler connector like "And", "But", "So", "Because"), and end_index must FINISH the thought (its text ends \
with a period, question mark, or exclamation).

Report each clip as a range of segment indices: start_index through end_index (inclusive).

Return {max_clips} clips whenever the video can genuinely support that many, each DISTINCT:
- Their segment ranges must NOT overlap - no clip may share a segment with another.
- Each must come from a different part or topic of the video. Never pick two versions of the same beat or line.
- Spread them across the whole video (beginning, middle, and end).

Only return fewer than {max_clips} if the video genuinely can't yield that many strong, non-overlapping \
20 to 30 second clips - return as many as it truly supports rather than padding with weak or overlapping picks.

List the clips strongest FIRST, weakest last. Use the pick_shorts tool to report your answer."""


def _system_prompt_c(max_clips):
    """Version C: moment SELECTION and LENGTH are both driven by this creator's
    real audience-performance data (see engine/creator_profile.py). Where A hunts
    for the generically strongest moment, C hunts for the moments THIS audience
    has actually rewarded, and targets 20-45s (her data: 20+s watched earns
    engagement, over ~45s underperforms) - enforced in find_best_moments as
    floor 18, cap 45."""
    return f"""You are an expert short-form video editor who turns long-form YouTube videos into 20 to 45 second \
Shorts, and you have this specific creator's real performance data to guide you.

You are given a transcript that has already been split into numbered segments, one per line, like:
[0] (00:00) Hey, how's it going?
[1] (00:03) I just won a hackathon and I'm not a software engineer.

Find the {max_clips} best possible standalone clips. Above all else, choose the moments this creator's own \
audience rewards, using the data below.

{creator_profile.WHAT_WORKS}

{creator_profile.WHAT_FLOPS}

Beyond matching that audience data, every clip must still be a clean, watchable Short:
- OPENS ON A SCROLL-STOPPING MOMENT that lands in the very first second - the hook that decides whether a \
viewer keeps watching. Never open on setup, context, or slow build-up.
- STARTS AT THE BEGINNING OF A COMPLETE SENTENCE. The start_index segment must be the first word of a \
sentence, never mid-thought, and must NOT begin with a filler word such as "And", "But", "So", "Or", \
"Because", "Plus", or "Also".
- Is self-contained: a viewer who never saw the full video can follow it without missing context.
- Has a clear payoff, punchline, emotional peak, or "aha" moment. The end_index segment must FINISH that \
thought - its text must end with a period, question mark, or exclamation.
- LENGTH 20 TO 45 SECONDS, ideally around 25 to 40. This is tuned to her performance data: shorts her \
audience actually watches for 20+ seconds get more engagement, so a clip needs enough substance to earn that, \
but clips over about 45 seconds underperform, so never go past 45. Add up the segment durations to judge length.

When two moments are both clean, cutable clips, prefer the one that better matches the audience data above: a \
raw, vulnerable, first-person moment with real personal stakes beats an abstract, impersonal one.

Report each clip as a range of segment indices: start_index (the first segment to include) through end_index \
(the last segment to include, inclusive).

Return {max_clips} clips whenever the video can genuinely support that many. They MUST be distinct:
- Their segment ranges must NOT overlap - no clip may share a segment with another clip.
- Each must come from a different part or topic of the video. Never pick two variations of the same beat, the \
same story, or the same line said twice.
- Spread them across the whole video (beginning, middle, and end), not clustered in one stretch.

Only return fewer than {max_clips} if the video is genuinely too short or too repetitive to yield {max_clips} strong, \
non-overlapping clips - in that case return as many genuinely distinct ones as it truly supports, rather than \
padding with weak or overlapping picks.

List the clips in order of quality, your single strongest clip FIRST and weakest last. Use the pick_shorts \
tool to report your answer."""


def _format_timestamp(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


# --- Clean sentence boundaries -------------------------------------------------
# A clip must OPEN on a full, punchy sentence (never mid-thought, never a filler
# word) and CLOSE on a completed thought (never trailing into a new idea). The
# prompt asks the model for this; these snap helpers enforce it as a safety net,
# since the model can still hand back a ragged range.

WEAK_OPENERS = {"and", "but", "so", "or", "because", "plus", "also", "yet"}


def _ends_sentence(text):
    """True if this segment finishes a thought (ends with sentence punctuation),
    ignoring any trailing quote or bracket."""
    t = (text or "").rstrip().rstrip('"”’)]')
    return bool(t) and t[-1] in segment.SENTENCE_END


def _first_word(text):
    """The segment's first word, lowercased and stripped of surrounding
    punctuation - used to reject weak opening connectors."""
    t = (text or "").strip().lstrip('"“‘([')
    parts = t.split()
    return parts[0].strip(",.!?;:-—…").lower() if parts else ""


# How far (in segments) a snap is allowed to travel looking for a clean sentence
# boundary. Sentences are usually one segment, so a real boundary is always close.
# If none is found within this window, we KEEP the model's original pick rather
# than dragging the clip across the video - which is what would otherwise happen
# on transcripts with little or no punctuation (e.g. YouTube auto-captions), where
# forcing a boundary would collapse every clip to the whole video.
_SNAP_WINDOW = 3


def _starts_sentence(segments, i):
    """A segment opens a sentence if it's the first one, or the segment before it
    ended with sentence punctuation."""
    return i == 0 or _ends_sentence(segments[i - 1]["text"])


def _snap_start(segments, i, end):
    """Return a clean opening segment: the start of the sentence `i` lands in, and
    if that sentence opens on a weak filler word, the next sentence start instead.
    Bounded by _SNAP_WINDOW so a punctuation-poor transcript keeps the original
    pick instead of collapsing, and the weak-word skip never jumps past `end`
    (which would shrink the clip to nothing)."""
    n = len(segments)
    orig = min(max(i, 0), n - 1)

    # Walk back to the sentence start, but no further than the window.
    j = orig
    steps = 0
    while j > 0 and not _starts_sentence(segments, j) and steps < _SNAP_WINDOW:
        j -= 1
        steps += 1
    if not _starts_sentence(segments, j):
        j = orig  # no real boundary nearby - keep the model's pick

    # If it opens on a weak connector, try the next sentence start within the
    # window, but only if that start still leaves room before the clip's end.
    if _first_word(segments[j]["text"]) in WEAK_OPENERS:
        k = j + 1
        steps = 0
        while k < n and not _starts_sentence(segments, k) and steps < _SNAP_WINDOW:
            k += 1
            steps += 1
        # Only skip if it still leaves real clip after it (never collapse to one
        # segment); otherwise keep this complete sentence even if it opens weakly.
        if k < n and k < end and _starts_sentence(segments, k):
            j = k
    return j


def _snap_end(segments, i, start):
    """Return a segment that finishes a thought: the last sentence-ending segment
    at or before `i` (not before `start`), else the next one after `i`. Bounded by
    _SNAP_WINDOW; if no boundary is near, keep the model's original end."""
    n = len(segments)
    orig = min(max(i, start), n - 1)

    j = orig
    steps = 0
    while j > start and not _ends_sentence(segments[j]["text"]) and steps < _SNAP_WINDOW:
        j -= 1
        steps += 1
    if _ends_sentence(segments[j]["text"]) and j >= start:
        return j

    k = orig
    steps = 0
    while k < n and not _ends_sentence(segments[k]["text"]) and steps < _SNAP_WINDOW:
        k += 1
        steps += 1
    if k < n and _ends_sentence(segments[k]["text"]):
        return k

    return orig  # no boundary nearby - keep the model's pick


# A clip shorter than this is unusable as a Short (target is 25-60s). If snapping
# ever leaves one this short (e.g. the end got pulled back onto the start), we
# extend it forward to reach a viable length - see _extend_end_to_floor.
_MIN_CLIP_SECONDS = 12


def _extend_end_to_floor(segments, i0, i1, min_seconds):
    """If the snapped clip is too short, advance the end to successive completed
    thoughts (keeping a clean ending) until it reaches min_seconds or the
    transcript runs out. Prevents a one-segment clip when the model's end trailed
    off unpunctuated and the end snapped back onto the start."""
    n = len(segments)
    j = i1
    while (segments[j]["end"] - segments[i0]["start"]) < min_seconds and j < n - 1:
        k = j + 1
        while k < n and not _ends_sentence(segments[k]["text"]):
            k += 1
        if k >= n:
            break  # no further completed thought - keep the clean short clip
        j = k
    return j


# --- On-screen title banner headline ------------------------------------------
# A separate, tiny generation for the big headline burned across the TOP of the
# short (the optional title banner, see engine/clip.py). This is NOT the YouTube
# title and NOT the captions - it is the on-screen hook a viewer reads in the
# first second. It is deliberately much shorter and punchier than the working
# clip title, and it obeys the no-dash rule (long dashes read as AI).

WRITE_HEADLINE_TOOL = {
    "name": "write_headline",
    "description": "Report one short on-screen title-banner headline for the clip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "The on-screen headline text. At most 60 characters, ideally 3 to 7 words.",
            }
        },
        "required": ["headline"],
    },
}

_HEADLINE_SYSTEM = """You write the big TITLE BANNER that sits across the very top of a vertical short \
video (Reels / TikTok / YouTube Shorts), above the speaker's head. This is NOT the YouTube title and NOT the \
captions. It is the on-screen hook a viewer reads in the first second while deciding whether to keep scrolling.

Rules for the headline:
- VERY SHORT. Aim for 3 to 7 words, never more than 60 characters. It has to fit at a large size on one or two lines.
- It must make someone STOP SCROLLING: a bold claim, a relatable feeling, a curiosity gap, a "POV:" line, or a \
"friendly reminder that..." Match the real emotional core of this specific clip.
- Sound like a warm, real person talking to a friend. No clickbait, no ALL CAPS, no emoji, no hashtags, and no \
surrounding quotation marks.
- NEVER use an em dash or en dash. Use a comma, a period, or a plain hyphen instead.
- Do not just repeat the spoken words verbatim. Capture the hook behind them.

Return exactly one headline using the write_headline tool."""

# Version C only: the same banner rules plus the creator's proven hook patterns.
# Kept separate so Versions A and B write headlines exactly as they always have.
_HEADLINE_SYSTEM_C = _HEADLINE_SYSTEM.replace(
    "\n\nReturn exactly one headline",
    f"\n\n{creator_profile.WINNING_HOOK_PATTERNS}\n\nReturn exactly one headline",
)


def _clean_headline(text):
    """Tidy the model's headline: drop wrapping quotes, swap any long dash for a
    comma (the no-dash rule), collapse doubled spaces, and hard-cap length so it
    can never overflow the 80-char banner input."""
    h = (text or "").strip().strip('"“”‘’\'')
    h = h.replace("—", ", ").replace("–", ", ")  # em / en dash -> comma
    while "  " in h:
        h = h.replace("  ", " ")
    return h.strip(" ,").strip()[:80]


def write_headline(clip_text, candidate_title, reason, api_key, avoid=None, version="A"):
    """Writes one short on-screen banner headline for a single clip. `avoid` is
    the current headline the user wants replaced (the Suggest another button), so
    the model is told to take a fresh angle instead of repeating it.

    `version` "C" adds the creator's proven hook patterns to the brief; "A"/"B"
    write headlines exactly as before (unchanged)."""
    client = anthropic.Anthropic(api_key=api_key)
    system = _HEADLINE_SYSTEM_C if (version or "A").upper() == "C" else _HEADLINE_SYSTEM

    user = (
        f'The clip\'s spoken words:\n"{clip_text}"\n\n'
        f"A working title we already have for it: {candidate_title}\n"
        f"Why this moment is engaging: {reason}"
    )
    if avoid:
        user += f'\n\nGive a DIFFERENT headline from this one, a genuinely fresh angle: "{avoid}"'

    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=system,
        tools=[WRITE_HEADLINE_TOOL],
        tool_choice={"type": "tool", "name": "write_headline"},
        messages=[{"role": "user", "content": user}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "write_headline":
            headline = _clean_headline(block.input.get("headline", ""))
            if headline:
                return headline
    raise RuntimeError("Claude didn't return a headline. Try again.")


def _trim_end_to_max(segments, i0, i1, max_seconds):
    """Pull the clip's end back so it fits within `max_seconds`, landing on the
    LATEST sentence-ending segment that still fits (keeps a clean finish). If no
    complete-thought boundary fits inside the cap, keep the model's end rather
    than cutting mid-sentence (a rare clip that runs a little long but reads
    right beats one chopped off mid-thought). Used by Version B's 20-30s cap."""
    best = None
    for j in range(i0, i1 + 1):
        if segments[j]["end"] - segments[i0]["start"] > max_seconds:
            break
        if _ends_sentence(segments[j]["text"]):
            best = j
    return best if best is not None else i1


def find_best_moments(words, api_key, version="A"):
    """Returns (candidates, segments). Each candidate carries both segment
    indices (start_index/end_index, for the trim UI) and the seconds they map
    to (start_seconds/end_seconds/thumbnail_seconds, for cutting).

    `version` selects the editing style: "A" is the balanced 25-60s cut, "B" is
    the punchy 20-30s cut (immediate hook, one dense idea). The length window is
    enforced AFTER the model answers (see the floor/cap below), because snapping
    to clean sentence boundaries can otherwise drift a clip outside the target."""
    client = anthropic.Anthropic(api_key=api_key)

    segments = segment.split_segments(words)
    if not segments:
        raise RuntimeError("This video's transcript came through empty, so there's nothing to analyze.")
    n = len(segments)

    # How many clips to look for scales with the video's length - about one per
    # minute - so a 10-minute video surfaces up to ~10 and a short video isn't
    # padded with weak picks. Floored at 3 so short videos still yield a few, and
    # capped at 15 (the tool schema's ceiling). The model still returns fewer if
    # the video can't genuinely support this many, always strongest-first.
    duration_seconds = segments[-1]["end"] if segments else 0
    max_clips = max(3, min(15, round(duration_seconds / 60)))

    # Per-version length window. Enforced below after the model answers.
    #   A: balanced, 25-60s target, floor 12, no hard cap (matches old behavior).
    #   B: punchy, 20-30s target, floor 18, hard cap 30.
    if version == "B":
        system_prompt = _system_prompt_b(max_clips)
        floor_seconds, cap_seconds = 18, 30
        # B promises 20-30s. If a spot in the transcript can't be extended to a
        # full ~16s+ complete thought, drop it rather than show a stubby clip
        # under the punchy label.
        drop_below = 16
    elif version == "C":
        # C picks moments using the creator's performance data (prompt above),
        # and its LENGTH is tuned to that data too: her shorts need 20+ seconds
        # of watchable substance to earn engagement, and clips over ~45s
        # underperform. So 20-45s, enforced after the model answers, landing on
        # clean sentence ends. Floor 18 gives a little slack under the 20 target.
        system_prompt = _system_prompt_c(max_clips)
        floor_seconds, cap_seconds = 18, 45
        drop_below = None
    else:
        system_prompt = _system_prompt(max_clips)
        floor_seconds, cap_seconds = _MIN_CLIP_SECONDS, None
        drop_below = None

    transcript_text = "\n".join(
        f"[{i}] ({_format_timestamp(s['start'])}) {s['text']}" for i, s in enumerate(segments)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=system_prompt,
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
            kept_ranges = []  # (i0, i1) of clips already kept, to guarantee distinctness
            for c in block.input["candidates"]:
                # Defensive: very rarely the model hands back a candidate that
                # isn't a proper object (or is missing its indices). Skip it
                # rather than failing the whole analysis.
                if not isinstance(c, dict) or "start_index" not in c or "end_index" not in c:
                    continue
                i0 = max(0, min(int(c["start_index"]), n - 1))
                i1 = max(0, min(int(c["end_index"]), n - 1))
                if i1 < i0:
                    i0, i1 = i1, i0
                # Clean boundaries: open on a full, punchy sentence and close on a
                # finished thought - even if the model handed back a ragged range.
                # Bounded so punctuation-poor transcripts keep the original pick.
                i0 = _snap_start(segments, i0, i1)
                i1 = _snap_end(segments, i1, i0)
                if i1 < i0:
                    i1 = i0
                # Enforce the version's length window. Extend a too-short clip up
                # to the floor, then (Version B) pull a too-long clip back under
                # the cap, landing on a clean sentence end either way.
                if segments[i1]["end"] - segments[i0]["start"] < floor_seconds:
                    i1 = _extend_end_to_floor(segments, i0, i1, floor_seconds)
                if cap_seconds is not None and segments[i1]["end"] - segments[i0]["start"] > cap_seconds:
                    i1 = _trim_end_to_max(segments, i0, i1, cap_seconds)
                # Version B only: drop a clip that still can't reach a usable
                # length (the transcript had no full thought there to extend to).
                if drop_below is not None and segments[i1]["end"] - segments[i0]["start"] < drop_below:
                    continue
                # Distinctness guard: candidates arrive best-first, so if this one
                # shares any segment with a clip we've already kept, drop it - the
                # user wants *distinct* shorts, not near-duplicates of one moment.
                if any(i0 <= k1 and k0 <= i1 for k0, k1 in kept_ranges):
                    continue
                kept_ranges.append((i0, i1))
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
