# GitHub Actions — privát aux-stat repository

A projekt: https://github.com/ak91hu/aux-stat

A **START.bat megmarad** a Windowsos, interaktív használathoz. A GitHub Actions
ugyanazt a scriptet indítja kérdések nélkül; az adatokat letölthető artifactba menti.
A workflow mindig a legfrissebb stabil Python 3 verziót kéri (`3.x`,
`check-latest: true`).

## Egyszeri beállítás

1. [Settings → Secrets and variables → Actions](https://github.com/ak91hu/aux-stat/settings/secrets/actions).
2. **New repository secret**: `ACFREEDOM_USERNAME`, értéke az AC Freedom
   e-mail-cím vagy telefonszám.
3. **New repository secret**: `ACFREEDOM_PASSWORD`, értéke az AC Freedom jelszó.
4. Több klímánál a **Variables** fülön add hozzá az `ACFREEDOM_DEVICE` változót:
   értéke a pontos eszköznév vagy endpointId. Egy klímánál nem szükséges.
   Az opcionális `ACFREEDOM_REGION` alapértéke `eu`.

A belépési adatok kizárólag a fogyasztáslekérő lépés környezeti változóiba
kerülnek. Ne írd őket a kódba, YAML-ba vagy a Run workflow mezőibe.

## Fogyasztási riport e-mailben

A GitHub saját Actions értesítései a futás állapotát közlik. A napi fogyasztási
riport és CSV-melléklet küldéséhez külső SMTP-szolgáltató szükséges.
A workflow saját Python SMTP-küldőt használ, további e-mail action nélkül.

Ingyenes lehetőség a **Brevo Free**, jelenleg napi 300 levéllel:
https://www.brevo.com/products/transactional-email/

1. Hozz létre Brevo fiókot, és állíts be igazolt feladót a szolgáltató
   előírásai szerint. A Brevo útmutatója domainhitelesítést is kérhet.
2. A **Settings → SMTP & API → SMTP** oldalon másold ki az SMTP login értékét,
   és hozz létre SMTP key-t. Ez az SMTP-jelszó; nem a Brevo-fiók jelszava,
   és nem az API key.
3. A GitHub repo **Secrets** fülén állítsd be:

| Secret | Érték |
| --- | --- |
| EMAIL_TO | A fogyasztási riport címzettjének e-mail-címe |
| EMAIL_FROM | A Brevóban igazolt feladó e-mail-címe |
| SMTP_USERNAME | A Brevo SMTP oldalon látható Login |
| SMTP_PASSWORD | A létrehozott Brevo SMTP key |

4. A **Secrets** vagy **Variables** fülön állítsd be (mindkettő támogatott;
   ha mindkét helyen szerepel ugyanaz a név, a Secret értékét használja):

| Secret vagy Variable | Érték |
| --- | --- |
| SMTP_HOST | smtp-relay.brevo.com |
| SMTP_PORT | 587 (STARTTLS, alapértelmezés); vagy 465 (TLS) |

Más hitelesített SMTP-szolgáltatóval is működik. Az e-mail tartalmazza az ismert
fogyasztás összegét, a napi bontást, a részleges és hiányzó napok jelölését,
a csatolt energy_daily.csv fájlt. Az e-mail nem tartalmaz letöltési vagy workflow-linket.
Az e-mail HTML formátumú: összesítés, napi átlag, medián és legnagyobb napi
érték, beágyazott PNG oszlopdiagram és napi táblázat szerepel benne.
Az átlag, a medián és a maximum csak a jelentett, nem részleges napokból készül.
A grafikon a legutóbbi 31 napot mutatja (zöld: jelentett, barna: részleges,
szürke kereszt: hiányzó adat). A táblázat és a CSV a teljes időszakot tartalmazza.
A kép magában a levélben utazik, nincs külső képkiszolgáló; képek tiltásakor
is olvasható a táblázat. Szöveges levelezőhöz külön plain text változat készül.
A grafikonhoz a workflow telepíti a `requirements-email.txt` függőségeit.
Sikertelen lekérésnél is megpróbál állapotjelentést küldeni; hiányzó mérésre
nem ír 0 kWh-t. Sikertelen levélküldés hibássá teszi a jobot, az addig feltöltött
artifactok elérhetők maradnak. A **Csak tesztek** mód nem küld levelet.

Az SMTP-secretek kizárólag az e-mail küldési lépésben hozzáférhetők.
A START.bat továbbra is a helyi lekérdezést indítja, e-mailt nem küld automatikusan.

[GitHub: futási értesítések](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs),
[Brevo SMTP beállítás](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP).

## Futtatás és letöltés

1. [Actions → AUX napi fogyasztás](https://github.com/ak91hu/aux-stat/actions/workflows/energy.yml).
2. **Run workflow**, ág: `main`.
3. A kezdőnap **2026-09-16**, a zárónap üresen az aktuális budapesti nap.
   A **Csak tesztek** opciót hagyd kikapcsolva a valódi lekéréshez.
4. Indítsd el. A tesztek sikeres lefutása után megtörténik a belépés és az export.
5. A futás oldalán az **Artifacts → aux-energy-...** csomagot töltsd le.

A csomagban a futási dátummal jelölt könyvtárban az **energy_daily.csv**
tartalmazza a napi fogyasztást kWh-ban. A mai nap `partial`, a hiányzó mérés
üres mező; nem automatikusan nulla. Az összeg a futás naplójában és a
**summary.json** fájlban is szerepel. A kitakart nyers válaszok a `raw/` mappában
találhatók. Az artifact 7 napig érhető el a privát repo jogosult felhasználóinak.

Hibás vagy hiányzó secret esetén a futás hibás lesz; a summary.json akkor is
mentésre kerül, ha a script elindult. Felismerhető mérés hiányánál figyelmeztetés
jelenik meg. A CSV-ben jelölt hiányzó napokat akkor is ellenőrizd, ha a futás sikeres.

A workflow **minden vasárnap 20:00-kor, Europe/Budapest időzóna szerint**
automatikusan lefut és elküldi a fogyasztási riportot a meglévő EMAIL_TO secret
címére. Az időzóna kezeli a téli és nyári időszámítást. A GitHub terhelésétől
függően az indítás késhet néhány percet.

Az automatikus futás 2026-09-16-tól a futás napjáig kérdez; a vasárnapi nap
még részleges. Ugyanazokat a secreteket használja, mint a kézi indítás.
A kézi indítás megmarad; a push nem indít felhőlekérdezést.
Az első technikai próba a **Csak tesztek** opcióval secret nélkül is futtatható.

[GitHub időzített workflow-k](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

[GitHub: Actions secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions),
[artifactok](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow).

## Automatikus CI és indulás előtti ellenőrzés

A [Forráskód ellenőrzése](https://github.com/ak91hu/aux-stat/actions/workflows/ci.yml)
workflow minden `main` push és az ágra nyitott pull request esetén tesztel
Python 3.11-en és a legfrissebb stabil Python 3-on. Kézzel is indítható.
Ez a pipeline nem használ secreteket, nem jelentkezik be a felhőbe és nem küld levelet.

A fogyasztási workflow normál futás előtt ellenőrzi az `ACFREEDOM_USERNAME`,
`ACFREEDOM_PASSWORD`, `EMAIL_TO`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_USERNAME`
és `SMTP_PASSWORD` beállításokat. Hiány esetén a változók neve jelenik meg,
az értékük nem. A lekérés és a levélküldés ilyen esetben elmarad.
A **Csak tesztek** mód továbbra is secretek nélkül működik.
