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


def _walk(obj):
    """Yield dictionaries found anywhere in a nested JSON-like structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _walk(it)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _pick_team_value(item: dict, side: str, team_id: int, home_id: int | None, away_id: int | None):
    """Get numeric value for a stat item for the given team.

    Supports shapes with 'home'/'away' or team-scoped {'teamId','value'}.
    """
    if not isinstance(item, dict):
        return None
    # Common shape: { name, home, away }
    if side in ("home", "away") and side in item and isinstance(item.get(side), (int, float)):
        return item.get(side)
    # Team-specific shape: { name, teamId, value }
    t_id = item.get("teamId") or (item.get("team") or {}).get("id")
    if t_id is not None and int(t_id) == int(team_id):
        val = item.get("value") or item.get("statValue") or item.get("current")
        if isinstance(val, (int, float)):
            return val
    # Another possibility: { homeValue, awayValue }
    hv = item.get("homeValue")
    av = item.get("awayValue")
    if side == "home" and isinstance(hv, (int, float)):
        return hv
    if side == "away" and isinstance(av, (int, float)):
        return av
    # Some APIs use { comparator: '%', home/away as strings like '85%' }
    hv = item.get("home")
    av = item.get("away")
    val = hv if side == "home" else av
    if isinstance(val, str) and val.endswith("%"):
        try:
            return float(val.replace("%", "").strip())
        except Exception:
            return None
    return None


def _find_pass_stats_for_event(stats_payload: dict, ev: dict, team_id: int) -> dict:
    """Extract passes, accurate passes and accuracy % for team in event stats payload."""
    home_id = (ev.get("homeTeam") or {}).get("id")
    away_id = (ev.get("awayTeam") or {}).get("id")
    side = "home" if int(team_id) == int(home_id or -1) else "away"

    target_names = {
        "passes": {"passes"},
        "accurate": {"accurate passes", "accurate pass"},
        "accuracy_pct": {"accurate passes %", "accurate passes%", "pass success", "pass accuracy", "pass accuracy %"},
    }

    found = {"passes": None, "accurate": None, "accuracy_pct": None}
    # Walk through any nested lists/dicts and look for items that look like stat rows
    for item in _walk(stats_payload):
        if not isinstance(item, dict):
            continue
        name = _norm(item.get("name") or item.get("title") or item.get("label"))
        if not name:
            continue
        # Exact name matches
        for key, names in target_names.items():
            if found[key] is not None:
                continue
            if name in names:
                val = _pick_team_value(item, side, int(team_id), home_id, away_id)
                if isinstance(val, (int, float)):
                    found[key] = float(val)
        # Heuristics: if 'passes' but not 'key' or 'cross', prefer total passes
        if found["passes"] is None and name == "passes":
            val = _pick_team_value(item, side, int(team_id), home_id, away_id)
            if isinstance(val, (int, float)):
                found["passes"] = float(val)
        if found["accuracy_pct"] is None and ("accuracy" in name or name.endswith("%")) and "pass" in name:
            val = _pick_team_value(item, side, int(team_id), home_id, away_id)
            if isinstance(val, (int, float)):
                found["accuracy_pct"] = float(val)

    # Derive accuracy % if missing and we have counts
    if found["accuracy_pct"] is None and isinstance(found["passes"], (int, float)) and isinstance(found["accurate"], (int, float)) and found["passes"]:
        found["accuracy_pct"] = 100.0 * (found["accurate"] / found["passes"]) 

    return found


def compute_passes_averages(team_id: int, events: list[dict], limit: int = 5) -> dict:
    """Compute averages of passes metrics for last N finished events for the given team."""
    count_seen = 0
    sums = {"passes": 0.0, "accurate": 0.0, "accuracy_pct": 0.0}
    counts = {"passes": 0, "accurate": 0, "accuracy_pct": 0}

    for e in events:
        if (e.get("status") or {}).get("type") != "finished":
            continue
        event_id = e.get("id")
        if not event_id:
            continue
        stats_url = f"https://www.sofascore.com/api/v1/event/{event_id}/statistics"
        stats_payload = fetch_json_in_browser(stats_url)
        if not isinstance(stats_payload, dict):
            continue
        vals = _find_pass_stats_for_event(stats_payload, e, int(team_id))
        for k in ("passes", "accurate", "accuracy_pct"):
            v = vals.get(k)
            if isinstance(v, (int, float)):
                sums[k] += float(v)
                counts[k] += 1
        count_seen += 1
        if count_seen >= limit:
            break

    avgs = {}
    for k in ("passes", "accurate", "accuracy_pct"):
        avgs[f"avg_{k}"] = (sums[k] / counts[k]) if counts[k] > 0 else None

    return {
        "considered": min(count_seen, limit),
        "counts_per_metric": counts,
        **avgs,
    }


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
    # Compute average passes metrics for last 5 finished games
    try:
        passes_avg = compute_passes_averages(int(team_id), ev_full, limit=5)
    except Exception as exc:
        passes_avg = {"error": str(exc)}
    summary["passes_avg_last5"] = passes_avg
    # Print only first 5 events to keep output short
    summary["events"] = summary["events"][:5]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
