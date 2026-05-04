"""
sync_creators.py — Sync YouTube creator content for exercise discovery.

Run daily via cron (separate from the morning run.py job):
    python sync_creators.py

Add creators to TRUSTED_CREATORS in config.py, then run this to backfill.
"""
import config
from creators import sync_creator, creator_scores

if __name__ == "__main__":
    if not config.TRUSTED_CREATORS:
        print("[creators] No creators configured. Add entries to TRUSTED_CREATORS in config.py.")
    else:
        total = 0
        for creator in config.TRUSTED_CREATORS:
            if not creator.get("channel_id"):
                print(f"[creators] Skipping {creator['name']} — no channel_id")
                continue
            n = sync_creator(creator)
            total += n
        print(f"[creators] Done. {total} new videos processed.")

    scores = creator_scores()
    if scores:
        print(f"\nTop creator-recommended exercises ({len(scores)} scored):")
        for s in scores[:10]:
            print(f"  {s['exercise_name']}: {s['score']:.2f}")
