#!/usr/bin/env python3
"""Compiled package and deployment contract checks. Arahort."""
import importlib.util
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("publish_validate", ROOT / "tools/publish-validate.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
class DistributionTests(unittest.TestCase):
    def archive(self, path, extra=None, version="0.1.0"):
        files = {"app/server/index.mjs": "export const ok = true", "app/server/admin-cli.mjs": "export const ok = true", "app/client/index.html": "<!doctype html><main>test</main>", "app/package.json": json.dumps({"name": "dnd-ai-arahort", "version": version, "dependencies": {}}), "app/package-lock.json": "{}", "app/release.json": json.dumps({"version": version, "schemaVersion": 1})}
        files.update(extra or {})
        with tarfile.open(path, "w:gz") as archive:
            for name, text in files.items():
                raw = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
    def test_accepts_compiled_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "app.tar.gz"
            self.archive(source)
            validator.extract(source, pathlib.Path(folder) / "destination", "v0.1.0")
            self.assertTrue((pathlib.Path(folder) / "destination/app/server/index.mjs").exists())
    def test_rejects_sources_secrets_paths_and_version_mismatch(self):
        for name in ("../escape", "app/../escape", "app/server/index.ts", "app/client/main.js.map", "app/.env", "app/uploads/file.png", ".private/server.js", "app/client\\private.js"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                source = pathlib.Path(folder) / "app.tar.gz"
                self.archive(source, {name: "private"})
                with self.assertRaises(ValueError):
                    validator.extract(source, pathlib.Path(folder) / "destination", "v0.1.0")
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "app.tar.gz"
            self.archive(source, version="0.2.0")
            with self.assertRaisesRegex(ValueError, "version"):
                validator.extract(source, pathlib.Path(folder) / "destination", "v0.1.0")
    def test_checks_payload_digest_before_extracting(self):
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "app.tar.gz"
            checksum = source.with_name(source.name + ".sha256")
            destination = pathlib.Path(folder) / "destination"
            self.archive(source, version="0.1.1")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            for raw in ("0" * 64 + "  app.tar.gz\n", digest + "  ../app.tar.gz\n", digest + "  different.tar.gz\n", digest + "  app.tar.gz\n" + digest + "  extra.tar.gz\n"):
                checksum.write_text(raw, encoding="ascii")
                with self.subTest(checksum=raw), self.assertRaisesRegex(ValueError, "checksum"):
                    validator.extract(source, destination, "v0.1.1", checksum)
                self.assertFalse(destination.exists())
            checksum.write_text(digest + "  app.tar.gz\n", encoding="ascii")
            validator.extract(source, destination, "v0.1.1", checksum)
            self.assertTrue((destination / "app/server/index.mjs").is_file())
    def test_refuses_duplicate_members_and_inconsistent_package_version(self):
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "app.tar.gz"
            with tarfile.open(source, "w:gz") as archive:
                for name in ("app/client/index.html", "app/client/./index.html"):
                    raw = b"duplicate"
                    info = tarfile.TarInfo(name)
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                validator.extract(source, pathlib.Path(folder) / "destination", "v0.1.1")
            self.archive(source, {"app/package.json": json.dumps({"name": "dnd-ai-arahort", "version": "0.1.0"})}, version="0.1.1")
            with self.assertRaisesRegex(ValueError, "package version"):
                validator.extract(source, pathlib.Path(folder) / "destination", "v0.1.1")
    def test_compose_and_workflow_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML only needed for offline YAML syntax check")
        configs = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in ROOT.glob("compose*.yaml")}
        base = configs["compose.yaml"]
        self.assertNotIn("ports", base["services"]["db"])
        self.assertNotIn("build", base["services"]["app"])
        self.assertEqual(base["services"]["app"]["pull_policy"], "never")
        self.assertEqual(base["services"]["app"]["tmpfs"], ["/tmp:rw,nosuid,nodev,size=256m"])
        self.assertEqual(base["services"]["app"]["environment"]["DND_MAINTENANCE_FILE"], "/run/dnd-control/maintenance.json")
        self.assertIn("${DND_STATE_DIR:-/var/lib/dnd-ai-arahort}/control:/run/dnd-control:ro", base["services"]["app"]["volumes"])
        self.assertFalse(any("docker.sock" in str(value) for value in base["services"]["app"].values()))
        self.assertIn("${BIND_IP:-0.0.0.0}", configs["compose.http.yaml"]["services"]["app"]["ports"][0])
        self.assertEqual(configs["compose.https.yaml"]["services"]["caddy"]["ports"][0], "80:80")
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())
        self.assertIn("jobs", workflow)
        self.assertTrue(any("gh release upload" in step.get("run", "") for step in workflow["jobs"]["publish"]["steps"]))
        packager = next(step["run"] for step in workflow["jobs"]["publish"]["steps"] if step.get("name") == "Package public installer distribution")
        self.assertIn("release-source.json", packager.split())
        self.assertNotIn("THIRD-PARTY-NOTICES.md", packager.split())
        steps = {step.get("name"): step for step in workflow["jobs"]["publish"]["steps"] if step.get("name")}
        self.assertIn("--checksum", steps["Verify compiled payload and version"]["run"])
        self.assertIn("app.tar.gz.sha256", steps["Download precompiled draft Release asset"]["run"])
        self.assertIn('data["isDraft"] is True', steps["Require an unpublished stable Release"]["run"])
        self.assertIn('docker --config "$anonymous_config" pull', steps["Verify anonymous image download and compiled version"]["run"])
        self.assertIn("org.opencontainers.image.source=https://github.com/", steps["Build and publish immutable image"]["with"]["labels"])
    def test_publisher_embeds_only_validated_author_repository_beside_release_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / "release.json"
            source = target.with_name("release-source.json")
            source.write_text(json.dumps({"format": 1, "repository": "unexpected/previous"}))
            command = [sys.executable, str(ROOT / "tools/publish-manifest.py"), "Publisher/OurGame", "v0.1.1", "sha256:" + "f" * 64, str(target)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(source.read_text()), {"format": 1, "repository": "Publisher/OurGame"})
            self.assertEqual(json.loads(target.read_text())["image"], "ghcr.io/publisher/ourgame@sha256:" + "f" * 64)
            before = (target.read_text(), source.read_text())
            command[2] = "https://untrusted.test/Publisher/OurGame"
            refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual((target.read_text(), source.read_text()), before)
if __name__ == "__main__":
    unittest.main()
