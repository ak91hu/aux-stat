# GitLab pipeline beállítása

A Windowsos **START.bat megmarad**. A GitLab Linux runner ugyanazt a Python
scriptet futtatja kérdések nélkül. A kezdőnap **2026-09-16**, a zárónap a futás
napja Europe/Budapest szerint. A mai nap adatai részlegesek lehetnek.

## 1. Privát repository létrehozása és feltöltés

GitLabon hozz létre egy **üres, Private** projektet, kezdeti README nélkül.
Másold ki a projekt HTTPS clone URL-jét. Telepített Git mellett PowerShellben:

```powershell
cd C:\aux-toolkit
git init -b main
git add .gitignore .gitlab-ci.yml acfreedom_energy_probe.py send_energy_email.py vendor_constants.py requirements.txt requirements-email.txt START.bat README.md GITLAB.md THIRD_PARTY_NOTICES.md LICENSE-ha-aux-cloud.txt tests research/ENERGY_PROTOCOL.md
git commit -m "Add AUX daily energy export and manual GitLab pipeline"
git remote add origin https://gitlab.com/SAJAT_NEV/aux-energy.git
git push -u origin main
```

A remote URL-t cseréld ki a saját projektedére. GitLab-hitelesítésnél használj
Git credential managert vagy personal access tokent; az AC Freedom jelszava
nem a Git hitelesítéséhez való. Ha a mappa már Git repository, az init/remote
parancsokat a meglévő beállításnak megfelelően hagyd ki.

Az output, virtuális környezet, .env fájlok és kutatási APK nem kerülnek be.
A felsorolt fájlok között a START.bat is szerepel.

## 2. Titkos változók

Projekt → **Settings → CI/CD → Variables → Add variable**.
Mindkét változó típusa **Variable**, láthatósága **Masked and hidden**.
Kapcsold be a **Protect variable** opciót, az **Expand variable reference**
opciót hagyd kikapcsolva. A main legyen protected branch a projekt
**Settings → Repository → Branch rules** részében.

| Key | Value |
| --- | --- |
| ACFREEDOM_USERNAME | AC Freedom e-mail-címed vagy telefonszámod |
| ACFREEDOM_PASSWORD | AC Freedom jelszavad |

A belépési adatokat ne írd a YAML-ba, a repositoryba vagy a pipeline indításakor
megadott egyszeri változók közé. A script környezeti változókból olvassa őket,
és az exportált JSON-ból eltávolítja a hitelesítési adatokat.

Opcionális projektváltozók:

| Key | Alapérték / használat |
| --- | --- |
| ACFREEDOM_DEVICE | Egy eszköznél nem kell. Többnél pontos eszköznév vagy endpointId. |
| ACFREEDOM_START | 2026-09-16 |
| ACFREEDOM_END | Nincs megadva: a mai nap. Különben YYYY-MM-DD. |
| ACFREEDOM_REGION | eu |
| ACFREEDOM_KIND | Automatikusan email vagy phone a felhasználónév alapján. |

## 3. Pipeline indítása

**Build → Pipelines → New pipeline**, válaszd a **main** ágat, majd indítsd el.
A konfiguráció csak a GitLab felületéről, az alapértelmezett ágon indított
pipeline-t engedi. Push után nem indul automatikusan fogyasztáslekérés.

Linuxos Docker vagy Kubernetes GitLab Runner kell, amely támogatja az image
mezőt. GitLab.com projektnél engedélyezett hosted Linux runner és rendelkezésre
álló compute keret szükséges. Windows shell runnerhez más konfiguráció kell.

Először a `test` job fut szintetikus adatokkal, felhőhívás nélkül. Siker után az
`energy` job belép, és lekéri az adatokat. Egy időben egy energiajob futhat.
A jelszót futás közben nem kéri be.

## 4. Eredmények letöltése

Nyisd meg az **energy** jobot → **Job artifacts → Download** vagy **Browse**.
Az output egy dátummal jelölt alkönyvtárban található:

```text
output/<futás időpontja>/
    energy_daily.csv
    energy_points.csv
    summary.json
    devices.json
    raw/
```

Az **energy_daily.csv** tartalmazza a napi fogyasztást kWh-ban. A hiányzó
adat üres mező, a mai nap `partial` jelölésű. Az összeg a job naplójában és
a summary.json `daily.known_consumption_kwh` mezőjében is szerepel.
A nyers válaszok a raw könyvtárba kerülnek kitakart azonosítókkal.

Az artifact 7 napig marad meg, Maintainer vagy Owner szerepkörrel tölthető le
(az `artifacts:access: maintainer` GitLab 18.4+ funkció). Régebbi saját GitLabon
ennek helyére `access: developer` állítható.

Hibás belépésnél a job hibás lesz, a summary.json akkor is letölthető. Ha nincs
felismerhető mérési adat, a 2-es kilépési kód figyelmeztetésként jelenik meg;
ez nem jelent 0 kWh fogyasztást. A naptárban maradó hiányzó napokat és a
lekérdezési hibákat a CSV és a summary.json mutatja.

Hivatalos útmutatók: [CI/CD változók](https://docs.gitlab.com/ci/variables/),
[pipeline indítása](https://docs.gitlab.com/ci/pipelines/),
[artifactok](https://docs.gitlab.com/ci/jobs/job_artifacts/).
