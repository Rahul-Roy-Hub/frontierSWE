#!/usr/bin/env python3

import binascii
import os
import struct
import subprocess
import sys
import tempfile

ENGINE = os.environ.get("PAGESTORE_BIN", "/app/pagestore.py")
REWARD = os.environ.get("REWARD_PATH", "/logs/verifier/reward.txt")


def frame(payload: bytes) -> bytes:
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return struct.pack("<I", len(payload)) + payload + struct.pack("<I", crc)


def forged_insert(txn: int, table: str, row_id: int, body: str) -> bytes:
    nb = table.encode("utf-8")
    bb = body.encode("utf-8")
    payload = bytes([3]) + struct.pack("<I", txn)
    payload += struct.pack("<H", len(nb)) + nb
    payload += bytes([1]) + struct.pack("<q", row_id)
    payload += bytes([2]) + struct.pack("<I", len(bb)) + bb
    return frame(payload)


def run(db: str, sql: str) -> tuple[int, str]:
    path = os.path.join(db, "_script.sql")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sql)
    proc = subprocess.run(
        [sys.executable, ENGINE, "--db", db, "--file", path],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def expect(sql: str, out: str, code: int = 0) -> bool:
    with tempfile.TemporaryDirectory() as db:
        got_code, got_out = run(db, sql)
    return got_code == code and got_out == out


CASES: list[tuple[str, object]] = []


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn

    return wrap


@case("basic")
def basic():
    ok = expect(
        "CREATE TABLE cargo (id INT, label TEXT);\n"
        "INSERT INTO cargo VALUES (3, 'crate'), (1, 'drum');\n"
        "SELECT id, label FROM cargo ORDER BY id ASC;\n",
        "id\tlabel\n1\tdrum\n3\tcrate\n",
    )
    if not ok:
        return False
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE cargo (id INT, label TEXT);\nINSERT INTO cargo VALUES (1, 'drum');\n",
        )
        if code != 0:
            return False
        pages = open(os.path.join(db, "pages"), "rb").read()
        if not pages.startswith(b"PSQ1") or len(pages) % 4096 != 0:
            return False
        code, out = run(db, "SELECT label FROM cargo;\n")
        return code == 0 and out == "label\ndrum\n"


@case("where_logic")
def where_logic():
    return expect(
        "CREATE TABLE t (n INT);\n"
        "INSERT INTO t VALUES (1), (2), (3), (4);\n"
        "SELECT n FROM t WHERE (n = 1 OR n = 2) AND n <> 2 OR n = 4 ORDER BY n ASC;\n",
        "n\n1\n4\n",
    )


@case("order_text")
def order_text():
    return expect(
        "CREATE TABLE t (name TEXT, n INT);\n"
        "INSERT INTO t VALUES ('b', 1), ('a', 2), ('c', 0);\n"
        "SELECT name FROM t ORDER BY name ASC;\n",
        "name\na\nb\nc\n",
    )


@case("order_int_desc")
def order_int_desc():
    return expect(
        "CREATE TABLE t (name TEXT, n INT);\n"
        "INSERT INTO t VALUES ('b', 1), ('a', 2), ('c', 0);\n"
        "SELECT name, n FROM t ORDER BY n DESC;\n",
        "name\tn\na\t2\nb\t1\nc\t0\n",
    )


@case("empty")
def empty():
    return expect(
        "CREATE TABLE t (n INT, s TEXT);\nSELECT s, n FROM t;\n",
        "s\tn\n",
    )


@case("rollback")
def rollback():
    return expect(
        "CREATE TABLE t (n INT);\n"
        "INSERT INTO t VALUES (1);\n"
        "BEGIN;\n"
        "INSERT INTO t VALUES (9);\n"
        "ROLLBACK;\n"
        "SELECT n FROM t;\n",
        "n\n1\n",
    )


@case("explicit_commit")
def explicit_commit():
    return expect(
        "CREATE TABLE t (n INT);\n"
        "BEGIN;\n"
        "INSERT INTO t VALUES (5);\n"
        "SELECT n FROM t;\n"
        "COMMIT;\n"
        "SELECT n FROM t;\n",
        "n\n5\nn\n5\n",
    )


@case("unclosed")
def unclosed():
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE t (n INT);\nINSERT INTO t VALUES (1);\nBEGIN;\nINSERT INTO t VALUES (2);\n",
        )
        if code != 0:
            return False
        code, out = run(db, "SELECT n FROM t ORDER BY n ASC;\n")
        return code == 0 and out == "n\n1\n"


@case("two_tables")
def two_tables():
    return expect(
        "CREATE TABLE a (id INT);\n"
        "CREATE TABLE b (id INT, name TEXT);\n"
        "INSERT INTO a VALUES (1);\n"
        "INSERT INTO b VALUES (7, 'q');\n"
        "SELECT id FROM a;\n"
        "SELECT name, id FROM b ORDER BY id DESC;\n",
        "id\n1\nname\tid\nq\t7\n",
    )


@case("quotes")
def quotes():
    return expect(
        "CREATE TABLE t (s TEXT);\nINSERT INTO t VALUES ('it''s');\nSELECT s FROM t;\n",
        "s\nit's\n",
    )


@case("compare")
def compare():
    return expect(
        "CREATE TABLE t (n INT, s TEXT);\n"
        "INSERT INTO t VALUES (-2, 'b'), (10, 'a'), (0, 'c');\n"
        "SELECT n FROM t WHERE n <= 0 AND s <> 'c' ORDER BY n ASC;\n",
        "n\n-2\n",
    )


@case("atomic_insert")
def atomic_insert():
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE t (a INT, b TEXT);\n"
            "INSERT INTO t VALUES (1, 'ok');\n"
            "INSERT INTO t VALUES (2, 'also'), ('x', 'bad');\n",
        )
        if code == 0:
            return False
        code, out = run(db, "SELECT a, b FROM t;\n")
        return code == 0 and out == "a\tb\n1\tok\n"


@case("duplicate_table")
def duplicate_table():
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE t (a INT);\nINSERT INTO t VALUES (4);\nCREATE TABLE t (b TEXT);\n",
        )
        if code == 0:
            return False
        code, out = run(db, "SELECT a FROM t;\n")
        return code == 0 and out == "a\n4\n"


@case("torn_tail")
def torn_tail():
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE notes (id INT, body TEXT);\n"
            "INSERT INTO notes VALUES (1, 'kept');\n",
        )
        if code != 0:
            return False
        with open(os.path.join(db, "wal"), "ab") as fh:
            fh.write(b"\x00\x01\x02\x03\x04\x05\x06")
        code, out = run(db, "SELECT id, body FROM notes;\n")
        return code == 0 and out == "id\tbody\n1\tkept\n"


@case("uncommitted_frame")
def uncommitted_frame():
    with tempfile.TemporaryDirectory() as db:
        code, _out = run(
            db,
            "CREATE TABLE notes (id INT, body TEXT);\n"
            "INSERT INTO notes VALUES (1, 'kept');\n",
        )
        if code != 0:
            return False
        with open(os.path.join(db, "wal"), "ab") as fh:
            fh.write(forged_insert(900001, "notes", 99, "forged-row-not-visible"))
        code, out = run(db, "SELECT id, body FROM notes ORDER BY id ASC;\n")
        return code == 0 and out == "id\tbody\n1\tkept\n"


@case("not_and")
def not_and():
    return expect(
        "CREATE TABLE t (n INT);\n"
        "INSERT INTO t VALUES (1), (2);\n"
        "SELECT n FROM t WHERE NOT n = 1 AND n = 2 ORDER BY n ASC;\n",
        "n\n2\n",
    )


def write_reward(text: str) -> None:
    parent = os.path.dirname(REWARD)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(REWARD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main() -> int:
    if not os.path.isfile(ENGINE):
        write_reward("0\n")
        return 0
    passed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
        except Exception as exc:
            ok = False
            print(f"FAIL {name} {exc}")
        if ok:
            passed += 1
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}")
    total = len(CASES)
    if passed == total:
        write_reward("1\n")
    elif passed == 0:
        write_reward("0\n")
    else:
        write_reward(f"{passed / total:.6f}\n")
    print(f"{passed}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
