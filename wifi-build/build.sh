#!/bin/sh
# One-command build: vendor/ + build/ + site/  (needs python3, wabt: wasm2wat/wat2wasm/wasm-validate)
cd "$(dirname "$0")" && exec python3 build.py "$@"
