"""Offline configuration tests; never read the real .env or open a connection."""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.persistence import check_connection, db


def fake_dotenv(*, dotenv_path: Path, override: bool):
    """Small stand-in so these tests run before optional dependencies install."""
    assert override is False
    assert dotenv_path == db.ROOT / '.env'
    if not dotenv_path.exists():
        return False
    for line in dotenv_path.read_text('utf-8').splitlines():
        key, value = line.split('=', 1)
        if key not in os.environ:
            os.environ[key] = value
    return True


class DotenvConfigurationTest(unittest.TestCase):
    def environment(self, directory: Path, values: dict):
        return (patch.object(db, 'ROOT', directory),
                patch.dict(sys.modules, {'dotenv': types.SimpleNamespace(load_dotenv=fake_dotenv)}),
                patch.dict(os.environ, values, clear=True))

    def test_root_env_is_loaded_from_fixed_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('DATABASE_URL=postgresql://example.neon.tech/dev', encoding='utf-8')
            root_patch, module_patch, env_patch = self.environment(root, {})
            with root_patch, module_patch, env_patch:
                self.assertEqual('postgresql://example.neon.tech/dev', db.load_database_url())

    def test_explicit_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('DATABASE_URL=postgresql://file.neon.tech/dev', encoding='utf-8')
            explicit = 'postgresql://explicit.neon.tech/dev'
            root_patch, module_patch, env_patch = self.environment(root, {'DATABASE_URL': explicit})
            with root_patch, module_patch, env_patch:
                self.assertEqual(explicit, db.load_database_url())

    def test_check_module_uses_root_env_without_connecting_to_unknown_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('DATABASE_URL=postgresql://example.invalid/dev', encoding='utf-8')
            root_patch, module_patch, env_patch = self.environment(root, {})
            fake_psycopg = types.SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(AssertionError('No connection allowed')))
            with root_patch, module_patch, env_patch, patch.dict(sys.modules, {'psycopg': fake_psycopg}):
                result = check_connection.check()
                self.assertTrue(result['database_url_available'])
                self.assertFalse(result['connected'])
                self.assertIn('Neon host', result['diagnosis'])

    def test_missing_file_and_environment_fail_without_url_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_patch, module_patch, env_patch = self.environment(Path(tmp), {})
            with root_patch, module_patch, env_patch:
                result = check_connection.check()
                self.assertFalse(result['database_url_available'])
                self.assertNotIn('postgresql://', str(result))


if __name__ == '__main__':
    unittest.main(verbosity=2)
