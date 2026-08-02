import unittest
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


if __name__ == "__main__":
    unittest.main()
