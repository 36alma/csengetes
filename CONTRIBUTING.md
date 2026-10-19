# Közreműködés

Köszönjük, hogy hozzájárulnál a projekthez! A hibajelentéseket, ötleteket és pull requesteket is szívesen fogadjuk.

## Hibák és ötletek

- Hibához vagy ötlethez nyiss issue-t a megfelelő sablonnal.
- **Biztonsági hibát ne issue-ban jelents**, hanem a [SECURITY.md](SECURITY.md) szerint.

## Fejlesztői környezet

```bash
git clone https://github.com/36alma/csengetes.git
cd csengetes
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt pytest
copy config_example.json config.json
```

## Munkafolyamat

1. Forkold a repót, vagy indíts új ágat a `main`-ből (`feature/...`, `fix/...`).
2. Készítsd el a változtatást kis, jól körülhatárolt lépésekben.
3. Írj vagy frissíts tesztet a `tests/` mappában, és futtasd: `python -m pytest`.
4. Nyiss pull requestet a `main` ágra, a sablon kitöltésével.

## Kódolási elvek

- Kövesd a környező kód stílusát, elnevezéseit és kommentezési sűrűségét.
- Az API végpontok az `api/` csomagban, a háttérlogika a `services/` alatt van; a hálózati és a döntési logikát tartsd szétválasztva (lásd `services/devices/auth.py`).
- Külső bemenetet (kérés, UDP-datagram, fájl) mindig validálj, titkot ne naplózz és ne commitolj.

## Commit üzenetek

A repó a [Conventional Commits](https://www.conventionalcommits.org/) stílust használja, például:

```
feat: add pending device approval
fix: reject stale enrollment datagrams
test: cover token expiry
```

## Viselkedési kódex

A részvétellel elfogadod a [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) szabályait.
