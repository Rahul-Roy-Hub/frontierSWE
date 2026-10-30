#!/bin/bash
set -euo pipefail
cp /solution/server.py /app/server.py
python3 /solution/remediate.py
