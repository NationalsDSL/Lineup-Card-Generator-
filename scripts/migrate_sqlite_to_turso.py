import os
import sqlite3
from pathlib import Path

import libsql_experimental as libsql


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DB = Path(os.environ.get("LINEUP_DB_PATH", BASE_DIR / "baseball_app.db"))
TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

TABLES = [
    "app_users",
    "organizations",
    "teams",
    "players",
    "saved_team_lineups",
    "lineup_player_selections",
]


def rows_for_table(source_conn, table):
    source_conn.row_factory = sqlite3.Row
    rows = source_conn.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(row) for row in rows]


def create_schema(target):
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            username_key TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            short_name TEXT UNIQUE,
            logo_url TEXT
        )
        """
    )
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            organization_id INTEGER,
            logo_url TEXT,
            UNIQUE(name, organization_id)
        )
        """
    )
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            normalized_name TEXT,
            team_id INTEGER,
            primary_position TEXT,
            bats TEXT,
            throws TEXT,
            jersey_number TEXT,
            UNIQUE(normalized_name, team_id)
        )
        """
    )
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_team_lineups (
            team_id INTEGER PRIMARY KEY,
            lineup_spots INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    target.execute(
        """
        CREATE TABLE IF NOT EXISTS lineup_player_selections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_key TEXT NOT NULL,
            game_date TEXT NOT NULL,
            team_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(export_key, team_id, player_id)
        )
        """
    )
    target.execute(
        "CREATE INDEX IF NOT EXISTS idx_lineup_player_sel_team_date "
        "ON lineup_player_selections(team_id, game_date)"
    )
    target.commit()


def insert_rows(target, table, rows):
    if not rows:
        return 0

    columns = list(rows[0].keys())
    column_sql = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"

    for row in rows:
        target.execute(sql, tuple(row[column] for column in columns))
    return len(rows)


def main():
    if not SOURCE_DB.exists():
        raise SystemExit(f"Source database not found: {SOURCE_DB}")

    source = sqlite3.connect(SOURCE_DB)
    target = libsql.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

    create_schema(target)

    for table in reversed(TABLES):
        target.execute(f"DELETE FROM {table}")
    target.commit()

    total = 0
    for table in TABLES:
        rows = rows_for_table(source, table)
        count = insert_rows(target, table, rows)
        total += count
        print(f"{table}: {count} rows")

    target.commit()
    source.close()
    target.close()
    print(f"Done. Migrated {total} rows from {SOURCE_DB}.")


if __name__ == "__main__":
    main()
