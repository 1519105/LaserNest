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

K pálení je potřeba program, který posílá G-code do laseru – doporučujeme
**LaserGRBL** (zdarma, Windows): oficiální web **[lasergrbl.com](https://lasergrbl.com)**,
zdrojáky a vydání na **[github.com/arkypita/LaserGRBL](https://github.com/arkypita/LaserGRBL/releases)**.
Stahuj jen odtud – jiné „download“ weby často přibalují reklamy nebo malware.

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
| `deska.svg` | Jedno SVG s barvami: **černá výplň = gravír**, **červená čára = řez** – pro **LightBurn / Inkscape**. ⚠ **Ne pro LaserGRBL** (výplně neumí, gravír by jen obkreslil obrysy) |
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

## Použití s LaserGRBL – krok za krokem

### Jednorázová příprava

1. Připoj laser, v LaserGRBL zvol **COM port** a **115200** baud, připoj se (zelená ikona).
2. **Grbl → Grbl Configuration**: zkontroluj **`$32 = 1`** (laser mode) a hodnotu
   **`$30`** (max. S, obvykle 1000). Stejné `$30` nastav v profilu LaserNest
   jako „Max. S“ – jinak nebude sedět výkon v %.
3. V LaserNest si v **Profil laseru → Upravit** nastav rychlosti a výkony
   pro svůj materiál (vyzkoušej na odřezku a ulož – profil se pamatuje).

### Každá deska

1. **LaserNest:** rozměr desky → přidat objekty → počty → **▶ Rozmístit** →
   případně ručně doladit → **Export…**
2. Polož desku do laseru, najeď hlavou na **levý dolní roh desky** (při
   exportu s nulou „levý dolní roh“) a nastav tam **pracovní nulu**
   (v konzoli LaserGRBL `G92 X0 Y0`, nebo tlačítkem nastavení nuly).
3. Dál jedna ze dvou cest:

#### A) Hotový G-code (doporučeno)

1. **File → Open File →** `deska.gcode`.
2. V náhledu zkontroluj, že díly leží uvnitř desky (osa Y roste od spodního
   okraje – díly nahoře na desce mají velké Y, to je správně). Dlouhé tenké
   čáry jsou jen přejezdy s vypnutým laserem.
3. **▶ Run.** Laser sám nejdřív vygravíruje vše a pak vyřízne. Při volbě
   „Pauza (M0)“ se mezi gravírem a řezem zastaví – pokračuj tlačítkem
   **Resume / Cycle start** (třeba po kontrole gravíru nebo výměně čočky).

Rychlost a výkon jsou už v G-code z profilu, v LaserGRBL se nic nenastavuje.
Gravír a řez jde exportovat i do **dvou souborů** (Export → Obsah) a spustit je
postupně.

#### B) Obrázek gravíru + SVG řezu

Hodí se, když chceš rychlosti/výkony ladit přímo v LaserGRBL.

1. **File → Open File →** `deska_1_gravir.png`
   - **Line To Line Tracing**, kvalita **10 lines/mm** (nebo co máš v profilu),
     směr Horizontal.
   - V dalším okně: rychlost gravíru, S-MAX (výkon), **velikost přesně
     rozměr desky** (např. 300 × 350 mm) a **Offset X/Y = 0**.
   - Vypal.
2. **Neposouvej desku ani nulu.** **File → Open File →** `deska_2_rez.svg`
   - Laser mode **M3 – Constant Power**, rychlost a S-MAX pro řez,
     **Color filter: All**, velikost opět **rozměr desky**, **Offset 0/0**.
   - Počet průchodů nastav vpravo dole (Passes) a vyřízni.

Oba soubory mají plátno přesně jako deska, proto při stejné velikosti a
offsetu 0/0 gravír a řez přesně sedí na sebe.

### Časté problémy

| Problém | Příčina / řešení |
|---|---|
| Barevné `deska.svg` v LaserGRBL dělá z teček kroužky / zasekne se na „Loading“ | LaserGRBL u SVG neumí výplně – použij **G-code** nebo **PNG + SVG řezu** |
| Gravír a řez na sebe nesedí | Jiná velikost nebo offset při načtení (musí být rozměr desky a 0/0), nebo se mezi kroky posunula nula/deska |
| Výkon neodpovídá % | „Max. S“ v profilu ≠ `$30` v GRBL |
| Na začátku řádků je gravír tmavší | Chybí `$32=1` (laser mode) |
| Díly jsou mimo desku | Nula G-code je jinde než při exportu (levý dolní × levý horní roh) |
| U objektu je text červeně (řeže se) | Prohozený soubor řezu a gravíru – od verze 0.1.2 to program pozná a opraví sám |

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
