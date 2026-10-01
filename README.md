# AUX Stat — AC Freedom fogyasztási riportok

Önálló Python-program Windowsra, az AC Freedom / AUX Cloud fogyasztási adatainak
lekérdezésére. Bejelentkezik, felderíti az eszközöket, majd a kiválasztott eszköz
fogyasztását alapértelmezésben a telepítési dátumtól, **2026-09-16-tól**, a mai napig olvassa be napi
bontásban. Nem változtatja a klíma
beállításait és nem töröl adatot.

A program az AC Freedom 4.1.3 alkalmazás c0620000 termékhez tartozó
fogyasztási képernyőjének AUX-riportjait használja. A protokollt az alkalmazás
kódjának elemzésével azonosítottuk; a szeptemberi havi és éves felhőválaszok
összevetése is megtörtént. Más eszközöknél és időszakoknál az eredményeket
érdemes az alkalmazás megfelelő fogyasztási nézetével ellenőrizni.

Helyben CSV-exportot készít; GitHub Actionsből kézzel vagy hetente futtatható,
és HTML e-mailben is elküldi a napi bontást, a grafikont és a CSV-mellékletet.

## GitHub Actions és heti e-mail

A repository: [ak91hu/aux-stat](https://github.com/ak91hu/aux-stat).

1. A [repository Secrets beállításaiban](https://github.com/ak91hu/aux-stat/settings/secrets/actions)
   add meg az `ACFREEDOM_USERNAME` és `ACFREEDOM_PASSWORD` secreteket.
2. Az e-mailhez szükséges secretek: `EMAIL_TO`, `EMAIL_FROM`,
   `SMTP_USERNAME`, `SMTP_PASSWORD`. Az `SMTP_HOST` és `SMTP_PORT` secretként
   vagy repository variable-ként is megadható; az alapértelmezett port `587`.
3. Több klímánál az `ACFREEDOM_DEVICE` repository variable értéke legyen a
   pontos eszköznév vagy endpointId. Az `ACFREEDOM_REGION` alapértéke `eu`.
4. Az [Actions → AUX napi fogyasztás](https://github.com/ak91hu/aux-stat/actions/workflows/energy.yml)
   oldalon válaszd a **Run workflow** lehetőséget a `main` ágon.
   A kezdőnap alapértéke `2026-09-16`; üres zárónap esetén a mai napig kérdez.

A workflow vasárnap **20:00-kor, Europe/Budapest időzóna szerint** van
ütemezve. A kézi indítás is elérhető; a push nem indít fogyasztáslekérést.
Az új repóban a secreteket külön kell beállítani: a forráskód feltöltése
nem másolja át másik repó belépési vagy SMTP-adatait.

A **Csak tesztek** mód felhőbejelentkezés és e-mail-küldés nélkül fut.
Normál futásnál az eredmények az `aux-energy-...` artifactban tölthetők le
7 napig. Az e-mail összesítést, napi átlagot, legnagyobb napi értéket,
a legutóbbi 31 nap grafikonját és a teljes időszak CSV-jét tartalmazza.
A hiányzó és részleges napokat külön jelöli. Hiányzó SMTP-beállítás vagy
sikertelen küldés hibássá teszi a workflow-t.

Részletes beállítás és SMTP-példa: **[GITHUB.md](GITHUB.md)**.

## Windowsos indítás

GitLabon titkos változókkal, kézzel indítható pipeline-ban is futtatható:
**[GitLab beállítási útmutató](GITLAB.md)**. A Windowsos START.bat továbbra is használható.

1. Töltsd le és csomagold ki a [forráskódot](https://github.com/ak91hu/aux-stat/archive/refs/heads/main.zip).
   Python 3.11 vagy újabb verzió szükséges a `py` Python Launcherrel.
2. Kattints duplán a **START.bat** fájlra. Első alkalommal létrehozza a `.venv`
   környezetet és telepíti a függőségeket; rendszergazdai jogosultság nem kell.
3. Az első kérdésnél közvetlenül megadhatod az AC Freedom e-mail-címét vagy
   telefonszámát. A régi `email` / `phone` választás is működik.
4. Add meg a jelszót. Gépelés közben nem látszik, és nem kerül fájlba.
5. Több eszköznél válassz sorszámot; egy eszköznél automatikus a kiválasztás.
6. A dátumokat automatikusan választja: **2026-09-16-tól**, a klíma telepítésének
   napjától, a mai napig minden nap bekerül az `energy_daily.csv` fájlba.
   A jelszón és több eszköznél az eszközválasztáson
   kívül nem kér további adatot.

A régió alapértelmezésben Európa. A program először az érintett hónapok napi
összesítéseit kéri le, majd a hiányzó napokat külön napi riportból is megpróbálja
beolvasni. A mai naphoz a napi riport lezárt óráit összegzi. A kérések között
1 másodperc szünet van. Az aktuális munkamenet, jelszó és eszközkulcs nem kerül
mentésre. Jogosultsági, munkamenet-, gyakorisági vagy HTTP 5xx hibánál megáll.

## PowerShell

```powershell
git clone https://github.com/ak91hu/aux-stat.git
cd aux-stat
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe acfreedom_energy_probe.py
```

Csak az éves nézet:

```powershell
.\.venv\Scripts\python.exe acfreedom_energy_probe.py --view year --end 2026-10-01
```

Szeptemberi havi fogyasztás:

```powershell
.\.venv\Scripts\python.exe acfreedom_energy_probe.py --view month --end 2026-09-30
```

Alapértelmezett napi bontásnál a `--start` és `--end` valódi, mindkét végén
beleértett intervallumot jelöl. Például:

```powershell
.\.venv\Scripts\python.exe acfreedom_energy_probe.py --start 2026-09-16 --end 2026-09-30
```

Az explicit `--view year|month|day|all` az app megfelelő nézetét adja; ezeknél az
`--end` a vizsgált dátum, és a teljes év/hónap/nap kerül lekérdezésre. A fenti
havi parancs ezért egész szeptembert kérdezi le. A helyi indítás egyszeri
lekérdezést végez; a heti automatizálást a GitHub Actions workflow adja.

További kapcsolók: `--region eu|usa|cn|rus`, `--kind email|phone`, `--username`,
`--device "AUX Aura"`, `--list-only`, `--output C:\ACFreedomResults`,
`--timezone Europe/Budapest`. Jelszóhoz nincs parancssori kapcsoló.

## Eredmények

Minden futás külön könyvtárba kerül:

```text
output\20261001_123456_123456\
    summary.json
    devices.json
    energy_daily.csv        # alapértelmezett napi bontás, minden dátum sorával
    energy_points.csv       # csak felismert fogyasztási adat esetén
    raw\
        aux_year.json
        aux_month.json
        aux_day.json
```

A napi CSV oszlopai: `date`, `consumption_kwh`, `status`, `source`, `partial`.
A kWh értékek teljes pontossággal kerülnek bele. Minden dátum szerepel, a hiányzó
fogyasztás üres; a `reported` ismert napi érték, a `partial` a mai nap eddigi
lezárt óráinak összege. A `no_data` hiányzó rekord, a `query_error` kérési hiba,
a `not_queried` megszakítás miatt még le nem kérdezett dátum.

A **summary.json** `daily` része tartalmazza az ismert/hiányzó napok számát és az
ismert adatok összegét. Ez csak teljes adatkészlet esetén teljes időszaki összeg;
a mai nap befejezetlen. A `queries` elemei tartalmazzák a riportot, a hibakódot, az
adatpontok számát és, ha van adat, a `total_kwh` értéket. A CSV UTF-8 BOM kódolású,
pontosvesszővel tagolt. A program a felismerhető hitelesítési és eszközazonosító
mezőket kitakarja a mentésekben. A nyers válaszok megmaradnak elemzéshez.

Az éves, havi és napi összeg külön nézet: ezeket nem szabad egymással összeadni.
Az éves riport havi, a havi riport napi, a napi riport óránkénti adatokat ad.
A `tenelec` értékeket az app fogyasztási képernyője közvetlenül kWh-ként kezeli;
a program ugyanígy összegzi a megfelelő időbeli rekeszeket. Ismétlődő rekesznél
az utolsó érték számít, ahogy az appban is. A `table.total` nem fogyasztási összeg.

Az app az aktuális hónap nézetéből kihagyja a mai napot, az aktuális napi nézetből
a még be nem fejezett órát és az éves nézetből a jövőbeli hónapokat. A program
követi ezeket a megjelenítési korlátokat; a napi bontásban a mai napot külön
napi lekérdezéssel, részleges értékként pótolja. A szervertől visszakapott időbélyegeket
az apphoz hasonlóan kezeli; az AUX v2 kérés dátumát nem tolja el UTC+8-ra.

**Üres válasz nem jelent 0 kWh fogyasztást.** A hiányzó napokat nem számolja
nullának; az összeg csak az ismert méréseket tartalmazza. Az alkalmazással való
összevetéshez a legújabb futás `summary.json` és `energy_daily.csv` fájlját használd.

Kilépési kódok: 0 = eszközlista vagy felismert adat; 2 = nincs felismert adat;
1 = hiba; 130 = megszakítás. A `--legacy-probe` a régi, általános BroadLink
riportok diagnosztikáját futtatja a korábbi `--start` / `--end` intervallummal.
Az ottani számok mértékegysége továbbra is ismeretlen lehet.

## Belépési adatok és mentések

A repository nem tartalmaz személyes jelszót, hozzáférési tokent vagy SMTP-adatot.
A belépés helyben rejtett jelszóbekéréssel vagy környezeti változókból történik;
GitHub Actionsben repository secreteket használ. Kérdés nélküli futtatáshoz
az `ACFREEDOM_USERNAME` és `ACFREEDOM_PASSWORD` változók mellett a
`--non-interactive` kapcsoló szükséges. A program nem tölti be automatikusan
az `.env` fájlokat.

A `.gitignore` kizárja a helyi `output/`, `.venv/` és `.env` fájlokat.
A mentésekben a program kitakarja a felismert hitelesítési és eszközazonosító
mezőket; megosztás előtt ellenőrizd a fájlok tartalmát. A `vendor_constants.py`
a működéshez szükséges, nyilvános protokollállandókat tartalmazza.

## A protokoll forrása és ellenőrzése

A tényleges AUX útvonal: `POST /appfront/v1/webapi/device/stats`.
A riportok: `fw_auxoverseayearconsum_v1`, `fw_auxoverseamonthconsum_v1`,
`fw_auxoverseadayconsum_v1`; paraméter: `tenelec`.
Az éves kérés szokatlan januári 00. napját is az app kódjának megfelelően küldi.
A részletes forráselemzés: [research/ENERGY_PROTOCOL.md](research/ENERGY_PROTOCOL.md).

Automatizált tesztek (szimulált és kitakart rögzített felhőválaszokkal;
valódi bejelentkezés és levélküldés nélkül):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-email.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

A tesztek bejelentkezést, felderítést, kérésformátumot, naptári időszakokat,
adatfeldolgozást, összegzést, adatkiszűrést és hibakezelést ellenőriznek,
valamint a HTML e-mailt, a beágyazott grafikont és az SMTP TLS-kapcsolatát.
A tesztek a személyes belépési adatokat helyettesítő mintaértékeket használnak.

A bejelentkezés/inventory a [ha-aux-cloud](https://github.com/maeek/ha-aux-cloud)
MIT forrására épül, commit: `85ae111e77e92edd1b104b55a57f81ebe4c75cab`.
Az alkalmazáscsomag elemzés céljából az
[APKPure oldaláról](https://apkpure.net/ac-freedom/com.broadlink.acfreedom/download)
került letöltésre; nem kerül telepítésre vagy a ZIP-be. A Google Play csomagazonosító:
[com.broadlink.acfreedom](https://play.google.com/store/apps/details?id=com.broadlink.acfreedom).
Licenc és eredet: `THIRD_PARTY_NOTICES.md`, `LICENSE-ha-aux-cloud.txt`.
