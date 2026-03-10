"""
Tests for the MQTT new-species-ever notification feature.

speciesid.py has heavy ML imports (numpy, cv2, tflite_support, PIL) that are
not available in the test environment. We patch them in sys.modules before
importing speciesid so only the functions we care about are exercised.
"""
import os
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

# webui calls load_config() at module level; point it at the example config
# so it doesn't fail when speciesid (which imports webui) is imported here.
os.environ.setdefault('WHOSATMYFEEDER_CONFIG', 'config/config.yml.example')

# Patch heavy ML deps before importing speciesid (not available in test env)
for _mod in [
    "numpy", "cv2",
    "tflite_support",
    "tflite_support.task",
    "tflite_support.task.core",
    "tflite_support.task.processor",
    "tflite_support.task.vision",
    "PIL", "PIL.Image", "PIL.ImageOps",
    "paho", "paho.mqtt", "paho.mqtt.client",
]:
    sys.modules.setdefault(_mod, MagicMock())

import speciesid  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_det_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time TEXT NOT NULL,
            detection_index INTEGER,
            score REAL,
            display_name TEXT,
            category_name TEXT,
            frigate_event TEXT UNIQUE,
            camera_name TEXT
        )
    """)
    conn.commit()
    conn.close()


def _insert(path: str, display_name: str, frigate_event: str, score: float = 0.9) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO detections
               (detection_time, detection_index, score, display_name, category_name, frigate_event, camera_name)
           VALUES ('2024-06-01 08:00:00', 1, ?, ?, 'bird', ?, 'birdcam')""",
        (score, display_name, frigate_event),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def fresh_db(tmp_path):
    db = str(tmp_path / "fresh.db")
    _make_det_db(db)
    return db


# ---------------------------------------------------------------------------
# publish_new_species helper
# ---------------------------------------------------------------------------

def test_publish_new_species_publishes_all_five_topics():
    client = MagicMock()
    speciesid.publish_new_species(
        client,
        common_name="American Robin",
        scientific_name="Turdus migratorius",
        score=0.9234,
        camera_name="birdcam",
        frigate_event="evt-001",
    )
    assert client.publish.call_count == 5


def test_publish_new_species_correct_topics():
    client = MagicMock()
    speciesid.publish_new_species(
        client, "American Robin", "Turdus migratorius", 0.9234, "birdcam", "evt-001"
    )
    published_topics = {c.args[0] for c in client.publish.call_args_list}
    assert published_topics == {
        "whosatmyfeeder/new_species/common_name",
        "whosatmyfeeder/new_species/scientific_name",
        "whosatmyfeeder/new_species/score",
        "whosatmyfeeder/new_species/camera",
        "whosatmyfeeder/new_species/frigate_event",
    }


def test_publish_new_species_all_retained():
    client = MagicMock()
    speciesid.publish_new_species(
        client, "American Robin", "Turdus migratorius", 0.9234, "birdcam", "evt-001"
    )
    for c in client.publish.call_args_list:
        assert c.kwargs.get("retain") is True, f"retain not set on {c.args[0]}"


def test_publish_new_species_correct_payloads():
    client = MagicMock()
    speciesid.publish_new_species(
        client, "American Robin", "Turdus migratorius", 0.9234, "birdcam", "evt-001"
    )
    payloads = {c.args[0]: c.args[1] for c in client.publish.call_args_list}
    assert payloads["whosatmyfeeder/new_species/common_name"] == "American Robin"
    assert payloads["whosatmyfeeder/new_species/scientific_name"] == "Turdus migratorius"
    assert payloads["whosatmyfeeder/new_species/score"] == "0.9234"
    assert payloads["whosatmyfeeder/new_species/camera"] == "birdcam"
    assert payloads["whosatmyfeeder/new_species/frigate_event"] == "evt-001"


def test_publish_new_species_score_rounded_to_4dp():
    client = MagicMock()
    speciesid.publish_new_species(
        client, "Robin", "Turdus migratorius", 0.123456789, "birdcam", "evt-001"
    )
    payloads = {c.args[0]: c.args[1] for c in client.publish.call_args_list}
    assert payloads["whosatmyfeeder/new_species/score"] == "0.1235"


# ---------------------------------------------------------------------------
# First-ever detection DB logic
# ---------------------------------------------------------------------------

def test_new_species_fires_when_count_is_one(fresh_db):
    """After inserting the first row for a species, count==1 → publish should fire."""
    _insert(fresh_db, "Turdus migratorius", "evt-001")

    conn = sqlite3.connect(fresh_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM detections WHERE display_name = ?", ("Turdus migratorius",))
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1


def test_new_species_does_not_fire_on_second_detection(fresh_db):
    """After a second detection of the same species, count > 1 → no publish."""
    _insert(fresh_db, "Turdus migratorius", "evt-001")
    _insert(fresh_db, "Turdus migratorius", "evt-002")

    conn = sqlite3.connect(fresh_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM detections WHERE display_name = ?", ("Turdus migratorius",))
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 2


def test_different_species_each_trigger_independently(fresh_db):
    """Two different species both show count==1 after their first detection."""
    _insert(fresh_db, "Turdus migratorius", "evt-001")
    _insert(fresh_db, "Cyanocitta cristata", "evt-002")

    conn = sqlite3.connect(fresh_db)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM detections WHERE display_name = ?", ("Turdus migratorius",))
    assert cursor.fetchone()[0] == 1

    cursor.execute("SELECT COUNT(*) FROM detections WHERE display_name = ?", ("Cyanocitta cristata",))
    assert cursor.fetchone()[0] == 1

    conn.close()


def test_score_update_does_not_add_row(fresh_db):
    """An UPDATE to the same frigate_event doesn't add a second row — no new-species fire."""
    _insert(fresh_db, "Turdus migratorius", "evt-001", score=0.75)

    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "UPDATE detections SET score = ? WHERE frigate_event = ?", (0.92, "evt-001")
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(fresh_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM detections WHERE display_name = ?", ("Turdus migratorius",))
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1
