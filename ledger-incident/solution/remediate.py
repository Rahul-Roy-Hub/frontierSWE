#!/usr/bin/env python3

import json
import os

users_path = "/app/data/users.json"
cron_path = "/app/host/etc/cron.d/ledger-restore"
shadow_path = "/app/host/var/backups/users.json.shadow"

with open(users_path) as f:
    doc = json.load(f)
doc["users"] = [u for u in doc["users"] if u.get("username") != "night-audit"]
with open(users_path, "w", newline="\n") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")

for path in (cron_path, shadow_path):
    if os.path.lexists(path):
        os.remove(path)

report = {
    "initial_access": "path_traversal",
    "vulnerable_route": "/api/export",
    "parameter": "name",
    "rogue_admin": "night-audit",
    "first_malicious_timestamp": "2026-04-03T02:14:11Z",
    "persistence": [cron_path, shadow_path],
}
with open("/app/incident-report.json", "w", newline="\n") as f:
    json.dump(report, f, indent=2)
    f.write("\n")
