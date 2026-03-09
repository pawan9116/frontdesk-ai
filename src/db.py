"""SQLite database for practice reference data, bookings, and SMS logs."""

import json
import logging
import os
import sqlite3

logger = logging.getLogger("frontoffice-agent")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "agent.db")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    """Create tables and seed reference data if empty."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS providers (
            provider_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            locations TEXT NOT NULL  -- JSON array of location_ids
        );

        CREATE TABLE IF NOT EXISTS locations (
            location_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS procedure_codes (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            cash_price INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS insurance_rules (
            payer TEXT NOT NULL,
            plan TEXT NOT NULL,
            procedure_code TEXT NOT NULL,
            covered INTEGER NOT NULL,
            copay_estimate INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            PRIMARY KEY (payer, plan, procedure_code)
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            confirmation_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'booked',
            patient_first TEXT,
            patient_last TEXT,
            patient_phone TEXT,
            provider_id TEXT,
            location_id TEXT,
            appointment_type TEXT,
            slot_start TEXT,
            slot_end TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sms_log (
            message_id TEXT PRIMARY KEY,
            recipient TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

    """)
    conn.commit()

    # Seed reference data if tables are empty
    if conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0] == 0:
        _seed_reference_data(conn)

    logger.info("db_initialized path=%s", DB_PATH)


def _seed_reference_data(conn: sqlite3.Connection) -> None:
    """Insert default reference data."""
    practice = os.getenv("PRACTICE_NAME", "Smile Dental Care")

    conn.executemany(
        "INSERT INTO providers (provider_id, name, specialty, locations) VALUES (?, ?, ?, ?)",
        [
            ("PROV001", "Dr. Sarah Chen", "General Dentistry", json.dumps(["LOC001"])),
            ("PROV002", "Dr. James Wilson", "General Dentistry", json.dumps(["LOC001", "LOC002"])),
        ],
    )

    conn.executemany(
        "INSERT INTO locations (location_id, name, address) VALUES (?, ?, ?)",
        [
            ("LOC001", f"{practice} - San Jose", "123 Main St, San Jose, CA 95113"),
            ("LOC002", f"{practice} - Palo Alto", "456 University Ave, Palo Alto, CA 94301"),
        ],
    )

    conn.executemany(
        "INSERT INTO procedure_codes (code, name, cash_price) VALUES (?, ?, ?)",
        [
            ("D1110", "cleaning", 150),
            ("D0120", "exam", 95),
            ("D0210", "x-ray", 125),
            ("D2750", "crown", 1200),
        ],
    )

    conn.executemany(
        "INSERT INTO insurance_rules (payer, plan, procedure_code, covered, copay_estimate, notes) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("delta dental", "ppo", "D1110", 1, 25, "Covered under preventive care. One cleaning per 6 months."),
            ("delta dental", "ppo", "D0120", 1, 0, "Covered under preventive care."),
            ("delta dental", "ppo", "D0210", 1, 15, "Covered under diagnostic."),
            ("delta dental", "ppo", "D2750", 1, 350, "Major restorative. 50% coverage after deductible."),
            ("delta dental", "hmo", "D1110", 1, 10, "In-network only."),
            ("aetna", "ppo", "D1110", 1, 30, "Covered under preventive."),
            ("cigna", "dppo", "D1110", 0, 0, "Procedure not covered under this plan."),
        ],
    )

    conn.commit()
    logger.info("db_seeded with reference data")


# ── Reference data queries ───────────────────────────────────────────────────

def get_providers() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM providers").fetchall()
    return {
        r["provider_id"]: {"name": r["name"], "specialty": r["specialty"], "locations": json.loads(r["locations"])}
        for r in rows
    }


def get_locations() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM locations").fetchall()
    return {r["location_id"]: {"name": r["name"], "address": r["address"]} for r in rows}


def get_procedure_codes() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM procedure_codes").fetchall()
    return {r["code"]: {"name": r["name"], "cash_price": r["cash_price"]} for r in rows}


def get_appointment_type_to_code() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT code, name FROM procedure_codes").fetchall()
    return {r["name"]: r["code"] for r in rows}


def get_insurance_rule(payer: str, plan: str, procedure_code: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM insurance_rules WHERE payer = ? AND plan = ? AND procedure_code = ?",
        (payer.lower().strip(), plan.lower().strip(), procedure_code.upper().strip()),
    ).fetchone()
    if not row:
        return None
    return {"covered": bool(row["covered"]), "copay_estimate": row["copay_estimate"], "notes": row["notes"]}


# ── Bookings ─────────────────────────────────────────────────────────────────

def get_booking(id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


def save_booking(id: str, confirmation_id: str, patient_first: str, patient_last: str,
                 patient_phone: str, provider_id: str, location_id: str, appointment_type: str,
                 slot_start: str, slot_end: str, created_at: str) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO bookings
           (id, confirmation_id, status, patient_first, patient_last, patient_phone,
            provider_id, location_id, appointment_type, slot_start, slot_end, created_at)
           VALUES (?, ?, 'booked', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, confirmation_id, patient_first, patient_last, patient_phone,
         provider_id, location_id, appointment_type, slot_start, slot_end, created_at),
    )
    conn.commit()


# ── SMS log ──────────────────────────────────────────────────────────────────

def save_sms(message_id: str, recipient: str, message: str, created_at: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO sms_log (message_id, recipient, message, created_at) VALUES (?, ?, ?, ?)",
        (message_id, recipient, message, created_at),
    )
    conn.commit()


# ── Prompt helpers ───────────────────────────────────────────────────────────

def build_prompt_context() -> dict:
    """Build dynamic context for prompt template from DB."""
    locations = get_locations()
    providers = get_providers()
    procedures = get_procedure_codes()

    loc_lines = []
    for lid, loc in locations.items():
        loc_lines.append(f"- {lid}: {loc['name']} ({loc['address']})")

    prov_lines = []
    for pid, prov in providers.items():
        locs = ", ".join(locations[l]["name"].split(" - ")[-1] for l in prov["locations"] if l in locations)
        prov_lines.append(f"- {pid}: {prov['name']} ({prov['specialty']}) - {locs}")

    proc_lines = []
    for code, proc in procedures.items():
        proc_lines.append(f"- {proc['name']} = {code} (cash price: ${proc['cash_price']})")

    return {
        "locations_block": "\n".join(loc_lines),
        "providers_block": "\n".join(prov_lines),
        "procedures_block": "\n".join(proc_lines),
    }
