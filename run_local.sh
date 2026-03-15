#!/bin/bash

# Start PostgreSQL if not already running
if ! pg_isready -q 2>/dev/null; then
  echo "==> Starting PostgreSQL..."
  if [ -d /opt/homebrew/var/postgresql@14 ]; then
    pg_ctl -D /opt/homebrew/var/postgresql@14 start -l /opt/homebrew/var/log/postgresql@14.log
  elif [ -d /usr/local/var/postgresql@14 ]; then
    pg_ctl -D /usr/local/var/postgresql@14 start -l /usr/local/var/log/postgresql@14.log
  else
    echo "Could not find PostgreSQL data directory. Run setup_local.sh first."
    exit 1
  fi
  sleep 2
fi

export DATABASE_URL=postgresql://localhost/paddr_elo_local
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=localpassword
export SECRET_KEY=local-dev-secret

echo "Starting local dev server at http://localhost:5050"
echo "Admin login: admin / localpassword"
python app.py
