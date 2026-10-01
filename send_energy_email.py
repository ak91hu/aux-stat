"""Send the AUX daily CSV and a Hungarian summary using authenticated TLS SMTP."""
from __future__ import annotations

import argparse
import csv
import json
import io
import math
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from statistics import median


def measurement(row):
    try:
        value = Decimal(row.get("consumption_kwh", ""))
        return value if value.is_finite() and value >= 0 else None
    except InvalidOperation:
        return None


def display_number(value):
    return f"{Decimal(str(value)):.2f}".replace(".", ",") if value is not None else "—"


def daily_statistics(rows):
    complete = [value for row in rows
                if row.get("status") == "reported" and (value := measurement(row)) is not None]
    if not complete:
        return None, None, None
    return sum(complete) / len(complete), median(complete), max(complete)


def chart_png(rows):
    """Render locally: the email never needs a public chart URL or external service."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D

    visible = rows[-31:]
    figure = Figure(figsize=(9, 3.6), dpi=140, facecolor="white")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    for index, row in enumerate(visible):
        value = measurement(row)
        if value is None:
            axis.plot(index, 0, marker="x", color="#94a3b8", markersize=7, clip_on=False)
        else:
            color = "#b87932" if row.get("status") == "partial" else "#39746b"
            axis.bar(index, float(value), width=0.65, color=color, zorder=3)
            if value == 0:
                axis.plot(index, 0, marker="o", color=color, markersize=4, clip_on=False)
    axis.set_ylabel("Fogyasztás (kWh)", color="#475569")
    step = max(1, math.ceil(len(visible) / 12))
    ticks = list(range(0, len(visible), step))
    if visible and len(visible) - 1 not in ticks:
        ticks.append(len(visible) - 1)
    axis.set_xticks(ticks, [visible[i]["date"][5:].replace("-", ".") for i in ticks], rotation=35, ha="right")
    axis.set_xlim(-0.7, max(0.7, len(visible) - 0.3))
    axis.set_ylim(bottom=0)
    axis.grid(axis="y", color="#e2e8f0", zorder=0)
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        axis.spines[side].set_color("#cbd5e1")
    axis.tick_params(colors="#475569", labelsize=9)
    axis.legend(handles=[Patch(color="#39746b", label="Jelentett napi adat"),
                         Patch(color="#b87932", label="Részleges nap"),
                         Line2D([], [], marker="x", linestyle="none", color="#94a3b8", label="Hiányzó adat (nem 0)")],
                loc="upper center", bbox_to_anchor=(0.5, 1.2), ncol=3, frameon=False, fontsize=8)
    figure.tight_layout()
    stream = io.BytesIO()
    figure.savefig(stream, format="png", facecolor="white")
    return stream.getvalue()


def html_report(rows, daily, total, chart_id, workflow_url, workflow_status):
    average, daily_median, peak = daily_statistics(rows)
    period = f"{rows[0]['date']} – {rows[-1]['date']}" if rows else "Nem áll rendelkezésre napi adatsor"
    metrics = (("Ismert fogyasztás", total), ("Napi átlag", average),
               ("Napi medián", daily_median), ("Legnagyobb napi érték", peak))
    metric_cells = [
        f'<td width="50%" valign="top" style="padding:18px 14px;border:1px solid #d9dfda;'
        f'background:{"#edf3ee" if index == 0 else "#fafbf9"};">'
        f'<div style="font-size:12px;color:#626963;line-height:1.5;">{title}</div>'
        f'<div style="margin-top:10px;font-size:30px;color:#252b27;font-variant-numeric:tabular-nums;'
        f'white-space:nowrap;line-height:1.2;"><b>{display_number(value)}</b>'
        f' <span style="font-size:13px;font-weight:normal;color:#626963;">kWh</span></div></td>'
        for index, (title, value) in enumerate(metrics)]
    statistics_rows = "".join(f'<tr>{"".join(metric_cells[index:index + 2])}</tr>'
                              for index in range(0, len(metric_cells), 2))
    labels = {"reported": ("Jelentett", "#626963"), "partial": ("Részleges", "#946025"),
              "no_data": ("Nincs adat", "#626963"), "query_error": ("Lekérdezési hiba", "#a33c32")}
    table_rows = []
    for row in rows:
        label, color = labels.get(row.get("status"), ("Ismeretlen", "#626963"))
        table_rows.append(f'<tr>'
                          f'<td style="padding:10px 4px;border-bottom:1px solid #e5e7e4;">{escape(row["date"])}</td>'
                          f'<td align="right" style="padding:10px 4px;border-bottom:1px solid #e5e7e4;font-variant-numeric:tabular-nums;">{display_number(measurement(row))}</td>'
                          f'<td style="padding:10px 4px;border-bottom:1px solid #e5e7e4;color:{color};font-size:12px;">{label}</td></tr>')
    notes = "A mai nap részleges lehet. A hiányzó mérés nem jelent nulla fogyasztást."
    if total is None:
        notes = "A fogyasztás ismeretlen; nem sikerült mérési eredményt lekérni. Ez nem jelent 0 kWh-t."
    status = {"success": "Sikeres", "failure": "Hibás", "cancelled": "Megszakított"}.get(workflow_status, "Ismeretlen")
    graph = (f'<h2 style="font-size:18px;margin:28px 0 12px;">Napi fogyasztás</h2>'
             f'<p style="font-size:12px;color:#64748b;">{escape(rows[-31:][0]["date"])} – {escape(rows[-1]["date"])}'
             f'{" • a legutóbbi 31 nap" if len(rows) > 31 else ""}</p>'
             f'<img src="cid:{chart_id}" width="600" alt="Napi fogyasztás oszlopdiagram; a részleges napok barnák. A pontos értékek az alábbi táblázatban szerepelnek." '
             f'style="display:block;width:100%;max-width:600px;height:auto;">') if chart_id else ""
    return f'''<!DOCTYPE html><html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#f5f5f2;font-family:Arial,Helvetica,sans-serif;color:#252b27;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#ffffff;">
<tr><td style="padding:28px 20px 20px;border-top:3px solid #39746b;"><div style="font-size:12px;color:#626963;">AUX / Fogyasztási riport</div>
<h1 style="margin:10px 0;font-size:28px;font-weight:normal;">Klíma energiafogyasztás</h1><div style="color:#626963;font-size:14px;">{escape(period)}</div></td></tr>
<tr><td style="padding:0 14px 20px;"><table role="presentation" aria-label="Fogyasztási összesítés" width="100%" cellspacing="6" cellpadding="0" style="font-size:14px;"><tbody>{statistics_rows}</tbody></table>
<p style="color:#626963;font-size:12px;line-height:1.6;">Az összeg a részleges napokat is tartalmazza. Az átlag, a medián és a maximum csak a teljes, jelentett napokból készül.</p>
<p style="color:#626963;font-size:13px;line-height:1.6;">{notes}<br>
Adattal: <b>{escape(str(daily.get('days_with_data', 0)))}</b> nap · Hiányzó: <b>{escape(str(daily.get('days_without_data', '?')))}</b> nap · Részleges: <b>{escape(str(daily.get('partial_days', 0)))}</b> nap</p>
{graph}<h2 style="font-size:18px;margin:28px 0 12px;">Napi részletek</h2>
<table width="100%" cellspacing="0" cellpadding="0" style="font-size:14px;"><thead><tr style="color:#626963;">
<th scope="col" align="left" style="padding:10px 4px;border-bottom:1px solid #b9c1bb;">Dátum</th><th scope="col" align="right" style="padding:10px 4px;border-bottom:1px solid #b9c1bb;">kWh</th><th scope="col" align="left" style="padding:10px 4px;border-bottom:1px solid #b9c1bb;">Adat állapota</th>
</tr></thead><tbody>{''.join(table_rows) or '<tr><td colspan="3" style="padding:12px;">Nincs elérhető napi adat.</td></tr>'}</tbody></table>
<p style="margin-top:24px;font-size:12px;color:#626963;line-height:1.6;">Futás állapota: {status}. A teljes napi adatsor a csatolt CSV-ben található, ha készült export.
Ha a leveleződ nem jeleníti meg a grafikont, a táblázatban minden érték olvasható.</p></td></tr></table></td></tr></table></body></html>'''


def build_message(output, sender, recipient, workflow_url="", workflow_status="unknown"):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    candidates = sorted(Path(output).glob("*/summary.json"))
    summary_path = candidates[-1] if candidates else None
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path else {}
    daily = summary.get("daily", {})
    csv_path = summary_path.parent / "energy_daily.csv" if summary_path else None
    rows = []
    if csv_path and csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
    total = daily.get("known_consumption_kwh")
    message["Subject"] = (f"AUX fogyasztási riport • {display_number(total)} kWh" if total is not None
                          else "AUX fogyasztási riport — nincs mérési eredmény")
    lines = ["AUX klíma — napi fogyasztás", ""]
    if rows:
        lines.append(f"Időszak: {rows[0]['date']} – {rows[-1]['date']}")
    lines.append(f"Rendelkezésre álló fogyasztás összege: {total} kWh" if total is not None
                 else "A fogyasztás ismeretlen; ez nem jelent 0 kWh-t.")
    average, daily_median, peak = daily_statistics(rows)
    lines.extend([f"Napi átlag: {display_number(average)} kWh",
                  f"Napi medián: {display_number(daily_median)} kWh",
                  f"Legnagyobb napi érték: {display_number(peak)} kWh",
                  "Az átlag, a medián és a maximum csak a teljes, jelentett napokból készül."])
    if daily:
        lines.extend([f"Adattal rendelkező napok: {daily.get('days_with_data', '?')}",
                      f"Hiányzó napok: {daily.get('days_without_data', '?')}",
                      f"Részleges napok: {daily.get('partial_days', '?')}"])
    lines.extend([f"Workflow állapota: {workflow_status}",
                  f"Lekérdezés eredménye: {summary.get('outcome', 'nem készült összegzés')}", ""])
    if rows:
        lines.append("Napi bontás (a részleges és hiányzó napok külön jelölve):")
        labels = {"reported": "jelentett", "partial": "részleges", "no_data": "nincs adat", "query_error": "lekérdezési hiba"}
        for row in rows:
            value = f"{row['consumption_kwh']} kWh" if row['consumption_kwh'] else "ismeretlen"
            lines.append(f"{row['date']}: {value} ({labels.get(row['status'], row['status'])})")
    body = "\n".join(lines) + "\n"
    message.set_content(body)
    chart_id = make_msgid(domain="aux-stat.local")[1:-1] if any(measurement(row) is not None for row in rows) else None
    message.add_alternative(html_report(rows, daily, total, chart_id, workflow_url, workflow_status), subtype="html")
    if chart_id:
        message.get_payload()[-1].add_related(chart_png(rows), maintype="image", subtype="png",
                                            cid=f"<{chart_id}>", disposition="inline", filename="daily_consumption.png")
    if csv_path and csv_path.exists():
        message.add_attachment(csv_path.read_bytes(), maintype="text", subtype="csv", filename="energy_daily.csv")
    return message


def send_message(message, host, port, username, password):
    context = ssl.create_default_context()
    if port == 465:
        connection = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    elif port == 587:
        connection = smtplib.SMTP(host, port, timeout=30)
    else:
        raise ValueError("SMTP_PORT csak 465 (TLS) vagy 587 (STARTTLS) lehet.")
    with connection as server:
        if port == 587:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(username, password)
        refused = server.send_message(message)
        if refused:
            raise ValueError("Az SMTP-szerver nem fogadta el minden címzett címét.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AUX fogyasztási riport küldése e-mailben")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    required = ("EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        print("Hiányzó e-mail-beállítások: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        message = build_message(args.output, os.environ["EMAIL_FROM"], os.environ["EMAIL_TO"],
                                os.environ.get("WORKFLOW_URL", ""), os.environ.get("WORKFLOW_STATUS", "unknown"))
        send_message(message, os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or "587"),
                     os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
    except (OSError, ValueError, smtplib.SMTPException):
        # SMTP replies can echo account details; never copy them into CI logs.
        print("Az e-mail küldése sikertelen. Ellenőrizd az SMTP-beállításokat és a szolgáltatói hozzáférést.", file=sys.stderr)
        return 1
    print("A fogyasztási riport e-mailben elküldve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
