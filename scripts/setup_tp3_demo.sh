#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p data/bronze
cp -a data/demo/bronze/. data/bronze/

echo "Bronze demo ready"
find data/bronze -type f -name '*.json' | wc -l