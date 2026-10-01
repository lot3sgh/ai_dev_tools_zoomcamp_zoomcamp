"""Shared fixtures: synthetic takeout zip + throwaway Postgres wiring.

Tests run the pipeline through its single seam (the CLI) and assert on the
resulting database. Each test starts with the bronze/silver/pipeline schemas
dropped, so runs are isolated.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import psycopg
import pytest

from pipeline import config

# Same env resolution as the CLI itself (cli.main calls config.load_env):
# a .env at the repo root supplies POSTGRES_DB/USER/PASSWORD, PGHOST, PGPORT.
config.load_env()

TEST_DATABASE = "health_pipeline_test"


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Point the suite at a dedicated database so real data is never touched.

    Creates health_pipeline_test once per session (superuser pipeline can
    create databases), then every test DROPs/recreates schemas inside it.
    """
    os.environ["PGDATABASE"] = TEST_DATABASE
    maintenance = psycopg.conninfo.make_conninfo(**{
        **{k: config.db_env()[k] for k in ("host", "port", "user", "password")},
        "dbname": "postgres",
    })
    with psycopg.connect(maintenance, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,))
            exists = cur.fetchone() is not None
        if not exists:
            conn.execute(f"CREATE DATABASE {TEST_DATABASE}")
    yield

# ---------------------------------------------------------------- environment

SLEEP_CSV = """sleep_log_entry_id,timestamp,overall_score,composition_score,revitalization_score,duration_score,deep_sleep_in_minutes,resting_heart_rate,restlessness
53621952045,2026-09-20T08:14:30Z,82,,83,,93,73,0.14445574771108852
53612104642,2026-09-19T10:01:00Z,86,,87,,97,74,0.17135761589403972
53603187472,2026-09-18T07:43:30Z,81,,82,,83,75,0.14442013129102846
53603187471,2026-09-18T07:00:00Z,81,,82,,83,75,not-a-number
"""

AZM_SEPT = """date_time,heart_zone_id,total_minutes
2026-09-15T08:40,FAT_BURN,1
2026-09-15T09:36,FAT_BURN,1
2026-09-15T09:37,PEAK,1
"""

AZM_AUG = """date_time,heart_zone_id,total_minutes
2026-08-02T07:10,FAT_BURN,3
2026-08-02T08:00,CARDIO,2
"""

DEVICES_CSV = """wire_id,device_type,serial_number,enabled,fw_version
223ee3c51f33,MobileTrack,331fc5e33e22,false,APP51.0 BSL51.0
0B2680AA5243,Fitbit Air,61041WRAT006MJ,true,APP67.20001.253.2
"""

PROFILE_CSV = """id,full_name,first_name,last_name,display_name_setting,username,email_address,date_of_birth,child,country,state,city,timezone,locale,member_since,start_of_week,sleep_tracking,time_display_format,gender,height,weight,weight_unit,distance_unit,height_unit
C698DD,Tony L,Tony,L,name,renshou753@gmail.com,renshou753@gmail.com,1990-05-20,false,null,null,null,Asia/Shanghai,en_US,2024-07-23,SUNDAY,Normal,12hour,MALE,180.0,75.0,METRIC,METRIC,METRIC
"""

GLUCOSE_CSV = """value
5.5
"""

HRV_CSV = """timestamp,rmssd,coverage,low_frequency,high_frequency,full_sleep_breathing_rate
2026-09-20T03:14:30Z,51.7,0.901,678.7,817.2,12.1
2026-09-20T03:14:30Z,52.1,0.902,681.0,820.0,12.2
2026-09-18T02:45:00Z,BADROW,0.9,678.7,817.2,12.0
"""

STRESS_CSV = """date,stress_score,sleep_points,responsiveness_points,exertion_points,status
2026-09-20,67,85,60,70,restful
2026-09-19,32,90,80,55,restful
2026-09-18,not-a-number,85,60,70,restful
"""

SPO2_CSV = """timestamp,average_value,lower_bound,upper_bound,value
2026-09-20T08:14:30Z,,,,95.2
2026-09-19T10:01:00Z,94.1,93.0,95.5,
2026-09-18T10:01:00Z,99.0,,,not-a-number
"""

TEMP_CSV = """type,sleep_start,sleep_end,temperature_samples,nightly_temperature,baseline_relative_sample_sum,baseline_relative_nightly_standard_deviation,baseline_relative_sample_standard_deviation,recorded_time,temperature,sensor_type
IDT,2026-01-03T01:24,2026-01-03T09:03:30,453,28.44326710816777,-456.5484935897439,1.3246847872435916,0.5,,,
IDT,2026-01-04T00:21,2026-01-04T07:30:00,547,28.3981718464351,-200.5,1.1,0.4,,,
IDT,2026-01-05T00:10,2026-01-05T08:00:00,500,not-a-number,,-1.2,0.3,,,
,,,,,,,,2026-01-06T03:00:00,28.5,skin
"""

ACTIVITY_CSV = """timestamp,steps,beats_per_minute,distance,data_source
2026-09-20T00:05:00Z,14,72,5.2,Google Fitbit Air
2026-09-20T00:05:00Z,14,72,5.2,Google Fitbit Air
2026-09-19T23:59:00Z,0,65,0.1,Charge 4
2026-09-19T23:58:00Z,BADROW,65,0.1,Charge 4
"""


def build_fixture_zip(target: Path, name: str = "takeout-test.zip") -> Path:
    """Create a small synthetic Google Health takeout archive."""
    return make_zip(target / name, {
        "Takeout/Google Health/Sleep Score/sleep_score.csv": SLEEP_CSV,
        "Takeout/Google Health/Active Zone Minutes (AZM)/Active Zone Minutes - 2026-09-01.csv": AZM_SEPT,
        "Takeout/Google Health/Active Zone Minutes (AZM)/Active Zone Minutes - 2026-08-01.csv": AZM_AUG,
        "Takeout/Google Health/Paired Devices/Devices.csv": DEVICES_CSV,
        "Takeout/Google Health/Your Profile/Profile.csv": PROFILE_CSV,
        "Takeout/Google Health/Biometrics/Glucose 200706.csv": GLUCOSE_CSV,
        "Takeout/Google Health/Heart Rate Variability/hrv.csv": HRV_CSV,
        "Takeout/Google Health/Stress Score/stress.csv": STRESS_CSV,
        "Takeout/Google Health/Oxygen Saturation (SpO2)/spo2.csv": SPO2_CSV,
        "Takeout/Google Health/Temperature/temperature.csv": TEMP_CSV,
        "Takeout/Google Health/Physical Activity_GoogleData/activity.csv": ACTIVITY_CSV,
    })


def make_zip(path: Path, entries: dict[str, str]) -> Path:
    """Create a zip at `path` from entry-name -> content pairs."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry_name, content in entries.items():
            zf.writestr(entry_name, content)
    return path


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """A directory containing a synthetic takeout zip."""
    build_fixture_zip(tmp_path)
    return tmp_path


@pytest.fixture()
def reset_db() -> None:
    """Drop the pipeline schemas so each test observes a fresh run."""
    from pipeline import db

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS bronze CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS silver CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS gold CASCADE")
    yield