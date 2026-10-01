"""Standalone, read-only AUX Cloud energy diagnostic (Python 3.11+).

Authentication/inventory adapted from maeek/ha-aux-cloud (MIT).
Energy HTTP requests follow the AC Freedom c0620000 UI; live validation pending.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import getpass
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, time as daytime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from vendor_constants import (
    AES_INITIAL_VECTOR, BODY_ENCRYPT_KEY, COMPANY_ID, LICENSE_ID,
    PASSWORD_ENCRYPT_KEY, TIMESTAMP_TOKEN_ENCRYPT_KEY,
)

SERVERS = {
    "eu": "https://app-service-deu-f0e9ebbb.smarthomecs.de",
    "usa": "https://app-service-usa-fd7cc04c.smarthomecs.com",
    "cn": "https://app-service-chn-31a93883.ibroadlink.com",
    "rus": "https://app-service-rus-b8bbc3be.smarthomecs.com",
}
REPORTS = ("fw_energystats_v1", "fw_energystatus_v1", "fw_spminielec_v1")
AUX_REPORTS = {"year": "fw_auxoverseayearconsum_v1",
               "month": "fw_auxoverseamonthconsum_v1",
               "day": "fw_auxoverseadayconsum_v1"}
AUX_HISTORY_ENDPOINT = "appfront/v1/webapi/device/stats"
INSTALLATION_DATE = "2026-09-16"
SENSITIVE = re.compile(
    r"password|session|token|cookie|credential|email|phone|userid|familyid|"
    r"endpointid|deviceid|^did$|^mac$|key|secret|authorization|friendlyname",
    re.IGNORECASE,
)
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T_ ]\d{2}:\d{2}(?::\d{2})?")
AUTH_CODES = {-30129, -1000, -1009, -1012, -3003, -49009, 10011}
RATE_CODES = {-2001, -1036}


class ProbeError(Exception):
    def __init__(self, message, *, stop=False):
        super().__init__(message)
        self.stop = stop


def compact(value):
    return json.dumps(value, separators=(",", ":"))


def scrub(value, secrets=()):
    """Remove credential/identity fields and known secrets even in echoed text."""
    if isinstance(value, dict):
        return {
            str(k): "<redacted>" if SENSITIVE.search(str(k)) else scrub(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, "<redacted>")
    return value


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def extract_points(payload, metric):
    """Only extract recognized timestamped measurements, never status/ID numbers.

    Supports BroadLink table[].values timestamp maps and explicit metric records.
    The source unit and cumulative/interval semantics remain unverified.
    """
    points = []

    def add(stamp, field, value, path, unit="unknown"):
        number = numeric(value)
        if number is not None and isinstance(stamp, str) and STAMP.match(stamp):
            points.append({"timestamp_server": stamp, "metric": field,
                           "value": number, "unit": unit, "json_path": path})

    def walk(node, path="$"):
        if isinstance(node, dict):
            stamp = next((node[k] for k in ("occurtime", "time", "timestamp", "date", "datetime")
                          if isinstance(node.get(k), str) and STAMP.match(node[k])), None)
            if stamp:
                for field in ("tenelec", "elec", "power", "energy", "kwh"):
                    if field in node:
                        add(stamp, field, node[field], f"{path}.{field}", node.get("unit", "unknown"))
            for key, value in node.items():
                subpath = f"{path}.{key}"
                if STAMP.match(str(key)):
                    if isinstance(value, dict):
                        for field in ("tenelec", "elec", "power", "energy", "kwh"):
                            if field in value:
                                add(key, field, value[field], f"{subpath}.{field}", value.get("unit", "unknown"))
                    else:
                        add(key, metric, value, subpath)
                else:
                    walk(value, subpath)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload)
    return points


def cloud_status(payload):
    """Find top-level and common nested cloud error statuses."""
    for node in (payload, payload.get("data"), payload.get("event", {}).get("payload")
                 if isinstance(payload.get("event"), dict) else None):
        if isinstance(node, dict):
            for key in ("status", "code"):
                if key in node and node[key] not in (None, 0, "0"):
                    return node[key]
    return 0


def empty_history(payload):
    """Recognize explicit empty BroadLink tables, without interpreting them as zero energy."""
    table = payload.get("table")
    return isinstance(table, list) and (
        not table or all(
            isinstance(row, dict)
            and row.get("values") in (None, [], {})
            and row.get("total", 0) in (0, "0")
            and row.get("cnt", 0) in (0, "0")
            for row in table
        )
    )


class AuxCloud:
    def __init__(self, region="eu", session=None):
        self.url = SERVERS[region]
        self.session = session or requests.Session()
        self.userid = ""
        self.loginsession = ""
        self.secrets = []

    def headers(self):
        return {
            "Content-Type": "application/x-java-serialized-object",
            "licenseId": LICENSE_ID, "lid": LICENSE_ID, "language": "en",
            "appVersion": "2.2.10.456537160", "system": "android",
            "appPlatform": "android", "userid": self.userid,
            "loginsession": self.loginsession,
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G991B Build/SP1A.210812.016)",
        }

    def request(self, endpoint, payload=None, *, raw=None, headers=None, check=True):
        request_headers = {**self.headers(), **(headers or {})}
        try:
            response = self.session.post(
                f"{self.url}/{endpoint}", headers=request_headers,
                data=raw if raw is not None else compact(payload) if payload is not None else None,
                timeout=(5, 20), allow_redirects=False,
            )
        except requests.RequestException as exc:
            # Do not expose request bodies, account names or proxy credentials.
            raise ProbeError(f"Hálózati hiba: {type(exc).__name__} ({endpoint}).") from exc
        if response.status_code != 200:
            raise ProbeError(f"HTTP {response.status_code} ({endpoint}).",
                             stop=response.status_code in (401, 403, 429) or response.status_code >= 500)
        try:
            result = response.json()
        except ValueError as exc:
            raise ProbeError(f"A válasz nem JSON ({endpoint}).") from exc
        if not isinstance(result, dict):
            raise ProbeError(f"Váratlan JSON-formátum ({endpoint}).")
        status = cloud_status(result)
        try:
            code = int(status)
        except (ValueError, TypeError):
            code = None
        if code in AUTH_CODES | RATE_CODES:
            reason = "Lejárt munkamenet" if code in AUTH_CODES else "Túl sok kérés"
            raise ProbeError(f"{reason}: {code} ({endpoint}).", stop=True)
        if check and status != 0:
            raise ProbeError(f"Cloud hibakód: {status} ({endpoint}).")
        return result

    def login(self, username, password, kind="email"):
        self.secrets.extend([username, password])
        password_hash = hashlib.sha1(f"{password}{PASSWORD_ENCRYPT_KEY}".encode()).hexdigest()
        self.secrets.append(password_hash)
        body = compact({kind: username, "password": password_hash,
                        "companyid": COMPANY_ID, "lid": LICENSE_ID})
        timestamp = str(time.time())
        token = hashlib.md5(f"{body}{BODY_ENCRYPT_KEY}".encode()).hexdigest()
        key = hashlib.md5(f"{timestamp}{TIMESTAMP_TOKEN_ENCRYPT_KEY}".encode()).digest()
        encoded = body.encode()
        padded = encoded + b"\0" * (16 - len(encoded) % 16)
        encryptor = Cipher(algorithms.AES(key), modes.CBC(AES_INITIAL_VECTOR)).encryptor()
        result = self.request("account/login", raw=encryptor.update(padded) + encryptor.finalize(),
                              headers={"timestamp": timestamp, "token": token})
        if not all(isinstance(result.get(k), str) and result[k] for k in ("userid", "loginsession")):
            raise ProbeError("A belépési válaszból hiányzik a userid vagy loginsession.")
        self.userid, self.loginsession = result["userid"], result["loginsession"]
        self.secrets.extend([self.userid, self.loginsession])

    def discover(self):
        result = self.request("appsync/group/member/getfamilylist")
        data = result.get("data")
        families = data.get("familyList") if isinstance(data, dict) else None
        if not isinstance(families, list):
            raise ProbeError("Váratlan családlista-formátum.")
        devices, warnings, seen = [], [], set()
        for family in families:
            if not isinstance(family, dict):
                continue
            family_id = family.get("familyid") or family.get("familyId")
            if not family_id:
                warnings.append("Egy család azonosító nélkül érkezett.")
                continue
            self.secrets.append(str(family_id))
            for shared in (False, True):
                endpoint = ("appsync/group/sharedev/querylist?querytype=shared" if shared
                            else "appsync/group/dev/query?action=select")
                try:
                    result = self.request(endpoint, {"endpointId": ""} if shared else {"pids": []},
                                          headers={"familyid": str(family_id)})
                except ProbeError as exc:
                    if exc.stop:
                        raise
                    warnings.append(str(exc))
                    continue
                data = result.get("data")
                records = data.get("shareFromOther" if shared else "endpoints") if isinstance(data, dict) else None
                if not isinstance(records, list):
                    warnings.append(f"Váratlan eszközlista: {endpoint}")
                    continue
                for record in records:
                    device = record.get("devinfo") if shared and isinstance(record, dict) else record
                    if not isinstance(device, dict) or not device.get("endpointId"):
                        continue
                    identity = device["endpointId"]
                    if identity in seen:
                        continue
                    seen.add(identity)
                    device = {**device, "familyId": family_id, "shared": shared}
                    devices.append(device)
                    for key, value in device.items():
                        if SENSITIVE.search(key) and isinstance(value, str):
                            self.secrets.append(value)
        return devices, warnings


def interval(start, end, local_timezone, server_timezone):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("A kezdő dátum későbbi a záró dátumnál.")
    local, server = ZoneInfo(local_timezone), ZoneInfo(server_timezone)
    convert = lambda d, t: datetime.combine(d, t, local).astimezone(server).strftime("%Y-%m-%d_%H:%M:%S")
    return convert(first, daytime.min), convert(last, daytime(23, 59, 59))


def save_json(path, value, secrets):
    path.write_text(json.dumps(scrub(value, secrets), ensure_ascii=False, indent=2), encoding="utf-8")


def legacy_probe(client, device, start, end, output, summary):
    """Six bounded, experimental requests on the authenticated regional host."""
    points = []
    raw_dir = output / "raw"
    raw_dir.mkdir()
    for report in REPORTS:
        for operation, metric in (("stats", "elec"), ("status", "power")):
            endpoint = f"dataservice/v2/device/{operation}"
            body = {
                "credentials": {"userid": client.userid, "loginsession": client.loginsession,
                                "licenseid": LICENSE_ID},
                "report": report,
                "device": [{"did": device["endpointId"], "start": start, "end": end,
                            "params": [metric], "timefilter": {}}],
            }
            result_entry = {"report": report, "endpoint": endpoint, "metric": metric,
                            "experimental": True, "points": 0}
            print(f"Lekérdezés: {report} / {operation} ...", flush=True)
            stop = False
            try:
                result = client.request(endpoint, body, headers={"Content-Type": "text/plain; charset=UTF-8",
                                                               "familyid": str(device["familyId"])}, check=False)
                status = cloud_status(result)
                message = result.get("msg", result.get("message"))
                failed = status != 0 or (isinstance(message, str) and message.lower() not in ("ok", "success", ""))
                candidates = [] if failed else extract_points(result, metric)
                filename = f"{report}_{operation}.json"
                save_json(raw_dir / filename, result, client.secrets)
                outcome = ("api_error" if failed else "candidate_data" if candidates
                           else "server_returned_no_records" if empty_history(result)
                           else "no_recognized_points")
                result_entry.update(status=status, message=message, raw=f"raw/{filename}",
                                    points=len(candidates), outcome=outcome)
                points.extend({"report": report, "operation": operation, **point} for point in candidates)
                if outcome == "server_returned_no_records":
                    print("  A szerver ehhez a riporthoz üres adatsort adott vissza (nem 0 kWh).")
                else:
                    print(f"  {len(candidates)} mérési adatjelölt; cloud status={status}.")
            except ProbeError as exc:
                result_entry.update(outcome="request_error", error=str(exc))
                print(f"  {exc}")
                stop = exc.stop
            summary["queries"].append(result_entry)
            summary["candidate_points"] = len(points)
            save_json(output / "summary.json", summary, client.secrets)
            if stop:
                summary["stopped_early"] = True
                save_json(output / "summary.json", summary, client.secrets)
                return points
            time.sleep(1)
    return points


def aux_history_body(device, selected_date, view):
    """Reproduce the queryPower request in the c0620000 app resources.

    The app deliberately sends January day 00 for the annual query; preserve it.
    Dates are calendar strings from the app, without conversion to UTC+8.
    """
    selected = date.fromisoformat(selected_date)
    if view == "year":
        start = f"{selected.year}-01-00_00:00:00"
        end = f"{selected.year}-12-31_23:59:59"
    elif view == "month":
        start = f"{selected.year}-{selected.month:02d}-01_00:00:00"
        end = f"{selected.year}-{selected.month:02d}-{calendar.monthrange(selected.year, selected.month)[1]:02d}_23:59:59"
    elif view == "day":
        start, end = f"{selected}_00:00:00", f"{selected}_23:59:59"
    else:
        raise ValueError("Ismeretlen energia nézet.")
    return {"report": AUX_REPORTS[view], "querytype": "stats", "devtype": 43715,
            "heartbeatHost": "", "device": [{"did": device["endpointId"],
                "devtype": "43715", "offset": 0, "step": 1000,
                "params": ["tenelec"], "start": start, "end": end,
                "sortk": "occurtime", "reportType": view}]}


def aux_period_points(result, selected_date, view, now=None):
    """Map the first response table into hour/day/month buckets, as the UI does."""
    selected = date.fromisoformat(selected_date)
    now = now or datetime.now()
    table = result.get("table")
    if not isinstance(table, list) or not table or not isinstance(table[0], dict):
        return []
    buckets = {}
    for point in extract_points({"table": [table[0]]}, "tenelec"):
        if point["metric"] != "tenelec":
            continue
        try:
            stamp_text = point["timestamp_server"][:19]
            if view == "year" and stamp_text[8:10] == "00":
                stamp_text = stamp_text[:8] + "01" + stamp_text[10:]
            stamp = datetime.strptime(stamp_text, "%Y-%m-%d_%H:%M:%S")
        except ValueError:
            continue
        if stamp.year != selected.year:
            continue
        if view != "year" and stamp.month != selected.month:
            continue
        if view == "day" and stamp.day != selected.day:
            continue
        bucket = stamp.month if view == "year" else stamp.day if view == "month" else stamp.hour
        if selected.year == now.year:
            if view == "year" and bucket > now.month:
                continue
            if selected.month == now.month:
                if view == "month" and bucket >= now.day:
                    continue
                if view == "day" and selected.day == now.day and bucket >= now.hour:
                    continue
        # Last record wins within a bucket, matching the app's array assignment.
        buckets[bucket] = {**point, "unit": "kWh", "bucket": bucket}
    return [buckets[key] for key in sorted(buckets)]


def probe(client, device, selected_date, output, summary, view="all", prefix=""):
    points = []
    (output / "raw").mkdir(exist_ok=True)
    summary["energy_protocol"] = "acfreedom_4.1.3_c0620000_queryPower"
    summary["energy_kwh_verified"] = False  # Live verification is still required.
    summary["energy_request_source"] = "research/ENERGY_PROTOCOL.md"
    for current in ("year", "month", "day") if view == "all" else (view,):
        body = aux_history_body(device, selected_date, current)
        entry = {"report": body["report"], "endpoint": AUX_HISTORY_ENDPOINT,
                 "view": current, "selected_date": selected_date, "metric": "tenelec",
                 "request_start": body["device"][0]["start"],
                 "request_end": body["device"][0]["end"], "points": 0}
        print(f"AUX {current} fogyasztás lekérdezése: {body['report']} ...", flush=True)
        stop = False
        try:
            result = client.request(AUX_HISTORY_ENDPOINT, body, headers={
                "Content-Type": "application/json", "userId": client.userid,
                "loginsession": client.loginsession, "familyId": str(device["familyId"])}, check=False)
            filename = f"{prefix}aux_{current}.json"
            save_json(output / "raw" / filename, result, client.secrets)
            status = cloud_status(result)
            message = result.get("msg", result.get("message"))
            failed = status != 0 or (isinstance(message, str) and message.lower() not in ("ok", "success", ""))
            candidates = [] if failed else aux_period_points(
                result, selected_date, current,
                datetime.now(ZoneInfo(summary.get("local_timezone", "Europe/Budapest"))))
            entry.update(status=status, message=message, raw=f"raw/{filename}", points=len(candidates),
                         outcome="api_error" if failed else "aux_energy_data" if candidates
                         else "server_returned_no_records" if empty_history(result) else "no_recognized_points")
            if candidates:
                total = sum((Decimal(str(point["value"])) for point in candidates), Decimal(0))
                entry["total_kwh"] = str(total.quantize(Decimal("0.01")))
                print(f"  {len(candidates)} adatpont; összesen {entry['total_kwh']} kWh.")
                points.extend({"report": body["report"], "operation": current, **point} for point in candidates)
            else:
                print(f"  Nincs kiolvasható fogyasztási adat; {entry['outcome']}; status={status}.")
        except ProbeError as exc:
            entry.update(outcome="request_error", error=str(exc))
            print(f"  {exc}")
            stop = exc.stop
        summary["queries"].append(entry)
        summary["candidate_points"] = sum(query.get("points", 0) for query in summary["queries"])
        summary["unit_source"] = "AC Freedom consumption chart labels and unscaled tenelec values"
        save_json(output / "summary.json", summary, client.secrets)
        if stop:
            summary["stopped_early"] = True
            return points
        time.sleep(1)
    return points


def daily_history(client, device, start, end, output, summary):
    """Fetch calendar months, then individual missing days and today's partial data."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    today = datetime.now(ZoneInfo(summary.get("local_timezone", "Europe/Budapest"))).date()
    if first > last:
        raise ValueError("A kezdő dátum későbbi a záró dátumnál.")
    if last > today:
        raise ValueError("A záró dátum nem lehet a jövőben.")
    rows = {}
    current = first
    while current <= last:
        rows[current] = {"date": current.isoformat(), "consumption_kwh": "",
                         "status": "not_queried", "source": "", "partial": current == today}
        current += timedelta(days=1)
    summary.update(start_local=start, end_local=end, view="daily",
                   date_handling="local calendar strings; no timezone shift")
    points = []
    try:
        months = sorted({(day.year, day.month) for day in rows if day < today})
        for year, month in months:
            if summary.get("stopped_early"):
                break
            selected = f"{year}-{month:02d}-01"
            batch = probe(client, device, selected, output, summary, "month", f"{year}-{month:02d}_")
            points.extend(batch)
            entry = summary["queries"][-1]
            for day, row in rows.items():
                if day.year == year and day.month == month and day < today:
                    row["status"] = "query_error" if entry["outcome"] in ("request_error", "api_error") else "no_data"
            for point in batch:
                day = date.fromisoformat(point["timestamp_server"][:10])
                if day in rows and day < today:
                    rows[day].update(consumption_kwh=str(Decimal(str(point["value"]))),
                                     status="reported", source="month_report")
        # Some days have no monthly aggregate yet: query their hourly records.
        for day, row in rows.items():
            if summary.get("stopped_early"):
                break
            if row["status"] == "reported":
                continue
            batch = probe(client, device, day.isoformat(), output, summary, "day", f"{day}_")
            points.extend(batch)
            entry = summary["queries"][-1]
            if batch:
                total = sum((Decimal(str(point["value"])) for point in batch), Decimal(0))
                row.update(consumption_kwh=str(total),
                           status="partial" if day == today else "reported", source="day_report")
            elif entry["outcome"] in ("request_error", "api_error"):
                row.update(status="query_error", source="day_report")
            else:
                row.update(status="no_data", source="day_report")
        return points
    finally:
        with (output / "energy_daily.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=["date", "consumption_kwh", "status", "source", "partial"], delimiter=";")
            writer.writeheader()
            writer.writerows(rows.values())
        known = [row for row in rows.values() if row["consumption_kwh"] != ""]
        total = sum((Decimal(row["consumption_kwh"]) for row in known), Decimal(0))
        summary["daily"] = {"csv": "energy_daily.csv", "calendar_days": len(rows),
                            "days_with_data": len(known), "days_without_data": len(rows) - len(known),
                            "partial_days": sum(row["status"] == "partial" for row in known),
                            "known_consumption_kwh": str(total.quantize(Decimal("0.01"))) if known else None,
                            "range_complete": len(known) == len(rows) and not any(row["partial"] for row in known)}
        print(f"Napi bontás: {len(rows)} nap, {len(known)} naphoz adat, {len(rows) - len(known)} naphoz nincs adat.")
        if known:
            print(f"A rendelkezésre álló adatok összege: {summary['daily']['known_consumption_kwh']} kWh.")
        print("Napi CSV: energy_daily.csv; a mai nap részleges, a hiányzó értékek üresen maradnak.")


def select_device(devices, requested=None, non_interactive=False):
    if requested:
        matches = [d for d in devices if requested in (d.get("endpointId"), d.get("friendlyName"))]
        if len(matches) != 1:
            raise ProbeError("A --device érték nem jelöl ki pontosan egy eszközt.")
        return matches[0]
    if len(devices) == 1:
        return devices[0]
    if non_interactive:
        raise ProbeError("Több eszköz található. Add meg az ACFREEDOM_DEVICE változót (eszköznév vagy endpointId).")
    while True:
        choice = input(f"Eszköz sorszáma [1–{len(devices)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1]
        print("Érvénytelen sorszám.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AC Freedom / AUX Cloud energia diagnosztika")
    parser.add_argument("--region", choices=SERVERS, default=os.environ.get("ACFREEDOM_REGION") or "eu")
    parser.add_argument("--kind", choices=("email", "phone"), default=os.environ.get("ACFREEDOM_KIND") or None)
    parser.add_argument("--username", default=os.environ.get("ACFREEDOM_USERNAME") or None)
    parser.add_argument("--device", default=os.environ.get("ACFREEDOM_DEVICE") or None, help="Eszköznév vagy endpointId")
    parser.add_argument("--start", default=os.environ.get("ACFREEDOM_START") or None, help="Kezdő dátum: YYYY-MM-DD")
    parser.add_argument("--end", default=os.environ.get("ACFREEDOM_END") or None, help="Záró dátum: YYYY-MM-DD, a teljes napot beleértve")
    parser.add_argument("--non-interactive", action="store_true", help="Kérdések nélkül; belépési adatok környezeti változókból")
    parser.add_argument("--timezone", default="Europe/Budapest")
    parser.add_argument("--server-timezone", default="Asia/Shanghai")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--list-only", action="store_true", help="Csak belépés és eszközlista")
    parser.add_argument("--view", choices=("daily", "all", "year", "month", "day"), default="daily")
    parser.add_argument("--legacy-probe", action="store_true", help="Régi kísérleti BroadLink lekérdezések")
    args = parser.parse_args(argv)
    if args.start and args.end:
        interval(args.start, args.end, args.timezone, args.server_timezone)
    client = AuxCloud(args.region)
    output = args.output / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True)
    summary = {"version": 3, "server": client.url, "experimental_energy_api": args.legacy_probe,
               "local_timezone": args.timezone, "server_timezone_assumption": args.server_timezone,
               "queries": [], "candidate_points": 0, "energy_kwh_verified": False}
    print("AC Freedom Energy Probe v3 — automatikus napi fogyasztás\n"
          "Az AC Freedom fogyasztási képernyőjéből azonosított AUX lekérdezések.")
    try:
        username = args.username
        kind = args.kind
        password = os.environ.get("ACFREEDOM_PASSWORD")
        client.secrets.extend(value for value in (username, password) if value)
        if args.non_interactive and not username:
            raise ProbeError("Hiányzó ACFREEDOM_USERNAME környezeti változó.")
        if args.non_interactive and not password:
            raise ProbeError("Hiányzó ACFREEDOM_PASSWORD környezeti változó.")
        if not kind:
            if username:
                kind = "email" if "@" in username else "phone"
            else:
                while True:
                    entry = input("AC Freedom e-mail / telefonszám (vagy email/phone választás): ").strip()
                    if "@" in entry:
                        kind, username = "email", entry
                        break
                    if re.fullmatch(r"\+?[\d ()-]{6,}", entry):
                        kind, username = "phone", entry
                        break
                    if entry.lower() in ("", "email", "phone"):
                        kind = entry.lower() or "email"
                        break
                    print("E-mail-címet, telefonszámot, vagy az email / phone szót add meg.")
        if kind not in ("email", "phone"):
            raise ProbeError("A belépés típusa email vagy phone lehet.")
        username = username or input("AC Freedom e-mail / telefonszám: ").strip()
        if not username:
            raise ProbeError("Hiányzó fiókazonosító.")
        password = password or getpass.getpass("AC Freedom jelszó: ")
        if not password:
            raise ProbeError("Hiányzó jelszó.")
        print("Bejelentkezés...", flush=True)
        client.login(username, password, kind)
        del password
        summary["login"] = "ok"
        devices, warnings = client.discover()
        summary.update(device_count=len(devices), discovery_warnings=warnings)
        save_json(output / "devices.json", devices, client.secrets)
        for warning in warnings:
            print(f"Eszközfelderítési figyelmeztetés: {warning}")
        if not devices:
            raise ProbeError("Nincs elérhető eszköz a fiókban. Ellenőrizd a régiót és a fiókot.")
        print("Talált eszközök:")
        for index, device in enumerate(devices, 1):
            print(f"{index}. {device.get('friendlyName', 'Névtelen')}"
                  f" | productId={device.get('productId', '?')}"
                  f" | endpoint=…{str(device['endpointId'])[-8:]}")
        if args.list_only:
            summary["outcome"] = "inventory_only"
            return 0
        device = select_device(devices, args.device, args.non_interactive)
        summary["selected_device"] = {k: device.get(k) for k in ("productId", "shared")}
        today = datetime.now(ZoneInfo(args.timezone)).date()
        default_start, default_end = f"{today.year}-01-01", today.isoformat()
        if args.legacy_probe:
            start = args.start or (default_start if args.non_interactive else input(f"Kezdő dátum [{default_start}]: ").strip() or default_start)
            end = args.end or (default_end if args.non_interactive else input(f"Záró dátum [{default_end}]: ").strip() or default_end)
            first, last = interval(start, end, args.timezone, args.server_timezone)
            summary.update(start_local=start, end_local=end, start_server=first, end_server=last)
            points = legacy_probe(client, device, first, last, output, summary)
        elif args.view == "daily":
            start = args.start or INSTALLATION_DATE
            end = args.end or default_end
            print(f"Automatikus időszak: {start} → {end} (napi bontás).")
            points = daily_history(client, device, start, end, output, summary)
        else:
            end = args.end or default_end
            date.fromisoformat(end)
            summary.update(selected_date=end, view=args.view, date_handling="local calendar strings; no timezone shift")
            if args.start:
                print("Az AUX nézetek teljes évet/hónapot/napot kérnek a vizsgált dátum alapján; --start csak --legacy-probe mellett érvényes.")
            points = probe(client, device, end, output, summary, args.view)
        if points:
            safe_points = scrub(points, client.secrets)
            with (output / "energy_points.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(safe_points[0]), delimiter=";")
                writer.writeheader()
                writer.writerows(safe_points)
            summary["outcome"] = "candidate_data_requires_verification" if args.legacy_probe else "aux_energy_data_requires_app_comparison"
            print("Az eredményt hasonlítsd össze az AC Freedom megfelelő nap/hónap/év nézetével.")
            return 0
        summary["outcome"] = "no_recognized_energy_data"
        if summary["queries"] and all(
            query["outcome"] == "server_returned_no_records" for query in summary["queries"]
        ):
            summary["outcome"] = "all_history_queries_returned_empty"
            print("Minden elvégzett lekérdezés üres adatsort adott vissza.\n"
                  "A fogyasztás értéke ismeretlen; az appban látható adatot nem sikerült lekérni.\n"
                  "Az AC Freedom tényleges energia-riportját és kérését kell azonosítani.")
        else:
            print("Nem találtunk felismerhető energiaadatot. Ez nem jelent 0 kWh fogyasztást.")
        return 2
    except (ProbeError, ValueError, ZoneInfoNotFoundError) as exc:
        summary.update(outcome="error", error=str(exc))
        print(f"Hiba: {scrub(str(exc), client.secrets)}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        summary["outcome"] = "interrupted"
        print("\nA futás megszakadt.")
        return 130
    finally:
        save_json(output / "summary.json", summary, client.secrets)
        client.session.close()
        print(f"Eredmények: {output.resolve()}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, ZoneInfoNotFoundError) as exc:
        print(f"Indítási hiba: {exc}", file=sys.stderr)
        sys.exit(1)
