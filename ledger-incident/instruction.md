The ledger service in `/app` was left in a bad state. Read the historical access log, the account files, and the service code, then repair the service and write an incident report. This is a cleanup job: remove the extra access that was planted, and make file export stay inside the data directory. Do not add accounts, and do not delete evidence that sits outside the data directory.

`python3 /app/server.py --host 127.0.0.1 --port 8080` serves three routes:

- `GET /api/entries` returns the JSON array stored in `/app/data/entries.json`.
- `POST /api/entries` with a JSON object `{"amount": <integer>, "memo": <string>}` appends one entry. `amount` must be an integer and `memo` a string. The new `id` is one higher than the current maximum, or 1 when the file is empty. The response is HTTP 201 and the JSON entry.
- `GET /api/export?name=<file>` returns the text of one regular file inside `/app/data`. A name that resolves outside `/app/data` must be rejected with HTTP 400, 403, or 404, and the response body must not contain the outside file. That includes absolute paths and names whose dot segments are percent-encoded. `name=entries.json` must keep working. Leave `/app/host/canary.txt` where it is; deleting it is not a fix.

The existing opening entry must remain. Keep the command-line flags `--host` and `--port`.

The access log is `/app/host/var/log/ledger/access.log`. Leave that file unchanged. Staff who belong on the system are listed in `/app/data/staff.txt`. Accounts are stored in `/app/data/users.json`. One account there is not on the staff list. A cron file and a shadow copy would put that account back:

- `/app/host/etc/cron.d/ledger-restore`
- `/app/host/var/backups/users.json.shadow`

Delete both of those files. Remove the extra account from `/app/data/users.json` and leave `ledger-clerk` in place with role `clerk`.

Write `/app/incident-report.json` as a JSON object with exactly these keys:

- `initial_access`: `path_traversal`
- `vulnerable_route`: the request path used to read outside `/app/data`
- `parameter`: the query parameter that carried the file name
- `rogue_admin`: the username that is not listed in `/app/data/staff.txt`
- `first_malicious_timestamp`: the timestamp of the earliest access-log line that names `canary.txt`
- `persistence`: an array of the two paths listed above

`python3 /app/selfcheck.py` prints how many of the visible checks pass. That number should move in the same direction as the real score. The graded run also posts a new entry and requests the export with an absolute path and with percent-encoded dot segments.
