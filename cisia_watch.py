import json
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://testcisia.it/calendario.php?tolc=cents&l=it&lingua=inglese"
SNAPSHOT_FILE = Path("snapshot.json")
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]


def fetch_html():
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    return response.text


def extract_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    rows = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned.startswith("CENT@UNI") or cleaned.startswith("CENT@CASA"):
            rows.append(cleaned)

    return sorted(set(rows))


def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return []
    return json.loads(SNAPSHOT_FILE.read_text())


def save_snapshot(rows):
    SNAPSHOT_FILE.write_text(json.dumps(rows, indent=2))


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
    chunks = split_message(description)

    for i, chunk in enumerate(chunks):
        embed_title = title if i == 0 else f"{title} (cont.)"

        payload = {
            "embeds": [
                {
                    "title": embed_title,
                    "description": chunk,
                    "color": color,
                }
            ]
        }

        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()


def main():
    html = fetch_html()
    rows = extract_rows(html)
    old_rows = load_snapshot()

    added = sorted(set(rows) - set(old_rows))
    removed = sorted(set(old_rows) - set(rows))

    if not old_rows:
        description = (
            "First run completed.\n"
            f"Saved {len(rows)} current entries.\n"
            "From the next run onward, I will report changes."
        )
        send_discord_embed(
            title="CISIA check completed",
            description=description,
            color=16711680,  # red
        )
        print(description)
    elif added or removed:
        lines = []
        lines.append("Changes found.")

        if added:
            lines.append("")
            lines.append("New entries:")
            for item in added:
                lines.append(f"• {item}")

        if removed:
            lines.append("")
            lines.append("Removed entries:")
            for item in removed:
                lines.append(f"• {item}")

        description = "\n".join(lines)

        send_discord_embed(
            title="CISIA update detected",
            description=description,
            color=65280,  # green
        )
        print(description)
    else:
        description = (
            "No changes detected.\n"
            f"Entries checked: {len(rows)}"
        )
        send_discord_embed(
            title="CISIA check completed",
            description=description,
            color=16711680,  # red
        )
        print(description)

    save_snapshot(rows)


if __name__ == "__main__":
    main()
