#!/usr/bin/env python3
"""Validate a compiled release payload before publishing. Arahort, arahort.pro."""
import argparse
import json
import pathlib
import tarfile
def extract(source, destination, version):
    destination = pathlib.Path(destination).resolve()
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 30000 or sum(item.size for item in members) > 1024 * 1024 * 1024:
            raise ValueError("Release payload exceeds limits")
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "app":
                raise ValueError("Payload must contain only app/")
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
    release = json.loads((app / "release.json").read_text())
    if release.get("version", "").lstrip("v") != version.lstrip("v") or release.get("schemaVersion") != 1:
        raise ValueError("Compiled version/schema does not match the selected release")
    package = json.loads((app / "package.json").read_text())
    if package.get("devDependencies") or package.get("scripts"):
        raise ValueError("Production package must omit development dependencies and scripts")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("destination")
    parser.add_argument("version")
    args = parser.parse_args()
    extract(args.archive, args.destination, args.version)
