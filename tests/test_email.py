import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from unittest.mock import MagicMock, Mock, patch

import send_energy_email as mail


class EmailTests(unittest.TestCase):
    def test_report_has_daily_values_partial_and_missing_days_and_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "20261001_120000"
            output.mkdir()
            (output / "summary.json").write_text(json.dumps({
                "outcome": "aux_energy_data_requires_app_comparison",
                "daily": {"known_consumption_kwh": "1.50", "days_with_data": 2,
                          "days_without_data": 1, "partial_days": 1}}), encoding="utf-8")
            csv_bytes = ("date;consumption_kwh;status;source;partial\n"
                         "2026-09-29;1.0;reported;month_report;False\n"
                         "2026-09-30;;no_data;day_report;False\n"
                         "2026-10-01;0.5;partial;day_report;True\n").encode("utf-8-sig")
            (output / "energy_daily.csv").write_bytes(csv_bytes)
            message = mail.build_message(temp, "sender@example.com", "recipient@example.com",
                                         "https://github.com/test/actions/runs/1", "success")
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("1.50 kWh", body)
        self.assertIn("2026-09-30: ismeretlen (nincs adat)", body)
        self.assertIn("2026-10-01: 0.5 kWh (részleges)", body)
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "energy_daily.csv")
        self.assertEqual(attachments[0].get_payload(decode=True), csv_bytes)
        parsed = BytesParser(policy=default).parsebytes(message.as_bytes())
        html = parsed.get_body(preferencelist=("html",)).get_content()
        self.assertIn("1,50", html)
        self.assertIn("1,00", html)  # Average excludes the partial and missing day.
        images = [part for part in parsed.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(images), 1)
        self.assertIn("cid:" + images[0]["Content-ID"][1:-1], html)
        self.assertEqual(images[0].get_content_disposition(), "inline")
        self.assertTrue(images[0].get_payload(decode=True).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_missing_output_reports_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            message = mail.build_message(temp, "sender@example.com", "recipient@example.com", workflow_status="failure")
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("A fogyasztás ismeretlen", body)
        self.assertIn("failure", body)
        self.assertNotIn("összege: 0", body)
        self.assertEqual(list(message.iter_attachments()), [])
        self.assertNotIn("cid:", message.get_body(preferencelist=("html",)).get_content())

    def test_html_escapes_values_and_rejects_unsafe_links(self):
        rows = [{"date": "<script>alert(1)</script>", "consumption_kwh": "0", "status": "reported"}]
        html = mail.html_report(rows, {}, "0", None, "javascript:alert(1)", "<script>")
        self.assertNotIn("<script>", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("0,00", html)

    def test_invalid_or_missing_measurement_is_not_a_zero(self):
        for value in ("", "NaN", "Infinity", "-1", "not-a-number"):
            self.assertIsNone(mail.measurement({"consumption_kwh": value}))
        self.assertEqual(mail.measurement({"consumption_kwh": "0"}), 0)

    def test_tls_established_before_authentication(self):
        smtp = MagicMock()
        server = smtp.return_value.__enter__.return_value = Mock()
        server.send_message.return_value = {}
        message = mail.build_message("missing", "sender@example.com", "recipient@example.com")
        with patch.object(mail.smtplib, "SMTP", smtp):
            mail.send_message(message, "smtp.example.com", 587, "smtp-user", "secret-password")
        names = [call[0] for call in server.method_calls]
        self.assertLess(names.index("starttls"), names.index("login"))
        server.send_message.assert_called_once_with(message)

    def test_implicit_tls_on_port_465(self):
        smtp = MagicMock()
        server = smtp.return_value.__enter__.return_value = Mock()
        server.send_message.return_value = {}
        message = mail.build_message("missing", "sender@example.com", "recipient@example.com")
        with patch.object(mail.smtplib, "SMTP_SSL", smtp), patch.object(mail.smtplib, "SMTP") as plain:
            mail.send_message(message, "smtp.example.com", 465, "smtp-user", "secret-password")
        plain.assert_not_called()
        server.starttls.assert_not_called()
        server.login.assert_called_once_with("smtp-user", "secret-password")

    def test_missing_settings_do_not_send(self):
        with patch.dict(mail.os.environ, {}, clear=True), patch.object(mail, "send_message") as send, \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(mail.main([]), 1)
        self.assertIn("EMAIL_TO", log.getvalue())
        send.assert_not_called()

    def test_smtp_failures_never_log_credentials(self):
        environment = dict(EMAIL_TO="recipient@example.com", EMAIL_FROM="sender@example.com",
                           SMTP_HOST="smtp.example.com", SMTP_USERNAME="smtp-user", SMTP_PASSWORD="secret-password")
        with patch.dict(mail.os.environ, environment, clear=True), \
                patch.object(mail, "send_message", side_effect=mail.smtplib.SMTPAuthenticationError(535, b"secret-password")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(mail.main([]), 1)
        self.assertNotIn("secret-password", log.getvalue())


if __name__ == "__main__":
    unittest.main()
