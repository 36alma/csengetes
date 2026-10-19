# Biztonsági irányelvek

Ez a dokumentum a csengetési rendszer biztonsági modelljét, a támogatott verziókat és a sérülékenységek bejelentésének módját írja le.

## Támogatott verziók

A projekt aktívan fejlesztett, kiadási ágak nincsenek. Biztonsági javítások kizárólag a `main` ág legfrissebb állapotához készülnek.

| Verzió            | Támogatott |
| ----------------- | ---------- |
| `main` (legfrissebb) | ✅      |
| Régebbi állapotok | ❌         |

## Sérülékenység bejelentése

**Kérjük, ne nyiss nyilvános GitHub issue-t biztonsági hibára.**

1. Használd a GitHub **Private vulnerability reporting** funkcióját: a repó **Security** fülén a **Report a vulnerability** gombbal (<https://github.com/36alma/csengetes/security/advisories/new>).
2. Ha ez nem érhető el, vedd fel a kapcsolatot a repó tulajdonosával (`36alma`) a GitHub-profilján megadott elérhetőségen.

A bejelentésben kérjük:

- a hiba rövid leírását és a várható hatását,
- a reprodukálás lépéseit (kérés, datagram, konfiguráció),
- az érintett fájlt/komponenst és a verziót (commit hash).

Ne csatolj valódi `enroll_key`-t, eszköz-tokent vagy `config.json`/`devices.json` tartalmat.

A bejelentést a lehető leghamarabb megerősítjük, a javításról és a nyilvánosságra hozatal időzítéséről egyeztetünk veled. Felelős közzététel esetén a javítás megjelenéséig kérjük a részletek titokban tartását.

## Biztonsági modell

A rendszer három, élesen elkülönített felületet használ.

### 1. Admin felület (`127.0.0.1:8765`)

- Csak a loopback címen figyel (`HOST = "127.0.0.1"`), a hálózatról nem érhető el.
- Indításkor egyszeri, véletlen `AUTH_TOKEN` generálódik (`secrets.token_urlsafe(32)`), ami csak a memóriában él. A token a `/?token=...` hívással sütit (session) cserél: `HttpOnly`, `SameSite=Strict`.
- Sütit vagy érvényes tokent nem tartalmazó kérés `403 Forbidden` választ kap; a WebSocket-kapcsolatok is a session-sütit ellenőrzik.
- Az API-dokumentáció (`/docs`, `/redoc`, `/openapi.json`) ki van kapcsolva.
- A feltöltések mérete korlátozott (`max_upload_mb`, alapértelmezetten 20 MB).

### 2. Eszközszerver (`0.0.0.0:8766`)

Ez az egyetlen, hálózatra nyitott HTTP/WebSocket port, amelyen **kizárólag** a hang-stream végpont (`/api/mixer/stream`) van. Egy eszköz csak akkor kap hozzáférést, ha mindegyik feltétel teljesül:

- érvényes `Authorization: Bearer <token>` fejléc (a szerver csak a token SHA-256 hash-ét tárolja),
- az eszköz engedélyezett (`enabled`),
- a token nem járt le (`device_token_days`, alapértelmezetten 90 nap) és a legfrissebb,
- a kliens IP-címe megegyezik a regisztrált IP-vel.

A `X-Forwarded-For` fejlécet a szerver szándékosan **nem** veszi figyelembe (`proxy_headers=False`). A megtagadott és a sikeres kapcsolatokról audit napló készül.

### 3. Eszköz-jelentkezés (UDP, `enroll_port`, alapértelmezetten 8767)

- Alapértelmezetten **ki van kapcsolva**: csak akkor indul el, ha az `enroll_key` legalább 16 karakter hosszú.
- A datagram HMAC-SHA256 aláírást tartalmaz (`uuid|ver|ts`), az összehasonlítás időzítés-biztos (`hmac.compare_digest`).
- Védelmek: 512 bájtos méretkorlát, ±60 másodperces időeltérés-tűrés, visszajátszás elleni védelem (már látott aláírás elutasítása), v5 UUID formátum-ellenőrzés.
- Forrás-szűrés: alapértelmezetten csak privát hálózati címről fogad jelentkezést; az `enroll_cidr` ezt szűkíti.
- A jelentkezők csak a memóriában, legfeljebb 64 db-ig, 10 percig várakoznak. **Az eszköz felvétele és a token kiadása mindig admin döntés**; ütközés (ismert UUID más IP-ről, megváltozott IP, foglalt IP) esetén a jóváhagyás letiltott.

## Ismert korlátok

Ezek a tervezésből adódó, tudatosan vállalt korlátok, nem sérülékenységek:

- **Nincs TLS.** Az eszközszerver sima `ws://` kapcsolatot használ, a Bearer token és a hangadat titkosítatlanul megy a hálózaton. A rendszert megbízható, lehetőleg elkülönített (pl. külön VLAN) helyi hálózaton üzemeltesd, és ne tedd közvetlenül az internetre.
- **Az IP-ellenőrzés nem hitelesítés.** Az IP-cím hamisítható a helyi hálózaton; ez a Bearer tokent kiegészíti, nem helyettesíti.
- **Az `enroll_key` közös titok.** A firmware-be égetett kulcs kiolvasása (pl. fizikai hozzáféréssel) lehetővé teszi jelentkezési kérelmek küldését. Ez önmagában nem ad hozzáférést: a felvételt továbbra is az admin hagyja jóvá. Gyanú esetén cseréld a kulcsot.
- **A UDP jelentkezés forrás-IP-je hamisítható** a helyi hálózaton; a jóváhagyás előtt ellenőrizd az eszköz UUID-ját és IP-címét.
- **Az admin token a böngésző URL-jében** szerepel az első megnyitáskor. Mivel csak a loopbacken érvényes és folyamatonként új, a kockázata alacsony, de ne oszd meg.

## Üzemeltetési ajánlások

- Az `enroll_key` legyen hosszú, véletlen érték (pl. `python -c "import secrets; print(secrets.token_hex(32))"`), és sose kerüljön verziókezelésbe.
- A `config.json` és a `devices.json` titkos adatot tartalmazhat (kulcs, token-hash-ek, IP-k): a `.gitignore` kizárja őket, ne commitold és ne oszd meg.
- Állíts be `enroll_cidr`-t a tényleges eszközhálózatra, és tűzfalon is korlátozd a 8766/8767 portokat.
- Állíts rövidebb `device_token_days` értéket, ha az eszközök fizikailag kevésbé védett helyen vannak; a lejáró tokenekről a rendszer naplót ír.
- Eltűnt vagy kompromittálódott eszköz tokenjét azonnal tiltsd le (`enabled = false`) vagy töröld az eszközt.
- Tartsd frissen a függőségeket (`requirements.txt`), és időnként futtass függőség-auditot (pl. `pip-audit`).

## Hatókör

Hatókörbe tartozik a repó saját kódja (`server.py`, `device_server.py`, `api/`, `services/`, `static/`). Nem tartozik ide a harmadik féltől származó függőségek hibája (azt a saját upstream projektjüknek jelezd), valamint az olyan támadás, amely a gazdagép feletti már meglévő helyi rendszergazdai hozzáférést feltételezi.
