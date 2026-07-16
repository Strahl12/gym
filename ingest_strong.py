"""
ingest_strong.py — one-time backfill: parse a Strong app CSV export into the
sets table for a user.

Usage:
    python ingest_strong.py --user <name> [--csv path/to/strong_workouts.csv]

The CSV path defaults to ~/Downloads/strong_workouts.csv.
"""
import argparse
import sqlite3
import re
import sys
from pathlib import Path

import pandas as pd
import config

DEFAULT_CSV = Path.home() / "Downloads" / "strong_workouts.csv"

# ---------------------------------------------------------------------------
# Exercise name normalisation
# Strong names -> canonical names
# ---------------------------------------------------------------------------
EXERCISE_ALIASES = {
    "Bench Press (Barbell)":                "Barbell Bench Press",
    "Strict Military Press (Barbell)":      "Strict Military Press",
    "Pull Up":                              "Pull Up",
    "Chin Up":                              "Chin Up",
    "Bicep pull Up":                        "Pull Up",
    "Pull Up (Assisted)":                   "Pull Up (Assisted)",
    "Triceps Dip":                          "Weighted Dip",
    "Chest Dip":                            "Weighted Dip",
    "Front Squat (Barbell)":               "Front Squat",
    "Squat (Barbell)":                      "Barbell Back Squat",
    "Deadlift (Barbell)":                  "Deadlift",
    "Incline Bench Press (Barbell)":       "Incline Bench Press",
    "Incline Bench Press (Dumbbell)":      "Incline Dumbbell Press",
    "Bench Press (Dumbbell)":              "Dumbbell Bench Press",
    "Bench Press - Close Grip (Barbell)":  "Close Grip Bench Press",
    "Lat Pulldown (Cable)":                "Lat Pulldown",
    "Lat Pulldown - Underhand (Cable)":    "Underhand Lat Pulldown",
    "Lat Pulldown - Wide Grip (Cable)":    "Wide Grip Lat Pulldown",
    "Bent Over Row (Barbell)":             "Barbell Row",
    "Bent Over Row - Underhand (Barbell)": "Underhand Barbell Row",
    "Seated Row (Cable)":                  "Cable Row",
    "Lateral Raise (Dumbbell)":            "Lateral Raise",
    "Lateral Raise (Cable)":               "Cable Lateral Raise",
    "Skullcrusher (Barbell)":              "Skullcrusher",
    "Skullcrusher (Dumbbell)":             "Dumbbell Skullcrusher",
    "Triceps Extension (Cable)":           "Cable Triceps Extension",
    "Triceps Extension (Dumbbell)":        "Dumbbell Triceps Extension",
    "Triceps Pushdown (Cable - Straight Bar)": "Triceps Pushdown",
    "Bicep Curl (Barbell)":                "Barbell Curl",
    "Bicep Curl (Dumbbell)":              "Dumbbell Curl",
    "Hammer Curl (Dumbbell)":             "Hammer Curl",
    "Preacher Curl (Barbell)":            "Preacher Curl",
    "Face Pull (Cable)":                  "Face Pull",
    "Leg Press":                           "Leg Press",
    "Seated Leg Press (Machine)":         "Leg Press",
    "Leg Extension (Machine)":            "Leg Extension",
    "Lying Leg Curl (Machine)":           "Leg Curl",
    "Seated Leg Curl (Machine)":          "Leg Curl",
    "Hip Thrust (Barbell)":               "Hip Thrust",
    "Romanian Deadlift (Barbell)":        "Romanian Deadlift",
    "Bulgarian Split Squat":              "Bulgarian Split Squat",
    "Standing Calf Raise (Machine)":      "Calf Raise",
    "Seated Calf Raise (Machine)":        "Seated Calf Raise",

    # ── Oliver's Strong history → Hevy library titles ─────────────────────
    # (Strong names that match a Hevy title exactly need no entry; muscle
    # group for these comes from the hevy_exercise_library lookup.)
    "21s":                                "21s Bicep Curl",
    "Around the World":                   "Around The World",
    "Around The World / Fly":             "Around The World",
    "Back Cross Cable":                   "Rear Delt Reverse Fly (Cable)",
    "Back Extension":                     "Back Extension (Machine)",
    "Back Fly":                           "Rear Delt Reverse Fly (Dumbbell)",
    "Back Row":                           "Seated Row (Machine)",
    "Bench Press Narrow Grip":            "Close Grip Bench Press",
    "Bicep curl rope":                    "Bicep Curl (Cable)",
    "Cable Fly":                          "Cable Fly Crossovers",
    "Cable Fly (seated)":                 "Seated Chest Flys (Cable)",
    "Cable Lat Pull Down":                "Lat Pulldown",
    "Cable Twist":                        "Cable Twist (Down to up)",
    "Calf Press on Leg Press":            "Calf Press (Machine)",
    "Chest Fly":                          "Chest Fly (Machine)",
    "Concentration Curl (Dumbbell)":      "Concentration Curl",
    "D Lat Pull":                         "Single Arm Lat Pulldown",
    "Egyptian Raise":                     "Single Arm Lateral Raise (Cable)",
    "Face Pull (seated)":                 "Face Pull",
    "Flat Bench Row":                     "Seal Row (Dumbbell)",
    "Front Raise (hands up)":             "Front Raise (Dumbbell)",
    "Goblet Squat (Kettlebell)":          "Kettlebell Goblet Squat",
    "High Cable Fly":                     "Cable Fly Crossovers",
    "High Pull":                          "Deadlift High Pull",
    "Hip Abductor (Machine)":             "Hip Abduction (Machine)",
    "Hip Adductor (Machine)":             "Hip Adduction (Machine)",
    "Incline Curl (Dumbbell)":            "Seated Incline Curl (Dumbbell)",
    "Incline Row (Dumbbell)":             "Seal Row (Dumbbell)",
    "Iso-lateral Leg Raise":              "Single Leg Extensions",
    "Iso-lateral Shoulder Press":         "Shoulder Press (Machine Plates)",
    "Iso-lateral Wide Chest":             "Iso-Lateral Chest Press (Machine)",
    "Isolated Preacher Curl (one Arm)":   "Preacher Curl (Dumbbell)",
    "Landline Shoulder Press":            "Landmine Squat and Press",
    "Lateral Plates":                     "Lateral Raise",
    "Lateral Raise - Back (dumbbell)":    "Rear Delt Reverse Fly (Dumbbell)",
    "Lateral Raise Seated For Back":      "Rear Delt Reverse Fly (Dumbbell)",
    "Low Cable Fly":                      "Low Cable Fly Crossovers",
    "Machine Dip":                        "Seated Dip Machine",
    "Narrow Parallel Pull Up":            "Pull Up",
    "One-arm Straight Lat Pull Down":     "Single Arm Lat Pulldown",
    "Overhead Tricep Cable Extension":    "Overhead Triceps Extension (Cable)",
    "Pec Deck (Machine)":                 "Butterfly (Pec Deck)",
    "Pull Up (to Chest)":                 "Sternum Pull up (Gironda)",
    "Rear Deltoids":                      "Rear Delt Reverse Fly (Machine)",
    "Reverse Fly (Dumbbell)":             "Rear Delt Reverse Fly (Dumbbell)",
    "Roman Chair":                        "Back Extension (Hyperextension)",
    "Russian Twist":                      "Russian Twist (Bodyweight)",
    "Seal Row":                           "Seal Row (Dumbbell)",
    "Seated (lean back) Bicep Curl":      "Seated Incline Curl (Dumbbell)",
    "Shoulder Press":                     "Shoulder Press (Dumbbell)",
    "Stiff Leg Deadlift (Dumbbell)":      "Romanian Deadlift (Dumbbell)",
    "Straight arm pull down":             "Straight Arm Lat Pulldown (Cable)",
    "Tricep Cable Pull Down":             "Triceps Pushdown",
    "Tricep Pull Down Split":             "Triceps Rope Pushdown",
    "Tricep Pull Upwards":                "Overhead Triceps Extension (Cable)",
    "Triceps Extension":                  "Triceps Extension (Cable)",
    "Weighted Russian Twist":             "Russian Twist (Weighted)",
    "Wide-bar Neutral Grip Lateral Pull Down": "Lat Pulldown",
}

# ---------------------------------------------------------------------------
# Muscle group classification (canonical names)
# ---------------------------------------------------------------------------
MUSCLE_GROUPS = {
    # Push
    "Barbell Bench Press":       "push",
    "Dumbbell Bench Press":      "push",
    "Incline Bench Press":       "push",
    "Incline Dumbbell Press":    "push",
    "Close Grip Bench Press":    "push",
    "Strict Military Press":     "push",
    "Lateral Raise":             "push",
    "Cable Lateral Raise":       "push",
    "Weighted Dip":              "push",
    "Skullcrusher":              "push",
    "Dumbbell Skullcrusher":     "push",
    "Cable Triceps Extension":   "push",
    "Dumbbell Triceps Extension":"push",
    "Triceps Pushdown":          "push",
    # Pull
    "Pull Up":                   "pull",
    "Chin Up":                   "pull",
    "Pull Up (Assisted)":        "pull",
    "Lat Pulldown":              "pull",
    "Underhand Lat Pulldown":    "pull",
    "Wide Grip Lat Pulldown":    "pull",
    "Barbell Row":               "pull",
    "Underhand Barbell Row":     "pull",
    "Cable Row":                 "pull",
    "Face Pull":                 "pull",
    "Barbell Curl":              "arms",
    "Dumbbell Curl":             "arms",
    "Hammer Curl":               "arms",
    "Preacher Curl":             "arms",
    # Legs
    "Front Squat":               "legs",
    "Barbell Back Squat":        "legs",
    "Deadlift":                  "legs",
    "Romanian Deadlift":         "legs",
    "Leg Press":                 "legs",
    "Leg Extension":             "legs",
    "Leg Curl":                  "legs",
    "Hip Thrust":                "legs",
    "Bulgarian Split Squat":     "legs",
    "Calf Raise":                "legs",
    "Seated Calf Raise":         "legs",
}

BODYWEIGHT_EXERCISES = {
    "Pull Up", "Chin Up", "Pull Up (Assisted)", "Weighted Dip",
    "Push Up", "Bench Dip", "Chest Dip", "Triceps Dip",
    "Sternum Pull up (Gironda)", "Wide Pull Up",
}

def parse_session_type(workout_name: str) -> str:
    name = workout_name.lower()
    if any(x in name for x in ["push", "chest"]):
        return "push"
    if any(x in name for x in ["pull", "back", "bicep", "arm"]):
        return "pull"
    if "leg" in name:
        return "legs"
    if "arm" in name:
        return "arms"
    return "unknown"

def epley_e1rm(weight: float, reps: int) -> float | None:
    """Epley formula. Returns None for bodyweight-only sets (weight=0)."""
    if reps == 1:
        return float(weight)
    if weight <= 0:
        return None
    return weight * (1 + reps / 30)

def ingest(csv_path: Path, db_path: Path) -> None:
    df = pd.read_csv(csv_path, on_bad_lines='skip')
    df['Date'] = pd.to_datetime(df['Date'])
    df['date'] = df['Date'].dt.date
    df['Weight'] = pd.to_numeric(df['Weight'], errors='coerce').fillna(0)
    df['Reps'] = pd.to_numeric(df['Reps'], errors='coerce').fillna(0).astype(int)

    # Some Strong exports omit optional columns (e.g. Notes)
    for col in ("Notes", "RPE"):
        if col not in df.columns:
            df[col] = None

    # Drop obvious junk rows
    junk = {"777", "Dbl", ""}
    df['Exercise Name'] = df['Exercise Name'].astype(str).str.strip()
    df = df[~df['Exercise Name'].isin(junk)]
    df = df[df['Reps'] > 0]

    con = sqlite3.connect(db_path)

    # This import DROPs sets/bodyweight — a one-time backfill for a fresh user.
    # Refuse to destroy live Hevy history.
    try:
        hevy_rows = con.execute(
            "SELECT COUNT(*) FROM sets WHERE source = 'hevy'").fetchone()[0]
    except sqlite3.OperationalError:
        hevy_rows = 0
    if hevy_rows:
        print(f"Refusing to run: sets table already has {hevy_rows} Hevy-synced rows "
              f"and this import would drop them. Strong backfill must happen before "
              f"the first Hevy sync.", file=sys.stderr)
        sys.exit(1)

    # Session bucket per Hevy library title (seeded by migrate before import).
    # Covers exercises that map straight to a Hevy title without an entry in
    # the hand-kept MUSCLE_GROUPS dict.
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    lib_session = {
        _norm(title): stype
        for title, stype in con.execute(
            "SELECT title, session_type FROM hevy_exercise_library")
        if stype
    }

    # Normalise names
    df['exercise'] = df['Exercise Name'].map(EXERCISE_ALIASES).fillna(df['Exercise Name'])
    df['muscle_group'] = df['exercise'].map(MUSCLE_GROUPS)
    df['muscle_group'] = df['muscle_group'].fillna(
        df['exercise'].map(lambda e: lib_session.get(_norm(e))))
    df['muscle_group'] = df['muscle_group'].fillna("other")

    # Main lifts come from the active user's profile: canonical names + Hevy titles
    main_names = set(config.MAIN_LIFTS) | {
        v.get("hevy_name", "") for v in config.MAIN_LIFTS.values()}
    df['is_main_lift'] = df['exercise'].isin(main_names)
    df['is_bodyweight'] = df['exercise'].isin(BODYWEIGHT_EXERCISES)

    # Session type: try workout name first, fall back to majority muscle group
    df['session_type'] = df['Workout Name'].apply(parse_session_type)
    unknown_mask = df['session_type'] == 'unknown'
    if unknown_mask.any():
        # For each session with an unknown type, vote by majority muscle group
        def infer_from_exercises(group):
            counts = group[group['muscle_group'] != 'other']['muscle_group'].value_counts()
            return counts.index[0] if not counts.empty else 'unknown'
        inferred = (
            df[unknown_mask]
            .groupby(['date', 'Workout Name'])
            .apply(infer_from_exercises)
            .rename('inferred_type')
        )
        df = df.join(inferred, on=['date', 'Workout Name'])
        df.loc[unknown_mask, 'session_type'] = df.loc[unknown_mask, 'inferred_type']
        df.drop(columns='inferred_type', inplace=True)

    # Unique session ID per date+workout
    session_map = {}
    for i, (date, wname) in enumerate(df[['date','Workout Name']].drop_duplicates().values):
        session_map[(str(date), wname)] = f"strong_{i:04d}"
    df['session_id'] = df.apply(lambda r: session_map[(str(r['date']), r['Workout Name'])], axis=1)

    # e1RM
    df['e1rm'] = df.apply(lambda r: epley_e1rm(r['Weight'], r['Reps']), axis=1)

    # Build sets table
    sets = df[['session_id','date','Workout Name','session_type','muscle_group',
               'exercise','is_main_lift','is_bodyweight',
               'Set Order','Weight','Reps','e1rm','Notes','RPE']].copy()
    sets.columns = ['session_id','date','workout_name','session_type','muscle_group',
                    'exercise','is_main_lift','is_bodyweight',
                    'set_number','weight_kg','reps','e1rm','notes','rpe']
    sets['is_warmup'] = 0   # Strong export doesn't distinguish warmups
    sets['source'] = 'strong'

    con.execute("DROP TABLE IF EXISTS sets")
    con.execute("""
        CREATE TABLE sets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source        TEXT,
            session_id    TEXT,
            date          DATE,
            workout_name  TEXT,
            session_type  TEXT,
            muscle_group  TEXT,
            exercise      TEXT,
            is_main_lift  INTEGER,
            is_bodyweight INTEGER,
            is_warmup     INTEGER DEFAULT 0,
            set_number    INTEGER,
            weight_kg     REAL,
            reps          INTEGER,
            e1rm          REAL,
            notes         TEXT,
            rpe           REAL
        )
    """)

    sets.to_sql('sets', con, if_exists='append', index=False)
    con.commit()
    con.close()

    print(f"Ingested {len(sets)} sets across {sets['session_id'].nunique()} sessions")
    print(f"Date range: {sets['date'].min()} → {sets['date'].max()}")
    print(f"Exercises mapped: {sets[sets['muscle_group'] != 'other']['exercise'].nunique()} / {sets['exercise'].nunique()}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill sets from a Strong CSV export.")
    parser.add_argument("--user", required=True, help="User name (must exist under users/)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"Path to Strong CSV (default: {DEFAULT_CSV})")
    args = parser.parse_args()

    config.activate(args.user)
    if not args.csv.is_file():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)
    ingest(args.csv, Path(config.DB_PATH))
