"""Match our exercise catalog (exercises.json) against the gymvisual-based
exercises-dataset (github.com/hasaneyldrm/exercises-dataset) and vendor the
matched media into static/gvlib/.

Media is (c) Gym visual (gymvisual.com), redistributed by that repo with
permission at 180x180 with the attribution notice kept intact — the app shows
the credit line in the how-to sheet and NOTICE.md ships in static/gvlib/.

Usage:
    .env/bin/python match_gvlib.py            # match + report
    .env/bin/python match_gvlib.py --download # also fetch stills + GIFs

Output:
    static/gvlib/index.json    canonical name -> {id, img, gif, instructions,
                               primary, secondary}
    static/gvlib/media/<file>.jpg|.gif
    gvlib_overrides.json       optional manual fixes: {canonical: gv id|null}
"""
import json
import re
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).parent
RAW = "https://raw.githubusercontent.com/hasaneyldrm/exercises-dataset/main/"

# our muscle -> acceptable gv `target` values
MUSCLE_EQUIV = {
    "quadriceps": {"quads", "glutes"},
    "chest": {"pectorals", "serratus anterior"},
    "abdominals": {"abs"},
    "shoulders": {"delts", "traps", "levator scapulae"},
    "full_body": None,
    "biceps": {"biceps", "forearms"},
    "glutes": {"glutes", "hamstrings", "quads"},
    "lats": {"lats", "upper back"},
    "triceps": {"triceps"},
    "upper_back": {"upper back", "lats", "traps"},
    "cardio": None,
    "calves": {"calves"},
    "hamstrings": {"hamstrings", "glutes", "spine"},
    "traps": {"traps", "upper back", "levator scapulae"},
    "lower_back": {"spine", "glutes"},
    "forearms": {"forearms"},
    "neck": {"levator scapulae", "traps"},
    "abductors": {"abductors", "glutes"},
    "adductors": {"adductors"},
    "other": None,
}

EQUIP_WORDS = {"barbell", "dumbbell", "cable", "machine", "smith", "kettlebell",
               "band", "bodyweight", "ez", "trap", "plate", "weighted", "sled",
               "lever", "leverage", "assisted", "rope"}

SYN = {
    "flye": "fly", "flyes": "fly", "flys": "fly", "flies": "fly",
    "bicep": "biceps", "tricep": "triceps",
    "pushup": "push up", "pushups": "push up", "pullup": "pull up",
    "pullups": "pull up", "chinup": "chin up", "situp": "sit up",
    "pulldowns": "pulldown", "pullovers": "pullover",
    "pullaparts": "pull apart",
    "ab": "abdominal", "abs": "abdominal",
    "pec": "chest", "skullcrusher": "lying triceps extension",
    "rdl": "romanian deadlift", "sldl": "stiff leg deadlift",
    "lever": "machine", "leverage": "machine",
}


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\((.*?)\)", r" \1 ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\bsmith machine\b", "smith", s)
    s = re.sub(r"\bbody weight\b", "bodyweight", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(SYN.get(w, w) for w in s.split())


def stem(t: str) -> str:
    if len(t) > 4 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokens(s: str) -> set:
    drop = {"with", "the", "a", "on", "grip", "medium", "wide", "close", "seated",
            "standing", "version", "two", "arm", "single", "male", "female"}
    return {stem(t) for t in norm(s).split() if t not in drop}


def score(ours: str, theirs: str) -> float:
    a, b = tokens(ours), tokens(theirs)
    if not a or not b:
        return 0.0
    inter = len(a & b) / len(a | b)
    seq = SequenceMatcher(None, norm(ours), norm(theirs)).ratio()
    ea, eb = a & EQUIP_WORDS, b & EQUIP_WORDS
    if ea and eb and not (ea & eb):
        return 0.0
    return 0.6 * inter + 0.4 * seq


def main():
    download = "--download" in sys.argv
    ours = json.load(open(ROOT / "exercises.json"))
    gv = json.load(open("/tmp/gv.json"))
    overrides = {}
    opath = ROOT / "gvlib_overrides.json"
    if opath.exists():
        overrides = json.load(open(opath))

    by_id = {e["id"]: e for e in gv}
    index, missed = {}, []
    canonicals = {ex["canonical"] for ex in ours.values()}
    for ex in ours.values():
        name = ex["canonical"]
        if name in overrides:
            gid = overrides[name]
            if gid and gid in by_id:
                index[name] = by_id[gid]
            else:
                missed.append(name)
            continue
        allowed = MUSCLE_EQUIV.get(ex.get("muscle") or "other")
        best, best_s = None, 0.0
        for f in gv:
            if allowed is not None and f["target"] not in allowed:
                continue
            s = score(name, f["name"])
            if s > best_s:
                best, best_s = f, s
        if best and best_s >= 0.5:
            index[name] = best
        else:
            missed.append(name)

    # Overrides may also pin names that aren't catalog canonicals (e.g. the
    # athlete's own DB spellings like "21s Bicep Curl (Cables)").
    for name, gid in overrides.items():
        if name not in canonicals and gid and gid in by_id:
            index[name] = by_id[gid]

    # Alias keys: prescriptions and the DB sometimes use the Hevy title or an
    # alias rather than the canonical name — give them the same record.
    for ex in ours.values():
        rec = index.get(ex["canonical"])
        if not rec:
            continue
        for alias in [ex.get("hevy_title") or ""] + (ex.get("aliases") or []):
            if alias and alias not in index:
                index[alias] = rec

    out = ROOT / "static" / "gvlib"
    (out / "media").mkdir(parents=True, exist_ok=True)
    slim = {}
    for name, f in index.items():
        img = f["image"].split("/")[-1]
        gif = f["gif_url"].split("/")[-1]
        slim[name] = {
            "id": f["id"],
            "img": img,
            "gif": gif,
            "instructions": (f.get("instruction_steps") or {}).get("en") or [],
            "primary": [f["target"]],
            "secondary": [m for m in (f.get("secondary_muscles") or [])
                          if m != f["target"]],
        }
        if download:
            for sub, fn in (("images", img), ("videos", gif)):
                dest = out / "media" / fn
                if dest.exists():
                    continue
                try:
                    urllib.request.urlretrieve(RAW + sub + "/" + fn, dest)
                except Exception as e:
                    print(f"  ! {name}: {fn} failed ({e})")
    json.dump(slim, open(out / "index.json", "w"), separators=(",", ":"))
    if download:
        try:
            urllib.request.urlretrieve(RAW + "NOTICE.md", out / "NOTICE.md")
        except Exception:
            pass
    print(f"matched {len(index)}/{len(ours)}  (missed {len(missed)})")
    for m in missed:
        print("  miss:", m)


if __name__ == "__main__":
    main()
