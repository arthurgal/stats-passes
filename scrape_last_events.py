from __future__ import annotations
from playwright.sync_api import sync_playwright
import sys, json


def fetch_json_in_browser(url: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.sofascore.com", timeout=60_000)
        data = page.evaluate(
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
        browser.close()
    return data


def result_code_for_team(ev: dict, team_id: int) -> str | None:
    """Return 'W','D','L' from the perspective of team_id."""
    if not ev:
        return None
    hs = (ev.get("homeScore") or {}).get("current") if isinstance(ev.get("homeScore"), dict) else ev.get("homeScore")
    as_ = (ev.get("awayScore") or {}).get("current") if isinstance(ev.get("awayScore"), dict) else ev.get("awayScore")
    if hs is None or as_ is None:
        return None
    home_id = (ev.get("homeTeam") or {}).get("id")
    away_id = (ev.get("awayTeam") or {}).get("id")
    if team_id == home_id:
        return "W" if hs > as_ else ("D" if hs == as_ else "L")
    if team_id == away_id:
        return "W" if as_ > hs else ("D" if as_ == hs else "L")
    return None


def summarize_last_events(payload: dict) -> dict:
    events = payload.get("events") or payload.get("data") or []
    out = []
    for e in events:
        out.append(
            {
                "id": e.get("id"),
                "timestamp": e.get("startTimestamp"),
                "status": (e.get("status") or {}).get("type"),
                "tournament": (e.get("tournament") or {}).get("name"),
                "home": (e.get("homeTeam") or {}).get("name"),
                "away": (e.get("awayTeam") or {}).get("name"),
                "homeScore": (e.get("homeScore") or {}).get("current"),
                "awayScore": (e.get("awayScore") or {}).get("current"),
                "winnerCode": e.get("winnerCode"),
                "round": (e.get("roundInfo") or {}).get("round"),
                "venue": (e.get("venue") or {}).get("city"),
            }
        )
    return {"count": len(out), "events": out}


if __name__ == "__main__":
    team_id = sys.argv[1] if len(sys.argv) > 1 else "1957"
    url = f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0"
    data = fetch_json_in_browser(url)
    if not isinstance(data, dict):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        sys.exit(1)
    summary = summarize_last_events(data)
    # Compute form string for first N events (finished only)
    ev_full = (data.get("events") or data.get("data") or [])
    form = []
    for e in ev_full:
        if (e.get("status") or {}).get("type") != "finished":
            continue
        code = result_code_for_team(e, int(team_id))
        if code:
            form.append(code)
        if len(form) >= 5:
            break
    summary["form_last5"] = "".join(form)
    # Print only first 5 events to keep output short
    summary["events"] = summary["events"][:5]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
