#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

root = os.path.dirname(os.path.abspath(__file__))
marker = "CANARY-LEDGER-OUTSIDE-DATA-9f3a"
report_path = os.path.join(root, "incident-report.json")
cron_path = os.path.join(root, "host", "etc", "cron.d", "ledger-restore")
shadow_path = os.path.join(root, "host", "var", "backups", "users.json.shadow")
users_path = os.path.join(root, "data", "users.json")
log_path = os.path.join(root, "host", "var", "log", "ledger", "access.log")
canary_path = os.path.join(root, "host", "canary.txt")
server_path = os.path.join(root, "server.py")


def http_get(url):
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")


def report_ok():
    if not os.path.isfile(report_path):
        return False
    try:
        got = json.load(open(report_path))
    except Exception:
        return False
    first = None
    for line in open(log_path):
        if "canary.txt" in line:
            first = line.split()[0]
            break
    pers = [cron_path.replace("\\", "/"), shadow_path.replace("\\", "/")]
    return (
        got.get("initial_access") == "path_traversal"
        and got.get("vulnerable_route") == "/api/export"
        and got.get("parameter") == "name"
        and got.get("rogue_admin") == "night-audit"
        and got.get("first_malicious_timestamp") == first
        and sorted(got.get("persistence") or []) == sorted(pers)
    )


def accounts_ok():
    try:
        names = [u["username"] for u in json.load(open(users_path))["users"]]
    except Exception:
        return False
    return (not os.path.exists(cron_path)) and (not os.path.exists(shadow_path)) and names == ["ledger-clerk"]


def export_ok():
    try:
        if marker not in open(canary_path).read():
            return False
    except OSError:
        return False
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    proc = subprocess.Popen(
        [sys.executable, server_path, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ready = False
        base = "http://127.0.0.1:%d" % port
        for _ in range(50):
            if proc.poll() is not None:
                break
            try:
                status, body = http_get(base + "/api/export?name=entries.json")
            except OSError:
                time.sleep(0.1)
                continue
            if status == 200 and "opening" in body:
                ready = True
                break
            time.sleep(0.1)
        status, body = http_get(base + "/api/export?name=../../host/canary.txt")
        return ready and status in (400, 403, 404) and marker not in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    passed = 0
    if report_ok():
        passed += 1
    if accounts_ok():
        passed += 1
    if export_ok():
        passed += 1
    print("%d/3" % passed)
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
