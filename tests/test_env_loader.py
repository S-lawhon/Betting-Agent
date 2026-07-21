"""
tests/test_env_loader.py
─────────────────────────
Tests for src/env_loader.py — .env file parsing and os.environ injection.
Covers plain values, quoted values, multi-line PEM blocks, blank placeholders,
comment skipping, and shell-var priority.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.env_loader import parse_env_file, load, _strip_quotes


# ── Helpers ──────────────────────────────────────────────────────────

def _write_env(content: str) -> str:
    """Write content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


# ── TestParseEnvFile ─────────────────────────────────────────────────

class TestParseEnvFile(unittest.TestCase):

    def test_plain_assignment(self):
        p = _write_env("FOO=bar\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result["FOO"], "bar")

    def test_double_quoted_value(self):
        p = _write_env('KEY="hello world"\n')
        result = parse_env_file(Path(p))
        self.assertEqual(result["KEY"], "hello world")

    def test_single_quoted_value(self):
        p = _write_env("KEY='hello world'\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result["KEY"], "hello world")

    def test_comment_lines_skipped(self):
        p = _write_env("# this is a comment\nFOO=bar\n")
        result = parse_env_file(Path(p))
        self.assertNotIn("#", result)
        self.assertEqual(result["FOO"], "bar")

    def test_blank_lines_skipped(self):
        p = _write_env("\n\nFOO=bar\n\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result, {"FOO": "bar"})

    def test_export_prefix_stripped(self):
        p = _write_env("export MY_VAR=hello\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result["MY_VAR"], "hello")

    def test_empty_value_stored(self):
        p = _write_env("EMPTY_KEY=\n")
        result = parse_env_file(Path(p))
        self.assertIn("EMPTY_KEY", result)
        self.assertEqual(result["EMPTY_KEY"], "")

    def test_multiple_keys(self):
        p = _write_env("A=1\nB=2\nC=3\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result, {"A": "1", "B": "2", "C": "3"})

    def test_value_with_equals_sign(self):
        """Values containing '=' should not be split further."""
        p = _write_env("TOKEN=abc=def==\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result["TOKEN"], "abc=def==")

    def test_file_not_found_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_env_file(Path("/nonexistent/.env"))

    def test_inline_comment_not_stripped(self):
        """The parser does not strip inline comments — value is taken literally."""
        p = _write_env("KEY=value  # comment\n")
        result = parse_env_file(Path(p))
        # Inline comment is not stripped; the raw value is preserved
        self.assertIn("KEY", result)

    # ── Multi-line PEM tests ─────────────────────────────────────────

    def test_pem_rsa_key_parsed_as_multiline(self):
        """A -----BEGIN RSA PRIVATE KEY----- block should be read in full."""
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAFAKEDATA==\n"
            "MOREFAKEDATA==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        content = f"KALSHI_PRIVATE_KEY={pem}\nOTHER=value\n"
        p = _write_env(content)
        result = parse_env_file(Path(p))
        self.assertIn("KALSHI_PRIVATE_KEY", result)
        key_val = result["KALSHI_PRIVATE_KEY"]
        self.assertIn("-----BEGIN RSA PRIVATE KEY-----", key_val)
        self.assertIn("-----END RSA PRIVATE KEY-----", key_val)
        self.assertIn("MIIEpAIBAAKCAQEAFAKEDATA==", key_val)

    def test_pem_key_newlines_preserved(self):
        """Newlines within the PEM block must be preserved."""
        pem_body = "AAABBBCCC\nDDDEEEFFF\n"
        content = (
            f"MY_KEY=-----BEGIN RSA PRIVATE KEY-----\n"
            f"{pem_body}"
            f"-----END RSA PRIVATE KEY-----\n"
        )
        p = _write_env(content)
        result = parse_env_file(Path(p))
        val = result["MY_KEY"]
        self.assertIn("\n", val)
        self.assertIn("AAABBBCCC", val)
        self.assertIn("DDDEEE", val)

    def test_pem_followed_by_other_keys(self):
        """Keys defined after a PEM block should still be parsed."""
        content = (
            "KALSHI_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\n"
            "FAKEKEYDATA==\n"
            "-----END RSA PRIVATE KEY-----\n"
            "\n"
            "# A comment after the key\n"
            "ODDS_API_KEY=abc123\n"
            "FRED_API_KEY=xyz789\n"
        )
        p = _write_env(content)
        result = parse_env_file(Path(p))
        self.assertIn("KALSHI_PRIVATE_KEY", result)
        self.assertEqual(result["ODDS_API_KEY"], "abc123")
        self.assertEqual(result["FRED_API_KEY"], "xyz789")

    def test_pem_generic_private_key_format(self):
        """-----BEGIN PRIVATE KEY----- (PKCS#8) is also handled."""
        content = (
            "MY_KEY=-----BEGIN PRIVATE KEY-----\n"
            "SOMEKEYDATA\n"
            "-----END PRIVATE KEY-----\n"
        )
        p = _write_env(content)
        result = parse_env_file(Path(p))
        self.assertIn("MY_KEY", result)
        self.assertIn("-----BEGIN PRIVATE KEY-----", result["MY_KEY"])
        self.assertIn("-----END PRIVATE KEY-----", result["MY_KEY"])

    def test_non_pem_values_unaffected(self):
        """Regular values starting with '-' but not '-----BEGIN' are not affected."""
        p = _write_env("FLAG=-v\nKEY=value\n")
        result = parse_env_file(Path(p))
        self.assertEqual(result["FLAG"], "-v")
        self.assertEqual(result["KEY"], "value")


# ── TestLoad ─────────────────────────────────────────────────────────

class TestLoad(unittest.TestCase):

    def setUp(self):
        """Remove any test vars from os.environ before each test."""
        for k in ("_TEST_ENV_A", "_TEST_ENV_B", "_TEST_PEM_KEY"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k in ("_TEST_ENV_A", "_TEST_ENV_B", "_TEST_PEM_KEY"):
            os.environ.pop(k, None)

    def test_load_injects_into_environ(self):
        p = _write_env("_TEST_ENV_A=hello\n")
        load(p)
        self.assertEqual(os.environ.get("_TEST_ENV_A"), "hello")

    def test_load_skips_blank_placeholders(self):
        p = _write_env("_TEST_ENV_A=\n")
        load(p)
        self.assertNotIn("_TEST_ENV_A", os.environ)

    def test_load_does_not_override_existing_by_default(self):
        os.environ["_TEST_ENV_A"] = "original"
        p = _write_env("_TEST_ENV_A=new_value\n")
        load(p)
        self.assertEqual(os.environ["_TEST_ENV_A"], "original")

    def test_load_override_true_replaces_existing(self):
        os.environ["_TEST_ENV_A"] = "original"
        p = _write_env("_TEST_ENV_A=replaced\n")
        load(p, override=True)
        self.assertEqual(os.environ["_TEST_ENV_A"], "replaced")

    def test_load_returns_loaded_dict(self):
        p = _write_env("_TEST_ENV_A=foo\n_TEST_ENV_B=bar\n")
        result = load(p)
        self.assertIn("_TEST_ENV_A", result)
        self.assertIn("_TEST_ENV_B", result)

    def test_load_missing_file_returns_empty(self):
        result = load("/nonexistent/path/.env")
        self.assertEqual(result, {})

    def test_load_pem_key_injected_into_environ(self):
        """Multi-line PEM key should be correctly injected into os.environ."""
        content = (
            "_TEST_PEM_KEY=-----BEGIN RSA PRIVATE KEY-----\n"
            "FAKEKEYDATA==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        p = _write_env(content)
        load(p)
        val = os.environ.get("_TEST_PEM_KEY", "")
        self.assertIn("-----BEGIN RSA PRIVATE KEY-----", val)
        self.assertIn("-----END RSA PRIVATE KEY-----", val)
        self.assertIn("FAKEKEYDATA==", val)


# ── TestStripQuotes ──────────────────────────────────────────────────

class TestStripQuotes(unittest.TestCase):

    def test_double_quotes_removed(self):
        self.assertEqual(_strip_quotes('"hello"'), "hello")

    def test_single_quotes_removed(self):
        self.assertEqual(_strip_quotes("'hello'"), "hello")

    def test_no_quotes_unchanged(self):
        self.assertEqual(_strip_quotes("hello"), "hello")

    def test_mismatched_quotes_unchanged(self):
        self.assertEqual(_strip_quotes('"hello\''), '"hello\'')

    def test_empty_string_unchanged(self):
        self.assertEqual(_strip_quotes(""), "")

    def test_single_char_unchanged(self):
        self.assertEqual(_strip_quotes("x"), "x")

    def test_only_quotes_returns_empty(self):
        self.assertEqual(_strip_quotes('""'), "")


if __name__ == "__main__":
    unittest.main()
