from __future__ import annotations

import base64
import importlib.util
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"

TEST_ADMIN_USERNAME = "isolated_test_admin"
TEST_ADMIN_PASSWORD = "Isolated-Test-Password-42!"


class ServiceSiteTestCase(unittest.TestCase):
    """Integration tests using a fresh app import and temporary SQLite database."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "data" / "requests.db"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.database_path),
                "ADMIN_USERNAME": TEST_ADMIN_USERNAME,
                "ADMIN_PASSWORD": TEST_ADMIN_PASSWORD,
                # Force-disable Telegram notifications during tests so a
                # developer's real .env credentials never cause the test
                # suite to send live messages or hit the network.
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_CHAT_ID": "",
            },
            clear=False,
        )
        self.environment.start()

        self.module_name = f"_service_site_app_test_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(self.module_name, APP_PATH)
        if spec is None or spec.loader is None:
            self.fail(f"Не удалось создать import spec для {APP_PATH}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[self.module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(self.module_name, None)
            self.environment.stop()
            self.temporary_directory.cleanup()
            raise

        self.app_module: ModuleType = module
        self.app = module.app
        self.app.config.update(
            TESTING=True,
            DATABASE=str(self.database_path),
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        sys.modules.pop(self.module_name, None)
        self.environment.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def valid_form(**overrides: object) -> dict[str, object]:
        form: dict[str, object] = {
            "name": "Тестовый Клиент",
            "phone": "+7 (999) 123-45-67",
            "email": "client@example.com",
            "service": "landing",
            "options": [],
            "total_price": "30000",
            "comment": "Тестовая заявка",
        }
        form.update(overrides)
        return form

    @staticmethod
    def basic_auth_header(
        username: str = TEST_ADMIN_USERNAME,
        password: str = TEST_ADMIN_PASSWORD,
    ) -> dict[str, str]:
        credentials = f"{username}:{password}".encode("utf-8")
        token = base64.b64encode(credentials).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def fetch_all(
        self,
        query: str,
        parameters: tuple[object, ...] = (),
    ) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(query, parameters).fetchall()
        finally:
            connection.close()

    def request_count(self) -> int:
        row = self.fetch_all("SELECT COUNT(*) AS count FROM requests")[0]
        return int(row["count"])

    def test_public_routes_404_and_method_not_allowed(self) -> None:
        index_response = self.client.get("/")
        self.assertEqual(index_response.status_code, 200)
        self.assertIn("Иван Петров", index_response.get_data(as_text=True))

        thanks_response = self.client.get("/thanks")
        self.assertEqual(thanks_response.status_code, 200)
        self.assertIn("Спасибо!", thanks_response.get_data(as_text=True))

        method_checks = (
            ("get", "/submit"),
            ("post", "/"),
            ("post", "/thanks"),
            ("post", "/admin"),
        )
        for method, path in method_checks:
            with self.subTest(method=method.upper(), path=path):
                response = getattr(self.client, method)(
                    path,
                    headers=self.basic_auth_header(),
                )
                self.assertEqual(response.status_code, 405)

        missing_response = self.client.get("/page-that-does-not-exist")
        self.assertEqual(missing_response.status_code, 404)
        self.assertIn("404", missing_response.get_data(as_text=True))

    def test_admin_basic_auth_rejects_missing_invalid_and_malformed(self) -> None:
        missing_auth = self.client.get("/admin")
        self.assertEqual(missing_auth.status_code, 401)
        challenge = missing_auth.headers.get("WWW-Authenticate", "")
        self.assertIn("Basic", challenge)
        self.assertIn('realm="Admin"', challenge)

        rejected_headers = (
            self.basic_auth_header(password="wrong-password"),
            self.basic_auth_header(username="wrong-user"),
            {"Authorization": "Basic !!!not-valid-base64!!!"},
            {"Authorization": "Bearer not-a-basic-token"},
        )
        for headers in rejected_headers:
            with self.subTest(authorization=headers["Authorization"]):
                response = self.client.get("/admin", headers=headers)
                self.assertEqual(response.status_code, 401)

        valid_auth = self.client.get(
            "/admin",
            headers=self.basic_auth_header(),
        )
        self.assertEqual(valid_auth.status_code, 200)
        self.assertIn("Заявок пока нет", valid_auth.get_data(as_text=True))

    def test_invalid_form_preserves_values_and_does_not_insert(self) -> None:
        response = self.client.post(
            "/submit",
            data=self.valid_form(
                name="Сохранённое имя",
                phone="   ",
                email="не-email",
                service="store",
                options=["crm", "urgent"],
                comment="Этот комментарий не должен пропасть",
            ),
        )

        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("Укажите телефон.", html)
        self.assertIn("Введите корректный email.", html)
        self.assertIn('value="Сохранённое имя"', html)
        self.assertIn('value="не-email"', html)
        self.assertIn("Этот комментарий не должен пропасть", html)
        self.assertRegex(
            html,
            re.compile(r'<option[^>]+value="store"[^>]+selected', re.DOTALL),
        )
        for option in ("crm", "urgent"):
            with self.subTest(option=option):
                self.assertRegex(
                    html,
                    re.compile(
                        rf'<input[^>]+name="options"[^>]+'
                        rf'value="{option}"[^>]+checked',
                        re.DOTALL,
                    ),
                )
        self.assertEqual(self.request_count(), 0)

    def test_valid_submission_uses_prg_and_thanks_refresh_is_safe(self) -> None:
        response = self.client.post("/submit", data=self.valid_form())

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/thanks")
        self.assertEqual(self.request_count(), 1)

        first_thanks = self.client.get("/thanks")
        refreshed_thanks = self.client.get("/thanks")
        for thanks_response in (first_thanks, refreshed_thanks):
            self.assertEqual(thanks_response.status_code, 200)
            html = thanks_response.get_data(as_text=True)
            self.assertIn("Спасибо!", html)
            self.assertIn("Ваша заявка отправлена.", html)

        self.assertEqual(self.request_count(), 1)

    def test_server_ignores_forged_price_and_client_timestamp(self) -> None:
        response = self.client.post(
            "/submit",
            data=self.valid_form(
                service="store",
                options=["payments", "urgent"],
                total_price="-999999999",
                created_at="1900-01-01 00:00:00",
            ),
        )

        self.assertEqual(response.status_code, 303)
        rows = self.fetch_all("SELECT * FROM requests")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["service"], "Интернет-магазин")
        self.assertEqual(
            row["options"],
            "Подключение онлайн-оплаты, Срочная разработка",
        )
        self.assertEqual(row["total_price"], 125_000)
        self.assertNotEqual(row["created_at"], "1900-01-01 00:00:00")

    def test_duplicate_options_are_deduplicated_and_unknown_is_rejected(
        self,
    ) -> None:
        duplicate_response = self.client.post(
            "/submit",
            data=self.valid_form(
                options=["crm", "crm", "urgent"],
                **{"options[]": ["crm", "urgent"]},
                total_price="1",
            ),
        )
        self.assertEqual(duplicate_response.status_code, 303)

        stored = self.fetch_all(
            "SELECT options, total_price FROM requests"
        )[0]
        self.assertEqual(
            stored["options"],
            "Интеграция с CRM, Срочная разработка",
        )
        self.assertEqual(stored["total_price"], 80_000)

        unknown_response = self.client.post(
            "/submit",
            data=self.valid_form(
                name="Неизвестная опция",
                options=["crm", "not_in_catalog"],
            ),
        )
        self.assertEqual(unknown_response.status_code, 400)
        self.assertIn(
            "Выбрана неизвестная дополнительная опция.",
            unknown_response.get_data(as_text=True),
        )
        self.assertEqual(self.request_count(), 1)

    def test_exact_catalog_prices_and_control_totals(self) -> None:
        expected_services = {
            "landing": 30_000,
            "store": 80_000,
            "ai_agent": 65_000,
        }
        expected_options = {
            "crm": 20_000,
            "messengers": 25_000,
            "payments": 15_000,
            "urgent": 30_000,
        }
        actual_services = {
            key: int(item["price"])
            for key, item in self.app_module.SERVICES.items()
        }
        actual_options = {
            key: int(item["price"])
            for key, item in self.app_module.EXTRA_OPTIONS.items()
        }
        self.assertEqual(actual_services, expected_services)
        self.assertEqual(actual_options, expected_options)

        control_totals = (
            ("landing", [], 30_000),
            ("landing", ["crm", "messengers"], 75_000),
            ("landing", list(expected_options), 120_000),
            ("store", list(expected_options), 170_000),
            ("ai_agent", list(expected_options), 155_000),
        )
        for service, options, expected_total in control_totals:
            with self.subTest(service=service, options=options):
                self.assertEqual(
                    self.app_module.calculate_total(service, options),
                    expected_total,
                )

    def test_sqlite_schema_is_complete(self) -> None:
        self.assertTrue(self.database_path.is_file())
        self.assertEqual(
            Path(self.app.config["DATABASE"]).resolve(),
            self.database_path.resolve(),
        )

        schema_rows = self.fetch_all("PRAGMA table_info(requests)")
        self.assertEqual(
            [row["name"] for row in schema_rows],
            [
                "id",
                "name",
                "phone",
                "email",
                "service",
                "options",
                "total_price",
                "comment",
                "created_at",
            ],
        )
        schema = {row["name"]: row for row in schema_rows}
        self.assertEqual(schema["id"]["type"].upper(), "INTEGER")
        self.assertEqual(schema["id"]["pk"], 1)
        self.assertEqual(schema["total_price"]["type"].upper(), "INTEGER")
        for required_column in (
            "name",
            "phone",
            "email",
            "service",
            "options",
            "total_price",
            "comment",
            "created_at",
        ):
            with self.subTest(column=required_column):
                self.assertEqual(schema[required_column]["notnull"], 1)

    def test_sqli_is_data_and_stored_xss_is_escaped_in_admin(self) -> None:
        sql_payload = "Robert'); DROP TABLE requests;--"
        xss_payload = (
            '<script>alert("x")</script>'
            '<img src=x onerror=alert(1)>'
        )
        malicious_response = self.client.post(
            "/submit",
            data=self.valid_form(
                name=sql_payload,
                comment=xss_payload,
            ),
        )
        self.assertEqual(malicious_response.status_code, 303)

        stored = self.fetch_all(
            "SELECT name, comment FROM requests WHERE id = 1"
        )[0]
        self.assertEqual(stored["name"], sql_payload)
        self.assertEqual(stored["comment"], xss_payload)

        second_response = self.client.post(
            "/submit",
            data=self.valid_form(name="После SQL-инъекции"),
        )
        self.assertEqual(second_response.status_code, 303)
        self.assertEqual(self.request_count(), 2)

        admin_response = self.client.get(
            "/admin",
            headers=self.basic_auth_header(),
        )
        self.assertEqual(admin_response.status_code, 200)
        html = admin_response.get_data(as_text=True)
        self.assertIn("DROP TABLE requests", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)

    def test_submission_succeeds_when_telegram_is_unreachable(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "12345"},
        ):
            with mock.patch.object(
                self.app_module.urllib.request,
                "urlopen",
                side_effect=OSError("network unreachable"),
            ):
                response = self.client.post(
                    "/submit", data=self.valid_form()
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/thanks")
        self.assertEqual(self.request_count(), 1)

    def test_admin_shows_newest_request_first(self) -> None:
        first_response = self.client.post(
            "/submit",
            data=self.valid_form(name="Первый Клиент"),
        )
        second_response = self.client.post(
            "/submit",
            data=self.valid_form(name="Второй Клиент"),
        )
        self.assertEqual(first_response.status_code, 303)
        self.assertEqual(second_response.status_code, 303)

        admin_response = self.client.get(
            "/admin",
            headers=self.basic_auth_header(),
        )
        self.assertEqual(admin_response.status_code, 200)
        html = admin_response.get_data(as_text=True)
        self.assertLess(
            html.index("Второй Клиент"),
            html.index("Первый Клиент"),
        )


if __name__ == "__main__":
    unittest.main()
