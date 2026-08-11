"""Splits a word-level transcript into short, sentence-like segments.

A "segment" is the single unit of both picking (rank.py hands Claude numbered
segments and it returns segment indices, so its boundaries can never land
mid-sentence) and trimming (the UI lets Gabriela nudge a clip's start/end one
segment at a time). Defining it here once keeps those two in lockstep.

Descript transcripts carry real punctuation, so on those we break ONLY on
sentence-ending punctuation - every segment is a whole sentence, a complete
thought a clip can open and close on. YouTube auto-captions have no punctuation
at all, so for those we fall back to breaking on a speaking pause (a gap between
words) or a max length. We pick the mode per-transcript: the pause/length rules
are a crutch that would otherwise guillotine real sentences mid-thought on a
punctuated track (people pause and run past 24 words inside a single sentence),
so they only kick in when there's no punctuation to break on.
"""

SENTENCE_END = (".", "?", "!", "…")
TRAILING = '"”’)]'      # closing quotes/brackets that can sit after end punctuation
GAP_THRESHOLD = 0.65   # seconds of silence that we treat as a phrase break
MAX_WORDS = 24         # safety cap so a punctuation-less run can't grow forever
# On a punctuated track we keep whole sentences, but still cap pathological
# run-ons (e.g. a missing period from an ASR slip) so one bad spot can't grow
# into a 200-word mega-segment. Set high enough that normal sentences stay whole.
MAX_WORDS_PUNCTUATED = 60
# A track counts as "punctuated" if at least this share of its words end a
# sentence. Real speech runs ~0.05-0.07 (one end per 15-20 words); auto-captions
# run 0. The 0.02 line (one sentence per ~50 words) cleanly separates the two.
_PUNCTUATION_RATIO = 0.02


def _ends_sentence(text):
    """True if this token finishes a thought - ends with sentence punctuation,
    ignoring any trailing quote or bracket (e.g. that?" -> True)."""
    t = (text or "").rstrip().rstrip(TRAILING)
    return bool(t) and t[-1] in SENTENCE_END


def _is_punctuated(tokens):
    """Does this transcript carry real sentence punctuation (Descript) or none
    (YouTube auto-captions)? Decides whether we break on sentences or fall back
    to pause/length rules."""
    if not tokens:
        return False
    ends = sum(1 for w in tokens if _ends_sentence(w["text"]))
    return ends >= 3 and ends / len(tokens) >= _PUNCTUATION_RATIO


def _make(chunk):
    return {
        "start": round(chunk[0]["start"], 2),
        "end": round(chunk[-1]["end"], 2),
        "text": " ".join(w["text"].strip() for w in chunk).strip(),
    }


def split_segments(words):
    """words: [{start, end, text}, ...] -> [{start, end, text}, ...] in order."""
    tokens = [w for w in words if (w.get("text") or "").strip()]
    punctuated = _is_punctuated(tokens)
    cap = MAX_WORDS_PUNCTUATED if punctuated else MAX_WORDS

    segments = []
    chunk = []
    prev_end = None
    for w in tokens:
        text = w["text"].strip()
        # Break BEFORE this word. On a punctuated track this is only the run-on
        # safety cap; on an unpunctuated one it's also the pause break, since a
        # pause is the only signal a phrase has ended.
        if chunk and (
            len(chunk) >= cap
            or (not punctuated and w["start"] - prev_end > GAP_THRESHOLD)
        ):
            segments.append(_make(chunk))
            chunk = []
        chunk.append(w)
        prev_end = w["end"]
        # Break AFTER this word if it ends a sentence (trailing quote/bracket ok).
        if _ends_sentence(text):
            segments.append(_make(chunk))
            chunk = []
    if chunk:
        segments.append(_make(chunk))
    return segments
