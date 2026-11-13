"""A frontend ID-szerzodes ellenorzese: minden JS-ben hivatkozott ID letezik az index.html-ben."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "static", "index.html")
JS_PATH = os.path.join(ROOT, "static", "app.js")

# Jelenleg nincs ismert, meglévő eltérés (a JS minden getElementById-literálja megvan az HTML-ben).
# Ide csak indokolt, átmeneti eltérés kerülhet, megjegyzéssel.
KNOWN_MISSING = set()

# A Zenék panel DOM-szerződése (Task 12, F1)
MUSIC_IDS = {
    "storageText", "storageFill",
    "btnMusicUpload", "musicFileInput", "musicLoading", "musicErrorBox", "btnMusicRetry",
    "musicEmpty", "musicList", "musicSelectAll", "musicSelectedCount",
    "btnMusicPlaySelected", "btnMusicDeleteSelected",
    "btnMusicPlay", "btnMusicStop", "musicStatus", "musicUrl", "musicForce", "musicNowPlay", "btnMusicRefresh",
    # lejatszas-allapot, tekeres, loop, letoltesi folyamat (Task 14, F3)
    "nowTitle", "nowNext", "queueList", "musicSeek", "musicTime", "btnSeekBack", "btnSeekFwd", "btnMusicLoop",
    "musicDownloadBox", "musicDownloadBar", "musicDownloadText", "musicErrorMsg",
    # playlistek felulete (Task 15, F4)
    "playlistSelect", "btnPlaylistNew", "btnPlaylistRename", "btnPlaylistDelete", "btnPlaylistPlay",
    "playlistTracks", "playlistEmpty", "addToPlaylistSelect", "btnMusicAddToPlaylist",
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _html_ids():
    return set(re.findall(r'\bid="([^"]+)"', _read(HTML_PATH)))


def _js_ids():
    return set(re.findall(r'getElementById\(\s*["\']([^"\']+)["\']\s*\)', _read(JS_PATH)))


def test_every_js_id_exists_in_html():
    missing = _js_ids() - _html_ids() - KNOWN_MISSING
    assert not missing, f"a JS hivatkozik ID-re, ami nincs az index.html-ben: {sorted(missing)}"


def test_music_ids_present():
    missing = MUSIC_IDS - _html_ids()
    assert not missing, f"hianyzo Zenék-panel ID-k: {sorted(missing)}"
