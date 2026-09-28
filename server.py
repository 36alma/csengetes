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
from config_store import config
import schedule_store
import scheduler as scheduler_mod
import time_source
from logger_setup import LOG_PATH, setup_logging
from services.music import download as music_download
from services.music import MusicDownloadError, MusicService, validate_url

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
STATIC_DIR = os.path.join(BASE_DIR, "static")

logger = logging.getLogger("csengetes")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = secrets.token_bytes(32)

AUTH_TOKEN = secrets.token_urlsafe(32)

DEFAULT_MAX_UPLOAD_MB = 20

app.config["MAX_CONTENT_LENGTH"] = int(
    config.MAX_UPLOAD_MB * 1024 * 1024
    
)

player = audio_player.AudioPlayer()
time_src = time_source.TimeSource(ntp_server=config.NTP_SERVER)
scheduler = scheduler_mod.Scheduler(time_src, player)

# Az eszkozt nev alapjan keressuk meg (az index eszkozvaltozasnal elcsuszhat)
if config.DEVICE_NAME:
    _device_index = player.find_device_index(config.DEVICE_NAME)
    if _device_index is None:
        logger.warning("A mentett kimeneti eszkoz nem talalhato (%s), alapertelmezett hasznalata.", config.DEVICE_NAME)
    player.set_device(_device_index)
    config.DEVICE_INDEX = _device_index
player.set_volume(config.VOLUME_PERCENT)
time_src.set_mode(config.TIME_MODE)

_active_name = config.ACTIVE_SCHEDULE
if _active_name and _active_name in schedule_store.list_schedules():
    try:
        scheduler.set_active_schedule(schedule_store.load(_active_name))
    except (schedule_store.ScheduleError, OSError):
        pass


def _save_config() -> None:
    config.save_config()


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
            "active": config.ACTIVE_SCHEDULE,
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
    config.ACTIVE_SCHEDULE = name
    _save_config()
    logger.info("Aktiv csengetesi rend: %s", name)
    return jsonify({"ok": True})


# ---------- media ----------


@app.get("/api/media")
def list_media():
    return jsonify({"files": _media_files(), "default": config.DEFAULT_MEDIA_FILE})


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
    return jsonify({"max_upload_mb": config.MAX_UPLOAD_MB})


@app.post("/api/media/upload-limit")
def set_upload_limit():
    body = request.get_json(force=True)
    try:
        mb = float(body.get("max_upload_mb"))
    except (TypeError, ValueError):
        return jsonify({"error": "Ervenytelen ertek."}), 400
    if mb <= 0 or mb > 1024:
        return jsonify({"error": "A limit 0 es 1024 MB kozott lehet."}), 400
    config.MAX_UPLOAD_MB = mb
    _save_config()
    app.config["MAX_CONTENT_LENGTH"] = int(mb * 1024 * 1024)
    logger.info("Feltoltesi meretlimit beallitva: %.1f MB", mb)
    return jsonify({"ok": True, "max_upload_mb": mb})


@app.errorhandler(413)
def handle_too_large(_exc):
    limit = config.MAX_UPLOAD_MB
    logger.warning("Feltoltes elutasitva: tul nagy fajl (limit: %s MB)", limit)
    return jsonify({"error": f"A fajl tul nagy. A megengedett maximum: {limit} MB."}), 413


@app.post("/api/media/default")
def set_default_media():
    body = request.get_json(force=True)
    filename = body.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "Fajlnev kotelezo."}), 400
    config.DEFAULT_MEDIA_FILE = filename
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


# ---------- zene (YouTube) ----------

DOWNLOAD_DIR = music_download.DOWNLOAD_DIR
_music_lock = threading.Lock()
_music_current: MusicService | None = None


def _music_files() -> list[str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return sorted(f for f in os.listdir(DOWNLOAD_DIR) if f.lower().endswith(".mp3"))


def _music_path(filename: str) -> str | None:
    """A download/ mappan beluli, letezo fajl utvonala, kulonben None."""
    safe = os.path.basename(str(filename or "").strip())
    root = os.path.realpath(DOWNLOAD_DIR)
    path = os.path.realpath(os.path.join(root, safe))
    if not safe or not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path


def _start_music(service: MusicService):
    """Elinditja a szolgaltatast; egyszerre csak egy zene futhat."""
    global _music_current
    with _music_lock:
        if _music_current is not None and _music_current.status in ("downloading", "playing"):
            return jsonify({"error": "Mar fut egy zene, allitsd le elobb."}), 409
        _music_current = service
    threading.Thread(target=service.run, daemon=True).start()
    return jsonify({"ok": True, "started": True}), 202


@app.get("/api/music")
def list_music():
    return jsonify({"files": _music_files()})


@app.post("/api/music/play")
def play_music():
    body = request.get_json(force=True)
    urls = body.get("urls")
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "Az 'urls' mezo kotelezo."}), 400
    try:
        urls = [validate_url(u) for u in urls]
    except MusicDownloadError as exc:
        return jsonify({"error": str(exc)}), 400
    return _start_music(
        MusicService(
            urls,
            player,
            time_src,
            force=bool(body.get("force", False)),
            now_play=bool(body.get("now_play", False)),
        )
    )


@app.post("/api/music/play-file")
def play_music_file():
    body = request.get_json(force=True)
    names = body.get("filenames")
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return jsonify({"error": "A 'filenames' mezo kotelezo."}), 400
    paths = [_music_path(n) for n in names]
    if None in paths:
        return jsonify({"error": "A fajl nem talalhato."}), 404
    return _start_music(
        MusicService(
            None,
            player,
            time_src,
            force=bool(body.get("force", False)),
            now_play=bool(body.get("now_play", False)),
            files=paths,
        )
    )


@app.get("/api/music/status")
def music_status():
    svc = _music_current
    if svc is None:
        return jsonify({"status": "idle", "files": [], "current": None, "error": None})
    return jsonify(
        {
            "status": svc.status,
            "files": [os.path.basename(f) for f in svc.filename],
            "current": os.path.basename(svc.current) if svc.current else None,
            "error": svc.error,
        }
    )


@app.post("/api/music/stop")
def stop_music():
    svc = _music_current
    if svc is not None:
        svc.stop()
    else:
        player.stop()
    return jsonify({"ok": True})


@app.delete("/api/music/<name>")
def delete_music(name: str):
    path = _music_path(name)
    if path is None:
        return jsonify({"error": "A fajl nem talalhato."}), 404
    svc = _music_current
    if svc is not None and svc.status == "playing" and path in [os.path.realpath(f) for f in svc.filename]:
        return jsonify({"error": "A fajl jelenleg lejatszas alatt all."}), 409
    os.remove(path)
    logger.info("Zene torolve: %s", os.path.basename(path))
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
    config.TIME_MODE = mode
    _save_config()
    return jsonify({"ok": True})


@app.post("/api/time/sync")
def sync_time():
    body = request.get_json(force=True)
    server_host = body.get("ntp_server", "").strip()
    if server_host:
        time_src.set_ntp_server(server_host)
    config.NTP_SERVER = server_host
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
    config.NTP_SERVER = "manual"
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
    config.DEVICE_INDEX = index
    config.DEVICE_NAME = player.get_device_name()
    _save_config()
    return jsonify({"ok": True})


@app.post("/api/output/volume")
def set_volume():
    body = request.get_json(force=True)
    percent = float(body.get("percent", 100.0))
    player.set_volume(percent)
    config.VOLUME_PERCENT = player.get_volume()
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
