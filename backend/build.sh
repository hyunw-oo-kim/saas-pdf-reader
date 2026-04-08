#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# DB 마이그레이션
python -m alembic upgrade head
