"""Unit tests for webui.py Flask routes."""
import json
import sqlite3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_detection(det_db, frigate_event="evt-test-999", display_name="Turdus migratorius"):
    conn = sqlite3.connect(det_db)
    conn.execute(
        """INSERT INTO detections
               (detection_time, detection_index, score, display_name, category_name, frigate_event, camera_name)
           VALUES ('2024-06-02 10:00:00.000000', 99, 0.9, ?, 'bird', ?, 'birdcam')""",
        (display_name, frigate_event),
    )
    conn.commit()
    conn.close()


def _delete_detection(det_db, frigate_event):
    conn = sqlite3.connect(det_db)
    conn.execute("DELETE FROM detections WHERE frigate_event = ?", (frigate_event,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def test_index_returns_200(flask_client):
    response = flask_client.get("/")
    assert response.status_code == 200


def test_index_contains_html(flask_client):
    response = flask_client.get("/")
    assert b"<!DOCTYPE html>" in response.data or b"<html" in response.data


# ---------------------------------------------------------------------------
# /daily_summary redirect
# ---------------------------------------------------------------------------

def test_daily_summary_redirect(flask_client):
    response = flask_client.get("/daily_summary")
    assert response.status_code == 302
    assert "/daily_summary/20" in response.headers["Location"]


def test_daily_summary_redirect_preserves_query(flask_client):
    response = flask_client.get("/daily_summary?live=true")
    assert response.status_code == 302
    assert "live=true" in response.headers["Location"]


def test_daily_summary_date_returns_200(flask_client):
    response = flask_client.get("/daily_summary/2024-06-01")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/detections/recent
# ---------------------------------------------------------------------------

def test_api_recent_detections_default(flask_client):
    response = flask_client.get("/api/detections/recent")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert isinstance(data, list)
    assert len(data) <= 5


def test_api_recent_detections_custom_limit(flask_client):
    response = flask_client.get("/api/detections/recent?limit=2")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) <= 2


def test_api_recent_detections_cap_at_20(flask_client):
    response = flask_client.get("/api/detections/recent?limit=100")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) <= 20


# ---------------------------------------------------------------------------
# DELETE /detections/<frigate_event>
# ---------------------------------------------------------------------------

def test_delete_detection_success(flask_client, tmp_dbs):
    _insert_detection(tmp_dbs["det_db"], frigate_event="evt-delete-me")
    response = flask_client.delete("/detections/evt-delete-me")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert data["frigate_event"] == "evt-delete-me"


def test_delete_detection_not_found(flask_client):
    response = flask_client.delete("/detections/evt-does-not-exist")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["success"] is False


def test_delete_detection_idempotent(flask_client, tmp_dbs):
    """Second delete of same event returns 404, not 500."""
    _insert_detection(tmp_dbs["det_db"], frigate_event="evt-idempotent")
    flask_client.delete("/detections/evt-idempotent")
    response = flask_client.delete("/detections/evt-idempotent")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /detections/by_hour
# ---------------------------------------------------------------------------

def test_detections_by_hour_returns_200(flask_client):
    response = flask_client.get("/detections/by_hour/2024-06-01/8")
    assert response.status_code == 200


def test_detections_by_hour_empty_hour_returns_200(flask_client):
    response = flask_client.get("/detections/by_hour/2024-06-01/23")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /frigate proxy routes — timeout / error handling
# ---------------------------------------------------------------------------

def test_frigate_thumbnail_timeout_returns_fallback(flask_client, monkeypatch):
    """A timeout on the Frigate request returns the 1x1 fallback, not 500."""
    import requests as req
    def fake_get(*a, **kw):
        raise req.exceptions.Timeout("timed out")
    monkeypatch.setattr("webui.requests.get", fake_get)
    response = flask_client.get("/frigate/evt-test/thumbnail.jpg")
    assert response.status_code == 200
    assert response.content_type == "image/png"


def test_frigate_snapshot_timeout_returns_fallback(flask_client, monkeypatch):
    import requests as req
    def fake_get(*a, **kw):
        raise req.exceptions.Timeout("timed out")
    monkeypatch.setattr("webui.requests.get", fake_get)
    response = flask_client.get("/frigate/evt-test/snapshot.jpg")
    assert response.status_code == 200
    assert response.content_type == "image/png"


def test_frigate_clip_timeout_returns_fallback(flask_client, monkeypatch):
    import requests as req
    def fake_get(*a, **kw):
        raise req.exceptions.Timeout("timed out")
    monkeypatch.setattr("webui.requests.get", fake_get)
    response = flask_client.get("/frigate/evt-test/clip.mp4")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /detections/by_scientific_name — end_date handling
# ---------------------------------------------------------------------------

def test_by_scientific_name_no_end_date_returns_200(flask_client):
    response = flask_client.get("/detections/by_scientific_name/Turdus%20migratorius/2024-06-01")
    assert response.status_code == 200


def test_by_scientific_name_with_end_date_returns_501(flask_client):
    """end_date path is not implemented — must return 501, not 200/500."""
    response = flask_client.get("/detections/by_scientific_name/Turdus%20migratorius/2024-06-01/2024-06-07")
    assert response.status_code == 501
