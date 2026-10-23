#!/usr/bin/env python3
"""Create updater metadata after a successful image publication. Arahort."""
import json
import pathlib
import re
import sys
def write_manifest(repository, version, digest, destination):
    if not isinstance(repository, str) or not isinstance(version, str) or not isinstance(digest, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) or not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Invalid release arguments")
    data = {"format": 1, "version": version.lstrip("v"), "image": "ghcr.io/" + repository.lower() + "@" + digest, "digest": digest, "schemaVersion": 1, "minimumUpdaterVersion": 1, "rollbackCompatible": True}
    target = pathlib.Path(destination)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    target.with_name("release-source.json").write_text(json.dumps({"format": 1, "repository": repository}, indent=2) + "\n", encoding="utf-8")
if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise ValueError("Usage: publish-manifest.py AUTHOR/REPOSITORY vX.Y.Z sha256:DIGEST release.json")
    write_manifest(*sys.argv[1:])
