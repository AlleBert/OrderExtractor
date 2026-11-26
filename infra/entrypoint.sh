#!/bin/sh
set -eu
alembic upgrade head
exec python -m order_extractor server --data-dir /tmp/order
