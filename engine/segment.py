"""Splits a word-level transcript into short, sentence-like segments.

A "segment" is the single unit of both picking (rank.py hands Claude numbered
segments and it returns segment indices, so its boundaries can never land
mid-sentence) and trimming (the UI lets Gabriela nudge a clip's start/end one
segment at a time). Defining it here once keeps those two in lockstep.

Descript transcripts carry real punctuation, so segments break on sentence-ending
punctuation. YouTube auto-captions have none, so we fall back to breaking on a
speaking pause (a gap between words) or a max length. Both paths run every time,
so a mixed transcript still segments sensibly.
"""

SENTENCE_END = (".", "?", "!", "…")
GAP_THRESHOLD = 0.65   # seconds of silence that we treat as a phrase break
MAX_WORDS = 24         # safety cap so a punctuation-less run can't grow forever


def _make(chunk):
    return {
        "start": round(chunk[0]["start"], 2),
        "end": round(chunk[-1]["end"], 2),
        "text": " ".join(w["text"].strip() for w in chunk).strip(),
    }


def split_segments(words):
    """words: [{start, end, text}, ...] -> [{start, end, text}, ...] in order."""
    segments = []
    chunk = []
    prev_end = None
    for w in words:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        # Break BEFORE this word on a long pause or an over-long run.
        if chunk and (w["start"] - prev_end > GAP_THRESHOLD or len(chunk) >= MAX_WORDS):
            segments.append(_make(chunk))
            chunk = []
        chunk.append(w)
        prev_end = w["end"]
        # Break AFTER this word if it ends a sentence.
        if text[-1] in SENTENCE_END:
            segments.append(_make(chunk))
            chunk = []
    if chunk:
        segments.append(_make(chunk))
    return segments
