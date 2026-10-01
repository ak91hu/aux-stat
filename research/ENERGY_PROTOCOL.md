# AC Freedom energy protocol findings

Analyzed package: `com.broadlink.acfreedom`, APKPure download listing
`4.1.3.b415004f`, downloaded 2026-10-01 for static analysis only.

Download listing: https://apkpure.net/ac-freedom/com.broadlink.acfreedom/download
Play listing: https://play.google.com/store/apps/details?id=com.broadlink.acfreedom

APK SHA-256: `603e80cae7f31e22c07a5a0ff4821a80f102e04215628ee7a6eb704329066ba8`

The APK contains the matching product resource archive:
`assets/config/product_res/000000000000000000000000c0620000.zip`.

Request implementation: `zh-cn/assets/index-783e54f7.js`, function `rQ`, log label
`queryPower`. Request transport: function `on`; regional app host comes from the
native initialization response. HTTP method POST, JSON body, route:
`/appfront/v1/webapi/device/stats`.

Headers populated by the UI transport: `userId`, `loginsession`, `familyId`.
The UI's deviceID is the selected device identifier.

Request fields:

| Field | Value |
| --- | --- |
| report, year | fw_auxoverseayearconsum_v1 |
| report, month | fw_auxoverseamonthconsum_v1 |
| report, day | fw_auxoverseadayconsum_v1 |
| querytype | stats |
| devtype, outer | numeric 43715 |
| heartbeatHost | empty string |
| device[].did | selected device ID |
| device[].devtype | string 43715 |
| device[].offset | 0 |
| device[].step | 1000 |
| device[].params | list containing tenelec |
| device[].sortk | occurtime |
| device[].reportType | year / month / day |

Annual start is literally `YYYY-01-00_00:00:00` in the app, annual end is December
31 at 23:59:59. Preserve the unusual day 00 instead of silently correcting it.
Monthly/daily dates cover the selected complete calendar month/day. The app
formats these from local date components with no UTC+8 conversion in queryPower.

Response/display implementation: `zh-cn/assets/index-92d612f1.js`, consumption
curve component. It reads the first table's `values`, assigns `tenelec` to buckets
by `occurtime` (hour for day, day for month, month for year), and sums the buckets
with two decimal places for the displayed total. Axis and series units are kWh.
There is no 0.03125 conversion in this chart even though the separate device
profile describes such a unit for a live parameter. Do not apply that conversion
to these history report values.

`table[].total` is not used as the displayed consumption; the displayed total
comes from the bucket sum. Repeated buckets use the last assigned value.

The app limits visible buckets for the current date: completed hours for today,
completed days for this month, and months up to the current month for this year.
The Python implementation filters dates to the selected period and also applies
these visible-bucket limits when calculating the displayed totals.

Static analysis identifies the protocol. Live retrieval and agreement with the
user's application were subsequently observed for September 2026: the captured
monthly response contains 15 daily values (September 16–30), summing to
43.65625 kWh, and the annual response contains the same value with timestamp
`2026-09-00_00:00:00`. The parser therefore accepts day 00 for annual monthly
buckets. The September 30 daily response also contains hourly values.
The default v3 range export fills calendar dates, uses monthly daily totals,
queries missing daily records, and marks today's hourly sum as partial.

The APK is kept as a research
input, is not installed/executed, and is not redistributed in the Windows ZIP.
No delete/configuration/synchronization operation is called.
