import hashlib
import json
import os
import sqlite3
import unicodedata
import re
from io import BytesIO
from datetime import datetime, timedelta
from urllib.request import urlopen
from urllib.parse import urljoin

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, legal, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


st.set_page_config(page_title="Lineup Manager", page_icon=":baseball:", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("LINEUP_DB_PATH", os.path.join(BASE_DIR, "baseball_app.db"))
MLB_LOGO_URL = (
    "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/mlb.png"
    "&w=400&h=400&transparent=true"
)
DSL_LOGO_URL = "https://brandeps.com/logo-download/D/Dominican-Summer-League-logo-01.png"
MLB_LOGO_FILE = os.path.join(BASE_DIR, "assets", "logos", "png", "league_mlb.png")
DSL_LOGO_FILE = os.path.join(BASE_DIR, "assets", "logos", "png", "league_dsl.png")
OFFICIAL_FORM_FONT_CACHE = None


conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
cursor = conn.cursor()


def init_db():
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            short_name TEXT UNIQUE
        )
        """
    )

    cursor.execute("PRAGMA table_info(organizations)")
    org_columns = [row[1] for row in cursor.fetchall()]
    if "logo_url" not in org_columns:
        cursor.execute("ALTER TABLE organizations ADD COLUMN logo_url TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            organization_id INTEGER,
            UNIQUE(name, organization_id)
        )
        """
    )
    cursor.execute("PRAGMA table_info(teams)")
    team_columns = [row[1] for row in cursor.fetchall()]
    if "logo_url" not in team_columns:
        cursor.execute("ALTER TABLE teams ADD COLUMN logo_url TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            normalized_name TEXT,
            team_id INTEGER,
            primary_position TEXT,
            bats TEXT,
            throws TEXT,
            UNIQUE(normalized_name, team_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_team_lineups (
            team_id INTEGER PRIMARY KEY,
            lineup_spots INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lineup_player_sel_team_date "
        "ON lineup_player_selections(team_id, game_date)"
    )
    conn.commit()


init_db()


def inject_styles():
    st.markdown(
        """
        <style>
        :root {
            --ink: #102136;
            --sidebar-1: #0f2740;
            --sidebar-2: #18466c;
        }

        html, body, .stApp {
            font-family: "Segoe UI", sans-serif;
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 8% 6%, rgba(21, 160, 191, 0.14), transparent 35%),
                radial-gradient(circle at 88% 12%, rgba(15, 94, 136, 0.12), transparent 36%),
                linear-gradient(180deg, #f7fbff 0%, #eef4fb 100%);
            color: var(--ink);
        }

        [data-testid="stAppViewContainer"] .main .block-container {
            max-width: 1240px;
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, var(--sidebar-1) 0%, var(--sidebar-2) 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.12);
        }

        [data-testid="stSidebar"] * {
            color: #eef4ff !important;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 10px;
            margin-bottom: 8px;
            padding: 6px 10px;
            transition: all 0.18s ease;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.28);
            transform: translateX(2px);
        }

        .stMarkdown, .stText, .stCaption, .stAlert, p, li, label {
            color: var(--ink);
        }

        h1, h2, h3 {
            letter-spacing: 0.3px;
            color: #0d2238;
        }

        h4 {
            color: #123556;
        }

        .hero {
            background:
                linear-gradient(124deg, rgba(15, 94, 136, 0.98), rgba(21, 160, 191, 0.95));
            color: #ffffff;
            border-radius: 18px;
            padding: 20px 22px;
            margin-bottom: 12px;
            border: 1px solid rgba(255, 255, 255, 0.22);
            box-shadow: 0 14px 30px rgba(11, 63, 99, 0.26);
        }

        .hero h1 {
            margin: 0;
            font-size: 2.05rem;
            letter-spacing: 0.5px;
        }

        .hero p {
            margin: 5px 0 0;
            opacity: 0.92;
            font-size: 1rem;
        }

        [data-testid="stMetric"] {
            background: linear-gradient(180deg, #ffffff 0%, #f9fcff 100%);
            border: 1px solid #d6e3f2;
            border-radius: 14px;
            padding: 12px 14px;
            box-shadow: 0 8px 20px rgba(15, 39, 64, 0.08);
        }

        [data-testid="stMetricLabel"] p {
            color: #4b627b;
            font-weight: 600;
            letter-spacing: 0.2px;
        }

        [data-testid="stMetricValue"] {
            color: #0f2e4b;
            font-weight: 700;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid #d4e0ee;
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 8px 22px rgba(15, 39, 64, 0.08);
            background: #ffffff;
        }

        [data-testid="stDataFrame"] thead tr th {
            background: #eff5fc !important;
            color: #173858 !important;
            border-bottom: 1px solid #d7e4f1 !important;
            font-weight: 700 !important;
        }

        [data-testid="stDataFrame"] tbody tr:nth-child(even) td {
            background: #fbfdff !important;
        }

        [data-baseweb="input"] input,
        [data-baseweb="base-input"] input,
        [data-baseweb="textarea"] textarea {
            border-radius: 10px !important;
            border: 1px solid #c8d7e8 !important;
            background: #ffffff !important;
            color: var(--ink) !important;
        }

        [data-baseweb="select"] > div {
            border-radius: 10px !important;
            border: 1px solid #c8d7e8 !important;
            min-height: 42px;
            background: #ffffff !important;
            color: var(--ink) !important;
        }

        label p {
            color: #173858 !important;
            font-weight: 600 !important;
        }

        .stButton > button,
        .stDownloadButton > button {
            border-radius: 11px !important;
            border: 1px solid #0f5e88 !important;
            background: linear-gradient(135deg, #0f5e88, #0f7ea3) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            box-shadow: 0 8px 18px rgba(15, 94, 136, 0.26);
            transition: transform 0.15s ease, box-shadow 0.15s ease, filter 0.15s ease;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover {
            transform: translateY(-1px);
            filter: brightness(1.05);
            box-shadow: 0 12px 22px rgba(15, 94, 136, 0.32);
        }

        .stButton > button[kind="secondary"] {
            background: #ffffff !important;
            color: #153a5b !important;
            border: 1px solid #c3d4e6 !important;
            box-shadow: none !important;
        }

        .stCheckbox label p, .stRadio label p {
            font-weight: 600 !important;
        }

        .stAlert {
            border-radius: 12px !important;
            border: 1px solid #d5e3f2 !important;
            background: #f7fbff !important;
        }

        [data-testid="stExpander"] {
            border: 1px solid #d0deed !important;
            border-radius: 12px !important;
            background: #ffffff !important;
        }

        hr {
            border: none;
            border-top: 1px solid #d4e0ee;
            margin: 0.8rem 0 1rem 0;
        }

        .stCaption {
            color: #5a6d83 !important;
        }

        @media (max-width: 900px) {
            [data-testid="stAppViewContainer"] .main .block-container {
                padding-top: 0.6rem;
                padding-left: 0.8rem;
                padding-right: 0.8rem;
            }

            .hero h1 {
                font-size: 1.7rem;
            }

            .hero p {
                font-size: 0.95rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_styles()


def normalize_name(name):
    value = str(name).strip().lower()
    return unicodedata.normalize("NFKD", value).encode("ASCII", "ignore").decode()


def normalize_hand(value):
    hand = str(value).strip().upper()
    if hand in {"L", "R", "S"}:
        return hand
    return "-"


FIELD_POSITION_OPTIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "EH", "P"]
LINEUP_POSITION_OPTIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "EH"]
MAX_EXTRA_LIST_ENTRIES = 16
POSITION_NORMALIZATION_MAP = {
    "C": "C",
    "1B": "1B",
    "2B": "2B",
    "3B": "3B",
    "SS": "SS",
    "LF": "LF",
    "CF": "CF",
    "RF": "RF",
    "DH": "DH",
    "EH": "EH",
    "P": "P",
    "OF": "CF",
    "IF": "2B",
}


def normalize_position_choice(value):
    raw = str(value).upper().strip()
    if not raw:
        return "DH"

    cleaned = raw.replace(" ", "").replace("-", "")
    if cleaned in POSITION_NORMALIZATION_MAP:
        return POSITION_NORMALIZATION_MAP[cleaned]
    if "/" in raw:
        first = raw.split("/")[0].strip().replace(" ", "")
        if first in POSITION_NORMALIZATION_MAP:
            return POSITION_NORMALIZATION_MAP[first]
    return "DH"


def normalize_lineup_position_choice(value):
    pos = normalize_position_choice(value)
    if pos == "P":
        return "DH"
    if pos not in LINEUP_POSITION_OPTIONS:
        return "DH"
    return pos


def get_bat_color(bats):
    if bats == "L":
        return "#f87171"
    if bats == "S":
        return "#0ea5e9"
    return "#000000"


def get_throw_color(throws):
    if throws == "L":
        return "#dc2626"
    return "#111827"


def get_pitcher_row_color_by_throw(throws):
    throw_hand = str(throws).upper().strip()
    if throw_hand in {"S", "L"}:
        return "#f87171"
    return "#000000"


def find_column(df, candidates, label):
    normalized_cols = [(col, col.lower().strip()) for col in df.columns]

    for candidate in candidates:
        for original, normalized in normalized_cols:
            if normalized == candidate:
                return original

    for original, normalized in normalized_cols:
        if any(candidate in normalized for candidate in candidates):
            return original

    raise ValueError(
        f"Missing column for '{label}'. "
        f"Tried: {', '.join(candidates)}. "
        f"Available: {', '.join(df.columns)}"
    )


def get_or_create_org(short_name):
    short_name = str(short_name).strip()
    cursor.execute("SELECT id FROM organizations WHERE short_name=?", (short_name,))
    row = cursor.fetchone()
    if row:
        return int(row[0])

    cursor.execute(
        "INSERT INTO organizations (name, short_name) VALUES (?, ?)",
        (short_name, short_name),
    )
    conn.commit()
    return int(cursor.lastrowid)


def set_org_logo(short_name, logo_url):
    org_short = str(short_name).strip()
    logo = str(logo_url).strip()
    org_id = get_or_create_org(org_short)
    cursor.execute("UPDATE organizations SET logo_url=? WHERE id=?", (logo, org_id))
    conn.commit()
    return org_id


def set_team_logo(team_id, logo_url):
    logo = str(logo_url).strip()
    cursor.execute(
        "UPDATE teams SET logo_url=? WHERE id=?",
        (logo, int(team_id)),
    )
    conn.commit()


def get_org_logo(short_name):
    org_short = str(short_name).strip()
    cursor.execute("SELECT logo_url FROM organizations WHERE short_name=?", (org_short,))
    row = cursor.fetchone()
    if not row:
        return None
    value = row[0]
    return str(value).strip() if value else None


def parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_saved_team_lineup(team_id):
    cursor.execute(
        "SELECT lineup_spots, payload_json FROM saved_team_lineups WHERE team_id=?",
        (int(team_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None

    try:
        payload = json.loads(str(row[1] or "{}"))
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    return {"lineup_spots": int(row[0]), "payload": payload}


def save_team_lineup(team_id, lineup_spots, payload):
    payload_json = json.dumps(payload, ensure_ascii=True)
    cursor.execute(
        """
        INSERT INTO saved_team_lineups (team_id, lineup_spots, payload_json, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(team_id) DO UPDATE SET
            lineup_spots=excluded.lineup_spots,
            payload_json=excluded.payload_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (int(team_id), int(lineup_spots), payload_json),
    )
    conn.commit()


def apply_saved_lineup_to_session(side_key, team_id, player_ids, lineup_spots):
    saved = get_saved_team_lineup(team_id)
    if not saved:
        return

    payload = saved.get("payload", {})
    if not isinstance(payload, dict):
        return

    valid_player_ids = {int(pid) for pid in player_ids}

    saved_lineup_ids = []
    for raw_pid in payload.get("lineup_ids", []):
        pid = parse_int(raw_pid)
        if pid is None or pid not in valid_player_ids or pid in saved_lineup_ids:
            continue
        saved_lineup_ids.append(pid)

    max_spots = min(int(lineup_spots), len(saved_lineup_ids))
    saved_positions = payload.get("lineup_positions", [])
    for spot in range(1, max_spots + 1):
        pid = int(saved_lineup_ids[spot - 1])
        st.session_state[f"{side_key}_{team_id}_spot_{spot}"] = pid
        raw_pos = saved_positions[spot - 1] if spot - 1 < len(saved_positions) else "DH"
        norm_pos = normalize_lineup_position_choice(raw_pos)
        st.session_state[f"{side_key}_{team_id}_pos_{spot}"] = norm_pos
        st.session_state[f"{side_key}_{team_id}_pos_player_{spot}"] = pid

    pitcher_id = parse_int(payload.get("pitcher_id"))
    if pitcher_id is not None and pitcher_id in valid_player_ids:
        st.session_state[f"{side_key}_{team_id}_pitcher"] = int(pitcher_id)

    for sub in payload.get("substitutions", []):
        if not isinstance(sub, dict):
            continue
        spot = parse_int(sub.get("spot"))
        if spot is None or spot < 1 or spot > int(lineup_spots):
            continue
        st.session_state[f"{side_key}_{team_id}_sub_enabled_{spot}"] = bool(sub.get("enabled", False))
        sub_player_id = parse_int(sub.get("sub_player_id"))
        if sub_player_id is not None and sub_player_id in valid_player_ids:
            st.session_state[f"{side_key}_{team_id}_sub_player_{spot}"] = int(sub_player_id)
        inning = parse_int(sub.get("inning"))
        if inning is not None and 1 <= inning <= 11:
            st.session_state[f"{side_key}_{team_id}_sub_inning_{spot}"] = int(inning)

    note_enabled_flags = payload.get("note_enabled", [])
    note_texts = payload.get("note_texts", [])
    for spot in range(1, int(lineup_spots) + 1):
        enabled_value = False
        if spot - 1 < len(note_enabled_flags):
            enabled_value = bool(note_enabled_flags[spot - 1])
        st.session_state[f"{side_key}_{team_id}_note_enabled_{spot}"] = enabled_value
        note_value = ""
        if spot - 1 < len(note_texts):
            note_value = str(note_texts[spot - 1]).strip()
        st.session_state[f"{side_key}_{team_id}_note_text_{spot}"] = note_value

    no_extra_players = bool(payload.get("no_present_extra_players", False))
    st.session_state[f"{side_key}_{team_id}_no_extra_players"] = no_extra_players
    extra_player_ids = []
    for raw_pid in payload.get("extra_player_ids", []):
        pid = parse_int(raw_pid)
        if pid is None or pid not in valid_player_ids or pid in extra_player_ids:
            continue
        extra_player_ids.append(pid)
    st.session_state[f"{side_key}_{team_id}_extra_players_count"] = min(
        MAX_EXTRA_LIST_ENTRIES,
        len(extra_player_ids),
    )
    for idx, pid in enumerate(extra_player_ids[:MAX_EXTRA_LIST_ENTRIES], start=1):
        st.session_state[f"{side_key}_{team_id}_extra_player_{idx}"] = int(pid)

    no_extra_pitchers = bool(payload.get("no_present_extra_pitchers", False))
    st.session_state[f"{side_key}_{team_id}_no_extra_pitchers"] = no_extra_pitchers
    extra_pitcher_ids = []
    for raw_pid in payload.get("extra_pitcher_ids", []):
        pid = parse_int(raw_pid)
        if pid is None or pid not in valid_player_ids or pid in extra_pitcher_ids:
            continue
        extra_pitcher_ids.append(pid)
    st.session_state[f"{side_key}_{team_id}_extra_pitchers_count"] = min(
        MAX_EXTRA_LIST_ENTRIES,
        len(extra_pitcher_ids),
    )
    for idx, pid in enumerate(extra_pitcher_ids[:MAX_EXTRA_LIST_ENTRIES], start=1):
        st.session_state[f"{side_key}_{team_id}_extra_pitcher_{idx}"] = int(pid)


def build_lineup_export_key(game_date, away_team_id, home_team_id, away_state, home_state):
    payload = {
        "game_date": str(game_date),
        "away_team_id": int(away_team_id),
        "home_team_id": int(home_team_id),
        "away_lineup_ids": [int(pid) for pid in away_state.get("lineup_ids", [])],
        "home_lineup_ids": [int(pid) for pid in home_state.get("lineup_ids", [])],
        "away_lineup_positions": [str(pos) for pos in away_state.get("lineup_positions", [])],
        "home_lineup_positions": [str(pos) for pos in home_state.get("lineup_positions", [])],
        "away_pitcher_id": int(away_state.get("pitcher_id", 0)),
        "home_pitcher_id": int(home_state.get("pitcher_id", 0)),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def log_lineup_player_selections(export_key, game_date, team_id, lineup_ids):
    game_date_text = str(game_date)
    for player_id in lineup_ids:
        cursor.execute(
            """
            INSERT OR IGNORE INTO lineup_player_selections
            (export_key, game_date, team_id, player_id)
            VALUES (?, ?, ?, ?)
            """,
            (str(export_key), game_date_text, int(team_id), int(player_id)),
        )
    conn.commit()


def get_lineup_usage_counts(team_id, start_date, end_date):
    start_text = start_date.strftime("%Y-%m-%d")
    end_text = end_date.strftime("%Y-%m-%d")
    query = """
        SELECT
            player_id AS id,
            COUNT(DISTINCT export_key) AS games_selected
        FROM lineup_player_selections
        WHERE team_id = ?
          AND game_date BETWEEN ? AND ?
        GROUP BY player_id
    """
    return pd.read_sql(query, conn, params=(int(team_id), start_text, end_text))


def load_logo_reader(logo_url):
    url = str(logo_url).strip() if logo_url else ""
    if not url:
        return None

    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        local_candidates = [url, os.path.join(BASE_DIR, url)]
        for local_path in local_candidates:
            if not os.path.exists(local_path):
                continue
            try:
                with open(local_path, "rb") as image_file:
                    return ImageReader(BytesIO(image_file.read()))
            except Exception:
                continue
        return None

    try:
        with urlopen(url, timeout=8) as response:
            image_bytes = response.read()
            content_type = str(response.headers.get("Content-Type", "")).lower()

        if "image" in content_type:
            return ImageReader(BytesIO(image_bytes))

        # Some links point to a webpage. Try to resolve a real image URL.
        html = image_bytes.decode("utf-8", errors="ignore")
        candidates = []
        meta_patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pattern in meta_patterns:
            matches = re.findall(pattern, html, flags=re.IGNORECASE)
            for match in matches:
                if match:
                    candidates.append(match.strip())

        img_matches = re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']',
            html,
            flags=re.IGNORECASE,
        )
        candidates.extend([m.strip() for m in img_matches if m])

        seen = set()
        for candidate in candidates:
            absolute = urljoin(url, candidate)
            if absolute in seen:
                continue
            seen.add(absolute)
            try:
                with urlopen(absolute, timeout=8) as img_response:
                    candidate_bytes = img_response.read()
                    candidate_type = str(img_response.headers.get("Content-Type", "")).lower()
                if "image" in candidate_type:
                    return ImageReader(BytesIO(candidate_bytes))
                try:
                    return ImageReader(BytesIO(candidate_bytes))
                except Exception:
                    continue
            except Exception:
                continue

        return None
    except Exception:
        return None


def get_or_create_team(team_name, org_id):
    team_name = str(team_name).strip()
    cursor.execute(
        "SELECT id FROM teams WHERE name=? AND organization_id=?",
        (team_name, org_id),
    )
    row = cursor.fetchone()
    if row:
        return int(row[0])

    cursor.execute(
        "INSERT INTO teams (name, organization_id) VALUES (?, ?)",
        (team_name, org_id),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_teams():
    query = """
        SELECT
            t.id,
            t.name AS team,
            o.short_name AS org,
            t.logo_url AS team_logo_url,
            o.logo_url AS org_logo_url,
            COALESCE(NULLIF(TRIM(t.logo_url), ''), o.logo_url) AS logo_url,
            COUNT(p.id) AS player_count
        FROM teams t
        JOIN organizations o ON o.id = t.organization_id
        LEFT JOIN players p ON p.team_id = t.id
        GROUP BY t.id, t.name, t.logo_url, o.short_name, o.logo_url
        ORDER BY o.short_name, t.name
    """
    return pd.read_sql(query, conn)


def find_optional_column(df, candidates):
    normalized = {str(col).strip().lower(): col for col in df.columns}
    for name in candidates:
        key = str(name).strip().lower()
        if key in normalized:
            return normalized[key]
    return None


def get_roster(team_id):
    query = """
        SELECT id, name, primary_position, bats, throws
        FROM players
        WHERE team_id = ?
        ORDER BY name
    """
    return pd.read_sql(query, conn, params=(team_id,))


def add_player_to_team(team_id, name, primary_position, bats, throws):
    team_id = int(team_id)
    clean_name = str(name).strip()
    if not clean_name:
        return False, "Player name is required."

    normalized = normalize_name(clean_name)
    if not normalized:
        return False, "Player name is invalid."

    position = normalize_position_choice(primary_position)
    bats_value = normalize_hand(bats)
    throws_value = normalize_hand(throws)
    if bats_value == "-" or throws_value == "-":
        return False, "Bats and Throws are required."

    cursor.execute(
        "SELECT id FROM players WHERE normalized_name=? AND team_id=?",
        (normalized, team_id),
    )
    if cursor.fetchone():
        return False, "This player already exists on this roster."

    cursor.execute(
        """
        INSERT INTO players (name, normalized_name, team_id, primary_position, bats, throws)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (clean_name, normalized, team_id, position, bats_value, throws_value),
    )
    conn.commit()
    return True, "Player added."


def delete_player_from_team(team_id, player_id):
    cursor.execute(
        "DELETE FROM players WHERE id=? AND team_id=?",
        (int(player_id), int(team_id)),
    )
    deleted = cursor.rowcount > 0

    if deleted:
        cursor.execute(
            "DELETE FROM lineup_player_selections WHERE team_id=? AND player_id=?",
            (int(team_id), int(player_id)),
        )
        conn.commit()
    return deleted


def team_label(team_row):
    return (
        f"{team_row['org']} | {team_row['team']} "
        f"({int(team_row['player_count'])} players)"
    )


def format_team_name(team_row):
    return f"{team_row['org']} | {team_row['team']}"


def roster_table_for_ui(roster):
    return roster.rename(
        columns={
            "name": "Player",
            "primary_position": "Pos",
            "bats": "B",
            "throws": "T",
        }
    )[["Player", "Pos", "B", "T"]]


def lineup_records_from_ids(lineup_ids, lineup_positions, lineup_notes, player_map):
    records = []
    for idx, player_id in enumerate(lineup_ids):
        player = player_map[int(player_id)]
        note = "-"
        if lineup_notes and idx < len(lineup_notes):
            note = str(lineup_notes[idx]).strip() or "-"
        records.append(
            {
                "name": str(player["name"]),
                "primary_position": str(lineup_positions[idx]),
                "bats": str(player["bats"]),
                "throws": str(player["throws"]),
                "notes": note,
            }
        )
    return records


def hand_breakdown(lineup_records):
    counts = {"L": 0, "S": 0, "R": 0}
    for player in lineup_records:
        hand = str(player["bats"]).upper()
        if hand in counts:
            counts[hand] += 1
    return counts


def lineup_preview_df(team_name, lineup_records, pitcher_record):
    rows = []
    for idx, player in enumerate(lineup_records, start=1):
        rows.append(
            {
                "#": idx,
                "Player": player["name"],
                "Pos": player["primary_position"],
                "B": player["bats"],
                "T": player["throws"],
                "Notes": player.get("notes", "-"),
            }
        )

    rows.append(
        {
            "#": "P",
            "Player": pitcher_record["name"],
            "Pos": "SP",
            "B": pitcher_record["bats"],
            "T": pitcher_record["throws"],
            "Notes": pitcher_record.get("notes", "-"),
        }
    )
    df = pd.DataFrame(rows)
    df.attrs["title"] = team_name
    return df


def draw_pdf_team_block(
    pdf,
    x,
    y_top,
    width,
    team_title,
    lineup_records,
    pitcher_record,
    accent_hex,
    team_logo=None,
):
    header_h = 22
    col_h = 18
    row_h = 24
    summary_h = 0
    total_rows = len(lineup_records) + 1
    table_h = header_h + col_h + (total_rows * row_h) + summary_h

    num_w = 24
    pos_w = 34
    notes_w = 130
    name_w = width - num_w - pos_w - notes_w

    x_num_end = x + num_w
    x_name_end = x_num_end + name_w
    x_pos_end = x_name_end + pos_w
    x_notes_end = x_pos_end + notes_w
    y_bottom = y_top - table_h

    pdf.setStrokeColor(colors.HexColor("#1f2937"))
    pdf.setLineWidth(1)
    pdf.rect(x, y_bottom, width, table_h, stroke=1, fill=0)

    pdf.setFillColor(colors.HexColor(accent_hex))
    pdf.rect(x, y_top - header_h, width, header_h, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(x + 6, y_top - header_h + 6, team_title[:44])

    y_col_top = y_top - header_h
    y_col_bottom = y_col_top - col_h
    pdf.setFillColor(colors.HexColor("#e5e7eb"))
    pdf.rect(x, y_col_bottom, width, col_h, stroke=0, fill=1)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(x + (num_w / 2), y_col_bottom + 5, "#")
    pdf.drawString(x_num_end + 4, y_col_bottom + 5, "PLAYER")
    pdf.drawCentredString(x_name_end + (pos_w / 2), y_col_bottom + 5, "POS")
    pdf.drawCentredString(x_pos_end + (notes_w / 2), y_col_bottom + 5, "NOTES")

    y_rows_top = y_col_bottom
    y_rows_bottom = y_rows_top - (total_rows * row_h)

    if team_logo:
        body_h = y_rows_top - y_rows_bottom
        watermark_w = width * 0.80
        watermark_h = body_h * 0.80
        wm_x = x + ((width - watermark_w) / 2)
        wm_y = y_rows_bottom + ((body_h - watermark_h) / 2)
        pdf.saveState()
        if hasattr(pdf, "setFillAlpha"):
            pdf.setFillAlpha(0.18)
        pdf.drawImage(
            team_logo,
            wm_x,
            wm_y,
            width=watermark_w,
            height=watermark_h,
            preserveAspectRatio=True,
            mask="auto",
        )
        pdf.restoreState()

    for x_line in [x_num_end, x_name_end, x_pos_end]:
        pdf.line(x_line, y_rows_top, x_line, y_rows_bottom)

    for row_index in range(total_rows + 1):
        y_line = y_rows_top - (row_index * row_h)
        pdf.line(x, y_line, x + width, y_line)

    table_rows = []
    for idx, player in enumerate(lineup_records, start=1):
        table_rows.append(
            {
                "slot": str(idx),
                "name": str(player["name"]),
                "primary_position": str(player["primary_position"]),
                "bats": str(player["bats"]),
                "throws": str(player["throws"]),
                "notes": str(player.get("notes", "-")),
                "is_pitcher": False,
            }
        )
    table_rows.append(
        {
            "slot": "P",
            "name": str(pitcher_record["name"]),
            "primary_position": "SP",
            "bats": str(pitcher_record["bats"]),
            "throws": str(pitcher_record["throws"]),
            "notes": str(pitcher_record.get("notes", "-")),
            "is_pitcher": True,
        }
    )

    for idx, row in enumerate(table_rows):
        row_top = y_rows_top - (idx * row_h)
        y_main = row_top - 9
        y_sub = row_top - 19

        if row["is_pitcher"]:
            row_color = colors.HexColor(get_pitcher_row_color_by_throw(row["throws"]))
        else:
            row_color = colors.HexColor(get_bat_color(str(row["bats"]).upper()))

        pdf.setFillColor(row_color)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawCentredString(x + (num_w / 2), y_main, row["slot"])
        pdf.drawString(x_num_end + 4, y_main, row["name"][:28])
        pdf.drawCentredString(x_name_end + (pos_w / 2), y_main, row["primary_position"][:4])

        note_text = row.get("notes", "-")
        if note_text and str(note_text).strip() and str(note_text).strip() != "-":
            main_note = "SUB"
            sub_line = str(note_text)
        else:
            main_note = "-"
            sub_line = "-"
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(x_pos_end + 3, y_main, main_note[:18])
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawString(x_pos_end + 3, y_sub, sub_line[:26])

    return y_rows_bottom


def draw_pdf_scoreboard(pdf, x, y_bottom, width, away_name, home_name, away_logo, home_logo):
    row_h = 20
    num_rows = 3
    y_top = y_bottom + (num_rows * row_h)

    first_col = 100
    total_small_cols = 14
    small_w = (width - first_col) / total_small_cols

    pdf.setStrokeColor(colors.HexColor("#1f2937"))
    pdf.rect(x, y_bottom, width, num_rows * row_h, stroke=1, fill=0)

    for r in range(1, num_rows):
        y_line = y_bottom + (r * row_h)
        pdf.line(x, y_line, x + width, y_line)

    x_line = x + first_col
    pdf.line(x_line, y_bottom, x_line, y_top)
    for _ in range(total_small_cols - 1):
        x_line += small_w
        pdf.line(x_line, y_bottom, x_line, y_top)

    headers = [str(i) for i in range(1, 12)] + ["R", "H", "E"]
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 9)
    for idx, header in enumerate(headers):
        center_x = x + first_col + (idx * small_w) + (small_w / 2)
        pdf.drawCentredString(center_x, y_top - row_h + 6, header)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 9)
    away_cell_bottom = y_top - (2 * row_h)
    home_cell_bottom = y_top - (3 * row_h)

    logo_w = 18
    logo_h = 18
    logo_x = x + 6

    if away_logo:
        pdf.drawImage(
            away_logo,
            logo_x,
            away_cell_bottom + ((row_h - logo_h) / 2),
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )
    else:
        pdf.drawString(x + 6, away_cell_bottom + 6, away_name[:20])

    if home_logo:
        pdf.drawImage(
            home_logo,
            logo_x,
            home_cell_bottom + ((row_h - logo_h) / 2),
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )
    else:
        pdf.drawString(x + 6, home_cell_bottom + 6, home_name[:20])


def build_extra_lines(extra_records, no_present_flag, mode):
    if no_present_flag:
        return ["NO PRESENTAR"]
    if not extra_records:
        return ["-"]

    if mode == "players":
        return [
            f"{idx}. {p['name'][:24]} ({p['primary_position']})"
            for idx, p in enumerate(extra_records, start=1)
        ]
    return [
        f"{idx}. {p['name'][:24]} (T:{p['throws']})"
        for idx, p in enumerate(extra_records, start=1)
    ]


def draw_pdf_extra_block(
    pdf,
    x,
    y_top,
    width,
    title,
    lines,
    accent_hex,
):
    title_h = 16
    section_h = 12
    line_h = 11
    bottom_pad = 6

    row_count = max(1, (len(lines) + 1) // 2)
    block_h = title_h + section_h + (row_count * line_h) + bottom_pad
    y_bottom = y_top - block_h

    pdf.setStrokeColor(colors.HexColor("#1f2937"))
    pdf.rect(x, y_bottom, width, block_h, stroke=1, fill=0)

    pdf.setFillColor(colors.HexColor(accent_hex))
    pdf.rect(x, y_top - title_h, width, title_h, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(x + 6, y_top - title_h + 4, title)

    y_cursor = y_top - title_h - section_h
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(x + 6, y_cursor + 2, "LIST")
    y_cursor -= 2
    pdf.setFont("Helvetica-Bold", 8)
    content_x = x + 8
    content_w = width - 16
    col_gap = 10
    col_w = (content_w - col_gap) / 2
    right_x = content_x + col_w + col_gap
    split_index = row_count
    text_max = 28
    for row_idx in range(row_count):
        y_cursor -= line_h
        left_idx = row_idx
        right_idx = row_idx + split_index

        if left_idx < len(lines):
            left_text = str(lines[left_idx])[:text_max]
            pdf.drawString(content_x, y_cursor + 3, left_text)
        if right_idx < len(lines):
            right_text = str(lines[right_idx])[:text_max]
            pdf.drawString(right_x, y_cursor + 3, right_text)

    return y_bottom


def build_umpire_extra_rows(extra_records, no_present_flag, mode):
    if no_present_flag:
        return [("NO PRESENTAR", "-", "-")]
    if not extra_records:
        return [("-", "-", "-")]

    rows = []
    for record in extra_records:
        name = str(record.get("name", "-")).strip() or "-"
        if mode == "players":
            right = str(record.get("primary_position", "-")).strip() or "-"
        else:
            throw_hand = str(record.get("throws", "-")).strip().upper()
            right = f"{throw_hand}HP" if throw_hand in {"L", "R", "S"} else "-"
        note = str(record.get("notes", "-")).strip() or "-"
        rows.append((name, right, note))
    return rows


def draw_umpire_lineup_block(
    pdf,
    x,
    y_top,
    width,
    height,
    game_date,
    umpire,
    away_team_name,
    home_team_name,
    team_name,
    lineup_records,
    pitcher_record,
    extra_players_records,
    extra_pitchers_records,
    no_present_extra_players,
    no_present_extra_pitchers,
    copy_index,
    team_logo=None,
    mlb_logo=None,
):
    y_bottom = y_top - height
    header_h = 40
    section_header_h = 10
    gap_h = 3
    bottom_pad = 4
    signature_h = 16

    lineup_rows = len(lineup_records) + 1
    extra_players_rows = build_umpire_extra_rows(
        extra_players_records,
        no_present_extra_players,
        mode="players",
    )
    extra_pitchers_rows = build_umpire_extra_rows(
        extra_pitchers_records,
        no_present_extra_pitchers,
        mode="pitchers",
    )

    pdf.setStrokeColor(colors.HexColor("#1f2937"))
    pdf.setLineWidth(1)
    pdf.rect(x, y_bottom, width, height, stroke=1, fill=0)

    # Header block requested for umpire copies.
    date_text = game_date.strftime("%Y-%m-%d") if hasattr(game_date, "strftime") else str(game_date)
    pdf.setFillColor(colors.white)
    pdf.rect(x, y_top - header_h, width, header_h, stroke=0, fill=1)

    if team_logo:
        logo_w = 26
        logo_h = 26
        logo_x = x + 5
        logo_y = y_top - header_h + ((header_h - logo_h) / 2)
        pdf.drawImage(
            team_logo,
            logo_x,
            logo_y,
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )

    header_center_x = x + (width / 2)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 7.8)
    pdf.drawCentredString(header_center_x, y_top - 12, "Dominican Summer League")
    pdf.setFont("Helvetica-Bold", 7.2)
    pdf.drawCentredString(header_center_x, y_top - 20, "Official Batting Order")
    pdf.setFont("Helvetica", 7.0)
    pdf.drawCentredString(header_center_x, y_top - 28, f"Date: {date_text}")

    pdf.setFont("Helvetica-Bold", 6.9)
    pdf.drawRightString(x + width - 5, y_top - 12, f"COPY {copy_index}")
    pdf.setFont("Helvetica-Bold", 6.7)
    pdf.drawRightString(x + width - 5, y_top - 20, team_name[:22])

    content_top = y_top - header_h
    content_bottom = y_bottom + signature_h + bottom_pad
    content_h = max(40, content_top - content_bottom)

    # Give more vertical space to the starting lineup in umpire print.
    lineup_target_h = content_h * 0.68
    lineup_row_h = max(10, min(15, int((lineup_target_h - section_header_h) / max(1, lineup_rows))))
    lineup_rows_h = lineup_row_h * lineup_rows

    lineup_section_top = content_top
    lineup_header_bottom = lineup_section_top - section_header_h
    lineup_rows_top = lineup_header_bottom
    lineup_rows_bottom = lineup_rows_top - lineup_rows_h

    extras_total_top = lineup_rows_bottom - gap_h
    extras_total_bottom = content_bottom
    extras_total_h = max(36, extras_total_top - extras_total_bottom)
    extra_section_h = max(18, (extras_total_h - gap_h) / 2)

    players_section_top = extras_total_top
    players_header_bottom = players_section_top - section_header_h
    players_rows_top = players_header_bottom
    players_rows_bottom = players_section_top - extra_section_h

    pitchers_section_top = players_rows_bottom - gap_h
    pitchers_header_bottom = pitchers_section_top - section_header_h
    pitchers_rows_top = pitchers_header_bottom
    pitchers_rows_bottom = extras_total_bottom

    # MLB watermark in each extras box (players + pitchers), slightly larger.
    if mlb_logo:
        def draw_mlb_watermark(section_top, section_bottom):
            area_h = section_top - section_bottom
            if area_h <= 8:
                return
            wm_w = width * 0.64
            wm_h = area_h * 0.88
            wm_x = x + ((width - wm_w) / 2)
            wm_y = section_bottom + ((area_h - wm_h) / 2)
            pdf.saveState()
            if hasattr(pdf, "setFillAlpha"):
                pdf.setFillAlpha(0.09)
            pdf.drawImage(
                mlb_logo,
                wm_x,
                wm_y,
                width=wm_w,
                height=wm_h,
                preserveAspectRatio=True,
                mask="auto",
            )
            pdf.restoreState()

        draw_mlb_watermark(players_section_top, players_rows_bottom)
        draw_mlb_watermark(pitchers_section_top, pitchers_rows_bottom)

    # Optional top watermark for selected team
    if team_logo:
        top_area_h = lineup_rows_top - lineup_rows_bottom
        wm_w = width * 0.58
        wm_h = top_area_h * 0.82
        wm_x = x + ((width - wm_w) / 2)
        wm_y = lineup_rows_bottom + ((top_area_h - wm_h) / 2)
        pdf.saveState()
        if hasattr(pdf, "setFillAlpha"):
            pdf.setFillAlpha(0.06)
        pdf.drawImage(
            team_logo,
            wm_x,
            wm_y,
            width=wm_w,
            height=wm_h,
            preserveAspectRatio=True,
            mask="auto",
        )
        pdf.restoreState()

    # Starting lineup header
    pdf.setFillColor(colors.HexColor("#1e3a8a"))
    pdf.rect(x, lineup_header_bottom, width, section_header_h, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawString(x + 5, lineup_header_bottom + 3, "STARTING LINEUP")
    pdf.drawRightString(x + width - 4, lineup_header_bottom + 3, "NOTES")

    # Starting lineup table with NOTES column
    num_w = 18
    pos_w = 23
    notes_w = 58
    name_w = width - num_w - pos_w - notes_w
    x_num_end = x + num_w
    x_name_end = x_num_end + name_w
    x_pos_end = x_name_end + pos_w

    pdf.setStrokeColor(colors.HexColor("#1f2937"))
    pdf.setLineWidth(0.6)
    for x_line in [x_num_end, x_name_end, x_pos_end]:
        pdf.line(x_line, lineup_rows_top, x_line, lineup_rows_bottom)
    for row_index in range(lineup_rows + 1):
        y_line = lineup_rows_top - (row_index * lineup_row_h)
        pdf.line(x, y_line, x + width, y_line)

    lineup_rows_data = []
    for idx, player in enumerate(lineup_records, start=1):
        lineup_rows_data.append(
            (
                str(idx),
                str(player["name"]),
                str(player["primary_position"]),
                str(player.get("notes", "-")),
            )
        )
    lineup_rows_data.append(
        (
            "SP",
            str(pitcher_record["name"]),
            "SP",
            str(pitcher_record.get("notes", "-")),
        )
    )

    for idx, row_data in enumerate(lineup_rows_data):
        row_top = lineup_rows_top - (idx * lineup_row_h)
        y_text = row_top - lineup_row_h + 2
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 8.8)
        pdf.drawCentredString(x + (num_w / 2), y_text, row_data[0][:3])
        pdf.drawString(x_num_end + 3, y_text, row_data[1][:31])
        pdf.drawCentredString(x_name_end + (pos_w / 2), y_text, row_data[2][:4])
        pdf.setFont("Helvetica", 7.9)
        pdf.drawString(x_pos_end + 2, y_text, row_data[3][:11])

    def draw_extra_section(section_top, rows_top, rows_bottom, title, rows, first_number):
        pdf.setFillColor(colors.HexColor("#b91c1c"))
        pdf.rect(x, section_top - section_header_h, width, section_header_h, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 7.2)
        pdf.drawString(x + 5, section_top - section_header_h + 3, title)
        pdf.drawRightString(x + width - 4, section_top - section_header_h + 3, "NOTES")

        num_w2 = 18
        right_w2 = 24
        notes_w2 = 58
        name_w2 = width - num_w2 - right_w2 - notes_w2
        x_num_end2 = x + num_w2
        x_name_end2 = x_num_end2 + name_w2
        x_right_end2 = x_name_end2 + right_w2

        available_h = max(8, rows_top - rows_bottom)
        max_rows = max(1, len(rows))
        row_h2 = max(6, min(9, int(available_h / max_rows)))
        draw_rows = max(1, int(available_h / row_h2))
        rows_to_draw = rows[:draw_rows]

        pdf.setStrokeColor(colors.HexColor("#1f2937"))
        pdf.setLineWidth(0.55)
        for x_line in [x_num_end2, x_name_end2, x_right_end2]:
            pdf.line(x_line, rows_top, x_line, rows_bottom)

        for idx in range(draw_rows + 1):
            y_line = rows_top - (idx * row_h2)
            if y_line < rows_bottom:
                y_line = rows_bottom
            pdf.line(x, y_line, x + width, y_line)
            if y_line == rows_bottom:
                break

        for idx, row in enumerate(rows_to_draw):
            row_top = rows_top - (idx * row_h2)
            y_text = row_top - row_h2 + 2
            label_left = str(row[0])[:28]
            label_right = str(row[1])[:5]
            label_note = str(row[2])[:11]
            row_number = ""
            if label_left not in {"-", "NO PRESENTAR"}:
                row_number = str(first_number + idx)

            pdf.setFillColor(colors.black)
            pdf.setFont("Helvetica-Bold", 7.8)
            pdf.drawCentredString(x + (num_w2 / 2), y_text, row_number)
            pdf.drawString(x_num_end2 + 3, y_text, label_left)
            pdf.drawCentredString(x_name_end2 + (right_w2 / 2), y_text, label_right)
            pdf.setFont("Helvetica", 7.4)
            pdf.drawString(x_right_end2 + 2, y_text, label_note)

    extra_players_start = len(lineup_rows_data) + 1
    draw_extra_section(
        section_top=players_section_top,
        rows_top=players_rows_top,
        rows_bottom=players_rows_bottom,
        title="EXTRA PLAYERS",
        rows=extra_players_rows,
        first_number=extra_players_start,
    )

    extra_pitchers_start = extra_players_start + max(
        0,
        len([r for r in extra_players_rows if str(r[0]) not in {"-", "NO PRESENTAR"}]),
    )
    draw_extra_section(
        section_top=pitchers_section_top,
        rows_top=pitchers_rows_top,
        rows_bottom=pitchers_rows_bottom,
        title="EXTRA PITCHERS",
        rows=extra_pitchers_rows,
        first_number=extra_pitchers_start,
    )

    # Signatures at the bottom of each umpire lineup.
    sig_line_y = y_bottom + 10
    left_x1 = x + 8
    left_x2 = x + (width / 2) - 8
    right_x1 = x + (width / 2) + 8
    right_x2 = x + width - 8
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.6)
    pdf.line(left_x1, sig_line_y, left_x2, sig_line_y)
    pdf.line(right_x1, sig_line_y, right_x2, sig_line_y)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 6.7)
    pdf.drawString(left_x1, y_bottom + 2, "Manager Signature")
    pdf.drawString(right_x1, y_bottom + 2, "Umpire Signature")


def build_umpire_pdf(
    game_date,
    umpire,
    away_team_name,
    home_team_name,
    selected_team_name,
    selected_logo_url,
    selected_lineup_records,
    selected_pitcher_record,
    selected_extra_players,
    selected_extra_pitchers,
    selected_no_present_extra_players,
    selected_no_present_extra_pitchers,
):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(legal))
    width, height = landscape(legal)
    margin = 14
    gap = 8
    # 4 linear copies: one row, side by side.
    block_w = (width - (2 * margin) - (3 * gap)) / 4
    block_h = height - (2 * margin)

    # Border around full legal landscape page.
    outer_pad = 6
    inner_pad = 10
    pdf.saveState()
    pdf.setStrokeColor(colors.HexColor("#1e3a8a"))
    pdf.setLineWidth(1.4)
    pdf.rect(
        outer_pad,
        outer_pad,
        width - (2 * outer_pad),
        height - (2 * outer_pad),
        stroke=1,
        fill=0,
    )
    pdf.setStrokeColor(colors.HexColor("#b91c1c"))
    pdf.setLineWidth(1.0)
    pdf.rect(
        inner_pad,
        inner_pad,
        width - (2 * inner_pad),
        height - (2 * inner_pad),
        stroke=1,
        fill=0,
    )
    pdf.restoreState()

    selected_logo = load_logo_reader(selected_logo_url)
    mlb_logo = load_logo_reader(MLB_LOGO_FILE) or load_logo_reader(MLB_LOGO_URL)
    copy_index = 1
    block_top = height - margin
    for col in range(4):
        block_x = margin + (col * (block_w + gap))
        draw_umpire_lineup_block(
            pdf=pdf,
            x=block_x,
            y_top=block_top,
            width=block_w,
            height=block_h,
            game_date=game_date,
            umpire=umpire,
            away_team_name=away_team_name,
            home_team_name=home_team_name,
            team_name=selected_team_name,
            lineup_records=selected_lineup_records,
            pitcher_record=selected_pitcher_record,
            extra_players_records=selected_extra_players,
            extra_pitchers_records=selected_extra_pitchers,
            no_present_extra_players=selected_no_present_extra_players,
            no_present_extra_pitchers=selected_no_present_extra_pitchers,
            copy_index=copy_index,
            team_logo=selected_logo,
            mlb_logo=mlb_logo,
        )
        copy_index += 1

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def build_game_pdf(
    game_date,
    umpire,
    away_team_name,
    home_team_name,
    away_logo_url,
    home_logo_url,
    away_lineup_records,
    away_pitcher_record,
    away_extra_players,
    away_extra_pitchers,
    away_no_present_extra_players,
    away_no_present_extra_pitchers,
    home_lineup_records,
    home_pitcher_record,
    home_extra_players,
    home_extra_pitchers,
    home_no_present_extra_players,
    home_no_present_extra_pitchers,
):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 24

    # Full-page double border: outer blue line + inner red line.
    outer_pad = 6
    inner_pad = 11
    pdf.saveState()
    pdf.setStrokeColor(colors.HexColor("#1e3a8a"))
    pdf.setLineWidth(1.6)
    pdf.rect(
        outer_pad,
        outer_pad,
        width - (2 * outer_pad),
        height - (2 * outer_pad),
        stroke=1,
        fill=0,
    )
    pdf.setStrokeColor(colors.HexColor("#b91c1c"))
    pdf.setLineWidth(1.1)
    pdf.rect(
        inner_pad,
        inner_pad,
        width - (2 * inner_pad),
        height - (2 * inner_pad),
        stroke=1,
        fill=0,
    )
    pdf.restoreState()

    title_top = height - margin

    away_logo = load_logo_reader(away_logo_url)
    home_logo = load_logo_reader(home_logo_url)
    mlb_logo = load_logo_reader(MLB_LOGO_FILE) or load_logo_reader(MLB_LOGO_URL)
    dsl_logo = load_logo_reader(DSL_LOGO_FILE) or load_logo_reader(DSL_LOGO_URL)
    if mlb_logo:
        mlb_logo_w = 104
        mlb_logo_h = 64
        pdf.drawImage(
            mlb_logo,
            margin + 4,
            title_top - mlb_logo_h - 8,
            width=mlb_logo_w,
            height=mlb_logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )
    if dsl_logo:
        dsl_logo_w = 118
        dsl_logo_h = 64
        pdf.drawImage(
            dsl_logo,
            width - margin - dsl_logo_w - 4,
            title_top - dsl_logo_h - 8,
            width=dsl_logo_w,
            height=dsl_logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )

    pdf.setFillColor(colors.HexColor("#232323"))
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawCentredString(width / 2, title_top - 14, "GAME LINEUP CARD")
    away_code = str(away_team_name).split("|")[0].strip().upper() or "AWAY"
    home_code = str(home_team_name).split("|")[0].strip().upper() or "HOME"
    header_subtitle = (
        f"Boca Chica . Dominican Summer League . {away_code} vs {home_code}"
    )
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, title_top - 27, header_subtitle)

    info_y = title_top - 96
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin, info_y, f"Date: {game_date.strftime('%Y-%m-%d')}")
    umpire_text = umpire.strip() if str(umpire).strip() else "________________"
    pdf.drawString(margin + 160, info_y, f"Umpire: {umpire_text}")
    separator_y = info_y - 10
    pdf.line(margin, separator_y, width - margin, separator_y)

    table_top = separator_y - 18
    gap = 12
    table_width = (width - (2 * margin) - gap) / 2

    away_bottom = draw_pdf_team_block(
        pdf,
        x=margin,
        y_top=table_top,
        width=table_width,
        team_title=f"AWAY - {away_team_name}",
        lineup_records=away_lineup_records,
        pitcher_record=away_pitcher_record,
        accent_hex="#1e3a8a",
        team_logo=away_logo,
    )
    home_bottom = draw_pdf_team_block(
        pdf,
        x=margin + table_width + gap,
        y_top=table_top,
        width=table_width,
        team_title=f"HOME - {home_team_name}",
        lineup_records=home_lineup_records,
        pitcher_record=home_pitcher_record,
        accent_hex="#b91c1c",
        team_logo=home_logo,
    )

    extras_top = min(away_bottom, home_bottom) - 8
    away_player_lines = build_extra_lines(
        away_extra_players,
        away_no_present_extra_players,
        mode="players",
    )
    away_pitcher_lines = build_extra_lines(
        away_extra_pitchers,
        away_no_present_extra_pitchers,
        mode="pitchers",
    )
    home_player_lines = build_extra_lines(
        home_extra_players,
        home_no_present_extra_players,
        mode="players",
    )
    home_pitcher_lines = build_extra_lines(
        home_extra_pitchers,
        home_no_present_extra_pitchers,
        mode="pitchers",
    )

    away_players_bottom = draw_pdf_extra_block(
        pdf,
        x=margin,
        y_top=extras_top,
        width=table_width,
        title="AWAY EXTRA PLAYERS",
        lines=away_player_lines,
        accent_hex="#1e3a8a",
    )
    draw_pdf_extra_block(
        pdf,
        x=margin,
        y_top=away_players_bottom - 6,
        width=table_width,
        title="AWAY EXTRA PITCHERS",
        lines=away_pitcher_lines,
        accent_hex="#1e3a8a",
    )

    home_players_bottom = draw_pdf_extra_block(
        pdf,
        x=margin + table_width + gap,
        y_top=extras_top,
        width=table_width,
        title="HOME EXTRA PLAYERS",
        lines=home_player_lines,
        accent_hex="#b91c1c",
    )
    draw_pdf_extra_block(
        pdf,
        x=margin + table_width + gap,
        y_top=home_players_bottom - 6,
        width=table_width,
        title="HOME EXTRA PITCHERS",
        lines=home_pitcher_lines,
        accent_hex="#b91c1c",
    )

    draw_pdf_scoreboard(
        pdf,
        x=margin,
        y_bottom=margin + 18,
        width=width - (2 * margin),
        away_name=away_team_name,
        home_name=home_team_name,
        away_logo=away_logo,
        home_logo=home_logo,
    )

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def safe_filename(value):
    cleaned = "".join(ch if str(ch).isalnum() else "_" for ch in str(value))
    return cleaned.strip("_") or "team"


def get_official_form_fonts():
    global OFFICIAL_FORM_FONT_CACHE
    if OFFICIAL_FORM_FONT_CACHE:
        return OFFICIAL_FORM_FONT_CACHE

    normal_name = "Helvetica"
    bold_name = "Helvetica-Bold"
    font_candidates = [
        (
            os.path.join(BASE_DIR, "assets", "fonts", "Oswald-wght.ttf"),
            os.path.join(BASE_DIR, "assets", "fonts", "Oswald-wght.ttf"),
            "OswaldOfficial",
            "OswaldOfficial-Bold",
        ),
        (
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "ARIALN.TTF"),
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "ARIALNB.TTF"),
            "ArialNarrowForm",
            "ArialNarrowForm-Bold",
        ),
        (
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "bahnschrift.ttf"),
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "bahnschrift.ttf"),
            "BahnschriftForm",
            "BahnschriftForm-Bold",
        ),
    ]

    for normal_path, bold_path, candidate_normal, candidate_bold in font_candidates:
        if not (os.path.exists(normal_path) and os.path.exists(bold_path)):
            continue
        try:
            if candidate_normal not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(candidate_normal, normal_path))
            if candidate_bold not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(candidate_bold, bold_path))
            normal_name = candidate_normal
            bold_name = candidate_bold
            break
        except Exception:
            continue

    OFFICIAL_FORM_FONT_CACHE = (normal_name, bold_name)
    return OFFICIAL_FORM_FONT_CACHE


def truncate_text(value, max_chars):
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def official_card_team_name(team_name):
    raw = str(team_name).strip()
    if "|" in raw:
        _, right = raw.split("|", 1)
        if right.strip():
            return right.strip()
    return raw


def official_card_meta_line(game_date, venue_text):
    date_text = (
        game_date.strftime("%B %d, %Y").upper()
        if hasattr(game_date, "strftime")
        else str(game_date).upper()
    )
    venue = str(venue_text).strip().upper()
    if venue:
        return f"{venue} \u00b7 {date_text}"
    return date_text


def build_blank_official_team_payload(lineup_spots):
    blank_lineup = []
    for _ in range(int(lineup_spots)):
        blank_lineup.append(
            {
                "name": "",
                "primary_position": "",
                "bats": "",
                "throws": "",
                "notes": "",
            }
        )

    blank_pitcher = {
        "name": "",
        "primary_position": "",
        "bats": "",
        "throws": "",
        "notes": "",
    }
    return {
        "lineup": blank_lineup,
        "pitcher": blank_pitcher,
        "extra_players": [],
        "extra_pitchers": [],
        "no_present_extra_players": False,
        "no_present_extra_pitchers": False,
    }


def build_official_hand_groups(records, hand_key, columns):
    grouped = {column: [] for column in columns}
    for record in records or []:
        raw_hand = str(record.get(hand_key, "")).upper().strip()
        if hand_key == "throws":
            target_column = "LEFT-HANDED" if raw_hand == "L" else "RIGHT-HANDED"
        else:
            if raw_hand == "L":
                target_column = "LEFT-HANDED"
            elif raw_hand == "S":
                target_column = "SWITCH"
            else:
                target_column = "RIGHT-HANDED"
        if target_column in grouped:
            grouped[target_column].append(str(record.get("name", "")).strip())

    for column in grouped:
        grouped[column] = sorted([name for name in grouped[column] if name], key=str.upper)
    return grouped


def draw_centered_text(pdf, x_center, y, text, font_name, font_size, char_space=0):
    content = str(text)
    text_obj = pdf.beginText()
    text_obj.setFont(font_name, font_size)
    text_obj.setCharSpace(char_space)
    text_width = pdf.stringWidth(content, font_name, font_size)
    if char_space and len(content) > 1:
        text_width += char_space * (len(content) - 1)
    text_obj.setTextOrigin(x_center - (text_width / 2), y)
    text_obj.textLine(content)
    pdf.drawText(text_obj)


def draw_fitted_text(pdf, x, y, text, font_name, max_font_size, min_font_size, max_width):
    content = str(text)
    font_size = float(max_font_size)
    while font_size > float(min_font_size):
        if pdf.stringWidth(content, font_name, font_size) <= max_width:
            break
        font_size -= 0.2
    font_size = max(float(min_font_size), font_size)
    text_width = pdf.stringWidth(content, font_name, font_size)
    if text_width <= max_width:
        pdf.setFont(font_name, font_size)
        pdf.drawString(x, y, content)
        return

    text_obj = pdf.beginText()
    text_obj.setFont(font_name, font_size)
    text_obj.setTextOrigin(x, y)
    if text_width > 0:
        text_obj.setHorizScale((max_width / text_width) * 100)
    text_obj.textLine(content)
    pdf.drawText(text_obj)


def draw_centered_fitted_text(pdf, x_center, y, text, font_name, max_font_size, min_font_size, max_width):
    content = str(text)
    font_size = float(max_font_size)
    while font_size > float(min_font_size):
        if pdf.stringWidth(content, font_name, font_size) <= max_width:
            break
        font_size -= 0.2
    font_size = max(float(min_font_size), font_size)
    text_width = pdf.stringWidth(content, font_name, font_size)
    if text_width <= max_width:
        pdf.setFont(font_name, font_size)
        pdf.drawCentredString(x_center, y, content)
        return

    text_obj = pdf.beginText()
    text_obj.setFont(font_name, font_size)
    text_obj.setTextOrigin(x_center - (max_width / 2), y)
    if text_width > 0:
        text_obj.setHorizScale((max_width / text_width) * 100)
    text_obj.textLine(content)
    pdf.drawText(text_obj)


def draw_official_team_table(
    pdf,
    x,
    y_top,
    width,
    height,
    team_title,
    lineup_records,
    pitcher_record,
    team_logo=None,
    font_name="Helvetica",
    bold_font_name="Helvetica-Bold",
):
    title_gap = 20
    column_header_h = 18
    table_top = y_top - title_gap
    y_bottom = table_top - height
    rows = list(lineup_records) + [pitcher_record]
    total_rows = max(1, len(rows))
    pair_h = (height - column_header_h) / total_rows
    sub_row_h = pair_h / 2

    num_w = 22
    pos_w = 28
    change_w = 92
    original_w = width - num_w - pos_w - change_w

    x_num_end = x + num_w
    x_original_end = x_num_end + original_w
    x_pos_end = x_original_end + pos_w
    content_top = table_top - column_header_h

    pdf.setFillColor(colors.black)
    draw_centered_text(
        pdf,
        x + (width / 2),
        y_top - 14,
        truncate_text(team_title.upper(), 28),
        bold_font_name,
        18.2,
        char_space=0.15,
    )

    pdf.setStrokeColor(colors.HexColor("#202020"))
    pdf.setLineWidth(0.7)
    pdf.rect(x, y_bottom, width, height, stroke=1, fill=0)
    pdf.line(x, content_top, x + width, content_top)
    for x_line in [x_num_end, x_original_end, x_pos_end]:
        pdf.line(x_line, y_bottom, x_line, table_top)

    for row_index in range(total_rows + 1):
        y_line = content_top - (row_index * pair_h)
        pdf.line(x, y_line, x + width, y_line)
    for row_index in range(total_rows):
        y_line = content_top - (row_index * pair_h) - sub_row_h
        pdf.line(x_original_end, y_line, x + width, y_line)

    draw_centered_text(pdf, x_num_end + (original_w / 2), table_top - 13, "ORIGINAL", font_name, 8.8)
    draw_centered_text(pdf, x_original_end + (pos_w / 2), table_top - 13, "POS.", font_name, 8.8)
    draw_centered_text(pdf, x_pos_end + (change_w / 2), table_top - 13, "CHANGE", font_name, 8.8)

    if team_logo:
        logo_area_h = content_top - y_bottom
        wm_w = width * 0.72
        wm_h = logo_area_h * 0.7
        wm_x = x + ((width - wm_w) / 2)
        wm_y = y_bottom + ((logo_area_h - wm_h) / 2)
        pdf.saveState()
        if hasattr(pdf, "setFillAlpha"):
            pdf.setFillAlpha(0.12)
        pdf.drawImage(
            team_logo,
            wm_x,
            wm_y,
            width=wm_w,
            height=wm_h,
            preserveAspectRatio=True,
            mask="auto",
        )
        pdf.restoreState()

    for idx, row in enumerate(rows, start=1):
        row_top = content_top - ((idx - 1) * pair_h)
        top_baseline = row_top - sub_row_h + max(4, (sub_row_h * 0.18))
        lower_baseline = row_top - pair_h + max(4, (sub_row_h * 0.18))
        number_baseline = row_top - pair_h + max(6, (pair_h * 0.28))
        name_baseline = row_top - pair_h + max(7, (pair_h * 0.40))
        change_baseline = row_top - sub_row_h + max(5, (sub_row_h * 0.26))
        row_label = "P" if idx == len(rows) else str(idx)
        player_name = truncate_text(row.get("name", "").upper(), 25)
        player_pos = truncate_text(row.get("primary_position", "").upper(), 4)
        player_change = str(row.get("notes", "")).strip().upper()
        if idx == len(rows):
            row_color = colors.HexColor(get_pitcher_row_color_by_throw(row.get("throws", "")))
        else:
            row_color = colors.HexColor(get_bat_color(str(row.get("bats", "")).upper()))

        pdf.setFillColor(row_color)
        pdf.setFont(bold_font_name, 12.6)
        pdf.drawCentredString(x + (num_w / 2), number_baseline, row_label)
        draw_centered_fitted_text(
            pdf,
            x_num_end + (original_w / 2),
            name_baseline,
            player_name,
            font_name,
            max_font_size=18.2,
            min_font_size=11.8,
            max_width=original_w - 10,
        )
        pdf.drawCentredString(x_original_end + (pos_w / 2), top_baseline, player_pos)
        if player_change != "-":
            draw_fitted_text(
                pdf,
                x_pos_end + 3,
                change_baseline,
                player_change,
                font_name,
                max_font_size=8.8,
                min_font_size=4.8,
                max_width=change_w - 6,
            )


def draw_official_scoreboard(
    pdf,
    x,
    y_bottom,
    width,
    away_name,
    home_name,
    font_name="Helvetica",
    bold_font_name="Helvetica-Bold",
):
    row_h = 24
    num_rows = 3
    y_top = y_bottom + (num_rows * row_h)
    first_col = 148
    headers = [str(i) for i in range(1, 10)] + ["R", "H", "E"]
    small_w = (width - first_col) / len(headers)

    pdf.setStrokeColor(colors.HexColor("#202020"))
    pdf.setLineWidth(0.75)
    pdf.rect(x, y_bottom, width, num_rows * row_h, stroke=1, fill=0)

    for r in range(1, num_rows):
        y_line = y_bottom + (r * row_h)
        pdf.line(x, y_line, x + width, y_line)

    x_line = x + first_col
    pdf.line(x_line, y_bottom, x_line, y_top)
    for _ in range(len(headers) - 1):
        x_line += small_w
        pdf.line(x_line, y_bottom, x_line, y_top)

    pdf.setFillColor(colors.black)
    for idx, header in enumerate(headers):
        header_center_x = x + first_col + (idx * small_w) + (small_w / 2)
        draw_centered_text(pdf, header_center_x, y_top - row_h + 6, header, bold_font_name, 10.0)

    pdf.setFont(bold_font_name, 10.8)
    pdf.drawString(x + 10, y_top - (2 * row_h) + 6, truncate_text(str(away_name).upper(), 20))
    pdf.drawString(x + 10, y_top - (3 * row_h) + 6, truncate_text(str(home_name).upper(), 20))


def draw_official_hand_block(
    pdf,
    x,
    y_top,
    width,
    height,
    title,
    grouped_rows,
    min_rows,
    header_colors,
    font_name="Helvetica",
    bold_font_name="Helvetica-Bold",
):
    title_gap = 18
    column_header_h = 15
    box_top = y_top - title_gap
    y_bottom = box_top - height
    columns = list(grouped_rows.keys())
    col_w = width / max(1, len(columns))
    max_group_size = max([len(grouped_rows.get(col, [])) for col in columns] + [0])
    total_rows = max(min_rows, max_group_size if max_group_size > 0 else 0)
    row_h = (height - column_header_h) / max(1, total_rows)

    pdf.setFillColor(colors.black)
    draw_centered_text(pdf, x + (width / 2), y_top - 12, title, bold_font_name, 14.5, char_space=0.1)

    pdf.setStrokeColor(colors.HexColor("#202020"))
    pdf.setLineWidth(0.7)
    pdf.rect(x, y_bottom, width, height, stroke=1, fill=0)
    pdf.line(x, box_top - column_header_h, x + width, box_top - column_header_h)

    for idx in range(1, len(columns)):
        x_line = x + (idx * col_w)
        pdf.line(x_line, y_bottom, x_line, box_top)

    for row_index in range(total_rows + 1):
        y_line = box_top - column_header_h - (row_index * row_h)
        pdf.line(x, y_line, x + width, y_line)

    for idx, column in enumerate(columns):
        header_x = x + (idx * col_w) + (col_w / 2)
        pdf.setFillColor(colors.HexColor(header_colors.get(column, "#2f2f2f")))
        draw_centered_text(pdf, header_x, box_top - 12, column, font_name, 7.7)

        pdf.setFillColor(colors.black)
        values = grouped_rows.get(column, [])
        for row_index in range(total_rows):
            if row_index >= len(values):
                continue
            row_top = box_top - column_header_h - (row_index * row_h)
            baseline = row_top - row_h + max(4, (row_h * 0.2))
            pdf.setFont(font_name, 8.0)
            pdf.drawString(
                x + (idx * col_w) + 3,
                baseline,
                truncate_text(values[row_index].upper(), 16),
            )


def build_mlb_official_lineup_pdf(
    game_date,
    away_team_name,
    home_team_name,
    away_logo_url,
    home_logo_url,
    away_lineup_records,
    away_pitcher_record,
    away_extra_players,
    away_extra_pitchers,
    away_no_present_extra_players,
    away_no_present_extra_pitchers,
    home_lineup_records,
    home_pitcher_record,
    home_extra_players,
    home_extra_pitchers,
    home_no_present_extra_players,
    home_no_present_extra_pitchers,
    game_number="",
    venue_text="",
):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=legal)
    width, height = legal
    official_font, official_bold_font = get_official_form_fonts()
    outer_pad = 8
    inner_pad = 18
    margin = 22

    pdf.saveState()
    pdf.setStrokeColor(colors.HexColor("#d61d46"))
    pdf.setLineWidth(2.6)
    pdf.rect(
        outer_pad,
        outer_pad,
        width - (2 * outer_pad),
        height - (2 * outer_pad),
        stroke=1,
        fill=0,
    )
    pdf.setStrokeColor(colors.HexColor("#123f82"))
    pdf.setLineWidth(3.0)
    pdf.rect(
        inner_pad,
        inner_pad,
        width - (2 * inner_pad),
        height - (2 * inner_pad),
        stroke=1,
        fill=0,
    )
    pdf.restoreState()

    away_logo = load_logo_reader(away_logo_url)
    home_logo = load_logo_reader(home_logo_url)
    mlb_logo = load_logo_reader(MLB_LOGO_FILE) or load_logo_reader(MLB_LOGO_URL)

    if mlb_logo:
        logo_w = 108
        logo_h = 66
        pdf.drawImage(
            mlb_logo,
            (width - logo_w) / 2,
            height - 48,
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            mask="auto",
        )

    game_number_text = str(game_number).strip()
    if game_number_text:
        pdf.setFillColor(colors.HexColor("#303030"))
        pdf.setFont(official_font, 8.4)
        pdf.drawRightString(width - 44, height - 32, f"Game {game_number_text}")

    matchup_title = (
        f"{official_card_team_name(away_team_name)} AT "
        f"{official_card_team_name(home_team_name)}"
    ).upper()
    pdf.setFillColor(colors.HexColor("#1f1f1f"))
    draw_centered_text(
        pdf,
        width / 2,
        height - 74,
        truncate_text(matchup_title, 58),
        official_bold_font,
        22.8,
        char_space=0.04,
    )

    draw_centered_text(
        pdf,
        width / 2,
        height - 104,
        truncate_text(official_card_meta_line(game_date, venue_text), 76),
        official_font,
        11.6,
        char_space=0.02,
    )

    main_top = height - 134
    gap = 10
    team_width = (width - (2 * margin) - gap) / 2
    main_height = 438

    draw_official_team_table(
        pdf,
        x=margin,
        y_top=main_top,
        width=team_width,
        height=main_height,
        team_title=official_card_team_name(away_team_name),
        lineup_records=away_lineup_records,
        pitcher_record=away_pitcher_record,
        team_logo=away_logo,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )
    draw_official_team_table(
        pdf,
        x=margin + team_width + gap,
        y_top=main_top,
        width=team_width,
        height=main_height,
        team_title=official_card_team_name(home_team_name),
        lineup_records=home_lineup_records,
        pitcher_record=home_pitcher_record,
        team_logo=home_logo,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )

    position_colors = {
        "LEFT-HANDED": "#9a3412",
        "SWITCH": "#4c1d95",
        "RIGHT-HANDED": "#0f4c81",
    }
    pitcher_colors = {
        "LEFT-HANDED": "#9a3412",
        "RIGHT-HANDED": "#0f4c81",
    }

    away_position_groups = build_official_hand_groups(
        [] if away_no_present_extra_players else away_extra_players,
        hand_key="bats",
        columns=["LEFT-HANDED", "SWITCH", "RIGHT-HANDED"],
    )
    home_position_groups = build_official_hand_groups(
        [] if home_no_present_extra_players else home_extra_players,
        hand_key="bats",
        columns=["LEFT-HANDED", "SWITCH", "RIGHT-HANDED"],
    )
    away_pitcher_groups = build_official_hand_groups(
        [] if away_no_present_extra_pitchers else away_extra_pitchers,
        hand_key="throws",
        columns=["LEFT-HANDED", "RIGHT-HANDED"],
    )
    home_pitcher_groups = build_official_hand_groups(
        [] if home_no_present_extra_pitchers else home_extra_pitchers,
        hand_key="throws",
        columns=["LEFT-HANDED", "RIGHT-HANDED"],
    )

    lower_top = main_top - 30 - main_height
    players_h = 114
    pitchers_h = 92
    players_box_top = lower_top - 18
    pitchers_top = lower_top - 26 - players_h
    pitchers_box_top = pitchers_top - 18
    lower_boxes_bottom = pitchers_box_top - pitchers_h

    if mlb_logo:
        lower_area_h = players_box_top - lower_boxes_bottom
        wm_h = min(lower_area_h * 1.72, 365)
        wm_w = min(width * 1.08, 660)
        wm_x = (width - wm_w) / 2
        wm_y = lower_boxes_bottom + ((lower_area_h - wm_h) / 2)
        pdf.saveState()
        if hasattr(pdf, "setFillAlpha"):
            pdf.setFillAlpha(0.12)
        pdf.drawImage(
            mlb_logo,
            wm_x,
            wm_y,
            width=wm_w,
            height=wm_h,
            preserveAspectRatio=True,
            mask="auto",
        )
        pdf.restoreState()

    draw_official_hand_block(
        pdf,
        x=margin,
        y_top=lower_top,
        width=team_width,
        height=players_h,
        title="AVAILABLE POSITION PLAYERS",
        grouped_rows=away_position_groups,
        min_rows=5,
        header_colors=position_colors,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )
    draw_official_hand_block(
        pdf,
        x=margin + team_width + gap,
        y_top=lower_top,
        width=team_width,
        height=players_h,
        title="AVAILABLE POSITION PLAYERS",
        grouped_rows=home_position_groups,
        min_rows=5,
        header_colors=position_colors,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )

    draw_official_hand_block(
        pdf,
        x=margin,
        y_top=pitchers_top,
        width=team_width,
        height=pitchers_h,
        title="AVAILABLE PITCHERS",
        grouped_rows=away_pitcher_groups,
        min_rows=4,
        header_colors=pitcher_colors,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )
    draw_official_hand_block(
        pdf,
        x=margin + team_width + gap,
        y_top=pitchers_top,
        width=team_width,
        height=pitchers_h,
        title="AVAILABLE PITCHERS",
        grouped_rows=home_pitcher_groups,
        min_rows=4,
        header_colors=pitcher_colors,
        font_name=official_font,
        bold_font_name=official_bold_font,
    )

    scoreboard_y = 34
    draw_official_scoreboard(
        pdf,
        x=margin,
        y_bottom=scoreboard_y,
        width=width - (2 * margin),
        away_name=official_card_team_name(away_team_name),
        home_name=official_card_team_name(home_team_name),
        font_name=official_font,
        bold_font_name=official_bold_font,
    )

    signature_y = 18
    pdf.setStrokeColor(colors.HexColor("#2b2b2b"))
    pdf.setLineWidth(0.8)
    pdf.line(margin + 158, signature_y, width - margin - 104, signature_y)
    pdf.setFillColor(colors.black)
    pdf.setFont(official_font, 8.9)
    pdf.drawString(margin, signature_y + 4, "MANAGER SIGNATURE")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def lineup_editor(side_key, team_row, roster, lineup_spots):
    header_left, header_right = st.columns([4, 1])
    with header_left:
        st.markdown(f"#### {side_key}: {format_team_name(team_row)}")
    with header_right:
        logo_url = str(team_row.get("logo_url", "")).strip() if team_row.get("logo_url") else ""
        if logo_url:
            st.image(logo_url, width=72)

    if roster.empty:
        st.warning("This team has no players available.")
        return {"valid": False}

    roster = roster.copy()
    roster["id"] = roster["id"].astype(int)
    player_ids = roster["id"].tolist()
    player_map = {int(row["id"]): row for _, row in roster.iterrows()}

    st.caption("Roster")
    st.dataframe(
        roster_table_for_ui(roster),
        use_container_width=True,
        hide_index=True,
        height=320,
    )

    if len(player_ids) < lineup_spots:
        st.warning(
            f"Need at least {lineup_spots} players for batting spots. "
            f"This team has {len(player_ids)}."
        )
        return {"valid": False}

    def player_option(pid):
        p = player_map[int(pid)]
        return f"{p['name']} ({p['primary_position']} | B:{p['bats']} T:{p['throws']})"

    lineup_ids = []
    team_id = int(team_row["id"])
    hydrated_team_key = f"{side_key}_hydrated_team_id"
    if st.session_state.get(hydrated_team_key) != int(team_id):
        apply_saved_lineup_to_session(side_key, team_id, player_ids, lineup_spots)
        st.session_state[hydrated_team_key] = int(team_id)

    st.caption("Batting order (activa SUB o NOTE junto al jugador si aplica)")
    lineup_positions = []
    sub_ui_placeholders = {}
    note_ui_placeholders = {}
    for spot in range(1, lineup_spots + 1):
        key = f"{side_key}_{team_id}_spot_{spot}"
        current_value = st.session_state.get(key)
        available = [pid for pid in player_ids if pid not in lineup_ids or pid == current_value]

        if current_value in available:
            default_index = available.index(current_value)
        else:
            default_index = min(spot - 1, len(available) - 1)

        row_num_col, row_select_col, row_pos_col, row_note_col, row_sub_col = st.columns(
            [0.09, 0.51, 0.22, 0.09, 0.09], gap="small"
        )
        with row_num_col:
            st.markdown(f"**#{spot}**")
        with row_select_col:
            selected_id = st.selectbox(
                f"#{spot}",
                options=available,
                index=default_index,
                key=key,
                format_func=player_option,
                label_visibility="collapsed",
            )

        pos_key = f"{side_key}_{team_id}_pos_{spot}"
        pos_player_key = f"{side_key}_{team_id}_pos_player_{spot}"
        selected_player_default_pos = normalize_lineup_position_choice(
            player_map[int(selected_id)]["primary_position"]
        )

        if st.session_state.get(pos_player_key) != int(selected_id):
            st.session_state[pos_key] = selected_player_default_pos
            st.session_state[pos_player_key] = int(selected_id)

        current_pos = st.session_state.get(pos_key, selected_player_default_pos)
        if current_pos not in LINEUP_POSITION_OPTIONS:
            current_pos = selected_player_default_pos
        if current_pos not in LINEUP_POSITION_OPTIONS:
            current_pos = "DH"

        with row_pos_col:
            selected_pos = st.selectbox(
                f"Pos #{spot}",
                options=LINEUP_POSITION_OPTIONS,
                index=LINEUP_POSITION_OPTIONS.index(current_pos),
                key=pos_key,
                label_visibility="collapsed",
            )

        note_toggle_key = f"{side_key}_{team_id}_note_enabled_{spot}"
        with row_note_col:
            st.checkbox(
                f"NOTE #{spot}",
                key=note_toggle_key,
                label_visibility="collapsed",
                help=f"Activar nota manual para #{spot}",
            )

        toggle_key = f"{side_key}_{team_id}_sub_enabled_{spot}"
        with row_sub_col:
            st.checkbox(
                f"SUB #{spot}",
                key=toggle_key,
                label_visibility="collapsed",
                help=f"Activar sustitucion para #{spot}",
            )

        # Placeholders to render note/sub controls directly below this player row.
        note_ui_placeholders[spot] = st.empty()
        sub_ui_placeholders[spot] = st.empty()

        lineup_ids.append(int(selected_id))
        lineup_positions.append(str(selected_pos))

    duplicated_positions = sorted({p for p in lineup_positions if lineup_positions.count(p) > 1})
    if duplicated_positions:
        st.error(
            "Posiciones repetidas en el lineup: "
            + ", ".join(duplicated_positions)
            + ". Cada posicion debe ser unica."
        )

    pitcher_pool = roster[
        roster["primary_position"].str.upper().str.contains("P", na=False)
    ]["id"].astype(int).tolist()
    default_pitcher = pitcher_pool[0] if pitcher_pool else player_ids[0]

    pitcher_key = f"{side_key}_{team_id}_pitcher"
    current_pitcher = st.session_state.get(pitcher_key)
    if current_pitcher not in player_ids:
        current_pitcher = default_pitcher

    pitcher_id = st.selectbox(
        "Starting Pitcher",
        options=player_ids,
        index=player_ids.index(int(current_pitcher)),
        key=pitcher_key,
        format_func=player_option,
    )

    sub_pool = [pid for pid in player_ids if pid not in lineup_ids and pid != int(pitcher_id)]
    used_sub_ids = []
    lineup_notes = []
    manual_note_flags = []
    manual_note_texts = []
    substitution_valid = True
    substitutions_payload = []

    for idx, starter_id in enumerate(lineup_ids, start=1):
        starter_name = str(player_map[int(starter_id)]["name"])
        note_toggle_key = f"{side_key}_{team_id}_note_enabled_{idx}"
        note_enabled = bool(st.session_state.get(note_toggle_key, False))
        note_slot = note_ui_placeholders.get(idx)
        toggle_key = f"{side_key}_{team_id}_sub_enabled_{idx}"
        sub_enabled = bool(st.session_state.get(toggle_key, False))
        sub_slot = sub_ui_placeholders.get(idx)

        manual_note_text = ""
        if note_enabled:
            note_text_key = f"{side_key}_{team_id}_note_text_{idx}"
            if note_slot:
                with note_slot.container():
                    manual_note_text = st.text_input(
                        f"Nota #{idx}",
                        key=note_text_key,
                        value=str(st.session_state.get(note_text_key, "")),
                        placeholder="Escribe una nota para CHANGE",
                    ).strip()
            else:
                manual_note_text = str(st.session_state.get(note_text_key, "")).strip()
        elif note_slot:
            note_slot.empty()

        manual_note_flags.append(bool(note_enabled))
        manual_note_texts.append(manual_note_text)

        note_parts = []
        if manual_note_text:
            note_parts.append(manual_note_text)
        if sub_enabled:
            sub_player_key = f"{side_key}_{team_id}_sub_player_{idx}"
            current_sub_player = st.session_state.get(sub_player_key)
            available_subs = [
                pid for pid in sub_pool if pid not in used_sub_ids or pid == current_sub_player
            ]

            if not available_subs:
                if sub_slot:
                    with sub_slot.container():
                        st.error(
                            f"No hay jugadores disponibles para sustitucion en #{idx}. "
                            "Apaga una sustitucion previa o agrega mas jugadores al roster."
                        )
                substitution_valid = False
                note_parts.append("SUB PEND")
                lineup_notes.append(" | ".join([part for part in note_parts if part]) or "-")
                substitutions_payload.append(
                    {
                        "spot": int(idx),
                        "enabled": True,
                        "sub_player_id": None,
                        "inning": None,
                    }
                )
                continue

            if sub_slot:
                with sub_slot.container():
                    st.caption(f"#{idx} {starter_name} - Sustitucion")
                    sub_col1, sub_col2 = st.columns([0.72, 0.28], gap="small")
                    with sub_col1:
                        sub_player_id = st.selectbox(
                            f"Sustituto #{idx}",
                            options=available_subs,
                            index=0 if current_sub_player not in available_subs else available_subs.index(current_sub_player),
                            key=sub_player_key,
                            format_func=player_option,
                        )
                    inning_key = f"{side_key}_{team_id}_sub_inning_{idx}"
                    with sub_col2:
                        sub_inning = st.selectbox(
                            f"Inning #{idx}",
                            options=list(range(1, 12)),
                            key=inning_key,
                        )
            else:
                sub_player_id = available_subs[
                    0 if current_sub_player not in available_subs else available_subs.index(current_sub_player)
                ]
                inning_key = f"{side_key}_{team_id}_sub_inning_{idx}"
                sub_inning = st.session_state.get(inning_key, 1)

            used_sub_ids.append(int(sub_player_id))

            sub_name = str(player_map[int(sub_player_id)]["name"])
            note_parts.append(f"INN {sub_inning}: {sub_name[:12]}")
            substitutions_payload.append(
                {
                    "spot": int(idx),
                    "enabled": True,
                    "sub_player_id": int(sub_player_id),
                    "inning": int(sub_inning),
                }
            )
        elif sub_slot:
            sub_slot.empty()
            substitutions_payload.append(
                {
                    "spot": int(idx),
                    "enabled": False,
                    "sub_player_id": None,
                    "inning": None,
                }
            )

        note_text = " | ".join([part for part in note_parts if part]).strip()
        lineup_notes.append(note_text or "-")

    st.markdown("---")
    st.markdown("##### Extra Players")
    extra_player_candidates = [
        pid
        for pid in player_ids
        if (
            pid not in lineup_ids
            and pid != int(pitcher_id)
            and "P" not in str(player_map[int(pid)]["primary_position"]).upper()
        )
    ]
    max_extra_players = min(MAX_EXTRA_LIST_ENTRIES, len(extra_player_candidates))

    no_extra_players_key = f"{side_key}_{team_id}_no_extra_players"
    no_present_extra_players = st.checkbox(
        "No presentar Extra Players",
        value=(max_extra_players == 0),
        key=no_extra_players_key,
        disabled=(max_extra_players == 0),
    )

    extra_players_selected_ids = []
    extra_players_records = []
    if not no_present_extra_players:
        extra_players_count = st.number_input(
            "Cantidad Extra Players",
            min_value=0,
            max_value=max_extra_players,
            value=min(2, max_extra_players),
            step=1,
            key=f"{side_key}_{team_id}_extra_players_count",
        )

        for idx in range(1, int(extra_players_count) + 1):
            extra_key = f"{side_key}_{team_id}_extra_player_{idx}"
            current_extra = st.session_state.get(extra_key)
            available_extra = [
                pid
                for pid in extra_player_candidates
                if pid not in extra_players_selected_ids or pid == current_extra
            ]
            if not available_extra:
                break

            if current_extra in available_extra:
                default_index = available_extra.index(current_extra)
            else:
                default_index = min(idx - 1, len(available_extra) - 1)

            selected_extra = st.selectbox(
                f"Extra Player #{idx}",
                options=available_extra,
                index=default_index,
                key=extra_key,
                format_func=player_option,
            )
            extra_players_selected_ids.append(int(selected_extra))

        extra_players_records = []
        for player_id in extra_players_selected_ids:
            p = player_map[int(player_id)]
            extra_players_records.append(
                {
                    "name": str(p["name"]),
                    "primary_position": str(p["primary_position"]),
                    "bats": str(p["bats"]),
                    "throws": str(p["throws"]),
                }
            )

    st.markdown("---")
    st.markdown("##### Extra Pitchers")
    extra_pitcher_pool = roster[
        roster["primary_position"].str.upper().str.contains("P", na=False)
    ]["id"].astype(int).tolist()
    extra_pitcher_candidates = [
        pid
        for pid in extra_pitcher_pool
        if pid != int(pitcher_id) and pid not in lineup_ids
    ]
    max_extra_pitchers = min(MAX_EXTRA_LIST_ENTRIES, len(extra_pitcher_candidates))

    no_extra_pitchers_key = f"{side_key}_{team_id}_no_extra_pitchers"
    no_present_extra_pitchers = st.checkbox(
        "No presentar Extra Pitchers",
        value=(max_extra_pitchers == 0),
        key=no_extra_pitchers_key,
        disabled=(max_extra_pitchers == 0),
    )

    extra_pitchers_selected_ids = []
    extra_pitchers_records = []
    if not no_present_extra_pitchers:
        extra_pitchers_count = st.number_input(
            "Cantidad Extra Pitchers",
            min_value=0,
            max_value=max_extra_pitchers,
            value=min(2, max_extra_pitchers),
            step=1,
            key=f"{side_key}_{team_id}_extra_pitchers_count",
        )

        for idx in range(1, int(extra_pitchers_count) + 1):
            extra_pitcher_key = f"{side_key}_{team_id}_extra_pitcher_{idx}"
            current_extra_pitcher = st.session_state.get(extra_pitcher_key)
            available_extra_pitchers = [
                pid
                for pid in extra_pitcher_candidates
                if pid not in extra_pitchers_selected_ids or pid == current_extra_pitcher
            ]
            if not available_extra_pitchers:
                break

            if current_extra_pitcher in available_extra_pitchers:
                default_index = available_extra_pitchers.index(current_extra_pitcher)
            else:
                default_index = min(idx - 1, len(available_extra_pitchers) - 1)

            selected_extra_pitcher = st.selectbox(
                f"Extra Pitcher #{idx}",
                options=available_extra_pitchers,
                index=default_index,
                key=extra_pitcher_key,
                format_func=player_option,
            )
            extra_pitchers_selected_ids.append(int(selected_extra_pitcher))

        extra_pitchers_records = []
        for player_id in extra_pitchers_selected_ids:
            p = player_map[int(player_id)]
            extra_pitchers_records.append(
                {
                    "name": str(p["name"]),
                    "primary_position": str(p["primary_position"]),
                    "bats": str(p["bats"]),
                    "throws": str(p["throws"]),
                }
            )

    save_payload = {
        "lineup_ids": [int(pid) for pid in lineup_ids],
        "lineup_positions": [str(pos) for pos in lineup_positions],
        "pitcher_id": int(pitcher_id),
        "substitutions": substitutions_payload,
        "note_enabled": [bool(flag) for flag in manual_note_flags],
        "note_texts": [str(text) for text in manual_note_texts],
        "extra_player_ids": [int(pid) for pid in extra_players_selected_ids],
        "extra_pitcher_ids": [int(pid) for pid in extra_pitchers_selected_ids],
        "no_present_extra_players": bool(no_present_extra_players),
        "no_present_extra_pitchers": bool(no_present_extra_pitchers),
    }
    save_team_lineup(team_id=team_id, lineup_spots=int(lineup_spots), payload=save_payload)

    return {
        "valid": len(duplicated_positions) == 0 and substitution_valid,
        "team_id": int(team_id),
        "lineup_ids": lineup_ids,
        "lineup_positions": lineup_positions,
        "lineup_notes": lineup_notes,
        "pitcher_id": int(pitcher_id),
        "player_map": player_map,
        "team_name": format_team_name(team_row),
        "logo_url": str(team_row.get("logo_url", "")).strip() if team_row.get("logo_url") else "",
        "duplicated_positions": duplicated_positions,
        "extra_players": extra_players_records,
        "extra_pitchers": extra_pitchers_records,
        "no_present_extra_players": bool(no_present_extra_players),
        "no_present_extra_pitchers": bool(no_present_extra_pitchers),
    }


st.sidebar.markdown("## Lineup Manager")
menu = st.sidebar.radio(
    "Menu",
    ["Import CSV", "Import Logos", "View Teams", "Create Lineup"],
)


if menu == "Import CSV":
    st.markdown(
        """
        <div class="hero">
            <h1>Import Roster CSV</h1>
            <p>Load team data and save players directly into the database.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        df.columns = [str(c).strip() for c in df.columns]

        if df.empty:
            st.error("The uploaded CSV is empty.")
            st.stop()

        st.write("Preview")
        st.dataframe(df.head(), use_container_width=True)

        try:
            player_col = find_column(
                df,
                ["playerfullname", "player_name", "fullname", "player", "name"],
                "player name",
            )
            org_col = find_column(
                df,
                ["currentorg", "organization", "org"],
                "organization",
            )
            team_col = find_column(
                df,
                ["currentteamname", "teamname", "team"],
                "team",
            )
            pos_col = find_column(
                df,
                ["primaryposition", "position", "pos"],
                "position",
            )
            bats_col = find_column(
                df,
                ["batshand", "bats", "bat"],
                "bats",
            )
            throws_col = find_column(
                df,
                ["throwshand", "throws", "throw"],
                "throws",
            )
        except ValueError as err:
            st.error(str(err))
            st.stop()

        with st.expander("Detected columns", expanded=True):
            st.write(
                {
                    "player": player_col,
                    "organization": org_col,
                    "team": team_col,
                    "position": pos_col,
                    "bats": bats_col,
                    "throws": throws_col,
                }
            )

        records = []
        skipped_empty_name = 0
        skipped_missing_team_data = 0

        for _, row in df.iterrows():
            raw_name = row.get(player_col, "")
            raw_org = row.get(org_col, "")
            raw_team = row.get(team_col, "")

            if pd.isna(raw_name) or not str(raw_name).strip():
                skipped_empty_name += 1
                continue

            if (
                pd.isna(raw_org)
                or not str(raw_org).strip()
                or pd.isna(raw_team)
                or not str(raw_team).strip()
            ):
                skipped_missing_team_data += 1
                continue

            records.append(
                {
                    "name": str(raw_name).strip(),
                    "org": str(raw_org).strip(),
                    "team": str(raw_team).strip(),
                    "position": str(row.get(pos_col, "")).strip().upper() or "-",
                    "bats": normalize_hand(row.get(bats_col, "")),
                    "throws": normalize_hand(row.get(throws_col, "")),
                }
            )

        if not records:
            st.error("No valid rows found. Check player/team/org values in the CSV.")
            st.stop()

        summary_df = (
            pd.DataFrame(records)[["org", "team"]]
            .value_counts()
            .reset_index(name="Rows in CSV")
            .sort_values(["org", "team"])
            .reset_index(drop=True)
            .rename(columns={"org": "Organization", "team": "Team"})
        )
        st.caption("Teams detected in this CSV")
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

        replace_existing = st.checkbox(
            "Replace existing rosters for detected teams",
            value=False,
            help=(
                "Deletes current players for teams found in this CSV, then imports fresh data. "
                "Use this to fix previous multi-team imports."
            ),
        )

        if st.button("Import CSV into database", type="primary"):
            team_cache = {}
            inserted = 0
            duplicates = 0
            deleted_existing = 0
            per_team_inserted = {}

            if replace_existing:
                pairs = sorted({(r["org"], r["team"]) for r in records})
                for org_short, team_name in pairs:
                    org_id = get_or_create_org(org_short)
                    team_id = get_or_create_team(team_name, org_id)
                    team_cache[(org_short, team_name)] = team_id

                deleted_existing = len(team_cache)
                for team_id in set(team_cache.values()):
                    cursor.execute("DELETE FROM players WHERE team_id=?", (team_id,))
                conn.commit()

            for rec in records:
                key = (rec["org"], rec["team"])
                if key in team_cache:
                    team_id = team_cache[key]
                else:
                    org_id = get_or_create_org(rec["org"])
                    team_id = get_or_create_team(rec["team"], org_id)
                    team_cache[key] = team_id

                normalized = normalize_name(rec["name"])
                cursor.execute(
                    "SELECT id FROM players WHERE normalized_name=? AND team_id=?",
                    (normalized, team_id),
                )
                if cursor.fetchone():
                    duplicates += 1
                    continue

                cursor.execute(
                    """
                    INSERT INTO players
                    (name, normalized_name, team_id, primary_position, bats, throws)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec["name"],
                        normalized,
                        team_id,
                        rec["position"],
                        rec["bats"],
                        rec["throws"],
                    ),
                )
                inserted += 1
                per_team_inserted[key] = per_team_inserted.get(key, 0) + 1

            conn.commit()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Inserted", inserted)
            c2.metric("Duplicates skipped", duplicates)
            c3.metric("Empty names skipped", skipped_empty_name)
            c4.metric("Missing team/org skipped", skipped_missing_team_data)

            if replace_existing:
                st.success(
                    f"Rosters replaced for {deleted_existing} team(s), then imported from CSV."
                )

            if per_team_inserted:
                result_rows = []
                for (org_short, team_name), count in sorted(per_team_inserted.items()):
                    result_rows.append(
                        {
                            "Organization": org_short,
                            "Team": team_name,
                            "Players inserted": count,
                        }
                    )
                st.caption("Inserted per team")
                st.dataframe(
                    pd.DataFrame(result_rows),
                    use_container_width=True,
                    hide_index=True,
                )


if menu == "Import Logos":
    st.markdown(
        """
        <div class="hero">
            <h1>Import Logos</h1>
            <p>Assign a logo to a specific team so it appears in lineup exports and previews.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    teams_for_logo = get_teams()
    if not teams_for_logo.empty:
        teams_for_logo = teams_for_logo.copy()
        teams_for_logo["label"] = teams_for_logo.apply(team_label, axis=1)
        team_logo_options = teams_for_logo["id"].astype(int).tolist()
        team_logo_labels = {
            int(row["id"]): str(row["label"])
            for _, row in teams_for_logo.iterrows()
        }

        st.markdown("##### Assign Logo to One Team")
        with st.form("manual_team_logo_form", clear_on_submit=True):
            selected_team_for_logo = st.selectbox(
                "Team",
                options=team_logo_options,
                format_func=lambda team_id: team_logo_labels.get(int(team_id), str(team_id)),
            )
            manual_logo_url = st.text_input(
                "Logo URL or local asset path",
                placeholder="https://example.com/team-logo.png",
            )
            manual_logo_submit = st.form_submit_button(
                "Save Team Logo",
                type="primary",
                use_container_width=True,
            )
            if manual_logo_submit:
                if not str(manual_logo_url).strip():
                    st.error("Enter a logo URL or local file path first.")
                else:
                    set_team_logo(selected_team_for_logo, manual_logo_url)
                    selected_team_name = team_logo_labels.get(
                        int(selected_team_for_logo),
                        str(selected_team_for_logo),
                    )
                    st.success(f"Logo saved for team {selected_team_name}.")

        st.markdown("---")
    else:
        st.info("Import a roster first so you can assign logos to existing teams.")

    st.caption("CSV expected example: team, logo_url. Optional: org for more exact matching.")
    logo_csv = st.file_uploader("Upload Logo CSV", type=["csv"], key="org_logo_csv")

    if logo_csv:
        df_logo = pd.read_csv(logo_csv)
        df_logo.columns = [str(c).strip() for c in df_logo.columns]

        if df_logo.empty:
            st.error("The uploaded logo CSV is empty.")
            st.stop()

        st.write("Preview")
        st.dataframe(df_logo.head(), use_container_width=True)

        try:
            logo_col = find_column(
                df_logo,
                ["logo_url", "logo", "image_url", "image", "png", "url"],
                "logo url",
            )
        except ValueError as err:
            st.error(str(err))
            st.stop()

        org_col = find_optional_column(
            df_logo,
            ["org", "short_name", "organization", "currentorg"],
        )
        team_col = find_optional_column(
            df_logo,
            ["team", "team_name", "currentteamname", "teamname"],
        )
        if not team_col and not org_col:
            st.error("The logo CSV must include at least a team column or an organization column.")
            st.stop()

        st.caption("Detected columns")
        st.write({"organization": org_col, "team": team_col, "logo_url": logo_col})

        teams_catalog = get_teams()
        logos_by_team = {}
        logos_by_org = {}
        skipped_missing = 0
        skipped_unmatched = 0

        for _, row in df_logo.iterrows():
            raw_org = row.get(org_col, "") if org_col else ""
            raw_team = row.get(team_col, "") if team_col else ""
            raw_logo = row.get(logo_col, "")

            if pd.isna(raw_logo) or not str(raw_logo).strip():
                skipped_missing += 1
                continue

            logo_value = str(raw_logo).strip()
            org_value = "" if pd.isna(raw_org) else str(raw_org).strip()
            team_value = "" if pd.isna(raw_team) else str(raw_team).strip()

            if team_value:
                team_matches = teams_catalog[
                    teams_catalog["team"].astype(str).str.strip().eq(team_value)
                ]
                if org_value:
                    team_matches = team_matches[
                        team_matches["org"].astype(str).str.strip().eq(org_value)
                    ]

                if len(team_matches) == 1:
                    team_id = int(team_matches.iloc[0]["id"])
                    logos_by_team[team_id] = {
                        "org": str(team_matches.iloc[0]["org"]),
                        "team": str(team_matches.iloc[0]["team"]),
                        "logo_url": logo_value,
                    }
                    continue

                skipped_unmatched += 1
                continue

            if org_value:
                logos_by_org[org_value] = logo_value
                continue

            skipped_missing += 1

        if not logos_by_team and not logos_by_org:
            st.error("No valid team/logo rows found in CSV.")
            st.stop()

        detected_rows = []
        for _, payload in sorted(logos_by_team.items(), key=lambda item: (item[1]["org"], item[1]["team"])):
            detected_rows.append(
                {
                    "Target Type": "Team",
                    "Organization": payload["org"],
                    "Team": payload["team"],
                    "Logo URL": payload["logo_url"],
                }
            )
        for org_short, logo_url in sorted(logos_by_org.items()):
            detected_rows.append(
                {
                    "Target Type": "Organization",
                    "Organization": org_short,
                    "Team": "",
                    "Logo URL": logo_url,
                }
            )

        detected_rows = pd.DataFrame(detected_rows)
        st.caption("Logos to import")
        st.dataframe(detected_rows, use_container_width=True, hide_index=True)

        if st.button("Import logos", type="primary"):
            created_orgs = 0
            updated_org_logos = 0
            updated_team_logos = 0

            for team_id, payload in logos_by_team.items():
                set_team_logo(team_id, payload["logo_url"])
                updated_team_logos += 1

            for org_short, logo_url in logos_by_org.items():
                cursor.execute(
                    "SELECT id FROM organizations WHERE short_name=?",
                    (org_short,),
                )
                existed = cursor.fetchone() is not None
                set_org_logo(org_short, logo_url)
                if not existed:
                    created_orgs += 1
                updated_org_logos += 1

            st.success(
                f"Team logos imported: {updated_team_logos}. "
                f"Organization logos imported: {updated_org_logos}. "
                f"New organizations created: {created_orgs}. "
                f"Skipped missing rows: {skipped_missing}. "
                f"Skipped unmatched teams: {skipped_unmatched}."
            )

            logos_configured = pd.read_sql(
                """
                SELECT
                    o.short_name AS Organization,
                    t.name AS Team,
                    t.logo_url AS `Team Logo URL`,
                    o.logo_url AS `Organization Logo URL`,
                    COALESCE(NULLIF(TRIM(t.logo_url), ''), o.logo_url) AS `Logo In Use`
                FROM teams t
                JOIN organizations o ON o.id = t.organization_id
                WHERE COALESCE(NULLIF(TRIM(t.logo_url), ''), o.logo_url, '') <> ''
                ORDER BY o.short_name, t.name
                """,
                conn,
            )
            if not logos_configured.empty:
                st.caption("Configured logos by team")
                st.dataframe(logos_configured, use_container_width=True, hide_index=True)


if menu == "View Teams":
    st.markdown(
        """
        <div class="hero">
            <h1>Teams and Rosters</h1>
            <p>Review each team and its current roster.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    teams = get_teams()
    if teams.empty:
        st.warning("No teams available yet. Import a CSV first.")
        st.stop()

    total_players = int(teams["player_count"].sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Organizations", int(teams["org"].nunique()))
    c2.metric("Teams", int(len(teams)))
    c3.metric("Players", total_players)

    teams = teams.copy()
    teams["label"] = teams.apply(team_label, axis=1)
    team_options = teams["id"].astype(int).tolist()
    team_labels = {int(row["id"]): str(row["label"]) for _, row in teams.iterrows()}
    view_team_key = "view_teams_selected_team_id"
    view_team_picker_key = "view_teams_selected_team_picker"
    selected_team_state = parse_int(st.session_state.get(view_team_key))
    if selected_team_state is None or selected_team_state not in team_options:
        selected_team_state = int(team_options[0])
        st.session_state[view_team_key] = selected_team_state

    selected_picker_state = parse_int(st.session_state.get(view_team_picker_key))
    if selected_picker_state is None or selected_picker_state not in team_options:
        st.session_state[view_team_picker_key] = int(selected_team_state)

    selected_team_id = st.selectbox(
        "Select a team to view roster",
        options=team_options,
        key=view_team_picker_key,
        format_func=lambda team_id: team_labels.get(int(team_id), str(team_id)),
    )
    st.session_state[view_team_key] = int(selected_team_id)
    selected_team = teams.loc[teams["id"].astype(int) == int(selected_team_id)].iloc[0]

    roster = get_roster(int(selected_team["id"]))
    st.subheader(f"Roster: {selected_team['org']} | {selected_team['team']}")

    logo_url = str(selected_team.get("logo_url", "")).strip() if selected_team.get("logo_url") else ""
    if logo_url:
        st.image(logo_url, width=120)
    else:
        st.caption(f"No logo configured for team {selected_team['org']} | {selected_team['team']}.")

    team_id = int(selected_team["id"])
    st.markdown("##### Edit Roster")
    with st.form(key=f"add_player_form_{team_id}", clear_on_submit=True):
        add_col1, add_col2, add_col3, add_col4 = st.columns([0.42, 0.20, 0.19, 0.19], gap="small")
        with add_col1:
            new_player_name = st.text_input("Player Name")
        with add_col2:
            new_player_pos = st.selectbox(
                "Position",
                options=FIELD_POSITION_OPTIONS,
                index=FIELD_POSITION_OPTIONS.index("DH"),
            )
        with add_col3:
            new_player_bats = st.selectbox("Bats", options=["R", "L", "S"])
        with add_col4:
            new_player_throws = st.selectbox("Throws", options=["R", "L", "S"])

        add_submit = st.form_submit_button("Agregar jugador", type="primary", use_container_width=True)
        if add_submit:
            ok, message = add_player_to_team(
                team_id=team_id,
                name=new_player_name,
                primary_position=new_player_pos,
                bats=new_player_bats,
                throws=new_player_throws,
            )
            if ok:
                st.success(message)
                st.session_state[view_team_key] = int(team_id)
                st.rerun()
            else:
                st.error(message)

    if roster.empty:
        st.info("This team has no players yet.")
    else:
        st.markdown("##### Current Roster")
        roster_table = roster.rename(
            columns={
                "name": "Player",
                "primary_position": "Pos",
                "bats": "B",
                "throws": "T",
            }
        )[["Player", "Pos", "B", "T"]]
        st.dataframe(roster_table, use_container_width=True, hide_index=True)

        delete_ids = roster["id"].astype(int).tolist()
        delete_map = {
            int(row["id"]): f"{row['name']} ({row['primary_position']} | B:{row['bats']} T:{row['throws']})"
            for _, row in roster.iterrows()
        }
        delete_col1, delete_col2 = st.columns([0.74, 0.26], gap="small")
        with delete_col1:
            delete_player_id = st.selectbox(
                "Eliminar jugador del roster",
                options=delete_ids,
                key=f"delete_pick_{team_id}",
                format_func=lambda pid: delete_map.get(int(pid), str(pid)),
            )
        with delete_col2:
            st.write("")
            st.write("")
            if st.button("Eliminar", key=f"delete_player_btn_{team_id}", use_container_width=True):
                deleted = delete_player_from_team(team_id, int(delete_player_id))
                if deleted:
                    st.success(f"Jugador eliminado: {delete_map.get(int(delete_player_id), '')}")
                    st.session_state[view_team_key] = int(team_id)
                    st.rerun()
                else:
                    st.error("No se pudo eliminar el jugador.")

        st.markdown("##### Lineup Usage by Player")
        filter_col1, filter_col2, filter_col3 = st.columns([0.36, 0.32, 0.32], gap="small")
        with filter_col1:
            range_option = st.selectbox(
                "Range",
                options=["Last 7 days", "Last 15 days", "Last 30 days", "Custom range"],
                key=f"usage_range_{int(selected_team['id'])}",
            )

        today = datetime.today().date()
        if range_option == "Last 7 days":
            start_date = today - timedelta(days=6)
            end_date = today
            with filter_col2:
                st.caption(f"Start: {start_date.strftime('%Y-%m-%d')}")
            with filter_col3:
                st.caption(f"End: {end_date.strftime('%Y-%m-%d')}")
        elif range_option == "Last 15 days":
            start_date = today - timedelta(days=14)
            end_date = today
            with filter_col2:
                st.caption(f"Start: {start_date.strftime('%Y-%m-%d')}")
            with filter_col3:
                st.caption(f"End: {end_date.strftime('%Y-%m-%d')}")
        elif range_option == "Last 30 days":
            start_date = today - timedelta(days=29)
            end_date = today
            with filter_col2:
                st.caption(f"Start: {start_date.strftime('%Y-%m-%d')}")
            with filter_col3:
                st.caption(f"End: {end_date.strftime('%Y-%m-%d')}")
        else:
            default_start = today - timedelta(days=6)
            with filter_col2:
                start_date = st.date_input(
                    "Start Date",
                    value=default_start,
                    key=f"usage_start_{int(selected_team['id'])}",
                )
            with filter_col3:
                end_date = st.date_input(
                    "End Date",
                    value=today,
                    key=f"usage_end_{int(selected_team['id'])}",
                )
            if start_date > end_date:
                st.error("Start Date cannot be greater than End Date.")
                st.stop()

        usage_counts = get_lineup_usage_counts(int(selected_team["id"]), start_date, end_date)
        usage_table = roster[["id", "name", "primary_position"]].copy()
        usage_table = usage_table.merge(usage_counts, on="id", how="left")
        usage_table["games_selected"] = usage_table["games_selected"].fillna(0).astype(int)
        usage_table = usage_table.rename(
            columns={
                "name": "Player",
                "primary_position": "Pos",
                "games_selected": "Games Selected",
            }
        )[["Player", "Pos", "Games Selected"]]
        usage_table = usage_table.sort_values(
            by=["Games Selected", "Player"],
            ascending=[False, True],
        )
        st.dataframe(usage_table, use_container_width=True, hide_index=True)


if menu == "Create Lineup":
    st.markdown(
        """
        <div class="hero">
            <h1>Create Game Lineup</h1>
            <p>Select away and home teams, then export a printable PDF lineup card.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    teams = get_teams()
    teams = teams[teams["player_count"] > 0].copy()

    if teams.empty:
        st.warning("No teams with players found. Import at least one roster first.")
        st.stop()

    if len(teams) < 2:
        st.info(
            "Only one team with players is available. "
            "Home and Away can use the same roster until you import another team."
        )

    teams["label"] = teams.apply(team_label, axis=1)

    info_col1, info_col2, info_col3, info_col4 = st.columns([1, 1, 0.8, 1.8])
    with info_col1:
        game_date = st.date_input("Game Date", value=datetime.today().date(), format="YYYY-MM-DD")
    with info_col2:
        umpire = st.text_input("Umpire", value="")
    with info_col3:
        lineup_spots = st.selectbox("Batting Spots", options=[9, 10], index=1)
    with info_col4:
        official_game_number = st.text_input("Official Card: Game #", value="")

    official_venue = st.text_input(
        "Official Card: Venue / Location",
        value="",
        placeholder="Roger Dean Chevrolet Stadium - Jupiter, FL",
    )
    official_blank_col1, official_blank_col2 = st.columns(2, gap="large")
    with official_blank_col1:
        official_blank_away = st.checkbox(
            "Official Card: Leave Away blank",
            value=False,
            help="Use empty lineup boxes for the visitor/away team in the MLB official card.",
        )
    with official_blank_col2:
        official_blank_home = st.checkbox(
            "Official Card: Leave Home blank",
            value=False,
            help="Use empty lineup boxes for the home team in the MLB official card.",
        )

    team_col1, team_col2 = st.columns(2, gap="large")
    with team_col1:
        away_label = st.selectbox("Away Team", teams["label"], key="away_team_label")
    away_team = teams.loc[teams["label"] == away_label].iloc[0]

    if len(teams) == 1:
        home_options = teams.copy()
    else:
        home_options = teams[teams["id"] != int(away_team["id"])].copy()

    with team_col2:
        home_label = st.selectbox("Home Team", home_options["label"], key="home_team_label")
    home_team = home_options.loc[home_options["label"] == home_label].iloc[0]

    away_roster = get_roster(int(away_team["id"]))
    home_roster = get_roster(int(home_team["id"]))

    away_col, home_col = st.columns(2, gap="large")
    with away_col:
        away_state = lineup_editor("Away", away_team, away_roster, lineup_spots)
    with home_col:
        home_state = lineup_editor("Home", home_team, home_roster, lineup_spots)

    regular_exports_ready = bool(away_state.get("valid")) and bool(home_state.get("valid"))
    official_exports_ready = (
        (bool(away_state.get("valid")) or official_blank_away)
        and (bool(home_state.get("valid")) or official_blank_home)
    )

    if not regular_exports_ready and not official_exports_ready:
        st.info("Complete both lineups to enable PDF export, or leave a side blank for the official card.")
        st.stop()

    away_lineup = []
    away_pitcher_record = None
    if away_state.get("valid"):
        away_lineup = lineup_records_from_ids(
            away_state["lineup_ids"],
            away_state["lineup_positions"],
            away_state["lineup_notes"],
            away_state["player_map"],
        )
        away_pitcher = away_state["player_map"][away_state["pitcher_id"]]
        away_pitcher_record = {
            "name": str(away_pitcher["name"]),
            "primary_position": "SP",
            "bats": str(away_pitcher["bats"]),
            "throws": str(away_pitcher["throws"]),
            "notes": "-",
        }

    home_lineup = []
    home_pitcher_record = None
    if home_state.get("valid"):
        home_lineup = lineup_records_from_ids(
            home_state["lineup_ids"],
            home_state["lineup_positions"],
            home_state["lineup_notes"],
            home_state["player_map"],
        )
        home_pitcher = home_state["player_map"][home_state["pitcher_id"]]
        home_pitcher_record = {
            "name": str(home_pitcher["name"]),
            "primary_position": "SP",
            "bats": str(home_pitcher["bats"]),
            "throws": str(home_pitcher["throws"]),
            "notes": "-",
        }

    if regular_exports_ready:
        st.markdown("### Preview")
        preview_col1, preview_col2 = st.columns(2, gap="large")
        with preview_col1:
            st.markdown(f"**Away: {away_state['team_name']}**")
            st.dataframe(
                lineup_preview_df(away_state["team_name"], away_lineup, away_pitcher_record),
                use_container_width=True,
                hide_index=True,
            )
        with preview_col2:
            st.markdown(f"**Home: {home_state['team_name']}**")
            st.dataframe(
                lineup_preview_df(home_state["team_name"], home_lineup, home_pitcher_record),
                use_container_width=True,
                hide_index=True,
            )
    elif official_exports_ready:
        st.info("Official card blank-side mode enabled. Dugout and umpire PDFs stay disabled until both lineups are complete.")

    pdf_bytes = None
    umpire_pdf_bytes = None
    if regular_exports_ready:
        pdf_bytes = build_game_pdf(
            game_date=game_date,
            umpire=umpire,
            away_team_name=away_state["team_name"],
            home_team_name=home_state["team_name"],
            away_logo_url=away_state["logo_url"],
            home_logo_url=home_state["logo_url"],
            away_lineup_records=away_lineup,
            away_pitcher_record=away_pitcher_record,
            away_extra_players=away_state["extra_players"],
            away_extra_pitchers=away_state["extra_pitchers"],
            away_no_present_extra_players=away_state["no_present_extra_players"],
            away_no_present_extra_pitchers=away_state["no_present_extra_pitchers"],
            home_lineup_records=home_lineup,
            home_pitcher_record=home_pitcher_record,
            home_extra_players=home_state["extra_players"],
            home_extra_pitchers=home_state["extra_pitchers"],
            home_no_present_extra_players=home_state["no_present_extra_players"],
            home_no_present_extra_pitchers=home_state["no_present_extra_pitchers"],
        )

    dugout_filename = (
        f"lineup_{safe_filename(away_state['team_name'])}"
        f"_vs_{safe_filename(home_state['team_name'])}"
        f"_{game_date.strftime('%Y%m%d')}.pdf"
    )
    export_key = build_lineup_export_key(
        game_date=game_date.strftime("%Y-%m-%d"),
        away_team_id=int(away_team["id"]),
        home_team_id=int(home_team["id"]),
        away_state=away_state,
        home_state=home_state,
    )

    if regular_exports_ready:
        umpire_pick_options = [
            (
                "away",
                f"Away - {away_state['team_name']}",
                away_state["team_name"],
                away_state["logo_url"],
                away_lineup,
                away_pitcher_record,
                away_state["extra_players"],
                away_state["extra_pitchers"],
                away_state["no_present_extra_players"],
                away_state["no_present_extra_pitchers"],
            ),
            (
                "home",
                f"Home - {home_state['team_name']}",
                home_state["team_name"],
                home_state["logo_url"],
                home_lineup,
                home_pitcher_record,
                home_state["extra_players"],
                home_state["extra_pitchers"],
                home_state["no_present_extra_players"],
                home_state["no_present_extra_pitchers"],
            ),
        ]
        umpire_pick_labels = [opt[1] for opt in umpire_pick_options]
        umpire_pick_label = st.selectbox(
            "Umpire lineup source team",
            options=umpire_pick_labels,
            key="umpire_print_team",
        )
        umpire_selected = next(opt for opt in umpire_pick_options if opt[1] == umpire_pick_label)
        umpire_selected_team_name = umpire_selected[2]
        umpire_selected_logo_url = umpire_selected[3]
        umpire_selected_lineup = umpire_selected[4]
        umpire_selected_pitcher = umpire_selected[5]
        umpire_selected_extra_players = umpire_selected[6]
        umpire_selected_extra_pitchers = umpire_selected[7]
        umpire_selected_no_present_extra_players = umpire_selected[8]
        umpire_selected_no_present_extra_pitchers = umpire_selected[9]

        umpire_pdf_bytes = build_umpire_pdf(
            game_date=game_date,
            umpire=umpire,
            away_team_name=away_state["team_name"],
            home_team_name=home_state["team_name"],
            selected_team_name=umpire_selected_team_name,
            selected_logo_url=umpire_selected_logo_url,
            selected_lineup_records=umpire_selected_lineup,
            selected_pitcher_record=umpire_selected_pitcher,
            selected_extra_players=umpire_selected_extra_players,
            selected_extra_pitchers=umpire_selected_extra_pitchers,
            selected_no_present_extra_players=umpire_selected_no_present_extra_players,
            selected_no_present_extra_pitchers=umpire_selected_no_present_extra_pitchers,
        )

    away_official_payload = (
        build_blank_official_team_payload(lineup_spots)
        if official_blank_away
        else {
            "lineup": away_lineup,
            "pitcher": away_pitcher_record,
            "extra_players": away_state["extra_players"],
            "extra_pitchers": away_state["extra_pitchers"],
            "no_present_extra_players": away_state["no_present_extra_players"],
            "no_present_extra_pitchers": away_state["no_present_extra_pitchers"],
        }
    )
    home_official_payload = (
        build_blank_official_team_payload(lineup_spots)
        if official_blank_home
        else {
            "lineup": home_lineup,
            "pitcher": home_pitcher_record,
            "extra_players": home_state["extra_players"],
            "extra_pitchers": home_state["extra_pitchers"],
            "no_present_extra_players": home_state["no_present_extra_players"],
            "no_present_extra_pitchers": home_state["no_present_extra_pitchers"],
        }
    )
    official_pdf_bytes = build_mlb_official_lineup_pdf(
        game_date=game_date,
        away_team_name=away_state["team_name"],
        home_team_name=home_state["team_name"],
        away_logo_url=away_state["logo_url"],
        home_logo_url=home_state["logo_url"],
        away_lineup_records=away_official_payload["lineup"],
        away_pitcher_record=away_official_payload["pitcher"],
        away_extra_players=away_official_payload["extra_players"],
        away_extra_pitchers=away_official_payload["extra_pitchers"],
        away_no_present_extra_players=away_official_payload["no_present_extra_players"],
        away_no_present_extra_pitchers=away_official_payload["no_present_extra_pitchers"],
        home_lineup_records=home_official_payload["lineup"],
        home_pitcher_record=home_official_payload["pitcher"],
        home_extra_players=home_official_payload["extra_players"],
        home_extra_pitchers=home_official_payload["extra_pitchers"],
        home_no_present_extra_players=home_official_payload["no_present_extra_players"],
        home_no_present_extra_pitchers=home_official_payload["no_present_extra_pitchers"],
        game_number=official_game_number,
        venue_text=official_venue,
    )
    umpire_filename = None
    if regular_exports_ready:
        umpire_filename = (
            f"umpire_{safe_filename(umpire_selected_team_name)}"
            f"_{game_date.strftime('%Y%m%d')}.pdf"
        )
    official_filename = (
        f"mlb_official_{safe_filename(away_state['team_name'])}"
        f"_vs_{safe_filename(home_state['team_name'])}"
        f"_{game_date.strftime('%Y%m%d')}.pdf"
    )

    dl_col1, dl_col2, dl_col3 = st.columns(3, gap="large")
    with dl_col1:
        if regular_exports_ready:
            dugout_clicked = st.download_button(
                "Download Dugout Version Printable",
                data=pdf_bytes,
                file_name=dugout_filename,
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
        else:
            dugout_clicked = False
            st.button(
                "Download Dugout Version Printable",
                disabled=True,
                use_container_width=True,
            )
    with dl_col2:
        if regular_exports_ready:
            umpire_clicked = st.download_button(
                "Download Umpire Version Printable",
                data=umpire_pdf_bytes,
                file_name=umpire_filename,
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
        else:
            umpire_clicked = False
            st.button(
                "Download Umpire Version Printable",
                disabled=True,
                use_container_width=True,
            )
    with dl_col3:
        official_clicked = st.download_button(
            "Download MLB Official Lineup Card",
            data=official_pdf_bytes,
            file_name=official_filename,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

    if dugout_clicked or umpire_clicked or official_clicked:
        if away_state.get("valid") and not (official_clicked and not (dugout_clicked or umpire_clicked) and official_blank_away):
            log_lineup_player_selections(
                export_key=export_key,
                game_date=game_date.strftime("%Y-%m-%d"),
                team_id=int(away_state["team_id"]),
                lineup_ids=away_state["lineup_ids"],
            )
        if home_state.get("valid") and not (official_clicked and not (dugout_clicked or umpire_clicked) and official_blank_home):
            log_lineup_player_selections(
                export_key=export_key,
                game_date=game_date.strftime("%Y-%m-%d"),
                team_id=int(home_state["team_id"]),
                lineup_ids=home_state["lineup_ids"],
            )
