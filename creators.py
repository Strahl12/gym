"""
creators.py — YouTube creator content ingestion.

Polls trusted YouTube channels for new videos, fetches transcripts,
sends them to Claude to extract exercise mentions, and computes a
creator-weighted score per exercise. These scores surface in the
training context as an advisory signal for exercise selection.

No YouTube Data API key needed — uses public RSS feeds and youtube-transcript-api.
"""
import json
import math
import sqlite3
import requests
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Optional
import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"   # transcripts are long, use fast model
MAX_TOKENS    = 1500
RSS_URL       = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

SENTIMENT_VALUES = {"positive": 1.0, "neutral": 0.3, "cautionary": -0.5}


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── RSS polling ────────────────────────────────────────────────────────────

def fetch_new_videos(channel_id: str) -> list[dict]:
    """
    Fetch the RSS feed for a channel and return videos not yet in creator_videos.
    Each entry: {video_id, title, published_at}
    """
    resp = requests.get(RSS_URL.format(channel_id=channel_id), timeout=10)
    resp.raise_for_status()

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt":   "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ns)

    con = _con()
    known = {r[0] for r in con.execute("SELECT video_id FROM creator_videos").fetchall()}
    con.close()

    new = []
    for entry in entries:
        vid = entry.findtext("yt:videoId", namespaces=ns)
        if not vid or vid in known:
            continue
        title = entry.findtext("atom:title", namespaces=ns) or ""
        pub   = entry.findtext("atom:published", namespaces=ns) or ""
        new.append({"video_id": vid, "title": title, "published_at": pub[:10]})
    return new


# ── Transcript fetching ────────────────────────────────────────────────────

def fetch_transcript(video_id: str) -> Optional[str]:
    """Fetch the transcript for a YouTube video. Returns plain text or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api    = YouTubeTranscriptApi()
        result = api.fetch(video_id)
        return " ".join(s.text for s in result.snippets)
    except Exception:
        return None


# ── Claude analysis ────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """You are analysing a fitness YouTube video transcript to extract exercise recommendations.

Video: "{title}" by {creator}

## Known exercise names
Map every exercise you extract to the closest name in this list. Use the exact string from the list.
Only use a free-form name if the exercise genuinely does not appear here.

{exercise_list}

## Instructions
Extract every exercise the creator mentions with a clear recommendation. Return ONLY a JSON array — no explanation, no markdown.

Each item:
{{
  "exercise": "exact name from the known list above, or free-form if not present",
  "sentiment": "positive" | "neutral" | "cautionary",
  "recommendation": "replace" | "add" | "avoid" | "general",
  "context": "one sentence quote or paraphrase showing why",
  "count": <number of times mentioned>
}}

sentiment:
  positive    — creator recommends it, praises it, or calls it a favourite
  neutral     — mentioned without strong opinion
  cautionary  — flagged for risk, called overrated, or suggested to avoid

recommendation:
  replace  — explicitly suggested as a better alternative to something
  add      — suggested to include in training
  avoid    — explicitly warned against
  general  — discussed generally without a specific instruction

Only include exercises with a clear recommendation signal. Ignore technique cues, programming concepts,
and passing mentions that aren't about the exercise itself.
If the transcript has no exercise recommendations, return [].

Transcript:
{transcript}"""


def _canonical_exercise_list() -> str:
    """Build the reference list of canonical exercise names for the analysis prompt."""
    import exercise_lib
    db = exercise_lib.all_exercises()
    names = sorted(set(
        ex["canonical"] for ex in db.values()
        if ex.get("session_type") in ("push", "pull", "legs", "arms", "core")
    ))
    return "\n".join(names)


def analyze_transcript(video_id: str, title: str, transcript: str, creator_name: str) -> list[dict]:
    """Send a transcript to Claude and return structured exercise mentions."""
    # Truncate to ~6k words to stay well within token limits
    words = transcript.split()
    if len(words) > 6000:
        transcript = " ".join(words[:6000]) + " [truncated]"

    prompt = _ANALYSIS_PROMPT.format(
        title=title, creator=creator_name,
        transcript=transcript,
        exercise_list=_canonical_exercise_list(),
    )

    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key":          config.ANTHROPIC_API_KEY,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        },
        json={
            "model":      CLAUDE_MODEL,
            "max_tokens": MAX_TOKENS,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[creators] JSON parse error for {video_id}")
        return []


# ── DB writes ──────────────────────────────────────────────────────────────

def store_video(channel_id: str, video_id: str, title: str,
                published_at: str, transcript: Optional[str]) -> None:
    con = _con()
    con.execute("""
        INSERT OR IGNORE INTO creator_videos
            (video_id, channel_id, title, published_at, transcript_text)
        VALUES (?, ?, ?, ?, ?)
    """, (video_id, channel_id, title, published_at, transcript))
    con.commit()
    con.close()


def store_mentions(video_id: str, mentions: list[dict]) -> None:
    import exercise_lib
    con = _con()
    con.execute("DELETE FROM creator_exercise_mentions WHERE video_id = ?", (video_id,))
    for m in mentions:
        name    = m.get("exercise", "").strip()
        if not name:
            continue
        hevy_id = exercise_lib.resolve_id(name)
        con.execute("""
            INSERT INTO creator_exercise_mentions
                (video_id, exercise_name, hevy_id, sentiment, recommendation, context_snippet, mention_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id, name, hevy_id,
            m.get("sentiment", "neutral"),
            m.get("recommendation", "general"),
            m.get("context", "")[:300],
            int(m.get("count", 1)),
        ))
    con.execute("UPDATE creator_videos SET analyzed_at = datetime('now') WHERE video_id = ?", (video_id,))
    con.commit()
    con.close()


# ── Scoring ────────────────────────────────────────────────────────────────

def creator_scores() -> list[dict]:
    """
    Compute a weighted score per exercise across all creators and videos.

    score = Σ (creator_weight × sentiment_value × recency_decay × mention_count)
    recency_decay = e^(-age_days / 180)   — half-weight at ~6 months

    Returns list of {exercise_name, hevy_id, score} sorted descending, filtered
    to score >= CREATOR_SCORE_MIN.
    """
    con = _con()
    cutoff = (date.today() - timedelta(days=config.CREATOR_SCORE_LOOKBACK_DAYS)).isoformat()
    rows = con.execute("""
        SELECT m.exercise_name, m.hevy_id, m.sentiment, m.mention_count,
               v.published_at, c.weight
        FROM creator_exercise_mentions m
        JOIN creator_videos  v ON v.video_id   = m.video_id
        JOIN creators        c ON c.channel_id = v.channel_id
        WHERE v.published_at >= ? AND c.active = 1
    """, (cutoff,)).fetchall()
    con.close()

    totals: dict[str, dict] = {}
    today = date.today()
    for row in rows:
        age_days  = max(0, (today - date.fromisoformat(row["published_at"][:10])).days)
        decay     = math.exp(-age_days / 180)
        sentiment = SENTIMENT_VALUES.get(row["sentiment"], 0.0)
        increment = row["weight"] * sentiment * decay * row["mention_count"]

        key = row["exercise_name"]
        if key not in totals:
            totals[key] = {"exercise_name": key, "hevy_id": row["hevy_id"], "score": 0.0}
        totals[key]["score"] += increment

    results = [v for v in totals.values() if v["score"] >= config.CREATOR_SCORE_MIN]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def top_recommendations(session_type: Optional[str] = None, n: int = 10) -> list[dict]:
    """
    Return the top N creator-scored exercises for a given session type.
    If session_type is None, returns across all types.
    Each entry: {exercise_name, hevy_id, score, canonical}
    """
    import exercise_lib
    scores = creator_scores()

    if session_type:
        db = exercise_lib.all_exercises()
        def _stype(hevy_id: Optional[str]) -> str:
            if not hevy_id:
                return ""
            return db.get(hevy_id, {}).get("session_type", "")
        scores = [s for s in scores if _stype(s["hevy_id"]) == session_type]

    for s in scores:
        if s["hevy_id"]:
            canonical = exercise_lib.all_exercises().get(s["hevy_id"], {}).get("canonical", s["exercise_name"])
        else:
            canonical = s["exercise_name"]
        s["canonical"] = canonical

    return scores[:n]


# ── Sync one creator ───────────────────────────────────────────────────────

def sync_creator(creator: dict) -> int:
    """
    Poll for new videos, fetch transcripts, analyze. Returns count of new videos processed.
    """
    channel_id   = creator["channel_id"]
    creator_name = creator["name"]

    # Ensure creator row exists
    con = _con()
    con.execute("""
        INSERT OR IGNORE INTO creators (channel_id, name, weight) VALUES (?, ?, ?)
    """, (channel_id, creator_name, creator.get("weight", 1.0)))
    con.commit()
    con.close()

    new_videos = fetch_new_videos(channel_id)
    if not new_videos:
        print(f"[creators] {creator_name}: no new videos")
        return 0

    processed = 0
    for v in new_videos:
        vid   = v["video_id"]
        title = v["title"]
        print(f"[creators] {creator_name}: processing '{title}' ({vid})")

        transcript = fetch_transcript(vid)
        store_video(channel_id, vid, title, v["published_at"], transcript)

        if not transcript:
            print(f"[creators]   no transcript available")
            continue

        mentions = analyze_transcript(vid, title, transcript, creator_name)
        store_mentions(vid, mentions)
        print(f"[creators]   {len(mentions)} exercise mentions extracted")
        processed += 1

    return processed
