#!/usr/bin/env python3
"""Restricted host operations. Author: Arahort <alex@arahort.pro>."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import http.server
import json
import os
import pathlib
import posixpath
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
ROOT = pathlib.Path(__file__).resolve().parent.parent
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
ACTIVE = {"staging", "draining", "backing-up", "installing", "health-check", "opening", "rolling-back"}
def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
def file_hash(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
def atomic(path, data, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temp.unlink(missing_ok=True)
def read_env(path):
    values = {}
    for number, raw in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid .env line {number}")
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        elif any(char in value for char in "'\"\n\r"):
            raise ValueError(f"Unsupported .env value at line {number}")
        values[key] = value
    return values
def env_line(key, value):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in str(value) for c in "\n\r\x00"):
        raise ValueError("Invalid environment entry")
    # Compose single quotes prevent $, #, and whitespace interpolation.
    return key + "='" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
def set_env(path, changes):
    path = pathlib.Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(changes)
    out = []
    for line in lines:
        key = line.partition("=")[0]
        if key in changes:
            if key in remaining:
                out.append(env_line(key, remaining.pop(key)))
        else:
            out.append(line)
    out.extend(env_line(key, value) for key, value in remaining.items())
    atomic(path, "\n".join(out) + "\n")
def version_tuple(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError("Only stable semantic release versions are accepted")
    return tuple(int(part) for part in match.groups())
def validate_release(raw, repository):
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Configure DND_REPOSITORY=owner/repository first")
    if not isinstance(raw, dict) or raw.get("format") != 1:
        raise ValueError("Unsupported release manifest")
    version_tuple(raw.get("version", ""))
    digest = raw.get("digest", "")
    if not DIGEST.fullmatch(digest):
        raise ValueError("Invalid image digest")
    expected = "ghcr.io/" + repository.lower() + "@" + digest
    if raw.get("image") != expected:
        raise ValueError("Release image is outside the configured trusted repository")
    if raw.get("schemaVersion") != 1 or raw.get("minimumUpdaterVersion") != 1:
        raise ValueError("This release requires a newer updater; follow manual upgrade instructions")
    if not isinstance(raw.get("rollbackCompatible"), bool):
        raise ValueError("Missing rollback compatibility declaration")
    return raw
def build_login_args(root, mode, environment):
    ssh_keys = [key for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY") if environment.get(key)]
    if not ssh_keys:
        raise RuntimeError("SSH environment is missing. From your SSH terminal run: sudo --preserve-env=SSH_CONNECTION,SSH_CLIENT,SSH_TTY dndctl login")
    if mode not in ("http", "https", "external"):
        raise ValueError("Unsupported network mode")
    root = pathlib.Path(root)
    network = "compose.https.yaml" if mode == "https" else "compose.http.yaml"
    forwarded = [part for key in ssh_keys for part in ("-e", key)]
    # Existing container proxy/HOME are retained; Google activation must not inherit app secrets.
    denied = ("PG_HOST", "PG_PORT", "PG_DATABASE", "PG_USER", "PG_PASSWORD", "DATABASE_URL", "PGPASSWORD", "PGPASSFILE", "DND_SESSION_SECRET", "SESSION_SECRET", "DND_UPDATE_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
    scrubbed = [part for key in denied for part in ("-u", key)]
    return ["docker", "--host", "unix:///var/run/docker.sock", "compose", "--project-name", "dnd-ai-arahort", "--env-file", str(root / ".env"), "-f", "compose.yaml", "-f", network, "exec", "--workdir", "/data/work", *forwarded, "-e", "AGY_CLI_DISABLE_AUTO_UPDATE=true", "app", "env", *scrubbed, "agy", "--dangerously-skip-permissions"]
class Host:
    def __init__(self, root=ROOT):
        self.root = pathlib.Path(root).resolve()
        self.env_path = self.root / ".env"
        self.env = read_env(self.env_path)
        self.state = pathlib.Path(self.env.get("DND_STATE_DIR", "/var/lib/dnd-ai-arahort")).resolve()
        self.runtime = pathlib.Path("/run/dnd-ai-arahort")
        self.status_path = self.state / "updater-status.json"
        self.release_path = self.state / "checked-release.json"
        self.control_dir = self.state / "control"
        self.control_path = self.control_dir / "maintenance.json"
    def reload(self):
        self.env = read_env(self.env_path)
    def startup_maintenance(self):
        try:
            if self.control_path.is_symlink() or self.control_path.stat().st_size > 4096:
                return True
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {"maintenance"} or type(data["maintenance"]) is not bool:
                return True
            return data["maintenance"]
        except (OSError, ValueError):
            return True
    def set_startup_maintenance(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Maintenance state must be boolean")
        if self.control_dir.is_symlink() or self.control_path.is_symlink():
            raise ValueError("Maintenance control must not be a symlink")
        self.control_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chmod(self.control_dir, 0o755)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(self.control_dir, 0, 0)
        atomic(self.control_path, json.dumps({"maintenance": enabled}) + "\n", 0o644)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(self.control_path, 0, 0)
        # Retain the flag for old compatible images, but current containers read the durable file.
        set_env(self.env_path, {"DND_START_MAINTENANCE": "1" if enabled else "0"})
        self.reload()
    def initialize_control(self):
        if self.control_path.exists():
            enabled = self.startup_maintenance()
        else:
            enabled = self.env.get("DND_START_MAINTENANCE") == "1"
        if self.status().get("recoveryRequired") or self.status().get("phase") in ACTIVE:
            enabled = True
        self.set_startup_maintenance(enabled)
    def initialize_agy_workspace(self):
        data = self.state / "data"
        if data.is_symlink() or data.resolve() != self.state / "data":
            raise ValueError("AGY data directory cannot be a symlink or leave installation state")
        created_data = not data.exists()
        data.mkdir(mode=0o700, exist_ok=True)
        work = data / "work"
        if work.is_symlink() or work.resolve() != data / "work":
            raise ValueError("AGY workspace cannot be a symlink or leave installation data")
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            data_fd = os.open(data, flags)
            try:
                if created_data:
                    os.fchown(data_fd, 1000, 1000)
                try:
                    os.mkdir("work", mode=0o700, dir_fd=data_fd)
                except FileExistsError:
                    pass
                work_fd = os.open("work", flags, dir_fd=data_fd)
                try:
                    os.fchown(work_fd, 1000, 1000)
                    os.fchmod(work_fd, 0o700)
                finally:
                    os.close(work_fd)
            finally:
                os.close(data_fd)
        else:
            work.mkdir(mode=0o700, exist_ok=True)
            if created_data:
                os.chown(data, 1000, 1000)
            os.chown(work, 1000, 1000)
            os.chmod(work, 0o700)
        return work
    def redact(self, value):
        text = str(value)
        for key in ("AGY_PROXY", "PG_PASSWORD", "DND_UPDATE_TOKEN", "DND_SESSION_SECRET"):
            secret = self.env.get(key, "")
            if secret:
                text = text.replace(secret, "<redacted>")
        text = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "http://<redacted>@", text)
        return text[-2000:]
    def run(self, args, *, timeout=120, input=None, output=None, check=True, extra_env=None, stream=False):
        # No shell, no password arguments, no inherited host proxy or Docker context.
        if stream and output is None:
            raise ValueError("Streaming commands require an explicit output stream")
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}
        env.update(extra_env or {})
        if args and args[0] == "docker":
            args = ["docker", "--host", "unix:///var/run/docker.sock", *args[1:]]
        stdin = input if hasattr(input, "read") else None
        result = subprocess.run(args, cwd=self.root, env=env, input=None if stdin else input, stdin=stdin, stdout=output or subprocess.PIPE, stderr=subprocess.STDOUT if stream else subprocess.PIPE, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(self.redact((result.stderr or b"").decode("utf-8", "replace")) or f"Command failed (exit {result.returncode}); see command output above")
        return result
    def compose(self, *args, **kwargs):
        mode = self.env.get("DND_NETWORK_MODE", "http")
        if mode not in ("http", "https", "external"):
            raise ValueError("Unsupported network mode")
        files = ["-f", str(self.root / "compose.yaml"), "-f", str(self.root / ("compose.https.yaml" if mode == "https" else "compose.http.yaml"))]
        return self.run(["docker", "compose", "--project-name", "dnd-ai-arahort", "--env-file", str(self.env_path), *files, *args], **kwargs)
    def status(self):
        try:
            data = json.loads(self.status_path.read_text())
        except (FileNotFoundError, ValueError):
            data = {"phase": "idle", "available": False, "progress": 0, "error": None}
        data.update(enabled=bool(self.env.get("DND_REPOSITORY")), currentVersion=self.env.get("DND_VERSION", "local"), currentImage=self.env.get("DND_IMAGE"), schemaVersion=1)
        return data
    def update_status(self, **changes):
        status = self.status()
        status.update(changes, updatedAt=now())
        atomic(self.status_path, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
        return status
    @contextlib.contextmanager
    def operation(self):
        self.runtime.mkdir(mode=0o750, parents=True, exist_ok=True)
        with (self.runtime / "operation.lock").open("w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another installation operation is in progress") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    def get_json(self, url, maximum=262144):
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        proxy = self.env.get("AGY_PROXY", "")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy} if proxy else {}))
        request = urllib.request.Request(url, headers={"User-Agent": "dnd-ai-arahort-updater/1", "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json"})
        with opener.open(request, timeout=25) as response:
            final = urllib.parse.urlsplit(response.url)
            if final.scheme != "https" or final.hostname not in ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"):
                raise ValueError("Unexpected release download host")
            content = response.read(maximum + 1)
            if len(content) > maximum:
                raise ValueError("Release response is too large")
        return json.loads(content)
    def check(self):
        self.reload()
        repository = self.env.get("DND_REPOSITORY", "")
        if not REPOSITORY.fullmatch(repository):
            raise ValueError("Remote releases are not configured; use dndctl update-local for a local test")
        release = self.get_json("https://api.github.com/repos/" + repository + "/releases/latest")
        tag = release.get("tag_name", "")
        version_tuple(tag)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("Only published stable releases are accepted")
        manifest_url = "https://github.com/" + repository + "/releases/download/" + tag + "/release.json"
        manifest = validate_release(self.get_json(manifest_url), repository)
        if version_tuple(manifest["version"]) != version_tuple(tag):
            raise ValueError("Tag and release manifest differ")
        current = self.env.get("DND_VERSION", "local")
        available = current == "local" or version_tuple(manifest["version"]) > version_tuple(current)
        manifest["checkedAt"] = now()
        manifest["publishedAt"] = release.get("published_at")
        manifest["notes"] = str(release.get("body") or "")[:12000]
        manifest["url"] = "https://github.com/" + repository + "/releases/tag/" + tag
        atomic(self.release_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return self.update_status(phase="idle", available=available, checkedAt=manifest["checkedAt"], targetVersion=manifest["version"], release={key: manifest.get(key) for key in ("version", "notes", "publishedAt", "url", "rollbackCompatible")}, error=None, progress=0)
    def internal(self, path, body=None):
        # The secret is read in the container, never placed in docker's arguments.
        script = "const p=process.argv[1],b=JSON.parse(process.argv[2]);fetch('http://127.0.0.1:3334'+p,{method:b===null?'GET':'POST',headers:{'content-type':'application/json','X-DND-Update-Token':process.env.DND_UPDATE_TOKEN},body:b===null?undefined:JSON.stringify(b)}).then(async r=>{if(!r.ok)throw Error('Internal HTTP '+r.status);process.stdout.write(await r.text())}).catch(e=>{console.error(e.message);process.exit(1)})"
        result = self.compose("exec", "-T", "app", "node", "-e", script, path, json.dumps(body), timeout=20)
        return json.loads(result.stdout)
    def drain(self):
        self.set_startup_maintenance(True)
        self.update_status(phase="draining", progress=20, error=None)
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            result = self.internal("/api/internal/maintenance", {"enabled": True})
            if result.get("drained") and result.get("activeJobs") == 0:
                return
            time.sleep(2)
        raise RuntimeError("Active jobs did not finish within 20 minutes; no update applied")
    def wait_healthy(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = self.compose("exec", "-T", "app", "node", "-e", "fetch('http://127.0.0.1:3334/api/health').then(r=>{if(!r.ok)process.exit(1);return r.json()}).then(v=>process.stdout.write(JSON.stringify(v))).catch(()=>process.exit(1))", timeout=10, check=False)
            if result.returncode == 0:
                info = json.loads(result.stdout)
                expected = self.env.get("DND_VERSION", "local")
                if expected != "local" and info.get("version", "").lstrip("v") != expected.lstrip("v"):
                    raise RuntimeError("Application version does not match the selected image")
                return info
            time.sleep(3)
        raise RuntimeError("Application health check failed")
    def reopen(self):
        self.set_startup_maintenance(False)
        try:
            self.internal("/api/internal/maintenance", {"enabled": False})
        except Exception:
            # A lost response may follow successful reopening. Never trigger a data rollback here.
            self.set_startup_maintenance(True)
            raise
    def backup_stopped(self):
        self.update_status(phase="backing-up", progress=35)
        target = self.state / "backups" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target.mkdir(mode=0o700, parents=True)
        os.chmod(target.parent, 0o700)
        try:
            with (target / "database.dump").open("wb") as stream:
                self.compose("exec", "-T", "db", "pg_dump", "-U", "dnd", "-d", "dnd", "-Fc", output=stream, timeout=600)
            shutil.copyfile(self.env_path, target / "installation.env")
            with tarfile.open(target / "files.tar.gz", "w:gz") as archive:
                for name in ("data", "home", "caddy-data", "caddy-config"):
                    if (self.state / name).exists():
                        archive.add(self.state / name, arcname=name, filter=self.backup_member)
            hashes = {name: file_hash(target / name) for name in ("database.dump", "files.tar.gz", "installation.env")}
            atomic(target / "backup.json", json.dumps({"format": 1, "createdAt": now(), "image": self.env["DND_IMAGE"], "version": self.env.get("DND_VERSION"), "schemaVersion": 1, "sha256": hashes}, indent=2))
            for path in target.iterdir():
                os.chmod(path, 0o600)
            return target
        except Exception:
            # Keep incomplete files for operator inspection, never select them as a valid backup.
            atomic(target / "INCOMPLETE", "Backup failed; do not restore automatically.\n")
            raise
    def backup_member(self, member):
        # AGY may use absolute /home/dnd/... log symlinks. Store an equivalent relative
        # link so extraction on the host stays inside the persistent state directory.
        if member.issym():
            target = member.linkname
            base = pathlib.PurePosixPath(member.name).parts[0]
            if target.startswith("/"):
                aliases = {"/home/dnd": "home", "/data": "caddy-data" if base == "caddy-data" else "data", "/config": "caddy-config", str(self.state).replace("\\", "/"): ""}
                mapped = None
                for prefix, archive_root in aliases.items():
                    if target == prefix or target.startswith(prefix + "/"):
                        mapped = posixpath.join(archive_root, target[len(prefix):].lstrip("/"))
                        break
                if mapped is None:
                    raise ValueError("Backup symlink points outside persistent installation data")
            else:
                mapped = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), target))
            normalized = pathlib.PurePosixPath(mapped)
            if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts or normalized.parts[0] not in ("data", "home", "caddy-data", "caddy-config"):
                raise ValueError("Backup symlink escapes persistent installation data")
            member.linkname = posixpath.relpath(str(normalized), posixpath.dirname(member.name))
        return member
    def restore_stopped(self, target):
        self.set_startup_maintenance(True)
        target = pathlib.Path(target).resolve()
        if target.parent != (self.state / "backups").resolve() or (target / "INCOMPLETE").exists():
            raise ValueError("Only a complete backup inside this installation is accepted")
        meta = json.loads((target / "backup.json").read_text())
        if meta.get("format") != 1:
            raise ValueError("Unsupported backup")
        for name in ("database.dump", "files.tar.gz", "installation.env"):
            if file_hash(target / name) != meta["sha256"].get(name):
                raise ValueError("Backup checksum mismatch")
        with tarfile.open(target / "files.tar.gz", "r:gz") as archive:
            for member in archive.getmembers():
                name = pathlib.PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] not in ("data", "home", "caddy-data", "caddy-config"):
                    raise ValueError("Unsafe backup entry")
                if member.issym():
                    link = pathlib.PurePosixPath(member.linkname)
                    destination = (self.state / member.name).parent.joinpath(member.linkname).resolve()
                    if link.is_absolute() or not destination.is_relative_to(self.state):
                        raise ValueError("Backup symlink points outside installation state")
                elif member.islnk():
                    link = pathlib.PurePosixPath(member.linkname)
                    if link.is_absolute() or ".." in link.parts or not link.parts or link.parts[0] not in ("data", "home", "caddy-data", "caddy-config"):
                        raise ValueError("Unsafe backup hard link")
                elif not (member.isfile() or member.isdir()):
                    raise ValueError("Backup special files require manual recovery")
            for name in ("data", "home", "caddy-data", "caddy-config"):
                path = (self.state / name).resolve()
                if path.parent != self.state:
                    raise ValueError("Unsafe state path")
                if path.exists():
                    shutil.rmtree(path)
            archive.extractall(self.state, filter="data")
        with (target / "database.dump").open("rb") as stream:
            self.compose("exec", "-T", "db", "pg_restore", "-U", "dnd", "-d", "dnd", "--clean", "--if-exists", "--exit-on-error", input=stream, timeout=600)
        atomic(self.env_path, (target / "installation.env").read_text())
        self.set_startup_maintenance(True)
        for name in ("data", "home"):
            self.run(["chown", "-R", "1000:1000", str(self.state / name)])
    def apply_image(self, image, version, *, remote=False, compatibility=False, changes=None):
        self.reload()
        if self.status().get("recoveryRequired") or self.startup_maintenance():
            raise RuntimeError("Manual recovery is required before another update")
        previous_env = self.env_path.read_text()
        backup = None
        stopped = False
        committed = False
        self.update_status(phase="staging", targetVersion=version, progress=5, error=None)
        try:
            if remote:
                self.run(["docker", "pull", image], timeout=900)
            inspected = self.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=30).stdout.decode().strip()
            if not DIGEST.fullmatch(inspected):
                raise ValueError("Cannot resolve immutable local image ID")
            # Always deploy immutable IDs after verifying/pulling the trusted digest.
            self.drain()
            self.compose("stop", "app", timeout=90)
            stopped = True
            backup = self.backup_stopped()
            self.update_status(phase="installing", progress=60, backup=str(backup), rollbackCompatible=compatibility)
            set_env(self.env_path, {**(changes or {}), "DND_IMAGE": image if remote else inspected, "DND_VERSION": version})
            self.set_startup_maintenance(True)
            self.compose("up", "-d", "--no-deps", "app", timeout=180)
            self.update_status(phase="health-check", progress=80)
            self.wait_healthy()
            info = self.internal("/api/internal/update-info")
            if info.get("schemaVersion") != 1 or info.get("maintenance") is not True or info.get("activeJobs") != 0:
                raise RuntimeError("Application maintenance/schema verification failed")
            self.update_status(phase="opening", progress=95)
            committed = True
            self.reopen()
            return self.update_status(phase="complete", available=False, progress=100, error=None, completedAt=now())
        except Exception as error:
            original = self.redact(error)
            if committed:
                # Once opening is attempted, new writes may exist even if the response was lost.
                # Preserve candidate data and require recovery instead of restoring an older backup.
                try:
                    self.set_startup_maintenance(True)
                    self.internal("/api/internal/maintenance", {"enabled": True})
                except Exception:
                    pass
                self.update_status(phase="failed", error=original + "; candidate data preserved after reopening attempt; verify and run dndctl recover", progress=95, recoveryRequired=True)
            elif stopped:
                try:
                    self.update_status(phase="rolling-back", progress=85, error=original)
                    self.compose("stop", "app", timeout=90, check=False)
                    if backup is not None:
                        # Restore even for compatible schemas: migrations might partially fail.
                        self.restore_stopped(backup)
                    else:
                        atomic(self.env_path, previous_env)
                        self.set_startup_maintenance(True)
                    self.compose("up", "-d", "--no-deps", "app", timeout=180)
                    self.wait_healthy()
                    self.reopen()
                    self.update_status(phase="failed", error=original, progress=0, rolledBack=True)
                except Exception as rollback_error:
                    self.update_status(phase="failed", error=original + "; recovery failed: " + self.redact(rollback_error), progress=0, recoveryRequired=True)
            else:
                try:
                    self.reopen()
                except Exception:
                    pass
                self.update_status(phase="failed", error=original, progress=0)
            raise RuntimeError(original) from None
    def apply(self, expected):
        if not isinstance(expected, str) or not VERSION.fullmatch(expected):
            raise ValueError("Explicit checked release version is required")
        manifest = validate_release(json.loads(self.release_path.read_text()), self.env.get("DND_REPOSITORY", ""))
        if manifest["version"] != expected:
            raise ValueError("The selected version no longer matches the checked release")
        checked = datetime.datetime.fromisoformat(manifest["checkedAt"])
        if datetime.datetime.now(datetime.timezone.utc) - checked > datetime.timedelta(hours=1):
            raise ValueError("Check for updates again before confirming")
        current = self.env.get("DND_VERSION", "local")
        if current != "local" and version_tuple(expected) <= version_tuple(current):
            raise ValueError("Remote downgrade/reinstall is not allowed")
        return self.apply_image(manifest["image"], expected, remote=True, compatibility=manifest["rollbackCompatible"])
class UnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = True
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "DNDUpdater/1"
    def setup(self):
        self.request.settimeout(10)
        super().setup()
    def log_message(self, *_args):
        pass
    def respond(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path != "/v1/status":
            return self.respond(404, {"error": "Unknown operation"})
        self.server.host.reload()
        self.respond(200, self.server.host.status())
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(min(max(0, length), 1025))
            if not 0 < length <= 1024 or self.headers.get("Transfer-Encoding"):
                return self.respond(400, {"error": "Invalid request body size"})
            if self.path not in ("/v1/check", "/v1/apply"):
                return self.respond(404, {"error": "Unknown operation"})
            body = json.loads(payload)
            if not isinstance(body, dict) or set(body) - ({"version"} if self.path.endswith("apply") else set()):
                return self.respond(400, {"error": "Unsupported request fields"})
            if self.server.busy:
                return self.respond(409, {"error": "Another operation is in progress"})
            if self.path == "/v1/check":
                with self.server.host.operation():
                    return self.respond(200, self.server.host.check())
            expected = body.get("version")
            if not isinstance(expected, str) or not VERSION.fullmatch(expected):
                return self.respond(400, {"error": "Explicit version required"})
            operation = self.server.host.operation()
            operation.__enter__()
            self.server.busy = True
            try:
                self.server.host.update_status(phase="staging", progress=0, targetVersion=expected, error=None)
            except Exception:
                self.server.busy = False
                operation.__exit__(None, None, None)
                raise
            def work():
                try:
                    self.server.host.apply(expected)
                except Exception as error:
                    self.server.host.update_status(phase="failed", error=self.server.host.redact(error))
                finally:
                    operation.__exit__(None, None, None)
                    self.server.busy = False
            try:
                threading.Thread(target=work, daemon=True).start()
            except Exception:
                self.server.busy = False
                operation.__exit__(None, None, None)
                raise
            self.respond(202, self.server.host.status())
        except Exception as error:
            self.respond(400, {"error": self.server.host.redact(error)})
def serve(host):
    host.runtime.mkdir(mode=0o750, parents=True, exist_ok=True)
    gid = int(host.env["DND_UPDATER_GID"])
    os.chown(host.runtime, 0, gid)
    os.chmod(host.runtime, 0o750)
    path = host.runtime / "updater.sock"
    path.unlink(missing_ok=True)
    if host.status().get("phase") in ACTIVE:
        host.set_startup_maintenance(True)
        host.update_status(phase="failed", recoveryRequired=True, error="Updater stopped during an operation. Inspect backups and run dndctl recover before reopening writes.")
    with UnixServer(str(path), Handler) as server:
        server.host = host
        server.busy = False
        os.chown(path, 0, gid)
        os.chmod(path, 0o660)
        server.serve_forever()
def main():
    parser = argparse.ArgumentParser(description="DND AI Arahort host administration")
    parser.add_argument("command", choices=("serve", "status", "check", "update", "update-local", "backup", "restore", "recover", "effort", "login", "logs", "reset-password"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--confirm-restore", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run using sudo")
    host = Host()
    if args.command == "serve":
        return serve(host)
    if args.command == "status":
        print(json.dumps(host.status(), ensure_ascii=False, indent=2))
        return
    if args.command == "logs":
        result = host.compose("logs", "--tail=100", "app", check=False)
        print(host.redact(result.stdout.decode("utf-8", "replace")))
        return
    if args.command == "login":
        # exec preserves the real SSH terminal for the OAuth URL/code conversation.
        arguments = build_login_args(host.root, host.env.get("DND_NETWORK_MODE", "http"), os.environ)
        host.initialize_agy_workspace()
        os.chdir(host.root)
        for key in ("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            os.environ.pop(key, None)
        os.execvp("docker", arguments)
    with host.operation():
        if args.command == "check":
            result = host.check()
        elif args.command == "update":
            status = host.check()
            result = host.apply(args.value or status["targetVersion"])
        elif args.command == "update-local":
            if not args.value or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}", args.value):
                parser.error("Provide an already built local image reference")
            label = host.run(["docker", "image", "inspect", args.value, "--format", '{{index .Config.Labels "org.opencontainers.image.version"}}']).stdout.decode().strip()
            version = label if VERSION.fullmatch(label) else "local"
            result = host.apply_image(args.value, version)
        elif args.command == "effort":
            if args.value not in ("low", "medium", "high"):
                parser.error("effort requires low, medium or high")
            result = host.apply_image(host.env["DND_IMAGE"], host.env.get("DND_VERSION", "local"), compatibility=True, changes={"DND_MODEL_EFFORT": args.value})
        elif args.command == "backup":
            host.drain()
            host.compose("stop", "app", timeout=90)
            try:
                result = {"backup": str(host.backup_stopped())}
            finally:
                host.set_startup_maintenance(True)
                host.compose("up", "-d", "--no-deps", "app")
                host.wait_healthy()
                host.reopen()
                host.update_status(phase="idle", progress=0)
        elif args.command == "restore":
            if not args.confirm_restore or not args.value:
                parser.error("restore requires backup path and --confirm-restore; all later data will be replaced")
            try:
                host.drain()
            except Exception:
                print("Drain unavailable; explicit restore confirmation permits stopping the unavailable application.", file=sys.stderr)
            host.set_startup_maintenance(True)
            host.compose("stop", "app", timeout=90)
            host.restore_stopped(args.value)
            host.compose("up", "-d", "--no-deps", "app")
            host.wait_healthy()
            host.reopen()
            result = host.update_status(phase="idle", progress=0, error=None, recoveryRequired=False)
        elif args.command == "recover":
            host.wait_healthy()
            host.reopen()
            result = host.update_status(phase="idle", recoveryRequired=False, error=None, progress=0)
        elif args.command == "reset-password":
            import getpass
            if not args.value:
                parser.error("Specify administrator name")
            password = getpass.getpass("Новый пароль: ")
            if not password:
                parser.error("Пароль не должен быть пустым")
            if len(password) > 256 or any(c in password for c in "\r\n\x00"):
                parser.error("Пароль должен содержать до 256 символов без переносов строк и нулевого символа")
            if password != getpass.getpass("Повтори пароль: "):
                parser.error("Пароли не совпали")
            host.compose("exec", "-T", "app", "node", "server/admin-cli.mjs", "reset-password", "--name", args.value, input=(password + "\n").encode())
            result = {"changed": True}
        else:
            parser.error("Unsupported operation")
        print(json.dumps(result, ensure_ascii=False, indent=2))
if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Operation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
