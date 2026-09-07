import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings


class SettingsConfigTests(unittest.TestCase):
    def test_uses_only_pydantic_v2_settings_config(self):
        self.assertNotIn("Config", Settings.__dict__)
        self.assertEqual(Settings.model_config["env_file"], ".env")
        self.assertEqual(Settings.model_config["extra"], "ignore")

    def test_reads_env_file_and_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "DATABASE_URL=sqlite:///./regression.db\n"
                "UNKNOWN_LEGACY_SETTING=ignored\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                configured = Settings(_env_file=env_file)

        self.assertEqual(configured.DATABASE_URL, "sqlite:///./regression.db")


if __name__ == "__main__":
    unittest.main()
