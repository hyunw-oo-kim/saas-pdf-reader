#!/bin/bash
set -e

pip install --upgrade pip
pip install -e .

# DB 마이그레이션
python -m alembic upgrade head
