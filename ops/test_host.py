#!/usr/bin/env python3
"""Offline tests of release validation, data preservation and rollback. Arahort."""
import contextlib
import importlib.util
import http.client
import io
import json
import os
import pathlib
import re
import subprocess
import socketserver
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
def installer_module():
    try:
        import grp
    except ImportError:
        sys.modules["grp"] = types.SimpleNamespace()
    sys.modules["host"] = host_module
    spec = importlib.util.spec_from_file_location("dnd_installer", pathlib.Path(__file__).with_name("install.py"))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer
try:
    import fcntl
except ImportError:
    sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda *_args: None)
if not hasattr(socketserver, "UnixStreamServer"):
    socketserver.UnixStreamServer = socketserver.TCPServer
spec = importlib.util.spec_from_file_location("dnd_host", pathlib.Path(__file__).with_name("host.py"))
host_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_module)
class FakeHost(host_module.Host):
    def __init__(self, root):
        state = pathlib.Path(root) / "state"
        state.mkdir()
        for name in ("data", "home"):
            (state / name).mkdir()
            (state / name / "keep.txt").write_text("original " + name)
        host_module.set_env(pathlib.Path(root) / ".env", {"DND_STATE_DIR": str(state), "DND_IMAGE": "sha256:" + "a" * 64, "DND_VERSION": "0.1.0", "DND_MODEL_EFFORT": "high", "DND_START_MAINTENANCE": "0", "DND_REPOSITORY": "author/game", "PG_PASSWORD": "secret", "DND_SESSION_SECRET": "session", "DND_UPDATE_TOKEN": "updater", "AGY_PROXY": "http://user:pass@proxy.test:3128"})
        super().__init__(root)
        self.set_startup_maintenance(False)
        self.runtime = pathlib.Path(root) / "runtime"
        self.records = []
        self.fail_candidate = False
        self.fail_pull = False
    def run(self, args, **kwargs):
        self.records.append(("run", args))
        if args[:2] == ["docker", "pull"] and self.fail_pull:
            raise RuntimeError("registry unavailable")
        return subprocess.CompletedProcess(args, 0, ("sha256:" + "b" * 64 + "\n").encode(), b"")
    def compose(self, *args, **kwargs):
        self.records.append(("compose", args, dict(self.env)))
        if args and args[0] in ("stop", "up"):
            self.records.append(("durable-maintenance", args[0], self.startup_maintenance()))
        if "pg_dump" in args:
            kwargs["output"].write(b"database snapshot")
        if "pg_restore" in args:
            self.records.append(("restored-db", kwargs["input"].read()))
        return subprocess.CompletedProcess(args, 0, b"{}", b"")
    def internal(self, path, body=None):
        self.records.append(("internal", path, body))
        if path.endswith("update-info"):
            return {"schemaVersion": 1, "maintenance": True, "activeJobs": 0}
        return {"drained": True, "activeJobs": 0}
    def wait_healthy(self):
        self.records.append(("healthy", self.env["DND_IMAGE"]))
        if self.fail_candidate and self.env["DND_IMAGE"] != "sha256:" + "a" * 64:
            (self.state / "data" / "keep.txt").write_text("failed migration")
            raise RuntimeError("candidate health failed")
        return {"ok": True}
class HostTests(unittest.TestCase):
    def test_ubuntu_lts_versions_map_to_their_own_docker_repository_and_reject_other_systems(self):
        installer = installer_module()
        for version, codename in (("22.04", "jammy"), ("24.04", "noble"), ("26.04", "resolute")):
            with self.subTest(version=version):
                text = f'PRETTY_NAME="Ubuntu {version}.2 LTS"\nID=ubuntu\nVERSION_ID="{version}"\nVERSION_CODENAME={codename}\nUBUNTU_CODENAME={codename}\n'
                actual = installer.ubuntu_codename(text)
                self.assertEqual(actual, codename)
                repository = installer.docker_repository_source(actual)
                self.assertIn(f"\nSuites: {codename}\n", repository)
                self.assertIn("URIs: https://download.docker.com/linux/ubuntu\n", repository)
                self.assertEqual(installer.ubuntu_codename(f'ID="ubuntu"\nVERSION_ID={version}\n'), codename)
        invalid = ['ID=debian\nVERSION_ID="24.04"\n', 'ID=linuxmint\nID_LIKE=ubuntu\nVERSION_ID="22.04"\n', 'ID=ubuntu\nVERSION_ID="20.04"\n', 'ID=ubuntu\nVERSION_ID="24.10"\n', 'ID=ubuntu\nVERSION_ID="26.10"\n', 'ID=ubuntu\nVERSION_ID="22.04.2"\n', 'ID=ubuntu\nVERSION_ID="22.04"\nVERSION_CODENAME=noble\n', 'ID=ubuntu\nVERSION_ID="26.04"\nUBUNTU_CODENAME="resolute;command"\n', 'ID=ubuntu\nVERSION_ID="22.04"\nVERSION_ID="24.04"\n', 'ID=ubuntu\nVERSION_ID="24.04\n']
        for text in invalid:
            with self.subTest(invalid=text), self.assertRaises(ValueError):
                installer.ubuntu_codename(text)
        with self.assertRaises(ValueError):
            installer.docker_repository_source("jammy\nInjected: yes")
    def test_existing_docker_for_amnezia_is_inspected_without_apt_service_or_proxy_changes(self):
        installer = installer_module()
        with patch.object(installer.shutil, "which", return_value="/usr/bin/docker"), patch.object(installer, "command") as commands, patch.object(installer, "atomic", side_effect=AssertionError("Existing Docker configuration must not be written")), patch.object(installer.subprocess, "run", side_effect=AssertionError("No extra systemctl or apt probes permitted")):
            installer.ensure_docker("http://private:password@proxy.test:3128", "jammy")
            self.assertEqual([entry.args[0] for entry in commands.call_args_list], [["docker", "info", "--format", "Docker daemon ready"], ["docker", "compose", "version"]])
        with patch.object(installer.shutil, "which", return_value="/usr/bin/docker"), patch.object(installer, "command", side_effect=subprocess.CalledProcessError(1, ["docker", "info"])) as commands, patch.object(installer, "atomic", side_effect=AssertionError("No repair by overwriting config")):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.ensure_docker("", "jammy")
            self.assertEqual(commands.call_count, 1, "A failed existing daemon must not be repaired by apt or restart")
    def test_installer_requires_safe_tar_filter_on_python_31012_or_newer(self):
        installer = installer_module()
        for version in ((3, 10, 12), (3, 12, 0), (3, 14, 0)):
            with self.subTest(version=version), patch.object(installer.sys, "version_info", version), patch.object(installer.tarfile, "data_filter", lambda *_args: None, create=True):
                installer.validate_python_runtime()
        with patch.object(installer.sys, "version_info", (3, 10, 11)), self.assertRaisesRegex(ValueError, "3.10.12"):
            installer.validate_python_runtime()
        with patch.object(installer.sys, "version_info", (3, 12, 0)), patch.object(installer.tarfile, "data_filter", None), self.assertRaisesRegex(ValueError, "data_filter"):
            installer.validate_python_runtime()
    def test_env_roundtrip_preserves_unrelated_values_and_no_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / ".env"
            path.write_text("# retained\nUNRELATED=value\n")
            value = "http://user:p%40ss$word%27@host:3128/path#fragment"
            host_module.set_env(path, {"AGY_PROXY": value, "LITERAL": "'\\$()# safe"})
            self.assertEqual(host_module.read_env(path)["AGY_PROXY"], value)
            self.assertEqual(host_module.read_env(path)["LITERAL"], "'\\$()# safe")
            host_module.set_env(path, {"AGY_PROXY": ""})
            self.assertEqual(host_module.read_env(path)["UNRELATED"], "value")
            self.assertTrue(path.read_text().startswith("# retained\n"))
    def test_manifest_rejects_other_registry_repository_and_invalid_schema(self):
        raw = {"format": 1, "version": "0.1.1", "digest": "sha256:" + "f" * 64, "image": "ghcr.io/author/game@sha256:" + "f" * 64, "schemaVersion": 1, "minimumUpdaterVersion": 1, "rollbackCompatible": True}
        self.assertEqual(host_module.validate_release(raw, "author/game"), raw)
        for key, value in (("image", "ghcr.io/attacker/game@sha256:" + "f" * 64), ("schemaVersion", 2), ("digest", "latest"), ("version", "0.1.1;command"), ("minimumUpdaterVersion", 2)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                host_module.validate_release({**raw, key: value}, "author/game")
    def test_success_stages_before_drain_and_keeps_writes_closed_until_healthy(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            result = host.apply_image("ghcr.io/author/game@sha256:" + "f" * 64, "0.1.1", remote=True, compatibility=True)
            self.assertEqual(result["phase"], "complete")
            self.assertEqual(host.env["DND_START_MAINTENANCE"], "0")
            self.assertEqual(json.loads(host.control_path.read_text()), {"maintenance": False})
            self.assertFalse(host_module.Host(folder).startup_maintenance(), "A new process/container restart must observe writes reopened")
            self.assertTrue(all(row[2] for row in host.records if row[0] == "durable-maintenance"))
            self.assertEqual(host.records[0][1][:2], ["docker", "pull"])
            up = next(row for row in host.records if row[0] == "compose" and row[1][0] == "up")
            self.assertEqual(up[2]["DND_START_MAINTENANCE"], "1")
            backup = pathlib.Path(result["backup"])
            self.assertTrue((backup / "backup.json").is_file())
            self.assertEqual((host.state / "data/keep.txt").read_text(), "original data")
            healthy_index = next(i for i, row in enumerate(host.records) if row[0] == "healthy")
            reopen_index = next(i for i, row in enumerate(host.records) if row[0] == "internal" and row[2] == {"enabled": False})
            self.assertLess(healthy_index, reopen_index)
    def test_failed_candidate_restores_database_files_configuration_and_previous_image(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            host.fail_candidate = True
            with self.assertRaisesRegex(RuntimeError, "candidate health failed"):
                host.apply_image("ghcr.io/author/game@sha256:" + "f" * 64, "0.1.1", remote=True)
            self.assertEqual(host.status()["phase"], "failed")
            self.assertTrue(host.status()["rolledBack"])
            self.assertEqual(host.env["DND_IMAGE"], "sha256:" + "a" * 64)
            self.assertEqual(host.env["DND_VERSION"], "0.1.0")
            self.assertEqual(host.env["DND_START_MAINTENANCE"], "0")
            self.assertFalse(host_module.Host(folder).startup_maintenance())
            self.assertEqual((host.state / "data/keep.txt").read_text(), "original data")
            self.assertIn(("restored-db", b"database snapshot"), host.records)
    def test_failed_download_does_not_stop_or_backup_application(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            host.fail_pull = True
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                host.apply_image("ghcr.io/author/game@sha256:" + "f" * 64, "0.1.1", remote=True)
            self.assertFalse(any(row[0] == "compose" and "stop" in row[1] for row in host.records))
            self.assertFalse((host.state / "backups").exists())
    def test_failure_after_reopening_preserves_new_writes_instead_of_restoring_old_data(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            reopen = host.reopen
            def lost_confirmation():
                reopen()
                (host.state / "data/keep.txt").write_text("accepted after candidate opened")
                raise RuntimeError("reopen confirmation lost")
            host.reopen = lost_confirmation
            with self.assertRaisesRegex(RuntimeError, "confirmation lost"):
                host.apply_image("ghcr.io/author/game@sha256:" + "f" * 64, "0.1.1", remote=True)
            self.assertEqual((host.state / "data/keep.txt").read_text(), "accepted after candidate opened")
            self.assertEqual(host.env["DND_VERSION"], "0.1.1")
            self.assertTrue(host.status()["recoveryRequired"])
            self.assertTrue(host_module.Host(folder).startup_maintenance())
            self.assertFalse(any(row[0] == "restored-db" for row in host.records))
    def test_backup_integrity_checked_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            backup = host.backup_stopped()
            (backup / "database.dump").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                host.restore_stopped(backup)
            self.assertEqual((host.state / "data/keep.txt").read_text(), "original data")
    def test_backup_normalizes_absolute_container_symlinks_and_rejects_host_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            link = tarfile.TarInfo("home/.gemini/antigravity-cli/cli.log")
            link.type = tarfile.SYMTYPE
            link.linkname = "/home/dnd/.gemini/antigravity-cli/log/current.log"
            self.assertEqual(host.backup_member(link).linkname, "log/current.log")
            link.linkname = "/etc/shadow"
            with self.assertRaisesRegex(ValueError, "outside"):
                host.backup_member(link)
            link.linkname = "../../../../../../etc/shadow"
            with self.assertRaisesRegex(ValueError, "escapes"):
                host.backup_member(link)
    def test_recovery_blocks_a_second_update(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            host.update_status(recoveryRequired=True)
            with self.assertRaisesRegex(RuntimeError, "recovery"):
                host.apply_image("image:tag", "0.1.1")
            self.assertEqual(host.records, [])
    def test_durable_control_overrides_stale_container_environment_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            host.drain()
            self.assertEqual(json.loads(host.control_path.read_text()), {"maintenance": True})
            self.assertTrue(host_module.Host(folder).startup_maintenance())
            host.reopen()
            host_module.set_env(host.env_path, {"DND_START_MAINTENANCE": "1"})
            restarted = host_module.Host(folder)
            self.assertFalse(restarted.startup_maintenance(), "Durable false must override the env=1 left in an existing Docker container")
            for value in ('{"maintenance":"false"}', '{"maintenance":0}', '{"maintenance":false,"extra":true}', 'invalid'):
                host.control_path.write_text(value)
                self.assertTrue(restarted.startup_maintenance())
            host.control_path.unlink()
            self.assertTrue(restarted.startup_maintenance())
            host.set_startup_maintenance(True)
            self.assertTrue(host_module.Host(folder).startup_maintenance())
    def test_scrubs_proxy_password_database_and_update_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            output = host.redact("secret session updater http://user:pass@proxy.test:3128")
            for secret in ("secret", "session", "updater", "user:pass"):
                self.assertNotIn(secret, output)
    def test_login_preserves_only_remote_ssh_markers_without_values_or_app_secrets_in_argv(self):
        environment = {"SSH_CONNECTION": "192.0.2.20 40400 192.0.2.10 22", "SSH_CLIENT": "192.0.2.20 40400 22", "SSH_TTY": "/dev/pts/4", "PG_PASSWORD": "never-copy-db-password", "GOOGLE_API_KEY": "never-copy-google-key", "DND_UPDATE_TOKEN": "never-copy-update-token", "HTTP_PROXY": "http://user:password@proxy.test:3128"}
        arguments = host_module.build_login_args(pathlib.Path("/opt/dnd-ai-arahort"), "https", environment)
        forwarded = [arguments[index + 1] for index, item in enumerate(arguments[:-1]) if item == "-e"]
        self.assertEqual(forwarded, ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
        self.assertIn("compose.https.yaml", arguments)
        self.assertEqual(arguments[-1], "agy")
        self.assertNotIn("-T", arguments, "Interactive OAuth needs the real terminal")
        for value in environment.values():
            self.assertNotIn(value, arguments)
        self.assertNotIn("PG_PASSWORD", forwarded)
        self.assertNotIn("GOOGLE_API_KEY", forwarded)
        removed = [arguments[index + 1] for index, item in enumerate(arguments[:-1]) if item == "-u"]
        for key in ("PG_PASSWORD", "DATABASE_URL", "DND_SESSION_SECRET", "DND_UPDATE_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            self.assertIn(key, removed)
        partial = host_module.build_login_args(pathlib.Path("/opt/dnd-ai-arahort"), "http", {"SSH_CONNECTION": "remote-marker"})
        self.assertEqual(partial.count("-e"), 1)
        with self.assertRaisesRegex(RuntimeError, "sudo --preserve-env=SSH_CONNECTION,SSH_CLIENT,SSH_TTY dndctl login"):
            host_module.build_login_args(pathlib.Path("/opt/dnd-ai-arahort"), "http", {"PG_PASSWORD": "secret"})
    def test_socket_protocol_rejects_arbitrary_commands_fields_and_oversized_body(self):
        with tempfile.TemporaryDirectory() as folder:
            host = FakeHost(folder)
            with socketserver.TCPServer(("127.0.0.1", 0), host_module.Handler) as server:
                server.host = host
                server.busy = False
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for method, path, body, expected in (("GET", "/v1/status", None, 200), ("POST", "/v1/run", "{}", 404), ("POST", "/v1/apply", '{"version":"0.1.1","image":"attacker/image"}', 400), ("POST", "/v1/check", '{"url":"https://attacker.test"}', 400), ("POST", "/v1/apply", '{"version":"0.1.1;command"}', 400), ("POST", "/v1/check", "x" * 1025, 400)):
                        with self.subTest(path=path, body=body):
                            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
                            response = connection.getresponse()
                            data = json.loads(response.read())
                            self.assertEqual(response.status, expected, data)
                            connection.close()
                    self.assertEqual(host.records, [])
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
    def test_agy_initialization_preserves_existing_settings_and_authentication(self):
        try:
            import grp
        except ImportError:
            sys.modules["grp"] = types.SimpleNamespace()
        sys.modules["host"] = host_module
        spec = importlib.util.spec_from_file_location("dnd_installer", pathlib.Path(__file__).with_name("install.py"))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            home = pathlib.Path(folder)
            settings = home / ".gemini/antigravity-cli/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"model": "Existing model", "permissions": {"allow": ["read_file(assets)"]}}))
            auth = settings.parent / "account-test.json"
            auth.write_text("existing-account-sentinel")
            with patch.object(os, "chown", create=True):
                installer.initialize_agy(home)
            changed = json.loads(settings.read_text())
            self.assertEqual(changed["model"], "Existing model")
            self.assertEqual(changed["permissions"]["allow"], ["read_file(assets)"])
            self.assertIn("command(*)", changed["permissions"]["deny"])
            self.assertIn("mcp(*)", changed["permissions"]["deny"])
            self.assertIn("read_url(*)", changed["permissions"]["deny"])
            self.assertEqual(changed["toolPermission"], "request-review")
            self.assertEqual(auth.read_text(), "existing-account-sentinel")
            arguments, environment = installer.build_proxy_args("http://account:secret@proxy.test:3128")
            self.assertFalse(any("secret" in argument or "proxy.test" in argument or "=" in argument for argument in arguments))
            self.assertEqual(environment["HTTPS_PROXY"], "http://account:secret@proxy.test:3128")
            self.assertEqual(arguments.count("--build-arg"), 4)
            self.assertEqual(installer.build_proxy_args(""), ([], {}))
    def test_installer_uses_only_author_release_config_and_refuses_unbound_remote_before_changes(self):
        try:
            import grp
        except ImportError:
            sys.modules["grp"] = types.SimpleNamespace()
        sys.modules["host"] = host_module
        spec = importlib.util.spec_from_file_location("dnd_installer", pathlib.Path(__file__).with_name("install.py"))
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder) / "source"
            source.mkdir()
            config = source / "release-source.json"
            config.write_text(json.dumps({"format": 1, "repository": ""}))
            with patch.object(installer, "ask", side_effect=AssertionError("Repository selection must never prompt")), patch.object(installer, "yes", side_effect=AssertionError("Repository selection must never ask permission")), patch.object(installer, "ensure_docker", side_effect=AssertionError("Invalid author config must fail before Docker changes")):
                self.assertEqual(installer.release_repository(source, local=True), "")
                with self.assertRaisesRegex(ValueError, "--local"):
                    installer.release_repository(source)
            for data in ({"format": True, "repository": "author/game"}, {"format": 2, "repository": "author/game"}, {"format": 1, "repository": "https://github.com/author/game"}, {"format": 1, "repository": "author/game;command"}, {"format": 1, "repository": None}, {"format": 1, "repository": "author/game", "url": "https://untrusted.test"}, []):
                with self.subTest(data=data):
                    config.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        installer.release_repository(source, local=True)
            config.write_text(json.dumps({"format": 1, "repository": "Publisher/OurGame"}))
            self.assertEqual(installer.release_repository(source), "Publisher/OurGame")
            self.assertEqual(installer.release_repository(source, local=True), "Publisher/OurGame")
            with patch.object(installer, "SOURCE", source):
                target = pathlib.Path(folder) / "installation"
                installer.copy_distribution(target, local=False)
            self.assertEqual(json.loads((target / "release-source.json").read_text()), {"format": 1, "repository": "Publisher/OurGame"})
            config.unlink()
            with self.assertRaisesRegex(ValueError, "release-source.json"):
                installer.release_repository(source, local=True)
            config.write_text(json.dumps({"format": 1, "repository": ""}))
            target = pathlib.Path(folder) / "installation-without-state"
            original_read = pathlib.Path.read_text
            original_match = re.fullmatch
            def read_for_test(path, *args, **kwargs):
                return 'ID=ubuntu\nVERSION_ID="24.04"\n' if path.as_posix().endswith("/etc/os-release") else original_read(path, *args, **kwargs)
            def match_for_test(pattern, value, *args, **kwargs):
                # The test runs on Windows too; only bypass the host-specific absolute-path check.
                return True if pattern == r"/[A-Za-z0-9_./-]+" else original_match(pattern, value, *args, **kwargs)
            with patch.object(installer, "SOURCE", source), patch.object(installer.os, "geteuid", return_value=0, create=True), patch.object(installer.platform, "machine", return_value="x86_64"), patch.object(pathlib.Path, "read_text", read_for_test), patch.object(re, "fullmatch", match_for_test), patch.object(sys, "argv", ["install.py", "--directory", str(target)]), patch.object(installer, "proxy_question") as proxy, patch.object(installer, "ensure_docker") as docker:
                with self.assertRaisesRegex(ValueError, "--local"):
                    installer.main()
                proxy.assert_not_called()
                docker.assert_not_called()
            self.assertFalse(target.exists(), "Unbound remote installer must not create installation files")
            target.mkdir()
            saved = target / ".env"
            saved.write_text("PRESERVED='configuration'\n")
            installed = types.SimpleNamespace(env={"DND_INSTALL_MODE": "local", "AGY_PROXY": "saved-proxy"})
            with patch.object(installer, "SOURCE", source), patch.object(installer.os, "geteuid", return_value=0, create=True), patch.object(installer.platform, "machine", return_value="x86_64"), patch.object(pathlib.Path, "read_text", read_for_test), patch.object(re, "fullmatch", match_for_test), patch.object(sys, "argv", ["install.py", "--resume", "--directory", str(target)]), patch.object(installer, "release_repository", side_effect=AssertionError("Resume must keep the saved installation repository")), patch.object(installer, "Host", return_value=installed), patch.object(installer, "ensure_docker") as docker, patch.object(installer, "finish") as finish:
                installer.main()
                docker.assert_called_once_with("saved-proxy", "noble")
                finish.assert_called_once_with(installed, True)
            self.assertEqual(saved.read_text(), "PRESERVED='configuration'\n")
if __name__ == "__main__":
    unittest.main()
