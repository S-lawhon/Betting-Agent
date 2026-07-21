"""
tests/test_placeholder.py
─────────────────────────
Phase 0: verify the project skeleton is importable and config loads cleanly.
Uses stdlib unittest so the suite runs without any third-party packages.

Run:
    python3 -m unittest discover -s tests -v
    # or just:
    python3 -m unittest tests.test_placeholder -v
"""

import json
import logging
import sys
import unittest
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── Config loading ────────────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):

    def _load(self):
        import yaml
        with (ROOT / "config.yaml").open() as fh:
            return yaml.safe_load(fh)

    def test_config_file_exists(self):
        self.assertTrue((ROOT / "config.yaml").exists(), "config.yaml not found")

    def test_config_is_valid_yaml(self):
        config = self._load()
        self.assertIsInstance(config, dict, "config.yaml must be a YAML mapping")

    def test_config_has_required_keys(self):
        config = self._load()
        required = {"environment", "log_level", "kalshi", "odds_api", "risk", "edge", "paths"}
        missing = required - set(config.keys())
        self.assertFalse(missing, f"config.yaml missing keys: {missing}")

    def test_default_environment_is_demo(self):
        """Guard against accidentally shipping a live-mode default."""
        config = self._load()
        self.assertEqual(
            config["environment"], "demo",
            "Default environment must be 'demo'. Set ENVIRONMENT=live explicitly."
        )

    def test_env_example_exists(self):
        self.assertTrue((ROOT / ".env.example").exists(), ".env.example not found")


# ── Schema files ──────────────────────────────────────────────────────────────

class TestSchemas(unittest.TestCase):

    SCHEMAS = [
        "trade_log.schema.json",
        "unmatched_markets.schema.json",
        "edge_history.schema.json",
    ]

    def _schema_path(self, name):
        return ROOT / "data" / "schema" / name

    def test_all_schema_files_exist(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                self.assertTrue(
                    self._schema_path(name).exists(),
                    f"Missing schema file: {name}"
                )

    def test_schemas_are_valid_json(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                with self._schema_path(name).open() as fh:
                    schema = json.load(fh)
                self.assertIsInstance(schema, dict)

    def test_schemas_have_object_type(self):
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                with self._schema_path(name).open() as fh:
                    schema = json.load(fh)
                self.assertEqual(
                    schema.get("type"), "object",
                    f"{name} must declare type: object"
                )

    def test_trade_log_schema_has_required_fields(self):
        with (self._schema_path("trade_log.schema.json")).open() as fh:
            schema = json.load(fh)
        required = set(schema.get("required", []))
        expected = {"fingerprint", "timestamp_utc", "mode", "edge_pct", "ev", "action"}
        missing = expected - required
        self.assertFalse(missing, f"trade_log schema missing required fields: {missing}")

    def test_schema_jsonschema_validates(self):
        """Meta-validation: each schema is itself a valid JSON Schema draft."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        for name in self.SCHEMAS:
            with self.subTest(schema=name):
                with self._schema_path(name).open() as fh:
                    schema = json.load(fh)
                # jsonschema.Draft7Validator.check_schema raises on invalid schema
                jsonschema.Draft7Validator.check_schema(schema)


# ── Logger setup ──────────────────────────────────────────────────────────────

class TestLoggerSetup(unittest.TestCase):

    def test_logger_setup_importable(self):
        from src.logger_setup import configure_logging, get_logger  # noqa: F401

    def test_configure_logging_runs(self):
        from src.logger_setup import configure_logging
        configure_logging("WARNING")  # should not raise

    def test_get_logger_has_expected_methods(self):
        from src.logger_setup import configure_logging, get_logger
        configure_logging("WARNING")
        logger = get_logger("test_module", phase="test")
        for method in ("info", "warning", "error", "debug", "critical"):
            with self.subTest(method=method):
                self.assertTrue(
                    callable(getattr(logger, method, None)),
                    f"Logger missing callable method: {method}"
                )

    def test_logger_bind_returns_new_logger(self):
        from src.logger_setup import configure_logging, get_logger
        configure_logging("WARNING")
        base = get_logger("test_bind")
        bound = base.bind(extra_key="extra_val")
        self.assertIsNotNone(bound)
        self.assertTrue(callable(bound.info))


# ── main.py ───────────────────────────────────────────────────────────────────

class TestMain(unittest.TestCase):

    def test_main_exits_cleanly(self):
        import os
        os.chdir(ROOT)
        from src.main import main
        # max_cycles=0 skips the scan loop so the test exits immediately
        result = main(max_cycles=0)
        self.assertEqual(result, 0, f"main() returned non-zero exit code: {result}")

    def test_load_config_raises_on_missing_file(self):
        from src.main import load_config
        with self.assertRaises(FileNotFoundError):
            load_config(ROOT / "nonexistent_config.yaml")


# ── Folder structure ──────────────────────────────────────────────────────────

class TestFolderStructure(unittest.TestCase):

    REQUIRED_PATHS = [
        "src/__init__.py",
        "src/main.py",
        "src/logger_setup.py",
        "tests/__init__.py",
        "data/schema",
        "data/trade_logs",
        "scripts",
        "cowork",
        "requirements.txt",
        "config.yaml",
        ".env.example",
        ".gitignore",
        "README.md",
    ]

    def test_required_paths_exist(self):
        for rel_path in self.REQUIRED_PATHS:
            with self.subTest(path=rel_path):
                self.assertTrue(
                    (ROOT / rel_path).exists(),
                    f"Expected path missing: {rel_path}"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
