import pandas as pd
import sqlite3
import re
from pathlib import Path

DB_PATH = Path("/home/claude/gym.db")
CSV_PATH = Path("/mnt/user-data/uploads/strong.csv")

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

MAIN_LIFTS = {
    "Barbell Bench Press",
    "Strict Military Press",
    "Pull Up",
    "Weighted Dip",
    "Front Squat",
}

BODYWEIGHT_EXERCISES = {"Pull Up", "Chin Up", "Pull Up (Assisted)", "Weighted Dip"}

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

    # Drop obvious junk rows
    junk = {"777", "Dbl", ""}
    df = df[~df['Exercise Name'].isin(junk)]
    df = df[df['Reps'] > 0]

    # Normalise names
    df['exercise'] = df['Exercise Name'].map(EXERCISE_ALIASES).fillna(df['Exercise Name'])
    df['muscle_group'] = df['exercise'].map(MUSCLE_GROUPS).fillna("other")
    df['is_main_lift'] = df['exercise'].isin(MAIN_LIFTS)
    df['is_bodyweight'] = df['exercise'].isin(BODYWEIGHT_EXERCISES)

    # Session type from workout name
    df['session_type'] = df['Workout Name'].apply(parse_session_type)

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
    sets['source'] = 'strong'

    con = sqlite3.connect(db_path)

    con.execute("DROP TABLE IF EXISTS sets")
    con.execute("""
        CREATE TABLE sets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source       TEXT,
            session_id   TEXT,
            date         DATE,
            workout_name TEXT,
            session_type TEXT,
            muscle_group TEXT,
            exercise     TEXT,
            is_main_lift INTEGER,
            is_bodyweight INTEGER,
            set_number   INTEGER,
            weight_kg    REAL,
            reps         INTEGER,
            e1rm         REAL,
            notes        TEXT,
            rpe          REAL
        )
    """)

    con.execute("DROP TABLE IF EXISTS bodyweight")
    con.execute("""
        CREATE TABLE bodyweight (
            date      DATE PRIMARY KEY,
            weight_kg REAL,
            source    TEXT
        )
    """)

    sets.to_sql('sets', con, if_exists='append', index=False)
    con.commit()
    con.close()

    print(f"Ingested {len(sets)} sets across {sets['session_id'].nunique()} sessions")
    print(f"Date range: {sets['date'].min()} → {sets['date'].max()}")
    print(f"Exercises mapped: {sets[sets['muscle_group'] != 'other']['exercise'].nunique()} / {sets['exercise'].nunique()}")

if __name__ == "__main__":
    ingest(CSV_PATH, DB_PATH)
