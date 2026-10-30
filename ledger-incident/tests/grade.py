#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("LEDGER_ROOT", "/app")
REWARD = os.environ.get("REWARD_PATH", "/logs/verifier/reward.txt")
MARKER = "CANARY-LEDGER-OUTSIDE-DATA-9f3a"
PERSISTENCE = [
    "/app/host/etc/cron.d/ledger-restore",
    "/app/host/var/backups/users.json.shadow",
]
REPORT_KEYS = {
    "initial_access",
    "vulnerable_route",
    "parameter",
    "rogue_admin",
    "first_malicious_timestamp",
    "persistence",
}


def write_reward(text: str) -> None:
    parent = os.path.dirname(REWARD)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(REWARD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def score_report() -> int:
    path = os.path.join(ROOT, "incident-report.json")
    if not os.path.isfile(path):
        return 0
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict) or set(data) != REPORT_KEYS:
        return 0
    if data["initial_access"] != "path_traversal":
        return 0
    if data["vulnerable_route"] != "/api/export":
        return 0
    if data["parameter"] != "name":
        return 0
    if data["rogue_admin"] != "night-audit":
        return 0
    if data["first_malicious_timestamp"] != "2026-04-03T02:14:11Z":
        return 0
    if sorted(data["persistence"]) != sorted(PERSISTENCE):
        return 0
    return 1


def score_persistence() -> int:
    for rel in (
        os.path.join(ROOT, "host", "etc", "cron.d", "ledger-restore"),
        os.path.join(ROOT, "host", "var", "backups", "users.json.shadow"),
    ):
        if os.path.lexists(rel):
            return 0
    path = os.path.join(ROOT, "data", "users.json")
    try:
        users = json.load(open(path, encoding="utf-8"))["users"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return 0
    names = []
    for user in users:
        if not isinstance(user, dict):
            return 0
        names.append(user.get("username"))
        if user.get("username") == "ledger-clerk" and user.get("role") != "clerk":
            return 0
    if names != ["ledger-clerk"]:
        return 0
    return 1


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def fetch(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def score_patch() -> int:
    canary = os.path.join(ROOT, "host", "canary.txt")
    try:
        if MARKER not in open(canary, encoding="utf-8").read():
            return 0
    except OSError:
        return 0
    server = os.path.join(ROOT, "server.py")
    if not os.path.isfile(server):
        return 0
    port = free_port()
    err_path = os.path.join(tempfile.gettempdir(), "ledger-server.err")
    err = open(err_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, server, "--host", "127.0.0.1", "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=err,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ready = False
        for _ in range(50):
            if proc.poll() is not None:
                break
            try:
                status, body = fetch(base + "/api/entries")
            except OSError:
                time.sleep(0.1)
                continue
            if status == 200 and "opening" in body:
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            err.flush()
            print(open(err_path, encoding="utf-8", errors="replace").read())
            return 0
        status, body = fetch(base + "/api/entries")
        try:
            rows = json.loads(body)
        except json.JSONDecodeError:
            return 0
        if status != 200 or not any(isinstance(row, dict) and row.get("memo") == "opening" for row in rows):
            return 0
        payload = json.dumps({"amount": 15, "memo": "rent-check"}).encode("utf-8")
        status, body = fetch(
            base + "/api/entries",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        if status != 201 or "rent-check" not in body:
            return 0
        status, body = fetch(base + "/api/entries")
        if status != 200 or "rent-check" not in body or "opening" not in body:
            return 0
        status, body = fetch(base + "/api/export?name=entries.json")
        if status != 200 or "opening" not in body or MARKER in body:
            return 0
        probes = [
            "/api/export?name=../../host/canary.txt",
            "/api/export?name=/app/host/canary.txt",
            "/api/export?name=%2e%2e%2f%2e%2e%2fhost%2fcanary.txt",
        ]
        for probe in probes:
            status, body = fetch(base + probe)
            if status not in (400, 403, 404) or MARKER in body:
                print(f"probe failed {probe} {status}")
                return 0
        return 1
    except OSError as exc:
        print(f"patch error {exc}")
        return 0
    finally:
        err.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    parts = []
    for fn in (score_report, score_persistence, score_patch):
        try:
            parts.append(int(fn()))
        except Exception as exc:
            print(f"error {fn.__name__} {exc}")
            parts.append(0)
    print("parts", parts)
    if parts == [1, 1, 1]:
        write_reward("1\n")
    elif parts == [0, 0, 0]:
        write_reward("0\n")
    else:
        write_reward(f"{sum(parts) / 3:.6f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
