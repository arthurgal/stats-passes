from __future__ import annotations
from playwright.sync_api import sync_playwright
from datetime import datetime
import json


LEAGUES = {
    "Premier League": {"tournament_id": 17},
    "Brasileirão Série A": {"tournament_id": 325},
    "UEFA Champions League": {"tournament_id": 7},
}


def fetch_json(page, url: str):
    return page.evaluate(
        """
        async (u) => {
          const res = await fetch(u, {
            headers: {"accept":"application/json, text/plain, */*"},
            credentials: "include"
          });
          if (!res.ok) return { status: res.status };
          return await res.json();
        }
        """,
        url,
    )


def simplify_event(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "startTimestamp": e.get("startTimestamp"),
        "homeTeam": (e.get("homeTeam") or {"id": None, "name": None}),
        "awayTeam": (e.get("awayTeam") or {"id": None, "name": None}),
        "tournament": (e.get("tournament") or {"name": None}),
        "round": (e.get("roundInfo") or {}).get("round"),
        "status": (e.get("status") or {}).get("type"),
        "venue": (e.get("venue") or {}).get("city"),
    }


def main():
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.sofascore.com", timeout=60_000)

        for league, cfg in LEAGUES.items():
            tid = int(cfg["tournament_id"])
            url = f"https://www.sofascore.com/api/v1/unique-tournament/{tid}/featured-events"
            payload = fetch_json(page, url) or {}
            # payload shape: { featuredEvents: [ { events: [ ... ] }, ... ] }
            events = []
            fe = payload.get("featuredEvents") or []
            for block in fe:
                for e in (block or {}).get("events", []):
                    events.append(simplify_event(e))
            out[league] = {"tournament_id": tid, "events": events}

        browser.close()

    out["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open("featured-events.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved featured-events.json with leagues:", list(LEAGUES.keys()))


if __name__ == "__main__":
    main()
