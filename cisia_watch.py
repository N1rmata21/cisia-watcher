import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://testcisia.it/calendario.php?tolc=cents&l=it&lingua=inglese"
SNAPSHOT_FILE = Path("snapshot.json")
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

RED = 16711680
GREEN = 65280


def fetch_html():
    response = requests.get(
        URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.text


def normalize_text(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line]


def parse_row(line):
    if not (line.startswith("CENT@UNI") or line.startswith("CENT@HOME")):
        return None

    dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", line)
    if len(dates) < 2:
        return None

    booking_deadline = dates[0]
    test_date = dates[-1]
    mode = "CENT@UNI" if line.startswith("CENT@UNI") else "CENT@HOME"

    status = None
    seats = None

    if "AVAILABLE SEATS" in line:
        status = "AVAILABLE SEATS"
        m = re.search(r"\b(\d+)\s+AVAILABLE SEATS\b", line)
        if m:
            seats = int(m.group(1))
    elif "NOT LONGER AVAILABLE" in line:
        status = "NOT LONGER AVAILABLE"
    elif "NOT BOOKABLE" in line:
        status = "NOT BOOKABLE"
    elif "BOOKINGS CLOSED" in line:
        status = "BOOKINGS CLOSED"
        m = re.search(r"\b(\d+)\s+BOOKINGS CLOSED\b", line)
        if m:
            seats = int(m.group(1))

    working = line
    working = working.replace(mode, "", 1).strip()
    working = working.replace(booking_deadline, "", 1)
    working = working.replace(test_date, "", 1)

    for phrase in ["AVAILABLE SEATS", "NOT LONGER AVAILABLE", "NOT BOOKABLE", "BOOKINGS CLOSED"]:
        working = working.replace(phrase, "")

    working = re.sub(r"~~", " ", working)
    working = re.sub(r"\b\d+\b", " ", working)
    working = re.sub(r"\s+", " ", working).strip()

    tokens = working.split()
    city = tokens[-1] if tokens else ""
    label = working

    return {
        "mode": mode,
        "label": label,
        "city": city,
        "booking_deadline": booking_deadline,
        "test_date": test_date,
        "status": status or "UNKNOWN",
        "seats": seats,
        "raw": line,
    }


def extract_records(html):
    lines = normalize_text(html)
    records = []

    for line in lines:
        row = parse_row(line)
        if row:
            records.append(row)

    deduped = []
    seen = set()
    for r in records:
        key = record_key(r)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return sorted(
        deduped,
        key=lambda r: (r["test_date"], r["city"], r["mode"], r["booking_deadline"])
    )


def record_key(record):
    return "|".join([
        record.get("mode", ""),
        record.get("city", ""),
        record.get("booking_deadline", ""),
        record.get("test_date", ""),
        record.get("label", ""),
    ])


def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return []
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def save_snapshot(records):
    SNAPSHOT_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def split_message(text, limit=3500):
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for line in text.splitlines():
        candidate = current + ("\n" if current else "") + line
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = line

    if current:
        parts.append(current)

    return parts


def send_discord_embed(title, description, color):
    for chunk in split_message(description):
        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": chunk,
                    "color": color
                }
            ]
        }
        response = requests.post(WEBHOOK_URL, json=payload, timeout=30)
        response.raise_for_status()


def compare_records(old_records, new_records):
    old_map = {record_key(r): r for r in old_records}
    new_map = {record_key(r): r for r in new_records}

    new_entries = []
    removed_entries = []
    seat_changes = []
    status_changes = []

    shared_keys = sorted(old_map.keys() & new_map.keys())

    for key in sorted(new_map.keys() - old_map.keys()):
        new_entries.append(new_map[key])

    for key in sorted(old_map.keys() - new_map.keys()):
        removed_entries.append(old_map[key])

    for key in shared_keys:
        old = old_map[key]
        new = new_map[key]

        if old.get("seats") != new.get("seats"):
            seat_changes.append({
                "city": new["city"],
                "date": new["test_date"],
                "old": old.get("seats"),
                "new": new.get("seats"),
                "status": new.get("status"),
            })

        if old.get("status") != new.get("status"):
            status_changes.append({
                "city": new["city"],
                "date": new["test_date"],
                "old": old.get("status"),
                "new": new.get("status"),
            })

    return {
        "new_entries": new_entries,
        "removed_entries": removed_entries,
        "seat_changes": seat_changes,
        "status_changes": status_changes,
    }


def format_entry(entry):
    parts = [entry["city"], entry["test_date"], entry["status"]]
    if entry.get("seats") is not None:
        parts.append(f"seats: {entry['seats']}")
    return " | ".join(parts)


def build_change_message(changes):
    lines = []

    if changes["new_entries"]:
        lines.append("New entries")
        for item in changes["new_entries"]:
            lines.append(f"- {format_entry(item)}")

    if changes["seat_changes"]:
        if lines:
            lines.append("")
        lines.append("Seat changes")
        for item in changes["seat_changes"]:
            lines.append(
                f"- {item['city']} | {item['date']} | {item['old']} -> {item['new']}"
            )

    if changes["status_changes"]:
        if lines:
            lines.append("")
        lines.append("Availability changes")
        for item in changes["status_changes"]:
            lines.append(
                f"- {item['city']} | {item['date']} | {item['old']} -> {item['new']}"
            )

    if changes["removed_entries"]:
        if lines:
            lines.append("")
        lines.append("Removed entries")
        for item in changes["removed_entries"]:
            lines.append(f"- {format_entry(item)}")

    return "\n".join(lines).strip()


def main():
    html = fetch_html()
    new_records = extract_records(html)
    old_records = load_snapshot()

    if not old_records:
        save_snapshot(new_records)
        msg = (
            f"First run completed.\n"
            f"Saved {len(new_records)} entries.\n"
            f"Next checks will report detailed changes."
        )
        send_discord_embed("CISIA check completed", msg, RED)
        print(msg)
        return

    changes = compare_records(old_records, new_records)
    change_message = build_change_message(changes)

    if change_message:
        send_discord_embed("CISIA update detected", change_message, GREEN)
        print(change_message)
    else:
        msg = f"No changes detected.\nEntries checked: {len(new_records)}"
        send_discord_embed("CISIA check completed", msg, RED)
        print(msg)

    save_snapshot(new_records)


if __name__ == "__main__":
    main()
