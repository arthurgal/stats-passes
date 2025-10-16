# -*- coding: utf-8 -*-
"""
SofaScore Analyzer (versão final com fallback /standings/total)
- Evita erro "Não consegui obter times"
- Descobre times automaticamente (Premier + Brasileirão)
- Coleta overall/home/away + métricas derivadas
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import json, time, random

# =========================
# CONFIGURAÇÃO
# =========================
LEAGUES = {
    "Premier League": {
        "tournament_id": 17,
        "season_id": 76986,  # temporada atual
    },
    "Brasileirão Série A": {
        "tournament_id": 325,
        "season_id": 72034,  # temporada confirmada por você
    },
}

SLEEP_BETWEEN = (1.2, 2.2)

# =========================
# FUNÇÕES AUXILIARES
# =========================
def _sleep():
    time.sleep(random.uniform(*SLEEP_BETWEEN))


def fetch_json(page, url: str):
    """Executa fetch dentro do contexto do navegador (evita 403/CORS)."""
    js = f"""
        async () => {{
            const res = await fetch("{url}", {{
                headers: {{
                    "accept": "application/json, text/plain, */*",
                    "referer": "https://www.sofascore.com/",
                    "origin": "https://www.sofascore.com"
                }},
                credentials: "include"
            }});
            if (!res.ok) return {{ status: res.status }};
            return await res.json();
        }}
    """
    return page.evaluate(js)


def parse_teams_from_standings(payload: dict) -> dict[int, str]:
    """Extrai {team_id: team_name} de qualquer estrutura de standings."""
    teams = {}

    def try_rows(rows):
        if not isinstance(rows, list):
            return
        for row in rows:
            team = (row or {}).get("team") or (row or {}).get("participant") or {}
            tid = team.get("id")
            name = team.get("name") or team.get("shortName") or team.get("slug")
            if isinstance(tid, int) and tid not in teams:
                teams[tid] = name or str(tid)

    if not isinstance(payload, dict):
        return teams

    standings = payload.get("standings") or payload.get("tables") or []
    for table in standings:
        try_rows(table.get("rows"))
        try_rows(table.get("table"))
        for _, v in (table or {}).items():
            if isinstance(v, list):
                try_rows(v)

    return teams


def get_teams(page, tournament_id: int, season_id: int) -> dict[int, str]:
    """Tenta /standings/total → /standings para obter os times."""
    base = f"https://www.sofascore.com/api/v1/unique-tournament/{tournament_id}/season/{season_id}"

    # 1️⃣ tenta /standings/total
    url1 = f"{base}/standings/total"
    payload1 = fetch_json(page, url1)
    teams = parse_teams_from_standings(payload1 or {})
    if teams:
        print(f"✅ Times obtidos de /standings/total: {len(teams)}")
        return teams

    # 2️⃣ fallback simples
    url2 = f"{base}/standings"
    payload2 = fetch_json(page, url2)
    teams = parse_teams_from_standings(payload2 or {})
    if teams:
        print(f"✅ Times obtidos de /standings: {len(teams)}")
        return teams

    print(f"⚠️ Nenhum time encontrado via standings ({tournament_id}/{season_id})")
    return {}


def analyze_block(stats: dict) -> dict:
    """Extrai métricas básicas de um bloco statistics (overall/home/away)."""
    matches = stats.get("matches", 0) or 0
    possession = stats.get("averageBallPossession", 0.0) or 0.0
    total_passes = stats.get("totalPasses", 0) or 0
    accurate = stats.get("accuratePassesPercentage", 0.0) or 0.0
    ppg = (total_passes / matches) if matches else 0.0
    return {"posse": possession, "passes_jogo": ppg, "precisao": accurate}


def analyze_team(overall: dict, home: dict, away: dict) -> dict:
    """Calcula métricas derivadas e estilo de jogo."""
    o = analyze_block(overall or {})
    h = analyze_block(home or {})
    a = analyze_block(away or {})

    icj = (o["posse"] * o["precisao"]) / 100.0 if o["posse"] and o["precisao"] else 0.0
    ec = (o["passes_jogo"] / o["posse"]) if o["posse"] else 0.0
    const = abs((h["posse"] or 0.0) - (a["posse"] or 0.0))

    estilo = "Transição direta"
    if o["posse"] > 56 and o["precisao"] > 87:
        estilo = "Posse ofensiva"
    elif 50 <= o["posse"] <= 56 and o["precisao"] >= 85:
        estilo = "Posse neutra"
    elif o["posse"] < 50 and o["precisao"] >= 83:
        estilo = "Transição controlada"

    return {
        "posse_media": round(o["posse"], 2),
        "passes_por_jogo": round(o["passes_jogo"], 1),
        "precisao_passes": round(o["precisao"], 2),
        "icj": round(icj, 2),
        "ec": round(ec, 2),
        "estilo_jogo": estilo,
    }


# =========================
# MAIN
# =========================
def main():
    resultado_final = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.sofascore.com", timeout=60_000)
        time.sleep(2.5)

        for league_name, cfg in LEAGUES.items():
            tid, sid = cfg["tournament_id"], cfg["season_id"]
            print(f"\n📊 Coletando — {league_name} (tournament={tid}, season={sid})")

            teams = get_teams(page, tid, sid)
            if not teams:
                resultado_final[league_name] = {"erro": "sem standings"}
                continue

            league_rows = []
            total = len(teams)
            for i, (team_id, team_name) in enumerate(teams.items(), start=1):
                print(f"[{i}/{total}] {team_name} — coletando estatísticas...")
                base = f"https://www.sofascore.com/api/v1/team/{team_id}/unique-tournament/{tid}/season/{sid}/statistics"

                overall = fetch_json(page, f"{base}/overall")
                overall = overall.get("statistics") if isinstance(overall, dict) else None
                home = fetch_json(page, f"{base}/home")
                home = home.get("statistics") if isinstance(home, dict) else None
                away = fetch_json(page, f"{base}/away")
                away = away.get("statistics") if isinstance(away, dict) else None

                if not overall:
                    print(f"   ⚠️ {team_name}: sem dados ou bloqueado")
                    _sleep()
                    continue

                row = {"time": team_name}
                row.update(analyze_team(overall, home or {}, away or {}))
                league_rows.append(row)

                print(
                    f"   ✅ Posse {row['posse_media']:.1f}% | Passes {row['passes_por_jogo']:.1f} | "
                    f"Precisão {row['precisao_passes']:.1f}% | Estilo: {row['estilo_jogo']}"
                )
                _sleep()

            league_rows.sort(key=lambda r: (-r["icj"], -r["posse_media"]))
            resultado_final[league_name] = {
                "tournament_id": tid,
                "season_id": sid,
                "times": league_rows,
            }

        browser.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"sofascore_analysis_{ts}.json"
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, ensure_ascii=False, indent=2)

    print(f"\n✅ JSON salvo em: {file_name}")


if __name__ == "__main__":
    main()
