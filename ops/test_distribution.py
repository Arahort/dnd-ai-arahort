#!/usr/bin/env python3
"""Compiled package and deployment contract checks. Arahort."""
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("publish_validate", ROOT / "tools/publish-validate.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
class DistributionTests(unittest.TestCase):
    def archive(self, path, extra=None, version="0.1.0"):
        files = {"app/server/index.mjs": "export const ok = true", "app/server/admin-cli.mjs": "export const ok = true", "app/client/index.html": "<!doctype html><main>test</main>", "app/package.json": json.dumps({"name": "dnd-ai-arahort", "dependencies": {}}), "app/package-lock.json": "{}", "app/release.json": json.dumps({"version": version, "schemaVersion": 1})}
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
        for name in ("../escape", "app/../escape", "app/server/index.ts", "app/client/main.js.map", "app/.env", "app/uploads/file.png", ".private/server.js"):
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
if __name__ == "__main__":
    unittest.main()
