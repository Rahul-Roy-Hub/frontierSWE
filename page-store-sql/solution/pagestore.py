#!/usr/bin/env python3

import argparse
import binascii
import os
import struct
import sys

PAGE = 4096
MAGIC = b"PSQ1"
MAX_FRAME = 10_000_000
OP_BEGIN = 1
OP_CREATE = 2
OP_INSERT = 3
OP_COMMIT = 4
OP_ROLLBACK = 5

RESERVED = {
    "and",
    "asc",
    "begin",
    "by",
    "commit",
    "create",
    "desc",
    "from",
    "insert",
    "int",
    "into",
    "not",
    "or",
    "order",
    "rollback",
    "select",
    "table",
    "text",
    "values",
    "where",
}


class DBError(Exception):
    pass


class Table:
    def __init__(self, cols: list[tuple[str, str]]):
        self.cols = cols
        self.rows: list[tuple] = []


def _frame(payload: bytes) -> bytes:
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return struct.pack("<I", len(payload)) + payload + struct.pack("<I", crc)


def _payload(op: int, txn: int, body: bytes = b"") -> bytes:
    return bytes([op]) + struct.pack("<I", txn) + body


def _encode_create_body(name: str, cols: list[tuple[str, str]]) -> bytes:
    nb = name.encode("utf-8")
    buf = struct.pack("<H", len(nb)) + nb + struct.pack("<H", len(cols))
    for cname, typ in cols:
        cb = cname.encode("utf-8")
        tag = 1 if typ == "INT" else 2
        buf += bytes([tag]) + struct.pack("<H", len(cb)) + cb
    return buf


def _encode_row(cols: list[tuple[str, str]], row: tuple) -> bytes:
    buf = b""
    for (_cname, typ), val in zip(cols, row):
        if typ == "INT":
            buf += bytes([1]) + struct.pack("<q", int(val))
        else:
            raw = str(val).encode("utf-8")
            buf += bytes([2]) + struct.pack("<I", len(raw)) + raw
    return buf


def _encode_insert_body(name: str, cols: list[tuple[str, str]], row: tuple) -> bytes:
    nb = name.encode("utf-8")
    return struct.pack("<H", len(nb)) + nb + _encode_row(cols, row)


def _parse_prefix(data: bytes) -> tuple[list[bytes], int]:
    frames: list[bytes] = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        (length,) = struct.unpack_from("<I", data, i)
        if length > MAX_FRAME:
            break
        total = 4 + length + 4
        if i + total > n:
            break
        payload = data[i + 4 : i + 4 + length]
        (got,) = struct.unpack_from("<I", data, i + 4 + length)
        if (binascii.crc32(payload) & 0xFFFFFFFF) != got:
            break
        frames.append(payload)
        i += total
    return frames, i


def _take_name(buf: bytes, i: int) -> tuple[str, int]:
    if i + 2 > len(buf):
        raise DBError("truncated name")
    (nlen,) = struct.unpack_from("<H", buf, i)
    i += 2
    if i + nlen > len(buf):
        raise DBError("truncated name")
    name = buf[i : i + nlen].decode("utf-8")
    return name, i + nlen


def _decode_create_body(body: bytes) -> tuple[str, list[tuple[str, str]]]:
    name, i = _take_name(body, 0)
    if i + 2 > len(body):
        raise DBError("truncated create")
    (ncols,) = struct.unpack_from("<H", body, i)
    i += 2
    cols: list[tuple[str, str]] = []
    for _ in range(ncols):
        if i >= len(body):
            raise DBError("truncated column")
        tag = body[i]
        i += 1
        cname, i = _take_name(body, i)
        if tag == 1:
            cols.append((cname, "INT"))
        elif tag == 2:
            cols.append((cname, "TEXT"))
        else:
            raise DBError("bad column type")
    if i != len(body):
        raise DBError("trailing create bytes")
    return name, cols


def _decode_values(buf: bytes, i: int, cols: list[tuple[str, str]]) -> tuple[tuple, int]:
    vals = []
    for _cname, typ in cols:
        if i >= len(buf):
            raise DBError("truncated row")
        tag = buf[i]
        i += 1
        if typ == "INT":
            if tag != 1 or i + 8 > len(buf):
                raise DBError("bad int row")
            (val,) = struct.unpack_from("<q", buf, i)
            i += 8
        else:
            if tag != 2 or i + 4 > len(buf):
                raise DBError("bad text row")
            (nlen,) = struct.unpack_from("<I", buf, i)
            i += 4
            if i + nlen > len(buf):
                raise DBError("truncated text")
            val = buf[i : i + nlen].decode("utf-8")
            i += nlen
        vals.append(val)
    return tuple(vals), i


def _decode_insert_body(body: bytes, cols: list[tuple[str, str]]) -> tuple:
    _name, i = _take_name(body, 0)
    row, i = _decode_values(body, i, cols)
    if i != len(body):
        raise DBError("trailing insert bytes")
    return row


def _fits(blobs: list[bytes]) -> bool:
    if not blobs:
        return True
    return 8 + len(blobs) * 2 + sum(len(b) for b in blobs) <= PAGE


def _build_heap(blobs: list[bytes], nxt: int) -> bytes:
    page = bytearray(PAGE)
    end = PAGE
    offsets = []
    for blob in blobs:
        end -= len(blob)
        page[end : end + len(blob)] = blob
        offsets.append(end)
    struct.pack_into("<IHH", page, 0, nxt, len(blobs), 0)
    for i, off in enumerate(offsets):
        struct.pack_into("<H", page, 8 + 2 * i, off)
    if end < 8 + 2 * len(blobs):
        raise DBError("row spilled out of its page")
    return bytes(page)


def _encode_pages(tables: dict[str, Table], next_txn: int) -> bytes:
    pages: dict[int, bytes] = {}
    next_id = 2
    catalog = bytearray()
    catalog += struct.pack("<H", len(tables))
    for name, table in tables.items():
        blobs = [_encode_row(table.cols, row) for row in table.rows]
        chunks: list[list[bytes]] = []
        cur: list[bytes] = []
        for blob in blobs:
            if not _fits([blob]):
                raise DBError("row does not fit in one page")
            if _fits(cur + [blob]):
                cur.append(blob)
            else:
                chunks.append(cur)
                cur = [blob]
        if cur:
            chunks.append(cur)
        if chunks:
            ids = list(range(next_id, next_id + len(chunks)))
            next_id += len(chunks)
            first = ids[0]
            for i, chunk in enumerate(chunks):
                nxt = ids[i + 1] if i + 1 < len(ids) else 0
                pages[ids[i]] = _build_heap(chunk, nxt)
        else:
            first = 0
        nb = name.encode("utf-8")
        catalog += struct.pack("<H", len(nb)) + nb
        catalog += struct.pack("<H", len(table.cols))
        for cname, typ in table.cols:
            cb = cname.encode("utf-8")
            tag = 1 if typ == "INT" else 2
            catalog += bytes([tag]) + struct.pack("<H", len(cb)) + cb
        catalog += struct.pack("<II", first, len(table.rows))
    if len(catalog) > PAGE:
        raise DBError("schema does not fit in one page")
    schema = bytearray(PAGE)
    schema[: len(catalog)] = catalog
    pages[1] = bytes(schema)
    header = bytearray(PAGE)
    struct.pack_into("<4sIIII", header, 0, MAGIC, next_id, 1, 0, next_txn)
    pages[0] = bytes(header)
    for pid in range(next_id):
        if pid not in pages:
            raise DBError("page gap")
    return b"".join(pages[pid] for pid in range(next_id))


def _decode_pages(blob: bytes) -> tuple[dict[str, Table], int]:
    if len(blob) % PAGE != 0 or len(blob) < PAGE * 2:
        raise DBError("bad page file")
    page_count = len(blob) // PAGE
    magic, count, schema_page, freelist, next_txn = struct.unpack_from("<4sIIII", blob, 0)
    if magic != MAGIC or count != page_count or schema_page != 1 or freelist != 0:
        raise DBError("bad page header")
    schema = blob[PAGE : PAGE * 2]
    (ntables,) = struct.unpack_from("<H", schema, 0)
    i = 2
    tables: dict[str, Table] = {}
    planned: list[tuple[str, int, int]] = []
    for _ in range(ntables):
        name, i = _take_name(schema, i)
        (ncols,) = struct.unpack_from("<H", schema, i)
        i += 2
        cols = []
        for _c in range(ncols):
            tag = schema[i]
            i += 1
            cname, i = _take_name(schema, i)
            if tag == 1:
                cols.append((cname, "INT"))
            elif tag == 2:
                cols.append((cname, "TEXT"))
            else:
                raise DBError("bad stored type")
        first, nrows = struct.unpack_from("<II", schema, i)
        i += 8
        table = Table(cols)
        tables[name] = table
        planned.append((name, first, nrows))
    for name, first, nrows in planned:
        table = tables[name]
        pid = first
        seen = 0
        guard = 0
        while pid:
            if pid >= page_count:
                raise DBError("bad heap page")
            page = blob[pid * PAGE : (pid + 1) * PAGE]
            nxt, nslots, _unused = struct.unpack_from("<IHH", page, 0)
            for slot in range(nslots):
                (off,) = struct.unpack_from("<H", page, 8 + 2 * slot)
                row, _end = _decode_values(page, off, table.cols)
                table.rows.append(row)
                seen += 1
            pid = nxt
            guard += 1
            if guard > page_count:
                raise DBError("heap cycle")
        if seen != nrows:
            raise DBError("row count mismatch")
    return tables, next_txn


def _same(a: dict[str, Table], b: dict[str, Table]) -> bool:
    if list(a) != list(b):
        return False
    for name in a:
        if a[name].cols != b[name].cols or a[name].rows != b[name].rows:
            return False
    return True


class Parser:
    def __init__(self, text: str):
        self.s = text
        self.n = len(text)
        self.i = 0

    def skip(self) -> None:
        while self.i < self.n and self.s[self.i].isspace():
            self.i += 1

    def _word_span(self) -> tuple[str, int] | None:
        self.skip()
        if self.i >= self.n:
            return None
        c = self.s[self.i]
        if not (c.isalpha() or c == "_"):
            return None
        j = self.i + 1
        while j < self.n and (self.s[j].isalnum() or self.s[j] == "_"):
            j += 1
        return self.s[self.i : j], j

    def keyword(self, word: str) -> bool:
        span = self._word_span()
        if span and span[0].lower() == word:
            self.i = span[1]
            return True
        return False

    def parse_ident(self) -> str:
        span = self._word_span()
        if not span:
            raise DBError("expected identifier")
        if span[0].lower() in RESERVED:
            raise DBError(f"reserved word {span[0]}")
        self.i = span[1]
        return span[0]

    def expect(self, ch: str) -> None:
        self.skip()
        if self.i >= self.n or self.s[self.i] != ch:
            raise DBError(f"expected {ch}")
        self.i += 1

    def parse_string(self) -> str:
        self.skip()
        if self.i >= self.n or self.s[self.i] != "'":
            raise DBError("expected string")
        self.i += 1
        chars: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "'":
                if self.i + 1 < self.n and self.s[self.i + 1] == "'":
                    chars.append("'")
                    self.i += 2
                    continue
                self.i += 1
                text = "".join(chars)
                if any(ch in text for ch in "\t\r\n"):
                    raise DBError("text cannot contain tabs or newlines")
                return text
            chars.append(c)
            self.i += 1
        raise DBError("unterminated string")

    def parse_int(self) -> int:
        self.skip()
        if self.i >= self.n:
            raise DBError("expected integer")
        start = self.i
        if self.s[self.i] == "-":
            self.i += 1
        if self.i >= self.n or not self.s[self.i].isdigit():
            raise DBError("expected integer")
        while self.i < self.n and self.s[self.i].isdigit():
            self.i += 1
        try:
            val = int(self.s[start : self.i], 10)
        except ValueError as exc:
            raise DBError("bad integer") from exc
        if val < -9223372036854775808 or val > 9223372036854775807:
            raise DBError("integer out of range")
        return val

    def parse_literal(self) -> tuple[str, object]:
        self.skip()
        if self.i < self.n and self.s[self.i] == "'":
            return ("TEXT", self.parse_string())
        if self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] == "-"):
            return ("INT", self.parse_int())
        raise DBError("expected literal")

    def parse_tuple(self) -> list[tuple[str, object]]:
        self.expect("(")
        vals = [self.parse_literal()]
        while True:
            self.skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                vals.append(self.parse_literal())
                continue
            break
        self.expect(")")
        return vals

    def peek_op(self) -> str | None:
        self.skip()
        for op in ("<>", "<=", ">=", "=", "<", ">"):
            if self.s.startswith(op, self.i):
                return op
        return None

    def parse_primary(self):
        self.skip()
        if self.i < self.n and self.s[self.i] == "(":
            self.i += 1
            expr = self.parse_or()
            self.expect(")")
            return expr
        if self.i < self.n and (self.s[self.i] == "'" or self.s[self.i].isdigit() or self.s[self.i] == "-"):
            return ("lit", self.parse_literal())
        return ("col", self.parse_ident())

    def parse_cmp(self):
        left = self.parse_primary()
        op = self.peek_op()
        if not op:
            return left
        self.i += len(op)
        right = self.parse_primary()
        return ("cmp", op, left, right)

    def parse_unary(self):
        if self.keyword("not"):
            return ("not", self.parse_unary())
        return self.parse_cmp()

    def parse_and(self):
        left = self.parse_unary()
        while self.keyword("and"):
            right = self.parse_unary()
            left = ("and", left, right)
        return left

    def parse_or(self):
        left = self.parse_and()
        while self.keyword("or"):
            right = self.parse_and()
            left = ("or", left, right)
        return left

    def parse_create(self):
        name = self.parse_ident()
        self.expect("(")
        cols = [self.parse_col()]
        while True:
            self.skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                cols.append(self.parse_col())
                continue
            break
        self.expect(")")
        if len({c[0] for c in cols}) != len(cols):
            raise DBError("duplicate column")
        return ("create", name, cols)

    def parse_col(self) -> tuple[str, str]:
        name = self.parse_ident()
        if self.keyword("int"):
            return (name, "INT")
        if self.keyword("text"):
            return (name, "TEXT")
        raise DBError("expected type")

    def parse_insert(self):
        if not self.keyword("into"):
            raise DBError("expected INTO")
        name = self.parse_ident()
        if not self.keyword("values"):
            raise DBError("expected VALUES")
        tuples = [self.parse_tuple()]
        while True:
            self.skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                tuples.append(self.parse_tuple())
                continue
            break
        return ("insert", name, tuples)

    def parse_select(self):
        self.skip()
        if self.i < self.n and self.s[self.i] == "*":
            self.i += 1
            columns: list[str] | None = None
        else:
            columns = [self.parse_ident()]
            while True:
                self.skip()
                if self.i < self.n and self.s[self.i] == ",":
                    self.i += 1
                    columns.append(self.parse_ident())
                    continue
                break
        if not self.keyword("from"):
            raise DBError("expected FROM")
        name = self.parse_ident()
        where = None
        if self.keyword("where"):
            where = self.parse_or()
        order = None
        if self.keyword("order"):
            if not self.keyword("by"):
                raise DBError("expected BY")
            col = self.parse_ident()
            desc = False
            if self.keyword("desc"):
                desc = True
            elif self.keyword("asc"):
                desc = False
            order = (col, desc)
        return ("select", columns, name, where, order)

    def parse_statement(self):
        if self.keyword("create"):
            if not self.keyword("table"):
                raise DBError("expected TABLE")
            return self.parse_create()
        if self.keyword("insert"):
            return self.parse_insert()
        if self.keyword("select"):
            return self.parse_select()
        if self.keyword("begin"):
            return ("begin",)
        if self.keyword("commit"):
            return ("commit",)
        if self.keyword("rollback"):
            return ("rollback",)
        raise DBError("expected statement")

    def statements(self) -> list:
        out = []
        while True:
            self.skip()
            if self.i >= self.n:
                break
            if self.s[self.i] == ";":
                self.i += 1
                continue
            out.append(self.parse_statement())
            self.skip()
            if self.i < self.n and self.s[self.i] == ";":
                self.i += 1
            elif self.i < self.n:
                raise DBError("expected semicolon")
        return out


class Database:
    def __init__(self, path: str):
        self.path = path
        self.tables: dict[str, Table] = {}
        self.next_txn = 1
        self.in_txn = False
        self.snapshot: dict[str, Table] | None = None
        self.pending: list[tuple] = []

    @classmethod
    def open(cls, path: str) -> "Database":
        os.makedirs(path, exist_ok=True)
        db = cls(path)
        wal_path = os.path.join(path, "wal")
        data = b""
        if os.path.exists(wal_path):
            with open(wal_path, "rb") as fh:
                data = fh.read()
        frames, valid = _parse_prefix(data)
        if valid != len(data):
            with open(wal_path, "wb") as fh:
                fh.write(data[:valid])
                fh.flush()
                os.fsync(fh.fileno())
        db._replay(frames)
        db.write_pages()
        return db

    def _replay(self, frames: list[bytes]) -> None:
        pending: dict[int, list[tuple[int, bytes]]] = {}
        max_id = 0
        for payload in frames:
            if len(payload) < 5:
                continue
            op = payload[0]
            txn = struct.unpack_from("<I", payload, 1)[0]
            body = payload[5:]
            max_id = max(max_id, txn)
            if op == OP_BEGIN:
                pending.setdefault(txn, [])
            elif op == OP_ROLLBACK:
                pending[txn] = []
            elif op == OP_COMMIT:
                for item in pending.get(txn, []):
                    self._apply_logged(item[0], item[1])
                pending[txn] = []
            elif op in (OP_CREATE, OP_INSERT):
                pending.setdefault(txn, []).append((op, body))
        self.next_txn = max(1, max_id + 1)

    def _apply_logged(self, op: int, body: bytes) -> None:
        if op == OP_CREATE:
            name, cols = _decode_create_body(body)
            if name in self.tables:
                raise DBError("replayed duplicate table")
            self.tables[name] = Table(cols)
            return
        if op == OP_INSERT:
            name, _i = _take_name(body, 0)
            table = self.tables.get(name)
            if table is None:
                raise DBError("replayed insert into missing table")
            row = _decode_insert_body(body, table.cols)
            table.rows.append(row)
            return
        raise DBError("bad logged op")

    def _clone(self) -> dict[str, Table]:
        out: dict[str, Table] = {}
        for name, table in self.tables.items():
            copy = Table(list(table.cols))
            copy.rows = [tuple(row) for row in table.rows]
            out[name] = copy
        return out

    def write_pages(self) -> None:
        blob = _encode_pages(self.tables, self.next_txn)
        loaded, txn = _decode_pages(blob)
        if txn != self.next_txn or not _same(loaded, self.tables):
            raise DBError("page file does not round-trip")
        path = os.path.join(self.path, "pages")
        with open(path, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())

    def _append_frames(self, payloads: list[bytes]) -> None:
        path = os.path.join(self.path, "wal")
        with open(path, "ab") as fh:
            for payload in payloads:
                fh.write(_frame(payload))
            fh.flush()
            os.fsync(fh.fileno())

    def _flush(self) -> None:
        txn = self.next_txn
        self.next_txn += 1
        payloads = [_payload(OP_BEGIN, txn)]
        for op in self.pending:
            if op[0] == "create":
                payloads.append(_payload(OP_CREATE, txn, _encode_create_body(op[1], op[2])))
            else:
                table = self.tables[op[1]]
                payloads.append(
                    _payload(OP_INSERT, txn, _encode_insert_body(op[1], table.cols, op[2]))
                )
        payloads.append(_payload(OP_COMMIT, txn))
        self.pending = []
        self._append_frames(payloads)
        self.write_pages()

    def _rollback_open(self) -> None:
        if not self.in_txn:
            return
        self.tables = self.snapshot or {}
        self.pending = []
        self.snapshot = None
        self.in_txn = False

    def execute(self, text: str) -> str:
        try:
            statements = Parser(text).statements()
            chunks: list[str] = []
            for stmt in statements:
                try:
                    produced = self._exec(stmt)
                except DBError:
                    self._rollback_open()
                    raise
                if produced:
                    chunks.append(produced)
            self._rollback_open()
            return "".join(chunks)
        finally:
            if not self.in_txn:
                self.write_pages()

    def _exec(self, stmt) -> str:
        kind = stmt[0]
        if kind == "begin":
            if self.in_txn:
                raise DBError("transaction already open")
            self.in_txn = True
            self.snapshot = self._clone()
            self.pending = []
            return ""
        if kind == "commit":
            if not self.in_txn:
                raise DBError("no transaction")
            self._flush()
            self.in_txn = False
            self.snapshot = None
            return ""
        if kind == "rollback":
            if not self.in_txn:
                raise DBError("no transaction")
            self._rollback_open()
            return ""
        if self.in_txn:
            return self._apply_stmt(stmt)
        saved = self._clone()
        saved_pending = list(self.pending)
        try:
            produced = self._apply_stmt(stmt)
            self._flush()
            return produced
        except Exception:
            self.tables = saved
            self.pending = saved_pending
            raise

    def _apply_stmt(self, stmt) -> str:
        kind = stmt[0]
        if kind == "create":
            _k, name, cols = stmt
            if name in self.tables:
                raise DBError(f"table {name} exists")
            self.tables[name] = Table(cols)
            self.pending.append(("create", name, cols))
            return ""
        if kind == "insert":
            _k, name, tuples = stmt
            table = self.tables.get(name)
            if table is None:
                raise DBError(f"no table {name}")
            rows = []
            for values in tuples:
                if len(values) != len(table.cols):
                    raise DBError("column count mismatch")
                row = []
                for (_cname, typ), (vtyp, val) in zip(table.cols, values):
                    if vtyp != typ:
                        raise DBError("type mismatch")
                    row.append(val)
                rows.append(tuple(row))
            for row in rows:
                table.rows.append(row)
                self.pending.append(("insert", name, row))
            return ""
        if kind == "select":
            return self._select(stmt)
        raise DBError("bad statement")

    def _value(self, expr, rowmap: dict[str, tuple[str, object]]) -> tuple[str, object]:
        kind = expr[0]
        if kind == "lit":
            return expr[1]
        if kind == "col":
            name = expr[1]
            if name not in rowmap:
                raise DBError(f"unknown column {name}")
            return rowmap[name]
        if kind == "not":
            val = self._value(expr[1], rowmap)
            if val[0] != "BOOL":
                raise DBError("NOT needs a condition")
            return ("BOOL", not val[1])
        if kind in ("and", "or"):
            left = self._value(expr[1], rowmap)
            right = self._value(expr[2], rowmap)
            if left[0] != "BOOL" or right[0] != "BOOL":
                raise DBError("expected condition")
            if kind == "and":
                return ("BOOL", bool(left[1]) and bool(right[1]))
            return ("BOOL", bool(left[1]) or bool(right[1]))
        if kind == "cmp":
            _k, op, left_e, right_e = expr
            left = self._value(left_e, rowmap)
            right = self._value(right_e, rowmap)
            if left[0] != right[0] or left[0] not in ("INT", "TEXT"):
                raise DBError("type mismatch")
            lv, rv = left[1], right[1]
            ok = {
                "=": lv == rv,
                "<>": lv != rv,
                "<": lv < rv,
                ">": lv > rv,
                "<=": lv <= rv,
                ">=": lv >= rv,
            }[op]
            return ("BOOL", ok)
        raise DBError("bad expression")

    def _select(self, stmt) -> str:
        _k, columns, name, where, order = stmt
        table = self.tables.get(name)
        if table is None:
            raise DBError(f"no table {name}")
        indexes = {col[0]: i for i, col in enumerate(table.cols)}
        if columns is None:
            picked = list(range(len(table.cols)))
        else:
            picked = []
            for col in columns:
                if col not in indexes:
                    raise DBError(f"unknown column {col}")
                picked.append(indexes[col])
        chosen = list(table.rows)
        if where is not None:
            kept = []
            for row in chosen:
                rowmap = {col[0]: (col[1], row[i]) for i, col in enumerate(table.cols)}
                val = self._value(where, rowmap)
                if val[0] != "BOOL":
                    raise DBError("WHERE needs a condition")
                if val[1]:
                    kept.append(row)
            chosen = kept
        if order is not None:
            col, desc = order
            if col not in indexes:
                raise DBError(f"unknown column {col}")
            idx = indexes[col]
            chosen.sort(key=lambda row: row[idx], reverse=desc)
        header = [table.cols[i][0] for i in picked]
        lines = ["\t".join(header)]
        for row in chosen:
            cells = []
            for i in picked:
                val = row[i]
                if table.cols[i][1] == "INT":
                    cells.append(str(int(val)))
                else:
                    cells.append(str(val))
            lines.append("\t".join(cells))
        return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    try:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
        db = Database.open(args.db)
        sys.stdout.write(db.execute(text))
    except DBError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
