#!/usr/bin/env python3

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ENTRIES = os.path.join(DATA, "entries.json")


def read_entries():
    with open(ENTRIES) as f:
        return json.load(f)


def write_entries(rows):
    with open(ENTRIES, "w", newline="\n") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, code, body, ctype):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/entries":
            self.reply(200, json.dumps(read_entries()), "application/json")
            return
        if url.path == "/api/export":
            name = parse_qs(url.query).get("name", [""])[0]
            root = os.path.realpath(DATA)
            path = os.path.realpath(os.path.join(root, name))
            if path != root and not path.startswith(root + os.sep):
                self.reply(400, "outside\n", "text/plain; charset=utf-8")
                return
            if not os.path.isfile(path):
                self.reply(404, "not found\n", "text/plain; charset=utf-8")
                return
            with open(path) as f:
                self.reply(200, f.read(), "text/plain; charset=utf-8")
            return
        self.reply(404, "not found\n", "text/plain; charset=utf-8")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/entries":
            self.reply(404, "not found\n", "text/plain; charset=utf-8")
            return
        n = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = json.loads(self.rfile.read(n).decode())
            amount = body["amount"]
            memo = body["memo"]
            if type(amount) is not int or type(memo) is not str:
                raise ValueError
        except Exception:
            self.reply(400, "bad request\n", "text/plain; charset=utf-8")
            return
        rows = read_entries()
        nxt = max((int(row["id"]) for row in rows), default=0) + 1
        row = {"id": nxt, "amount": amount, "memo": memo}
        rows.append(row)
        write_entries(rows)
        self.reply(201, json.dumps(row), "application/json")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
