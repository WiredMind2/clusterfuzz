#!/usr/bin/env python3
"""Unit tests for deploy/ glue (no upstream / no network)."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asan_helpers  # noqa: E402
import load_target_config  # noqa: E402
import pr_fuzz_delta  # noqa: E402
import report_fuzz_otel  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


github_app_server = _load_module("github_app_server", SCRIPTS / "github_app_server.py")
post_pr_comments = _load_module("post_pr_comments", SCRIPTS / "post_pr_comments.py")


class AsanHelpersTest(unittest.TestCase):
    SAMPLE = """
ERROR: AddressSanitizer: heap-buffer-overflow on address 0x123
    #0 0xdeadbeef in LLVMFuzzerTestOneInput /src/demo_fuzz.cc:16:5
SUMMARY: AddressSanitizer: heap-buffer-overflow /src/demo_fuzz.cc:16 in LLVMFuzzerTestOneInput
"""

    def test_parse_asan(self):
        info = asan_helpers.parse_asan(self.SAMPLE)
        self.assertEqual(info.bug_type, "heap-buffer-overflow")
        self.assertIn("heap-buffer-overflow", info.summary)
        self.assertEqual(info.frame_func, "LLVMFuzzerTestOneInput")
        self.assertEqual(info.frame_file, "/src/demo_fuzz.cc")
        self.assertEqual(info.frame_line, "16")

    def test_demo_boom(self):
        self.assertTrue(asan_helpers.is_demo_boom_crash(b"BOOM"))
        self.assertFalse(asan_helpers.is_demo_boom_crash(b"SAFE"))

    def test_unknown_symbols(self):
        md = "## LLM triage\nUse `strncpy` and `MyFakeHelper` and `OtherFake`."
        self.assertTrue(
            asan_helpers.triage_mentions_unknown_symbols(md, {"LLVMFuzzerTestOneInput"})
        )


class PrFuzzDeltaTest(unittest.TestCase):
    def test_fuzzable_paths(self):
        self.assertTrue(pr_fuzz_delta.is_fuzzable("fuzz/is_safe_url_fuzzer.py"))
        self.assertTrue(pr_fuzz_delta.is_fuzzable("deploy/demo-target/demo_fuzz.cc"))
        self.assertFalse(pr_fuzz_delta.is_fuzzable("README.md"))
        self.assertFalse(pr_fuzz_delta.is_fuzzable("src/main.py"))


class WebhookSignatureTest(unittest.TestCase):
    def test_verify_ok(self):
        secret = "test-secret"
        body = b'{"action":"opened"}'
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(
            github_app_server.verify_signature(secret, body, f"sha256={digest}")
        )

    def test_verify_bad(self):
        self.assertFalse(
            github_app_server.verify_signature("x", b"{}", "sha256=deadbeef")
        )
        self.assertFalse(github_app_server.verify_signature("x", b"{}", None))

    def test_deny_list(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "deny.txt"
            path.write_text("# comment\nWiredMind2/Noisy\n\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GITHUB_APP_DENY_LIST": str(path)}):
                denied = github_app_server.load_deny_set()
            self.assertIn("wiredmind2/noisy", denied)


class OtelTest(unittest.TestCase):
    def test_disabled(self):
        with mock.patch.dict(os.environ, {"OTEL_DISABLED": "1"}, clear=False):
            self.assertEqual(report_fuzz_otel.main(), 0)

    def test_no_endpoint(self):
        env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
        env["OTEL_DISABLED"] = "0"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(report_fuzz_otel.main(), 0)


class PostPrCommentsTest(unittest.TestCase):
    def test_build_review_body_no_crash(self):
        body = post_pr_comments.build_review_body(
            crashes=[],
            log_excerpt="Skipped timed fuzz: no fuzzable delta",
            log_text="Skipped timed fuzz: no fuzzable delta",
            ui_url="http://127.0.0.1:9300",
            repo="WiredMind2/demo",
            pr_number="1",
        )
        self.assertIn("ClusterFuzz local", body)
        self.assertIn("no fuzzable delta", body)

    def test_build_review_body_boom(self):
        body = post_pr_comments.build_review_body(
            crashes=[("crash-0", b"BOOM")],
            log_excerpt="",
            log_text="",
            ui_url="http://127.0.0.1:9300",
            repo="WiredMind2/clusterfuzz",
            pr_number="9",
            smoke_demo=True,
        )
        self.assertIn("smoke", body.lower())
        self.assertIn("BOOM", body)


class LoadTargetConfigTest(unittest.TestCase):
    def test_parse_and_apply(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "animemanager.yaml"
            path.write_text(
                """
id: animemanager
github: WiredMind2/AnimeManager
engine: atheris
fuzz_seconds: 45
harnesses:
  - fuzz/a_fuzzer.py
ui_url: http://127.0.0.1:9300
llm:
  enabled: true
  model: test-model
  background: true
""".lstrip(),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CLUSTERFUZZ_TARGETS_DIR": td}):
                cfg = load_target_config.find_target(
                    github_repo="WiredMind2/AnimeManager"
                )
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg["engine"], "atheris")
            self.assertEqual(cfg["fuzz_seconds"], 45)
            self.assertEqual(cfg["harnesses"], ["fuzz/a_fuzzer.py"])
            env = load_target_config.apply_target_to_environ(cfg, {})
            self.assertEqual(env["FUZZ_ENGINE"], "atheris")
            self.assertEqual(env["FUZZ_SECONDS"], "45")
            self.assertEqual(env["LLM_FUZZ_ENABLED"], "1")
            self.assertEqual(env["LLM_FUZZ_MODEL"], "test-model")


if __name__ == "__main__":
    unittest.main()
