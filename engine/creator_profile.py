"""The creator's proven winning formula - a cheat sheet built from real YouTube
Shorts performance data, handed to every place where Claude makes a creative
choice: the moment picker (engine/rank.py) and the on-screen banner + YouTube
title writers.

Without this, the writers pick "generically engaging" moments. With it, they
pick the moments THIS creator's audience actually rewards, and phrase hooks the
way her winners were phrased. This is the single source of truth: edit it here
and the picker, the banner, and the title all get smarter at once.

To keep it current: after a new batch of shorts, replace the WINNERS / LOSERS
examples and the pattern notes below with what your latest numbers show. Nothing
else in the app needs to change.

Source: Sea your Horizon Shorts analysis, 28-day window (Jul-Aug 2026).
Headline finding: 94% of views come from the Shorts FEED (swiping), so the
click-through/thumbnail matters far less than the FIRST 1-2 SECONDS and the
on-screen hook. The real enemy is the ~67% swipe-away rate, won or lost on the
opening beat."""


# What this audience rewards, strongest signal first. Phrased so it can be
# dropped straight into an editor/writer system prompt.
WHAT_WORKS = """This creator's real audience data (YouTube Shorts, last 28 days) shows her viewers \
reward a specific kind of moment. Favor these strongly, in this order:
1. VULNERABLE FIRST-PERSON CONFESSIONS - a raw, personal admission she'd normally keep private. Her single \
best short was "I filmed myself every night just to feel less lonely" (7% click-through, far above the rest). \
Look for moments where she admits a struggle, a fear, a lonely or hard season, or something she's ashamed of.
2. RELATABLE PAIN POINTS named out loud - a feeling the viewer secretly has but rarely hears said. Winners: \
"Why you're exhausted even after resting", "Why even simple decisions feel impossible". Look for moments that \
diagnose a quiet, common ache (burnout, indecision, feeling stuck, losing yourself).
3. IDENTITY AND CAREER-TRANSITION STORIES with real stakes - leaving a career, not recognizing yourself, \
outgrowing an old life. Winners: "I looked in the mirror and didn't know who was looking back", "I lost myself \
when I left nursing". Her nursing-to-new-life arc especially lands.
4. TIMELY HOT TAKES on industry or wellness news - a reaction to something happening right now. Winner: \
"YouTube just doubled its requirements overnight".
5. A SINGLE POINTED QUESTION that makes a viewer stop and reflect on their own life. Winner: "The one \
question to ask yourself today"."""


# What tends to underperform - a SOFT tie-breaker, not a ban. The view spread in
# the data (roughly 600-1140 across one short window) is noisy, so this describes
# failure MODES to weigh against, never specific topics to avoid. Career and
# identity transitions are a proven winner (see WHAT_WORKS #3); it's advice with
# no personal moment behind it that falls flat, not the subject matter.
WHAT_FLOPS = """As a gentle tie-breaker only (the data here is noisy, so never treat this as a hard rule), \
weigh DOWN moments whose only content is:
- GENERIC ADVICE with no concrete, personal moment attached - a tip that could come from anyone's LinkedIn, \
with nothing confessed and nothing at stake for HER specifically.
- MOTIVATIONAL PLATITUDES floating free of a real, specific story from her own life.
- Passages with NO clear conflict, tension, or personal stake - nothing is risked, nothing is revealed.
This is about the MISSING personal stake, not the topic. A career switch, leaving nursing, or a piece of \
hard-won advice is a strong pick when it's tied to a real, vulnerable moment - only skip it when it's abstract \
and impersonal."""


# Title / hook phrasing patterns her winners share - for the banner and YouTube
# title writers.
WINNING_HOOK_PATTERNS = """Her hooks that worked share a shape. Lean into these patterns:
- "Why you're [uncomfortable feeling]..." - names a hidden ache. ("Why you're exhausted even after resting.")
- "I [vulnerable confession or action]..." - raw and first-person. ("I filmed myself every night just to \
feel less lonely.")
- "I [lost / didn't recognize / left]..." - an identity or transition turn. ("I lost myself when I left nursing.")
- "The one [question / exercise / thing] that..." - a single, specific promise. ("The one question to ask \
yourself today.")
Keep it concrete, first-person, and emotionally honest. Skip vague, upbeat, one-size-fits-all lines - those are \
the ones that got swiped past."""
