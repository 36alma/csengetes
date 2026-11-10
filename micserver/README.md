# Mikrofongép

Ez a kis program a mikrofonos gépen fut. A csengetési rendszer mixere kapcsolódik rá WebSocketen, tokennel azonosítva magát, és a hang a mixerben a csengő, a zene és a helyi mikrofon mellé keveredik.

## Telepítés

Python 3.10 vagy újabb kell. Másold a teljes `micserver` mappát a gépre. Minden parancsot a mappa **szülőjéből** futtass:

```
pip install -r micserver/requirements.txt
```

Linuxon a PortAudio könyvtár is kell (`libportaudio2`).

## Beállítás

1. A csengetési program **Eszközök → Mikrofongépek** paneljén vedd fel a gépet a címével. A kapott token csak egyszer látszik.
2. A mikrofonos gépen állítsd be a tokent:

   ```
   python -m micserver --set-token
   ```

3. Nem kötelező: másold a `config.example.json` fájlt `config.json` néven, és írd be a gép nevét. A bemeneti eszköz számát ezzel listázhatod:

   ```
   python -m micserver --list-inputs
   ```

4. Indítsd el a programot:

   ```
   python -m micserver
   ```

5. Engedélyezd a gép tűzfalán a bejövő TCP 8770-es portot, lehetőleg csak a mixer IP-címéről.

## Használat

A mikrofont a mixer kapcsolja be, a Mixer fül gép saját sávján lévő **Adás indítása** kapcsolóval. Amíg ki van kapcsolva, a gép nem vesz fel hangot.

A program ablakában látszik az állapot:

```
10:15:02  *** KI ***
10:21:47  *** ADÁSBAN ***
```

**A gép közelében tartózkodóknak tudniuk kell, hogy a mikrofon távolról bekapcsolható**, ezért a program ablaka maradjon látható, és tájékoztasd az érintetteket.

A mikrofon ne hallja a hangszórókat, különben a hang visszacsatol és gerjed.

## Token

A token soha nem megy át a hálózaton, a felek HMAC-kel bizonyítják, hogy ismerik. A mixer 24 óránként automatikusan lecseréli, ehhez a gépen nincs teendő.

Ha a `state.json` elveszett, vagy a token kiszivárgott: a mixerben az **Új token** gombbal kérj újat, és futtasd újra a `--set-token` parancsot. A program újraindítása nem kell.

A `state.json` titkos adatot tartalmaz, ne oszd meg.
