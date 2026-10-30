# Page-store SQL

Build the engine at `/app/pagestore.py`. The public checker is `/app/selfcheck.py`. It prints how many public scripts passed. That fraction moves with the real score, but the hidden suite is a different set of databases and statements.

## Command

```
python3 /app/pagestore.py --db <directory> --file <script.sql>
```

Create `<directory>` if it is missing. Read the script as UTF-8. On success, exit 0. On a statement error, roll the open transaction back, keep earlier commits, print the error on stderr, and exit 1. Successful `SELECT` results that already ran stay on stdout.

## Language

Keywords are case-insensitive. Identifiers are case-sensitive and match `[A-Za-z_][A-Za-z0-9_]*`. They must not be reserved words: `and`, `asc`, `begin`, `by`, `commit`, `create`, `desc`, `from`, `insert`, `int`, `into`, `not`, `or`, `order`, `rollback`, `select`, `table`, `text`, `values`, `where`.

```
script     = statement*
statement  = create | insert | select | BEGIN | COMMIT | ROLLBACK
create     = CREATE TABLE ident ( coldef ("," coldef)* )
coldef     = ident INT | ident TEXT
insert     = INSERT INTO ident VALUES tuple ("," tuple)*
tuple      = "(" literal ("," literal)* ")"
literal    = integer | string
select     = SELECT ( * | ident ("," ident)* ) FROM ident [WHERE expr] [ORDER BY ident [ASC | DESC]]
expr       = or
or         = and (OR and)*
and        = unary (AND unary)*
unary      = NOT unary | comparison
comparison = primary [("=" | "<>" | "<" | ">" | "<=" | ">=") primary]
primary    = literal | ident | "(" expr ")"
```

Semicolons separate statements. Empty statements are allowed. There is no arithmetic and no `NULL`.

Integers are optional `-` plus digits, parsed as signed 64-bit. `007` is `7`. A leading `+` is an error. Strings use single quotes, and `''` inside a string is one quote. Text may not contain tab, carriage return, or newline.

`AND` binds tighter than `OR`. `NOT` binds tighter than `AND`. Comparisons are non-associative. Both sides of a comparison must be the same type. A column reference uses the selected table. `ORDER BY` defaults to `ASC` and is a stable sort.

Outside an explicit transaction each statement commits on success. `BEGIN` starts a transaction. `COMMIT` commits it. `ROLLBACK` drops it. `BEGIN` inside a transaction, or `COMMIT` or `ROLLBACK` outside one, is an error. A failed statement drops the whole open transaction and does not change committed state. An autocommit failure leaves previous commits in place. If the script ends while a transaction is still open, roll that transaction back and exit 0. `SELECT` inside a transaction sees that transaction's rows.

`CREATE TABLE` needs one or more columns and a new name. `INSERT` values must match the column count and types, in column order. One `INSERT` with several tuples is atomic. `SELECT *` uses creation order. Results are TSV: a header line, then one line per row, columns separated by a single tab, lines ended by `\n`. Integers have no `+` and no leading zeros. Text is written as itself. Non-`SELECT` statements write nothing.

## Files

The database directory holds two files, `pages` and `wal`.

`pages` is a sequence of 4096-byte pages. Page 0:

| offset | type   | meaning                          |
|--------|--------|----------------------------------|
| 0      | 4 bytes| magic `PSQ1`                     |
| 4      | u32    | page count                       |
| 8      | u32    | schema page, always 1            |
| 12     | u32    | freelist head, always 0          |
| 16     | u32    | next transaction id              |

All integers are little-endian. Unused bytes are zero.

Page 1 is the catalog:

```
u16 table_count
repeat table_count times:
  u16 name_len, name utf-8
  u16 column_count
  repeat column_count times:
    u8 type          # 1 = INT, 2 = TEXT
    u16 name_len, name utf-8
  u32 first_heap_page   # 0 when the table has no rows
  u32 row_count
```

Heap pages start at page 2:

```
u32 next_page       # 0 when this is the last page of the table
u16 slot_count
u16 unused
repeat slot_count times:
  u16 row_offset    # from the start of this page
row bytes packed downward from offset 4096
```

A row is the column values in order:

```
INT:  u8 1, i64
TEXT: u8 2, u32 byte_len, utf-8 bytes
```

A row that does not fit in one page is an error. Rewrite `pages` from committed state after every successful commit and after recovery.

`wal` is the durable log and the source of truth on open. Each frame is:

```
u32 payload_len
payload bytes
u32 crc32          # zlib CRC-32 of the payload, unsigned 32-bit
```

A payload is:

```
u8 op
u32 txn_id
body
```

| op | name     | body |
|----|----------|------|
| 1  | BEGIN    | empty |
| 2  | CREATE   | same bytes as one catalog table entry, without the heap fields |
| 3  | INSERT   | u16 name_len, table name, then one row encoding |
| 4  | COMMIT   | empty |
| 5  | ROLLBACK | empty |

CREATE body:

```
u16 name_len, name utf-8
u16 column_count
repeat:
  u8 type
  u16 name_len, name utf-8
```

On open, read frames from the start of `wal`. Stop at the first frame that does not fit in the remaining bytes, whose length is greater than 10000000, or whose CRC does not match. That tail is torn: ignore it and leave committed state intact. You may rewrite `wal` so the torn tail is gone.

Group the accepted frames by `txn_id`. Apply `CREATE` and `INSERT` for a transaction only when a `COMMIT` frame for that same id is present, in log order. `ROLLBACK`, and any frames with no matching `COMMIT`, are discarded. A lone `INSERT` is not an autocommit. Replaying a committed prefix must not duplicate rows that the same prefix already committed. After recovery, `pages` must match the committed tables and start with `PSQ1`.
