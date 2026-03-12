import json
import os
import re
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
        if line.startswith("CENT@UNI") or line.startswith("CENT@CASA"):
            rows.append(" ".join(line.split()))

    return sorted(set(rows))


def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return []
    return json.loads(SNAPSHOT_FILE.read_text())


def save_snapshot(rows):
    SNAPSHOT_FILE.write_text(json.dumps(rows, indent=2))


def send_discord(message):
    requests.post(
        WEBHOOK_URL,
        json={"content": message[:2000]},
        timeout=30,
    )


def main():
    html = fetch_html()
    rows = extract_rows(html)

    old_rows = load_snapshot()

    added = list(set(rows) - set(old_rows))

    if added:
        msg = "🚨 CISIA update detected:\n\n" + "\n".join(added)
        print(msg)
        send_discord(msg)
    else:
        print("No changes")

    save_snapshot(rows)


if __name__ == "__main__":
    main()
