#!/usr/bin/env python3
"""Validate a compiled release payload before publishing. Arahort, arahort.pro."""
import argparse
import hashlib
import json
import pathlib
import re
import tarfile
def verify_checksum(source, checksum):
    source = pathlib.Path(source)
    expected = pathlib.Path(checksum).read_text(encoding="ascii").strip()
    match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", expected)
    if not match or match[2] != source.name:
        raise ValueError("Invalid compiled payload checksum file")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != match[1]:
        raise ValueError("Compiled payload checksum mismatch")
def extract(source, destination, version, checksum=None):
    if not isinstance(version, str) or not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Only a stable vX.Y.Z version can be published")
    if checksum is not None:
        verify_checksum(source, checksum)
    destination = pathlib.Path(destination).resolve()
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 30000 or sum(item.size for item in members) > 1024 * 1024 * 1024:
            raise ValueError("Release payload exceeds limits")
        names = set()
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if "\\" in member.name or path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "app":
                raise ValueError("Payload must contain only app/")
            if str(path) in names:
                raise ValueError("Duplicate compiled payload path: " + str(path))
            names.add(str(path))
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise ValueError("Links and special files are not allowed")
            if any(part in (".private", ".git", "node_modules", "uploads", "tts-cache", "logs") or part.startswith(".env") for part in path.parts):
                raise ValueError("Private or runtime path in release: " + str(path))
            if path.suffix.lower() in (".ts", ".tsx", ".vue", ".map", ".sqlite", ".pem", ".key", ".log", ".sql"):
                raise ValueError("Source, secret or runtime file in release: " + str(path))
        archive.extractall(destination, filter="data")
    app = destination / "app"
    for name in ("server/index.mjs", "server/admin-cli.mjs", "package.json", "package-lock.json", "client/index.html", "release.json"):
        if not (app / name).is_file():
            raise ValueError("Missing compiled release file: " + name)
    if tuple(map(int, version[1:].split("."))) >= (0, 1, 3) and not (app / "server/agy-cli.mjs").is_file():
        raise ValueError("Missing writable AGY launcher: server/agy-cli.mjs")
    release = json.loads((app / "release.json").read_text(encoding="utf-8"))
    if release.get("version") != version[1:] or release.get("schemaVersion") != 1:
        raise ValueError("Compiled version/schema does not match the selected release")
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    if package.get("version") != release["version"]:
        raise ValueError("Production package version does not match the compiled release")
    if package.get("devDependencies") or package.get("scripts"):
        raise ValueError("Production package must omit development dependencies and scripts")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("destination")
    parser.add_argument("version")
    parser.add_argument("--checksum")
    args = parser.parse_args()
    extract(args.archive, args.destination, args.version, args.checksum)
