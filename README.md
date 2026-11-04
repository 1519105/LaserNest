# LaserNest

Jednoduchý „slicer“ pro diodové lasery (Sculpfun, Atomstack, Ortur… s GRBL).
Zadáš rozměr desky, přidáš objekty (gravír + řez) a počty kusů, program je
**rozmístí podle skutečného tvaru** s otáčením a vyexportuje celou desku –
vždy se zachovaným pořadím **nejdřív gravír, potom řez**.

![náhled](docs/screenshot.png)

## Stažení

Hotové programy jsou v [Releases](../../releases) (sestaví se automaticky po každé změně v `main`):

- **Windows:** `LaserNest-windows-x64.exe` – stačí spustit (bez instalace).
- **Linux:** `LaserNest-linux-x86_64` – `chmod +x` a spustit.
  Na Ubuntu/Debianu může být potřeba `sudo apt install libxcb-cursor0`.

## Postup

1. **Deska** – rozměr (např. 300 × 350 mm), okraj od kraje a mezera mezi díly.
2. **Přidat…** – soubor řezu (SVG) a gravíru (PNG nebo SVG). Pokud se jmenují
   `něco_2_rez.svg` + `něco_1_gravir.png`, druhý soubor se doplní sám.
   Soubory jde také přetáhnout myší na okno. Stačí i **jen obrázek** (JPG/PNG)
   bez řezu – zadej jeho šířku v mm a díl se jen vygravíruje.
3. Zadej **počet** kusů. Zatržení **Vyplnit** doplní objekt do zbytku desky.
4. **▶ Rozmístit.** Pak lze díly ručně posouvat (myš), otáčet (`R`, `Shift+R`, `T`),
   mazat (`Del`) a duplikovat (`Ctrl+D`). Kolize se zobrazí červeně.
   Tlačítko **+1 na desku** přidá jeden kus vybraného objektu na volné místo.
5. **Export…**

### Co se exportuje

| Soubor | K čemu |
|---|---|
| `deska.svg` | Jedno SVG s barvami: **černá výplň = gravír**, **červená čára = řez** (LightBurn a spol. si udělají vrstvy podle barev) |
| `deska_1_gravir.png` + `deska_2_rez.svg` | Pro **LaserGRBL** – obojí má plátno přesně rozměr desky, načítej s offsetem 0/0 |
| `deska.gcode` | Hotový G-code: gravír (M4) a pak řez (M3) v jednom souboru, nebo zvlášť |

Řez jde vždy **nejdřív otvory, potom vnější obrys** každého dílu, díly jsou
seřazené tak, aby hlava zbytečně nejezdila. Gravír se pálí po souvislých
ostrůvcích (bez přejíždění přes celou desku).

### G-code a profily

Profil = rychlost a výkon pro gravír a řez, hustota řádků, počet průchodů,
max. S (GRBL `$30`) a air assist (M8/M9). Profily se ukládají do
`%APPDATA%\LaserNest\profiles.json` (Windows) nebo `~/.config/LaserNest/` (Linux).

- GRBL musí mít **laser mode `$32=1`**.
- Nula G-code je **levý dolní roh desky** (nebo levý horní – volba v exportu).
- Výchozí profily (překližka 3 mm, 5 W / 10 W) jsou orientační – **vždy ověř testem**.

### Vstupní soubory

- **Řez:** SVG s čarami (cesty, křivky, kruhy, obdélníky…). Jednotky se berou
  z SVG (mm, nebo px při 96 DPI jako Inkscape).
- **Gravír:** PNG/JPG/BMP (černobílý, práh 50 %) nebo SVG s vyplněnými tvary.
  Musí mít **stejné plátno jako řez** (stejný počátek vlevo nahoře) – rozměr
  rastru se přepočítá na šířku plátna řezu.

## Spuštění ze zdrojáků

```bash
pip install -r requirements.txt
python main.py
```

Sestavení vlastního exe/binárky: `pip install pyinstaller && python build.py`.
Testy: `python tests/test_core.py`. Kontrola G-code: `python tools/gcode_preview.py deska.gcode nahled.png`.

## Jak funguje nesting

Deska se převede na rastr obsazenosti (0,5 mm). Pro každý díl a natočení se
připraví maska obrysu zvětšená o polovinu mezery a všechna volná místa se
najdou najednou přes FFT. Díl jde na místo „co nejvýš a nejvíc vlevo“,
zkusí se několik pořadí a vezme se nejlepší. Výsledek se ověří přesnou geometrií.
