import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://testcisia.it/calendario.php?tolc=cents&l=it&lingua=inglese"
SNAPSHOT_FILE = Path("snapshot.json")
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

STATUS_VALUES = [
    "POSTI DISPONIBILI",
    "POSTI ESAURITI",
    "ISCRIZIONI CHIUSE",
    "ISCRIZIONI CONCLUSE",
]


def fetch_html():
    response = requests.get(
        URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.text


def extract_blocks(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    pattern = r"(CENT@(?:UNI|CASA).*?(?=CENT@(?:UNI|CASA)|$))"
    matches = re.findall(pattern, text)

    blocks = []
    for match in matches:
        row = " ".join(match.split())
        if len(row) > 20:
            blocks.append(row)

    return sorted(set(blocks))


def guess_city(text):
    tokens = text.split()
    if not tokens:
        return ""
    return tokens[-1]


def parse_block(block):
    dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", block)

    registration_deadline = dates[0] if len(dates) >= 1 else ""
    test_date = dates[-1] if len(dates) >= 2 else ""

    status = "UNKNOWN"
    for value in STATUS_VALUES:
        if value in block:
            status = value
            break

    seats = None
    seat_match = re.search(r"\b(\d+)\s+POSTI DISPONIBILI\b", block)
    if seat_match:
        seats = int(seat_match.group(1))

    mode = "CENT@UNI" if block.startswith("CENT@UNI") else "CENT@CASA"

    body = block
    body = body.replace("CENT@UNI", "", 1).strip()
    body = body.replace("CENT@CASA", "", 1).strip()

    for d in dates:
        body = body.replace(d, " ")

    for s in STATUS_VALUES:
        body = body.replace(s, " ")

    if seats is not None:
        body = re.sub(rf"\b{seats}\b", " ", body)

    body = re.sub(r"\s+", " ", body).strip()

    city = guess_city(body)

    return {
        "mode": mode,
        "city": city,
        "label": body,
        "registration_deadline": registration_deadline,
        "test_date": test_date,
        "status": status,
        "seats": seats,
        "raw": block,
    }


def make_key(record):
    return "|".join([
        record.get("mode", ""),
        record.get("city", ""),
        record.get("test_date", ""),
        record.get("registration_deadline", ""),
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
    old_map = {make_key(r): r for r in old_records}
    new_map = {make_key(r): r for r in new_records}

    new_entries = []
    removed_entries = []
    seat_changes = []
    status_changes = []

    for key in sorted(new_map.keys() - old_map.keys()):
        new_entries.append(new_map[key])

    for key in sorted(old_map.keys() - new_map.keys()):
        removed_entries.append(old_map[key])

    for key in sorted(new_map.keys() & old_map.keys()):
        old = old_map[key]
        new = new_map[key]

        if old.get("seats") != new.get("seats"):
            seat_changes.append({
                "city": new.get("city"),
                "date": new.get("test_date"),
                "old": old.get("seats"),
                "new": new.get("seats"),
            })

        if old.get("status") != new.get("status"):
            status_changes.append({
                "city": new.get("city"),
                "date": new.get("test_date"),
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
    parts = []
    if entry.get("city"):
        parts.append(entry["city"])
    if entry.get("test_date"):
        parts.append(entry["test_date"])
    if entry.get("status"):
        parts.append(entry["status"])
    if entry.get("seats") is not None:
        parts.append(f"seats: {entry['seats']}")
    return " | ".join(parts)


def build_message(changes, total_count):
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

    if not lines:
        return f"No changes detected.\nEntries checked: {total_count}"

    return "\n".join(lines)


def main():
    html = fetch_html()
    blocks = extract_blocks(html)
    new_records = [parse_block(block) for block in blocks]
    old_records = load_snapshot()

    if not old_records:
        save_snapshot(new_records)
        description = (
            f"First run completed.\n"
            f"Saved {len(new_records)} entries.\n"
            f"Next checks will report detailed changes."
        )
        send_discord_embed("CISIA check completed", description, 16711680)
        print(description)
        return

    changes = compare_records(old_records, new_records)
    message = build_message(changes, len(new_records))

    has_changes = any([
        changes["new_entries"],
        changes["removed_entries"],
        changes["seat_changes"],
        changes["status_changes"],
    ])

    if has_changes:
        send_discord_embed("CISIA update detected", message, 65280)
    else:
        send_discord_embed("CISIA check completed", message, 16711680)

    print(message)
    save_snapshot(new_records)


if __name__ == "__main__":
    main()
