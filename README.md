# Csengetési rend

Iskolai csengetési rendszer asztali alkalmazásként: időzített csengetés, zenelejátszás, mikrofonos kiszólás és hálózati hangszórók (ESP32) kiszolgálása egyetlen programból. A felület HTML/CSS/JS, a háttérben egy Python (FastAPI) szerver dolgozik, az ablakot a pywebview adja.

## Főbb funkciók

- **Csengetési rendek**: több, névvel mentett rend, az aktív rend szerint automatikus csengetés.
- **Pontos idő**: NTP-s vagy rendszeridő (`time_mode`).
- **Hangkeverő**: a csengő, a zene és a mikrofon egy közös kimenetre keveredik; a force-os csengő elsőbbséget kap.
- **Zene**: médiafájlok feltöltése és letöltés (yt-dlp).
- **Hálózati eszközök**: ESP32 hangszórók a WebSocket hang-streamen kapják a hangot, token- és IP-alapú hitelesítéssel; az eszközök UDP-n jelentkezhetnek, az admin hagyja jóvá őket.

## Követelmények

- Python 3.10+
- Hangkimeneti eszköz (sounddevice / PortAudio)
- Node.js (opcionális, csak a frontend teszthez)

## Telepítés és indítás

```bash
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy config_example.json config.json   # Linux/macOS: cp
python server.py
```

Az alkalmazás megnyitja az asztali ablakot. Ha a `pywebview` nincs telepítve, csak a szerver fut a `http://127.0.0.1:8765` címen (az első megnyitáshoz szükséges tokent a program az ablak URL-jébe teszi, így ablak nélkül nem triviális az elérés).

## Beállítás (`config.json`)

A `config_example.json` alapján. A fontosabb kulcsok:

| Kulcs | Jelentés |
| --- | --- |
| `time_mode` | `ntp` vagy rendszeridő |
| `active_schedule` | az aktív csengetési rend fájlja |
| `volume_percent` | hangerő |
| `max_upload_mb` | feltöltési méretkorlát |
| `device_port` | az eszközszerver portja (8766) |
| `device_token_days` | eszköztoken élettartama napokban |
| `enroll_key` | az eszköz-jelentkezés közös titka (min. 16 karakter; üresen a jelentkezés ki van kapcsolva) |
| `enroll_cidr` | a jelentkezést fogadó hálózat (üresen: privát címek) |
| `enroll_port` | az UDP jelentkezési port (8767) |

A `config.json`, a `devices.json`, a `media/` és a `schedules/` nincs verziókezelve.

## ESP32 eszközök

- [Rákötés a mixerre](ESP32_INTEGRACIO.md)
- [Jelentkezés (enrollment)](ESP32_JELENTKEZES.md)
- [Működés](ESP32_MUKODES.md)

## Tesztek

```bash
pip install pytest
python -m pytest
```

## Biztonság

Lásd a [SECURITY.md](SECURITY.md) fájlt. A rendszer megbízható helyi hálózatra készült, TLS nélkül.

## Közreműködés

Lásd a [CONTRIBUTING.md](CONTRIBUTING.md) és a [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) fájlt.
