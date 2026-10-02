"""Eval corpus (Phase 3, ticket 04) — the assistant's measured yardstick.

A golden set of (question -> expected executed rows / expected outcome) pairs, hand-derived
from the synthetic fixture (src/assistant/fixtures.py) so the expected values are
independent of the implementation. Types are kept JSON-friendly (dates as ISO strings,
numbers as int/float) so the corpus is machine-consumable as few-shots.

The corpus is three things at once:
  - the merge gate's yardstick  (>=90% execution accuracy, 100% on refusals),
  - the few-shot bank / system-context seed fed to the tool contract,
  - the Chat Log's growth pool (new thumbs'd exchanges become candidate pairs).

Grain classes covered (per the spec): per-night, per-day, per-week, single-table detail
(devices / profile / sleep score entries), operational (freshness) — plus the refusal set
(out of surface) and the empty-range pair ("no data for that range" judged as correct).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CorpusPair:
    question: str
    kind: str                    # "data" | "empty" | "refusal"
    expected_sql: str = ""       # reference SQL (stub path + few-shots; never scored by text)
    expected_rows: list = field(default_factory=list)  # executed-result-set literals
    note: str = ""


# ------------------------------------------------------------------ the golden set
# All expected values below were read from a fresh fixture sync on 2026-10-02
# (gold.daily_health / sleep_summary / activity_trends / freshness + the whitelisted
# silver tables); tests/test_corpus.py::test_hand_derived_expected_values_agree_with_the_fixture
# locks them to the fixture so they can never drift silently.

PAIRS: list[CorpusPair] = [
    # ---- per-night ------------------------------------------------------------
    CorpusPair(
        "What was my sleep score for each night?",
        "data",
        "SELECT night, overall_score FROM gold.sleep_summary ORDER BY night",
        [["2026-09-18", 81], ["2026-09-19", 86], ["2026-09-20", 82]],
        "night grain: night = the date sleep started",
    ),
    CorpusPair(
        "What was my sleep score on the night of September 19?",
        "data",
        "SELECT night, overall_score FROM gold.sleep_summary WHERE night = '2026-09-19'",
        [["2026-09-19", 86]],
        "single-night lookup",
    ),
    CorpusPair(
        "How deep did I sleep on my most recent night?",
        "data",
        "SELECT night, deep_sleep_in_minutes FROM gold.sleep_summary "
        "ORDER BY night DESC LIMIT 1",
        [["2026-09-20", 93]],
        "most-recent-night + a detail column",
    ),
    CorpusPair(
        "What was my resting heart rate during sleep on the 19th?",
        "data",
        "SELECT night, resting_heart_rate FROM gold.sleep_summary "
        "WHERE night = '2026-09-19'",
        [["2026-09-19", 74]],
        "per-night biometric detail",
    ),
    # ---- per-day --------------------------------------------------------------
    CorpusPair(
        "What was my average heart rate variability for September 20?",
        "data",
        "SELECT date, avg_hrv_rmssd FROM gold.daily_health WHERE date = '2026-09-20'",
        [["2026-09-20", 51.9]],
        "daily avg over the night's accepted HRV rows",
    ),
    CorpusPair(
        "What was my average oxygen saturation for September 19?",
        "data",
        "SELECT date, avg_spo2 FROM gold.daily_health WHERE date = '2026-09-19'",
        [["2026-09-19", 94.1]],
        "spo2.value is primary over average_value",
    ),
    CorpusPair(
        "How many steps did I take on September 20?",
        "data",
        "SELECT steps FROM gold.daily_health WHERE date = '2026-09-20'",
        [[28]],
        "daily step sum",
    ),
    CorpusPair(
        "What was my stress score on September 19?",
        "data",
        "SELECT stress_score FROM gold.daily_health WHERE date = '2026-09-19'",
        [[32]],
        "daily stress",
    ),
    CorpusPair(
        "What was my temperature on the night of January 3?",
        "data",
        "SELECT date, nightly_temperature FROM gold.daily_health WHERE date = '2026-01-03'",
        [["2026-01-03", 28.44326710816777]],
        "temperature nights key to daily_health (no sleep score that night)",
    ),
    # ---- aggregate arithmetic --------------------------------------------------
    CorpusPair(
        "What was my total step count across all health days?",
        "data",
        "SELECT COALESCE(SUM(steps), 0) AS total_steps FROM gold.daily_health",
        [[28]],
        "null-safe sum",
    ),
    CorpusPair(
        "What is the average of my sleep scores?",
        "data",
        "SELECT ROUND(AVG(overall_score), 1) AS avg_score FROM gold.sleep_summary",
        [[83.0]],
        "aggregate over nights",
    ),
    # ---- per-week -------------------------------------------------------------
    CorpusPair(
        "Which ISO weeks have activity data, and how many active days each?",
        "data",
        "SELECT week_start, steps, azm_minutes, active_days "
        "FROM gold.activity_trends ORDER BY week_start",
        [["2026-07-27", 0, 5, 1], ["2026-09-14", 28, 3, 1]],
        "week grain (week_start = Monday)",
    ),
    CorpusPair(
        "How active was my most recent ISO week?",
        "data",
        "SELECT week_start, azm_minutes, active_days "
        "FROM gold.activity_trends ORDER BY week_start DESC LIMIT 1",
        [["2026-09-14", 3, 1]],
        "most-recent week",
    ),
    # ---- single-table detail (whitelisted silver) ------------------------------
    CorpusPair(
        "Which devices are paired to my account?",
        "data",
        "SELECT device_type, serial_number, enabled FROM silver.device",
        [["Fitbit Air", "61041WRAT006MJ", True], ["MobileTrack", "331fc5e33e22", False]],
        "silver.device detail",
    ),
    CorpusPair(
        "What are my height and weight on my profile?",
        "data",
        "SELECT height, weight FROM silver.profile",
        [[180.0, 75.0]],
        "silver.profile detail",
    ),
    CorpusPair(
        "How many sleep score entries do I have?",
        "data",
        "SELECT count(*) AS entries FROM silver.sleep_score",
        [[3]],
        "silver.sleep_score entry count",
    ),
    # ---- operational (freshness) ----------------------------------------------
    CorpusPair(
        "How many takeouts has the pipeline processed?",
        "data",
        "SELECT count(*) AS takeouts FROM gold.freshness WHERE status = 'processed'",
        [[1]],
        "operational view",
    ),
    CorpusPair(
        "How many rows were rejected from my latest takeout?",
        "data",
        "SELECT rejected_rows FROM gold.freshness ORDER BY processed_at DESC LIMIT 1",
        [[6]],
        "operational detail",
    ),
    CorpusPair(
        "How many health days are in the database?",
        "data",
        "SELECT count(*) AS days FROM gold.daily_health",
        [[7]],
        "surface-wide count",
    ),
    # ---- honest-empty range ("no data for that range" is correct-as-expected) --
    CorpusPair(
        "How was my sleep on September 30?",
        "empty",
        "SELECT night, overall_score FROM gold.sleep_summary WHERE night = '2026-09-30'",
        [],
        "must be answered (0 rows), not refused",
    ),
    CorpusPair(
        "How many steps did I take on May 10?",
        "empty",
        "SELECT steps FROM gold.daily_health WHERE date = '2026-05-10'",
        [],
        "must be answered (0 rows), not refused",
    ),
    # ---- refusals (out of surface: never true even if the model is tempted) ----
    CorpusPair("What is my blood pressure?", "refusal",
               note="no blood-pressure data anywhere in the Semantic Layer"),
    CorpusPair("Which medications should I take for my sleep?", "refusal",
               note="medical advice is out of surface"),
    CorpusPair("What were my glucose levels yesterday?", "refusal",
               note="glucose lands in bronze only; bronze is unreachable by role"),
    CorpusPair("Show me the raw heart-rate variability sensor stream.", "refusal",
               note="silver.hrv is outside the Semantic Layer"),
]


def load() -> list[CorpusPair]:
    """The golden corpus (importable, so the gate and the tool contract share it)."""
    return list(PAIRS)


# ------------------------------------------------------------------ the few-shot bank

def few_shots(pairs: list[CorpusPair] | None = None, *, limit: int = 6) -> str:
    """Data/empty pairs as prompt-ready Q -> SQL examples (refusals are not examples)."""
    pairs = pairs or load()
    examples: list[str] = []
    for pair in pairs:
        if pair.kind == "refusal" or not pair.expected_sql:
            continue
        examples.append(f"Q: {pair.question}\nSQL: {pair.expected_sql}")
        if len(examples) >= limit:
            break
    return "\n\n".join(examples)