#!/usr/bin/env python3
"""Create updater metadata after a successful image publication. Arahort."""
import json
import pathlib
import re
import sys
repository, version, digest, destination = sys.argv[1:]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) or not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
    raise ValueError("Invalid release arguments")
data = {"format": 1, "version": version.lstrip("v"), "image": "ghcr.io/" + repository.lower() + "@" + digest, "digest": digest, "schemaVersion": 1, "minimumUpdaterVersion": 1, "rollbackCompatible": True}
pathlib.Path(destination).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
