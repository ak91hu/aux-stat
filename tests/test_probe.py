import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import acfreedom_energy_probe as p


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


def sample():
    # Shape published in the Smart Plug Mini HTTP capture, values synthetic.
    return {"status": 0, "msg": "ok", "table": [{"did": "private-device",
            "total": 2, "cnt": 2, "values": [
                {"elec": 43.66, "occurtime": "2026-09-01_08:00:00"},
                {"elec": 43.70, "occurtime": "2026-09-01_09:00:00"}]}]}


class ProtocolTests(unittest.TestCase):
    def test_login_encrypted_payload_and_headers(self):
        for kind in ("email", "phone"):
            with self.subTest(kind=kind):
                session = Mock()
                session.post.return_value = response({"status": 0, "userid": "user-id", "loginsession": "secret-session"})
                client = p.AuxCloud(session=session)
                with patch.object(p.time, "time", return_value=1720000000.5):
                    client.login("my-account", "my-password", kind)
                url = session.post.call_args.args[0]
                kwargs = session.post.call_args.kwargs
                self.assertEqual(url, p.SERVERS["eu"] + "/account/login")
                key = hashlib.md5((kwargs["headers"]["timestamp"] + p.TIMESTAMP_TOKEN_ENCRYPT_KEY).encode()).digest()
                decryptor = Cipher(algorithms.AES(key), modes.CBC(p.AES_INITIAL_VECTOR)).decryptor()
                plain = (decryptor.update(kwargs["data"]) + decryptor.finalize()).rstrip(b"\0")
                body = json.loads(plain)
                self.assertEqual(body[kind], "my-account")
                self.assertNotIn("my-password", plain.decode())
                self.assertEqual(body["password"], hashlib.sha1(("my-password" + p.PASSWORD_ENCRYPT_KEY).encode()).hexdigest())
                self.assertEqual(kwargs["headers"]["token"], hashlib.md5(plain + p.BODY_ENCRYPT_KEY.encode()).hexdigest())
                self.assertEqual(client.loginsession, "secret-session")
                self.assertFalse(kwargs["allow_redirects"])

    def test_bad_login_response(self):
        session = Mock()
        session.post.return_value = response({"status": 0})
        with self.assertRaises(p.ProbeError):
            p.AuxCloud(session=session).login("account", "password")

    def test_http_and_cloud_stop_conditions(self):
        for status in (401, 403, 429, 503):
            session = Mock()
            session.post.return_value = response({}, status)
            with self.assertRaises(p.ProbeError) as caught:
                p.AuxCloud(session=session).request("some/query")
            self.assertTrue(caught.exception.stop)
        for code in (-1036, -30129):
            session = Mock()
            session.post.return_value = response({"status": str(code)})
            with self.assertRaises(p.ProbeError) as caught:
                p.AuxCloud(session=session).request("some/query", check=False)
            self.assertTrue(caught.exception.stop)

    def test_invalid_json_and_timeout(self):
        session = Mock()
        result = response({})
        result.json.side_effect = ValueError("not json")
        session.post.return_value = result
        with self.assertRaises(p.ProbeError):
            p.AuxCloud(session=session).request("some/query")
        session.post.side_effect = requests.Timeout("password-leak")
        with self.assertRaises(p.ProbeError) as caught:
            p.AuxCloud(session=session).request("some/query")
        self.assertNotIn("password-leak", str(caught.exception))

    def test_own_and_shared_inventory_deduplicates(self):
        session = Mock()
        own = {"endpointId": "my-device", "friendlyName": "Aura", "devSession": "device-secret"}
        other = {"endpointId": "shared-device", "friendlyName": "Another"}
        session.post.side_effect = [
            response({"status": 0, "data": {"familyList": [{"familyid": "family-secret"}]}}),
            response({"status": 0, "data": {"endpoints": [own]}}),
            response({"status": 0, "data": {"shareFromOther": [{"devinfo": own}, {"devinfo": other}]}}),
        ]
        client = p.AuxCloud(session=session)
        devices, warnings = client.discover()
        self.assertEqual(len(devices), 2)
        self.assertEqual(warnings, [])
        self.assertEqual(devices[1]["familyId"], "family-secret")
        self.assertTrue(devices[1]["shared"])
        self.assertIn("device-secret", client.secrets)
        self.assertEqual(session.post.call_args.kwargs["headers"]["familyid"], "family-secret")


class DataTests(unittest.TestCase):
    def test_captured_annual_month_day_zero_is_consumption(self):
        result = json.loads((Path(__file__).parent / "fixtures/aux_year_2026_09.json").read_text())
        points = p.aux_period_points(result, "2026-09-30", "year", datetime(2026, 10, 1))
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["value"], 43.65625)

    def test_aux_app_request_report_dates_and_parameters(self):
        device = {"endpointId": "device"}
        year = p.aux_history_body(device, "2026-10-01", "year")
        self.assertEqual(year["report"], "fw_auxoverseayearconsum_v1")
        self.assertEqual(year["devtype"], 43715)
        self.assertEqual(year["device"][0]["start"], "2026-01-00_00:00:00")
        self.assertEqual(year["device"][0]["params"], ["tenelec"])
        self.assertEqual(year["device"][0]["step"], 1000)
        month = p.aux_history_body(device, "2024-02-01", "month")
        self.assertEqual(month["device"][0]["end"], "2024-02-29_23:59:59")
        day = p.aux_history_body(device, "2026-10-01", "day")
        self.assertEqual(day["device"][0]["start"], "2026-10-01_00:00:00")

    def test_aux_ui_bucket_values_not_total_metadata(self):
        result = {"status": 0, "table": [{"total": 999, "values": [
            {"occurtime": "2026-01-01_00:00:00", "tenelec": 20},
            {"occurtime": "2026-09-01_00:00:00", "tenelec": 23},
            {"occurtime": "2026-09-02_00:00:00", "tenelec": 23.66},
            {"occurtime": "2025-01-01_00:00:00", "tenelec": 1000}]}]}
        points = p.aux_period_points(result, "2026-10-01", "year")
        self.assertEqual([v["value"] for v in points], [20, 23.66])
        self.assertTrue(all(v["unit"] == "kWh" for v in points))
        self.assertEqual(p.aux_period_points(result, "2026-10-01", "day"), [])

    def test_current_month_excludes_today_as_app_does(self):
        result = {"table": [{"values": [
            {"occurtime": "2026-10-01_00:00:00", "tenelec": 4},
            {"occurtime": "2026-10-02_00:00:00", "tenelec": 9}]}]}
        points = p.aux_period_points(result, "2026-10-02", "month", datetime(2026, 10, 2, 11))
        self.assertEqual([p["value"] for p in points], [4])
        self.assertEqual(p.aux_period_points(result, "2026-10-01", "month", datetime(2026, 10, 1, 11)), [])

    def test_empty_server_response_is_not_zero_consumption(self):
        payload = {"status": 0, "msg": "ok", "table": [
            {"did": "private-device", "total": 0, "cnt": 0, "values": None}]}
        self.assertTrue(p.empty_history(payload))
        self.assertFalse(p.empty_history(sample()))
        self.assertFalse(p.empty_history({"status": 0}))
        self.assertEqual(p.extract_points(payload, "elec"), [])

    def test_real_broadlink_shape_and_zero_measurement(self):
        points = p.extract_points(sample(), "elec")
        self.assertEqual([v["value"] for v in points], [43.66, 43.70])
        self.assertEqual(points[0]["unit"], "unknown")
        self.assertEqual(p.extract_points({"occurtime": "2026-09-01_08:00:00", "power": 0}, "power")[0]["value"], 0)

    def test_status_metadata_is_not_energy(self):
        self.assertEqual(p.extract_points({"status": 0, "total": 4366, "data": {"did": 23, "energy": 43.66}}, "elec"), [])
        self.assertEqual(p.extract_points({"time": "2026-09-01_00:00:00", "status": 0}, "elec"), [])

    def test_invalid_numeric_measurements_are_skipped(self):
        for value in (True, False, "NaN", "inf", None, [43.66], {"status": 0}):
            self.assertEqual(p.extract_points({"occurtime": "2026-09-01_08:00:00", "elec": value}, "elec"), [])

    def test_timestamp_map(self):
        result = p.extract_points({"values": {"2026-09-01_08:00:00": "43.66"}}, "elec")
        self.assertEqual(result[0]["value"], 43.66)

    def test_budapest_dst_conversion(self):
        self.assertEqual(p.interval("2026-01-01", "2026-01-01", "Europe/Budapest", "Asia/Shanghai"),
                         ("2026-01-01_07:00:00", "2026-01-02_06:59:59"))
        self.assertEqual(p.interval("2026-09-01", "2026-09-01", "Europe/Budapest", "Asia/Shanghai"),
                         ("2026-09-01_06:00:00", "2026-09-02_05:59:59"))
        with self.assertRaises(ValueError):
            p.interval("2026-10-01", "2026-09-01", "Europe/Budapest", "Asia/Shanghai")

    def test_scrub_nested_keys_and_echoed_secrets(self):
        safe = p.scrub({"data": [{"devSession": "session-value", "did": "device-value",
                       "cookie": "cookie-value"}], "msg": "echo session-value",
                       "points": 3}, ["session-value"])
        serialized = json.dumps(safe)
        for secret in ("session-value", "device-value", "cookie-value"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(safe["points"], 3)


class RunTests(unittest.TestCase):
    def setUp(self):
        # CI secrets must never affect synthetic unit tests.
        environment = patch.dict(p.os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_non_interactive_secrets_login_without_prompts(self):
        session = Mock()
        session.post.side_effect = self.bootstrap()
        client = p.AuxCloud(session=session)
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "AuxCloud", return_value=client), \
                patch.dict(p.os.environ, {"ACFREEDOM_USERNAME": "ci@example.com", "ACFREEDOM_PASSWORD": "ci-secret-password"}), \
                patch("builtins.input", side_effect=AssertionError("CI must not prompt")), \
                patch.object(p.getpass, "getpass", side_effect=AssertionError("CI must not prompt")), \
                contextlib.redirect_stdout(io.StringIO()) as log:
            code = p.main(["--non-interactive", "--list-only", "--output", temp])
            files = "".join(f.read_text() for f in Path(temp).rglob("*.json"))
        self.assertEqual(code, 0)
        self.assertEqual(session.post.call_count, 4)
        for secret in ("ci@example.com", "ci-secret-password", "private-session"):
            self.assertNotIn(secret, files + log.getvalue())

    def test_missing_ci_secrets_fail_before_any_request(self):
        for environment, missing in (({}, "ACFREEDOM_USERNAME"),
                                     ({"ACFREEDOM_USERNAME": "ci@example.com"}, "ACFREEDOM_PASSWORD")):
            session = Mock()
            client = p.AuxCloud(session=session)
            with tempfile.TemporaryDirectory() as temp, patch.object(p, "AuxCloud", return_value=client), \
                    patch.dict(p.os.environ, environment, clear=True), \
                    patch("builtins.input", side_effect=AssertionError("CI must not prompt")), \
                    patch.object(p.getpass, "getpass", side_effect=AssertionError("CI must not prompt")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = p.main(["--non-interactive", "--output", temp])
                summary = json.loads(next(Path(temp).rglob("summary.json")).read_text())
            self.assertEqual(code, 1)
            self.assertIn(missing, summary["error"])
            session.post.assert_not_called()

    def test_ci_requires_unambiguous_device(self):
        devices = [{"friendlyName": "Aura", "endpointId": "one"},
                   {"friendlyName": "Bedroom", "endpointId": "two"}]
        with patch("builtins.input", side_effect=AssertionError("CI must not prompt")):
            with self.assertRaisesRegex(p.ProbeError, "ACFREEDOM_DEVICE"):
                p.select_device(devices, non_interactive=True)
            self.assertEqual(p.select_device(devices, "Bedroom", non_interactive=True), devices[1])

    def test_default_daily_range_starts_at_installation_and_includes_partial_today(self):
        month = json.loads((Path(__file__).parent / "fixtures/aux_month_2026_09.json").read_text())
        empty = {"status": 0, "msg": "ok", "table": [{"total": 0, "values": None}]}
        partial = {"status": 0, "msg": "ok", "table": [{"values": [
            {"occurtime": "2026-10-01_09:00:00", "tenelec": 0.5}]}]}
        session = Mock()
        session.post.side_effect = self.bootstrap() + [response(month), response(partial)]
        client = p.AuxCloud(session=session)
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "AuxCloud", return_value=client), \
                patch.object(p.getpass, "getpass", return_value="test-password"), \
                patch("builtins.input", side_effect=AssertionError("No date prompt expected")), \
                patch.object(p.time, "sleep"), patch.object(p, "datetime", wraps=datetime) as clock, \
                contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = datetime(2026, 10, 1, 12, tzinfo=ZoneInfo("Europe/Budapest"))
            code = p.main(["--kind", "email", "--username", "test-account", "--output", temp])
            output = next(Path(temp).iterdir())
            with (output / "energy_daily.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            summary = json.loads((output / "summary.json").read_text())
            self.assertTrue((output / "raw/2026-09_aux_month.json").exists())
            self.assertTrue((output / "raw/2026-10-01_aux_day.json").exists())
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["date"], "2026-09-16")
        self.assertEqual(rows[0]["consumption_kwh"], "0.03125")
        self.assertEqual(rows[0]["status"], "reported")
        self.assertEqual(rows[-1]["date"], "2026-10-01")
        self.assertEqual(rows[-1]["consumption_kwh"], "0.5")
        self.assertEqual(rows[-1]["status"], "partial")
        self.assertEqual(summary["daily"]["days_with_data"], 16)
        self.assertEqual(summary["daily"]["known_consumption_kwh"], "44.16")
        self.assertFalse(summary["daily"]["range_complete"])
        self.assertEqual(summary["daily"]["days_without_data"], 0)
        self.assertEqual(len(summary["queries"]), 2)

    def test_daily_rate_limit_preserves_all_calendar_rows(self):
        client = Mock(userid="user", loginsession="session", secrets=[])
        client.request.side_effect = p.ProbeError("Too many requests", stop=True)
        summary = {"queries": [], "local_timezone": "Europe/Budapest"}
        with tempfile.TemporaryDirectory() as temp, patch.object(p.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            output = Path(temp)
            points = p.daily_history(client, {"endpointId": "dev", "familyId": "family"},
                                     "2026-09-01", "2026-09-30", output, summary)
            with (output / "energy_daily.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
        self.assertEqual(points, [])
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(row["consumption_kwh"] == "" for row in rows))
        self.assertTrue(summary["stopped_early"])
        client.request.assert_called_once()

    def test_email_entered_at_first_prompt_logs_in(self):
        session = Mock()
        session.post.side_effect = self.bootstrap()
        client = p.AuxCloud(session=session)
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "AuxCloud", return_value=client), \
                patch("builtins.input", side_effect=["someone@example.com"]) as prompt, \
                patch.object(p.getpass, "getpass", return_value="test-password"), \
                contextlib.redirect_stdout(io.StringIO()):
            code = p.main(["--list-only", "--output", temp])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(session.post.call_count, 4)
        self.assertIn("someone@example.com", client.secrets)

    def run_main(self, replies, legacy=True):
        session = Mock()
        session.post.side_effect = replies
        client = p.AuxCloud(session=session)
        with tempfile.TemporaryDirectory() as temp, patch.object(p, "AuxCloud", return_value=client), \
                patch.object(p.getpass, "getpass", return_value="test-password"), \
                patch.object(p.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            args = ["--kind", "email", "--username", "test-account", "--end", "2026-10-01", "--output", temp]
            if legacy:
                args.extend(["--start", "2026-09-01", "--legacy-probe"])
            else:
                args.extend(["--view", "all"])
            result = p.main(args)
            output = next(Path(temp).iterdir())
            files = {str(f.relative_to(output)): f.read_text(encoding="utf-8-sig")
                     for f in output.rglob("*") if f.is_file()}
        return result, files, session

    def bootstrap(self):
        return [response({"status": 0, "userid": "private-user", "loginsession": "private-session"}),
                response({"status": 0, "data": {"familyList": [{"familyid": "private-family"}]}}),
                response({"status": 0, "data": {"endpoints": [{"endpointId": "private-device",
                    "friendlyName": "Aura", "productId": "c0620000", "cookie": "private-cookie"}]}}),
                response({"status": 0, "data": {"shareFromOther": []}})]

    def test_full_diagnostic_produces_sanitized_json_and_csv(self):
        code, files, session = self.run_main(self.bootstrap() + [response(sample())] * 6)
        self.assertEqual(code, 0)
        self.assertIn("energy_points.csv", files)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["candidate_points"], 12)
        self.assertFalse(summary["energy_kwh_verified"])
        self.assertEqual(len(summary["queries"]), 6)
        for secret in ("test-password", "test-account", "private-session", "private-cookie", "private-device", "private-user", "private-family"):
            self.assertNotIn(secret, "".join(files.values()))
        energy_call = session.post.call_args_list[4]
        body = json.loads(energy_call.kwargs["data"])
        self.assertEqual(body["credentials"]["loginsession"], "private-session")
        self.assertEqual(body["device"][0]["did"], "private-device")

    def test_v2_requests_app_endpoint_and_exports_consumption(self):
        year = {"status": 0, "msg": "ok", "table": [{"total": 2, "values": [
            {"occurtime": "2026-01-01_00:00:00", "tenelec": 20},
            {"occurtime": "2026-09-01_00:00:00", "tenelec": 23.66}]}]}
        empty = {"status": 0, "msg": "ok", "table": [{"total": 0, "cnt": 0, "values": None}]}
        code, files, session = self.run_main(self.bootstrap() + [response(year), response(empty), response(empty)], legacy=False)
        self.assertEqual(code, 0)
        summary = json.loads(files["summary.json"])
        self.assertEqual(len(summary["queries"]), 3)
        self.assertEqual(summary["queries"][0]["total_kwh"], "43.66")
        self.assertEqual(summary["outcome"], "aux_energy_data_requires_app_comparison")
        self.assertFalse(summary["energy_kwh_verified"])
        self.assertIn("energy_points.csv", files)
        call = session.post.call_args_list[4]
        self.assertTrue(call.args[0].endswith("/appfront/v1/webapi/device/stats"))
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(call.kwargs["headers"]["userId"], "private-user")
        self.assertEqual(json.loads(call.kwargs["data"])["device"][0]["reportType"], "year")

    def test_api_error_does_not_create_false_csv(self):
        payload = {**sample(), "status": -7, "msg": "unsupported"}
        code, files, _ = self.run_main(self.bootstrap() + [response(payload)] * 6)
        self.assertEqual(code, 2)
        self.assertNotIn("energy_points.csv", files)
        self.assertEqual(json.loads(files["summary.json"])["candidate_points"], 0)

    def test_all_empty_history_responses_have_specific_diagnosis(self):
        payload = {"status": 0, "msg": "ok", "table": [
            {"did": "private-device", "total": 0, "cnt": 0, "values": None}]}
        code, files, _ = self.run_main(self.bootstrap() + [response(payload)] * 6)
        self.assertEqual(code, 2)
        self.assertNotIn("energy_points.csv", files)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["outcome"], "all_history_queries_returned_empty")
        self.assertTrue(all(q["outcome"] == "server_returned_no_records" for q in summary["queries"]))

    def test_rate_limit_stops_and_preserves_summary(self):
        code, files, session = self.run_main(self.bootstrap() + [response({}, 429)])
        self.assertEqual(code, 2)
        summary = json.loads(files["summary.json"])
        self.assertTrue(summary["stopped_early"])
        self.assertEqual(len(summary["queries"]), 1)
        self.assertEqual(session.post.call_count, 5)
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
