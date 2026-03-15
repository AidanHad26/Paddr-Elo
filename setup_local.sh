#!/bin/bash
set -e

echo "==> Checking for Homebrew..."
if ! command -v brew &>/dev/null; then
  echo "Homebrew not found. Install it from https://brew.sh then re-run this script."
  exit 1
fi

echo "==> Installing PostgreSQL (if not already installed)..."
brew install postgresql@14

echo "==> Starting PostgreSQL service..."
brew services start postgresql@14

echo "==> Creating local database 'paddr_elo_local' (if it doesn't exist)..."
createdb paddr_elo_local 2>/dev/null || echo "Database already exists, skipping."

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "Setup complete! Run ./run_local.sh to start the dev server."
