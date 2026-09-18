"""Csengetesi rend - Flask backend + pywebview asztali ablak.

A UI teljes egeszeben HTML/CSS/JS (static/), a Python csak a hattermotort
(utemezo, hanglejatszas, NTP, fajlkezeles) es egy helyi REST API-t ad.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, request, send_from_directory, session
from werkzeug.utils import secure_filename

import audio_player
import config_store
import schedule_store
import scheduler as scheduler_mod
import time_source
from logger_setup import LOG_PATH, setup_logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
STATIC_DIR = os.path.join(BASE_DIR, "static")

logger = logging.getLogger("csengetes")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = secrets.token_bytes(32)

AUTH_TOKEN = secrets.token_urlsafe(32)

DEFAULT_MAX_UPLOAD_MB = 20

config_data = config_store.load_config()
app.config["MAX_CONTENT_LENGTH"] = int(
    config_data.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB) * 1024 * 1024
)

player = audio_player.AudioPlayer()
time_src = time_source.TimeSource(ntp_server=config_data.get("ntp_server", "pool.ntp.org"))
scheduler = scheduler_mod.Scheduler(time_src, player)

_device_index = config_data.get("device_index")
if _device_index is not None:
    player.set_device(_device_index)
player.set_volume(config_data.get("volume_percent", 100.0))
time_src.set_mode(config_data.get("time_mode", "ntp"))

_active_name = config_data.get("active_schedule")
if _active_name and _active_name in schedule_store.list_schedules():
    try:
        scheduler.set_active_schedule(schedule_store.load(_active_name))
    except (schedule_store.ScheduleError, OSError):
        pass


def _save_config() -> None:
    config_store.save_config(config_data)


def _media_files() -> list[str]:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    return sorted(f for f in os.listdir(MEDIA_DIR) if f.lower().endswith((".mp3", ".wav", ".ogg")))


@app.before_request
def _require_auth_token():
    if request.path == "/" and request.args.get("token") == AUTH_TOKEN:
        session["authed"] = True
        return None
    if session.get("authed"):
        return None
    abort(403)


# ---------- statikus fajlok ----------


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# ---------- csengetesi rendek ----------


@app.get("/api/schedules")
def list_schedules():
    return jsonify(
        {
            "schedules": schedule_store.list_schedules(),
            "active": config_data.get("active_schedule"),
        }
    )


@app.get("/api/schedules/<name>")
def get_schedule(name: str):
    try:
        data = schedule_store.load(name)
    except (schedule_store.ScheduleError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(data)


@app.post("/api/schedules/<name>")
def save_schedule(name: str):
    data = request.get_json(force=True)
    try:
        schedule_store.save(name, data)
    except schedule_store.ScheduleError as exc:
        return jsonify({"error": str(exc)}), 400
    scheduler.set_active_schedule(data)
    return jsonify({"ok": True})


@app.post("/api/schedules")
def new_schedule():
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nev kotelezo."}), 400
    if not name.endswith(".json"):
        name += ".json"
    data = {"name": name.replace(".json", ""), "events": []}
    try:
        schedule_store.save(name, data)
    except schedule_store.ScheduleError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "filename": name})


@app.post("/api/schedules/<name>/activate")
def activate_schedule(name: str):
    try:
        data = schedule_store.load(name)
    except (schedule_store.ScheduleError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    scheduler.set_active_schedule(data)
    config_data["active_schedule"] = name
    _save_config()
    logger.info("Aktiv csengetesi rend: %s", name)
    return jsonify({"ok": True})


# ---------- media ----------


@app.get("/api/media")
def list_media():
    return jsonify({"files": _media_files(), "default": config_data.get("default_media_file", "")})


@app.post("/api/media/upload")
def upload_media():
    if "file" not in request.files:
        return jsonify({"error": "Nincs csatolt fajl."}), 400
    file = request.files["file"]
    filename = secure_filename(file.filename or "")
    if not filename.lower().endswith((".mp3", ".wav", ".ogg")):
        return jsonify({"error": "Csak MP3/WAV/OGG fajl tolthato fel."}), 400
    os.makedirs(MEDIA_DIR, exist_ok=True)
    dest = os.path.join(MEDIA_DIR, filename)
    file.save(dest)
    logger.info("Media feltoltve: %s", filename)
    return jsonify({"ok": True, "filename": filename})


@app.get("/api/media/upload-limit")
def get_upload_limit():
    return jsonify({"max_upload_mb": config_data.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB)})


@app.post("/api/media/upload-limit")
def set_upload_limit():
    body = request.get_json(force=True)
    try:
        mb = float(body.get("max_upload_mb"))
    except (TypeError, ValueError):
        return jsonify({"error": "Ervenytelen ertek."}), 400
    if mb <= 0 or mb > 1024:
        return jsonify({"error": "A limit 0 es 1024 MB kozott lehet."}), 400
    config_data["max_upload_mb"] = mb
    _save_config()
    app.config["MAX_CONTENT_LENGTH"] = int(mb * 1024 * 1024)
    logger.info("Feltoltesi meretlimit beallitva: %.1f MB", mb)
    return jsonify({"ok": True, "max_upload_mb": mb})


@app.errorhandler(413)
def handle_too_large(_exc):
    limit = config_data.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB)
    logger.warning("Feltoltes elutasitva: tul nagy fajl (limit: %s MB)", limit)
    return jsonify({"error": f"A fajl tul nagy. A megengedett maximum: {limit} MB."}), 413


@app.post("/api/media/default")
def set_default_media():
    body = request.get_json(force=True)
    filename = body.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "Fajlnev kotelezo."}), 400
    config_data["default_media_file"] = filename
    _save_config()
    logger.info("Alapertelmezett csengohang beallitva: %s", filename)
    return jsonify({"ok": True})


@app.post("/api/media/play")
def play_media():
    body = request.get_json(force=True)
    filename = body.get("filename", "").strip()
    force = bool(body.get("force", False))
    if not filename:
        return jsonify({"error": "Fajlnev kotelezo."}), 400
    safe_name = secure_filename(filename)
    media_dir = os.path.realpath(MEDIA_DIR)
    filepath = os.path.realpath(os.path.join(media_dir, safe_name))
    is_contained = filepath == media_dir or filepath.startswith(media_dir + os.sep)
    if not safe_name or not is_contained or not os.path.isfile(filepath):
        return jsonify({"error": f"A fajl nem talalhato: {filename}"}), 404
    logger.info("Lejatszas inditva (%s), force=%s", filename, force)
    try:
        player.play(filepath, blocking=True, force=force)
    except audio_player.AudioPlayerError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    return jsonify({"ok": True})


# ---------- ido ----------


@app.get("/api/time")
def get_time():
    return jsonify(
        {
            "mode": time_src.get_mode(),
            "ntp_server": time_src.get_ntp_server(),
            "now": time_src.get_now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.post("/api/time/mode")
def set_time_mode():
    body = request.get_json(force=True)
    mode = body.get("mode")
    try:
        time_src.set_mode(mode)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    config_data["time_mode"] = mode
    _save_config()
    return jsonify({"ok": True})


@app.post("/api/time/sync")
def sync_time():
    body = request.get_json(force=True)
    server_host = body.get("ntp_server", "").strip()
    if server_host:
        time_src.set_ntp_server(server_host)
        config_data["ntp_server"] = server_host
        _save_config()
    try:
        now = time_src.sync_now()
    except Exception as exc:
        logger.warning("NTP szinkron sikertelen, rendszerora hasznalata: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 200
    return jsonify({"ok": True, "now": now.strftime("%Y-%m-%d %H:%M:%S")})


@app.post("/api/time/manual")
def set_manual_time():
    body = request.get_json(force=True)
    text = body.get("value", "").strip()
    try:
        value = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"error": "Ervenytelen formatum. Varhato: YYYY-MM-DD HH:MM:SS"}), 400
    time_src.set_manual(value)
    config_data["time_mode"] = "manual"
    _save_config()
    return jsonify({"ok": True})


# ---------- kimenet ----------


@app.get("/api/output/devices")
def list_devices():
    devices = player.list_output_devices()
    return jsonify(
        {
            "devices": [{"index": idx, "name": name} for idx, name in devices],
            "current": player.get_device(),
            "volume": player.get_volume(),
            "max_volume": audio_player.MAX_VOLUME_PERCENT,
        }
    )


@app.post("/api/output/device")
def set_device():
    body = request.get_json(force=True)
    index = body.get("index")
    player.set_device(index)
    config_data["device_index"] = index
    _save_config()
    return jsonify({"ok": True})


@app.post("/api/output/volume")
def set_volume():
    body = request.get_json(force=True)
    percent = float(body.get("percent", 100.0))
    player.set_volume(percent)
    config_data["volume_percent"] = player.get_volume()
    _save_config()
    return jsonify({"ok": True, "percent": player.get_volume()})


# ---------- naplo ----------


@app.get("/api/logs")
def get_logs():
    limit = int(request.args.get("limit", 200))
    if not os.path.isfile(LOG_PATH):
        return jsonify({"lines": []})
    with open(LOG_PATH, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    return jsonify({"lines": [ln.rstrip("\n") for ln in lines[-limit:]]})


def _run_flask():
    app.run(host="127.0.0.1", port=8765, threaded=True, use_reloader=False)


def main():
    setup_logging()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    scheduler.start()

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    try:
        import webview

        webview.create_window(
            "Csengetesi rend",
            f"http://127.0.0.1:8765/?token={AUTH_TOKEN}",
            width=1040,
            height=760,
            min_size=(820, 600),
        )
        icon_path = os.path.join(STATIC_DIR, "favicon.ico")
        webview.start(icon=icon_path if os.path.isfile(icon_path) else None)
    except ImportError:
        logger.warning("pywebview nincs telepitve, csak a Flask szerver fut a http://127.0.0.1:8765 cimen.")
        flask_thread.join()

    scheduler.stop()


if __name__ == "__main__":
    main()
