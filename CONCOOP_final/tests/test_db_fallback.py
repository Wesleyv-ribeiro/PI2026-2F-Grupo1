import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import app as app_module


class DatabaseFallbackTests(unittest.TestCase):
    def test_connect_db_raises_clear_runtime_error_when_postgres_is_unavailable(self):
        with patch.object(
            app_module,
            "DEFAULT_DATABASE_URL",
            "postgresql://invalid:invalid@127.0.0.1:5432/agrolink",
        ), patch.object(
            app_module.psycopg2,
            "connect",
            side_effect=app_module.OperationalError("db unavailable"),
        ):
            with self.assertRaises(RuntimeError) as exc:
                app_module.connect_db()

        self.assertIn("Nao foi possivel conectar ao PostgreSQL", str(exc.exception))
        self.assertIn("Verifique se o servidor está rodando", str(exc.exception))

    def test_connect_db_clears_non_ascii_windows_env_before_connecting(self):
        def fake_connect(*args, **kwargs):
            self.assertNotIn("USERPROFILE", app_module.os.environ)
            self.assertEqual(kwargs["user"], "postgres")
            self.assertEqual(kwargs["password"], "Morango")
            self.assertEqual(kwargs["dbname"], "concoop")
            return object()

        with patch.object(
            app_module,
            "DEFAULT_DATABASE_URL",
            "postgresql://postgres:Morango@127.0.0.1:5432/concoop",
        ), patch.dict(
            app_module.os.environ,
            {"USERPROFILE": "C:\\Users\\Usuário", "DATABASE_URL": "postgresql://postgres:Morango@127.0.0.1:5432/concoop"},
            clear=True,
        ), patch.object(app_module.psycopg2, "connect", side_effect=fake_connect):
            app_module.connect_db()


class QuotationRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_quotation_page_renders_selected_cepea_indicator(self):
        with patch.object(app_module, "get_db", return_value=object()):
            response = self.client.get("/cotacoes?produto=soja-pr")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Soja — Paraná".encode(), response.data)
        self.assertIn(b"widgetproduto.js.php", response.data)
        self.assertIn(b"CEPEA/ESALQ", response.data)

    def test_mobile_home_returns_public_products_and_verified_vets(self):
        class FakeCursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class FakeDb:
            def execute(self, query):
                if "FROM products p" in query:
                    return FakeCursor([{
                        "id": 7,
                        "title": "Milho",
                        "description": "Sacas selecionadas",
                        "price": 85.5,
                        "stock": 10,
                        "made_to_order": 0,
                        "image_path": None,
                        "producer_name": "Joana",
                        "city": "Chapecó",
                    }])
                return FakeCursor([{
                    "id": 4,
                    "name": "Dr. Paulo",
                    "city": "Chapecó",
                    "bio": "Atendimento rural",
                }])

        with patch.object(app_module, "get_db", return_value=FakeDb()):
            response = self.client.get("/api/mobile/home")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["products"][0]["title"], "Milho")
        self.assertEqual(response.json["vets"][0]["name"], "Dr. Paulo")

    def test_unknown_indicator_uses_safe_default(self):
        with patch.object(app_module, "get_db", return_value=object()):
            response = self.client.get("/cotacoes?produto=https://example.com")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Milho — Indicador ESALQ/BM&amp;FBOVESPA".encode(), response.data)
        self.assertNotIn(b"https://example.com", response.data)


class MobileSyncTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_service_sync_is_authenticated_and_idempotent(self):
        class FakeCursor:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeDb:
            def __init__(self):
                self.receipts = set()
                self.service_inserts = 0

            def execute(self, query, params=None):
                if "COUNT(*) FROM messages" in query:
                    return FakeCursor({"count": 0})
                if "FROM users" in query and "WHERE id = ?" in query:
                    return FakeCursor({
                        "id": 9,
                        "name": "Prestador",
                        "email": "prestador@example.com",
                        "role": "servidor",
                        "city": "Chapecó",
                        "bio": None,
                        "crmv": None,
                        "is_vet_verified": 0,
                        "profile_image": None,
                        "is_admin": 0,
                        "is_active": 1,
                    })
                if "SELECT client_id FROM mobile_sync_receipts" in query:
                    exists = params[1] in self.receipts
                    return FakeCursor({"client_id": params[1]} if exists else None)
                if "INSERT INTO services" in query:
                    self.service_inserts += 1
                elif "INSERT INTO mobile_sync_receipts" in query:
                    self.receipts.add(params[1])
                return FakeCursor()

            def commit(self):
                pass

        database = FakeDb()
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = 9
        form = {
            "client_id": "local-submission-001",
            "type": "service",
            "title": "Plantio",
            "description": "Preparo de solo",
        }
        with patch.object(app_module, "get_db", return_value=database):
            first = self.client.post("/api/mobile/sync", data=form)
            second = self.client.post("/api/mobile/sync", data=form)

        self.assertEqual(first.status_code, 201)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json["duplicate"])
        self.assertEqual(database.service_inserts, 1)

    def test_service_sync_requires_login(self):
        with patch.object(app_module, "get_db", return_value=object()):
            response = self.client.post(
                "/api/mobile/sync",
                data={"client_id": "local-submission-002", "type": "service"},
            )

        self.assertEqual(response.status_code, 401)


class AppDownloadTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_download_page_shows_preparation_state_without_release_apk(self):
        with patch.object(app_module, "get_db", return_value=object()), patch.object(
            app_module, "ANDROID_APP_DIR", Path("missing-download-folder")
        ):
            response = self.client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Download em preparação".encode(), response.data)
        self.assertNotIn(b"Baixar para Android", response.data)

    def test_android_download_is_sent_as_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            apk_path = Path(temp_dir) / app_module.ANDROID_APP_FILENAME
            apk_path.write_bytes(b"test-apk")
            with patch.object(app_module, "get_db", return_value=object()), patch.object(
                app_module, "ANDROID_APP_DIR", Path(temp_dir)
            ):
                response = self.client.get("/app/download")
            payload = response.data
            response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=CONCOOP-Android.apk", response.headers["Content-Disposition"])
        self.assertEqual(payload, b"test-apk")

    def test_mobile_session_requires_login(self):
        with patch.object(app_module, "get_db", return_value=object()):
            response = self.client.get("/api/mobile/session")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
