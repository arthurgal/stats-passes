# -*- coding: utf-8 -*-
"""
SofaScore Analyzer (versão final com fallback /standings/total)
- Evita erro "Não consegui obter times"
- Descobre times automaticamente (Premier + Brasileirão)
- Coleta overall/home/away + métricas derivadas
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import json, time, random, os

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


# =========================
# Estatísticas de passes por partida (para médias dos últimos jogos)
# =========================
def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _walk(it)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _pick_team_value(item: dict, side: str, team_id: int):
    if not isinstance(item, dict):
        return None
    if side in ("home", "away") and side in item and isinstance(item.get(side), (int, float)):
        return item.get(side)
    t_id = item.get("teamId") or (item.get("team") or {}).get("id")
    if t_id is not None and int(t_id) == int(team_id):
        val = item.get("value") or item.get("statValue") or item.get("current")
        if isinstance(val, (int, float)):
            return val
    hv = item.get("homeValue")
    av = item.get("awayValue")
    if side == "home" and isinstance(hv, (int, float)):
        return hv
    if side == "away" and isinstance(av, (int, float)):
        return av
    val = item.get("home") if side == "home" else item.get("away")
    if isinstance(val, str) and val.endswith("%"):
        try:
            return float(val.replace("%", "").strip())
        except Exception:
            return None
    return None


def _find_pass_stats_for_event(stats_payload: dict, ev: dict, team_id: int) -> dict:
    home_id = (ev.get("homeTeam") or {}).get("id")
    away_id = (ev.get("awayTeam") or {}).get("id")
    side = "home" if int(team_id) == int(home_id or -1) else "away"

    target_names = {
        "passes": {"passes"},
        "accurate": {"accurate passes", "accurate pass"},
        "accuracy_pct": {"accurate passes %", "accurate passes%", "pass success", "pass accuracy", "pass accuracy %"},
    }

    found = {"passes": None, "accurate": None, "accuracy_pct": None}
    for item in _walk(stats_payload):
        if not isinstance(item, dict):
            continue
        name = _norm(item.get("name") or item.get("title") or item.get("label"))
        if not name:
            continue
        for key, names in target_names.items():
            if found[key] is not None:
                continue
            if name in names:
                val = _pick_team_value(item, side, int(team_id))
                if isinstance(val, (int, float)):
                    found[key] = float(val)
        if found["passes"] is None and name == "passes":
            val = _pick_team_value(item, side, int(team_id))
            if isinstance(val, (int, float)):
                found["passes"] = float(val)
        if found["accuracy_pct"] is None and ("accuracy" in name or name.endswith("%")) and "pass" in name:
            val = _pick_team_value(item, side, int(team_id))
            if isinstance(val, (int, float)):
                found["accuracy_pct"] = float(val)

    if found["accuracy_pct"] is None and isinstance(found["passes"], (int, float)) and isinstance(found["accurate"], (int, float)) and found["passes"]:
        found["accuracy_pct"] = 100.0 * (found["accurate"] / found["passes"]) 

    return found


def compute_passes_averages(page, team_id: int, events: list[dict], limit: int = 5) -> dict:
    seen = 0
    sums = {"passes": 0.0, "accurate": 0.0, "accuracy_pct": 0.0}
    counts = {"passes": 0, "accurate": 0, "accuracy_pct": 0}

    # Use os to avoid name collision below
    for e in sorted((events or []), key=lambda x: x.get("startTimestamp") or 0, reverse=True):
        if (e.get("status") or {}).get("type") != "finished":
            continue
        event_id = e.get("id")
        if not event_id:
            continue
        stats_url = f"https://www.sofascore.com/api/v1/event/{event_id}/statistics"
        stats_payload = fetch_json(page, stats_url)
        vals = _find_pass_stats_for_event(stats_payload or {}, e, int(team_id))
        for k in ("passes", "accurate", "accuracy_pct"):
            v = vals.get(k)
            if isinstance(v, (int, float)):
                sums[k] += float(v)
                counts[k] += 1
        seen += 1
        _sleep()
        if seen >= limit:
            break

    avgs = {}
    for k in ("passes", "accurate", "accuracy_pct"):
        avgs[f"avg_{k}"] = (sums[k] / counts[k]) if counts[k] > 0 else None

    return {
        "considered": min(seen, limit),
        "counts_per_metric": counts,
        **avgs,
    }


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


def analyze_team(overall: dict) -> dict:
    """Calcula métricas derivadas e estilo de jogo."""
    o = analyze_block(overall or {})

    icj = (o["posse"] * o["precisao"]) / 100.0 if o["posse"] and o["precisao"] else 0.0
    ec = (o["passes_jogo"] / o["posse"]) if o["posse"] else 0.0


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
# �LTIMOS CONFRONTOS (forma)
# =========================
def _ev_score_pair(ev: dict):
    hs = (ev.get("homeScore") or {}).get("current") if isinstance(ev.get("homeScore"), dict) else ev.get("homeScore")
    as_ = (ev.get("awayScore") or {}).get("current") if isinstance(ev.get("awayScore"), dict) else ev.get("awayScore")
    return hs, as_


def _ev_result_for_team(ev: dict, team_id: int):
    hs, as_ = _ev_score_pair(ev)
    if hs is None or as_ is None:
        return None, None, None
    hid = (ev.get("homeTeam") or {}).get("id")
    aid = (ev.get("awayTeam") or {}).get("id")
    if team_id == hid:
        gf, ga = hs, as_
    elif team_id == aid:
        gf, ga = as_, hs
    else:
        return None, None, None
    if gf > ga:
        res = "W"
    elif gf == ga:
        res = "D"
    else:
        res = "L"
    return res, gf, ga


def aggregate_last_events(events: list, team_id: int, n: int = 5) -> dict:
    # Considera apenas jogos finalizados, ordenando do mais novo para o mais antigo
    evs = [e for e in events if ((e.get("status") or {}).get("type") == "finished")]
    evs.sort(key=lambda e: e.get("startTimestamp") or 0, reverse=True)
    evs = evs[:n]
    form = []
    w = d = l = 0
    gf = ga = 0
    for e in evs:
        res, g1, g2 = _ev_result_for_team(e, team_id)
        if not res:
            continue
        form.append(res)
        if res == "W":
            w += 1
        elif res == "D":
            d += 1
        else:
            l += 1
        gf += int(g1)
        ga += int(g2)
    count = len(form) if form else 0
    avg_gf = (gf / count) if count else 0.0
    avg_ga = (ga / count) if count else 0.0
    return {
        "form": "".join(form),
        "w": w,
        "d": d,
        "l": l,
        "gf": gf,
        "ga": ga,
        "avg_gf": round(avg_gf, 2),
        "avg_ga": round(avg_ga, 2),
        "count": count,
    }


def get_last_events_summary(page, team_id: int) -> dict:
    url = f"https://www.sofascore.com/api/v1/team/{team_id}/events/last/0"
    payload = fetch_json(page, url) or {}
    events = (payload.get("events") or payload.get("data") or [])
    last5 = aggregate_last_events(events, team_id, 5)
    last10 = aggregate_last_events(events, team_id, 10)
    # Passes médios últimos 5 (buscando estatísticas por partida)
    try:
        passes_avg5 = compute_passes_averages(page, int(team_id), events, limit=5)
    except Exception:
        passes_avg5 = {}
    return {"events": events, "last5": last5, "last10": last10, "passes_avg_last5": passes_avg5}


# =========================
# PERSISTÊNCIA EM BANCO (MySQL)
# =========================
MYSQL_DDL = [
    """
    CREATE TABLE IF NOT EXISTS run (
      run_id BIGINT AUTO_INCREMENT PRIMARY KEY,
      created_at DATETIME NOT NULL,
      source_file VARCHAR(255) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS league (
      tournament_id BIGINT PRIMARY KEY,
      name VARCHAR(100) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS season (
      season_id BIGINT PRIMARY KEY,
      tournament_id BIGINT NOT NULL,
      name VARCHAR(50) NULL,
      CONSTRAINT fk_season_league
        FOREIGN KEY (tournament_id) REFERENCES league (tournament_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS team (
      team_id BIGINT PRIMARY KEY,
      name VARCHAR(120) NOT NULL,
      KEY idx_team_name (name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS run_league (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_id BIGINT NOT NULL,
      tournament_id BIGINT NOT NULL,
      season_id BIGINT NOT NULL,
      league_name VARCHAR(100) NOT NULL,
      status ENUM('ok','erro') NOT NULL DEFAULT 'ok',
      error_msg VARCHAR(255) NULL,
      CONSTRAINT fk_runleague_run
        FOREIGN KEY (run_id) REFERENCES run(run_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
      CONSTRAINT fk_runleague_league
        FOREIGN KEY (tournament_id) REFERENCES league(tournament_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
      CONSTRAINT fk_runleague_season
        FOREIGN KEY (season_id) REFERENCES season(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
      UNIQUE KEY uq_runleague (run_id, tournament_id, season_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS team_metrics (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_id BIGINT NOT NULL,
      tournament_id BIGINT NOT NULL,
      season_id BIGINT NOT NULL,
      league_name VARCHAR(100) NOT NULL,
      team_id BIGINT NOT NULL,
      team_name VARCHAR(120) NOT NULL,
      posse_media DOUBLE NOT NULL,
      passes_por_jogo DOUBLE NOT NULL,
      precisao_passes DOUBLE NOT NULL,
      icj DOUBLE NOT NULL,
      ec DOUBLE NOT NULL,
      estilo_jogo VARCHAR(50) NOT NULL,
      rank_icj INT NULL,
      CONSTRAINT fk_metrics_run
        FOREIGN KEY (run_id) REFERENCES run(run_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
      CONSTRAINT fk_metrics_league
        FOREIGN KEY (tournament_id) REFERENCES league(tournament_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
      CONSTRAINT fk_metrics_season
        FOREIGN KEY (season_id) REFERENCES season(season_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
      CONSTRAINT fk_metrics_team
        FOREIGN KEY (team_id) REFERENCES team(team_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
      UNIQUE KEY uq_metrics (run_id, tournament_id, season_id, team_id),
      KEY idx_metrics_lookup (tournament_id, season_id, team_id),
      KEY idx_metrics_icj (tournament_id, season_id, icj),
      KEY idx_metrics_teamname (tournament_id, season_id, team_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
]


def _mysql_connect_from_env():
    """Abre conexão MySQL.
    Prioriza variáveis de ambiente; se ausentes, usa fallback padrão local:
      host=localhost, port=3306, user=root, password=12345678, database=apostar_stats
    Também cria o database se não existir (erro 1049).
    """
    try:
        import pymysql  # type: ignore
    except Exception:
        print("[DB] PyMySQL não encontrado. Instale com: pip install pymysql")
        raise

    host = os.getenv("DB_HOST", "127.0.0.1")
    user = os.getenv("DB_USER", "apostar")
    password = os.getenv("DB_PASS", "12345678")
    database = os.getenv("DB_NAME", "apostar_stats")
    port = int(os.getenv("DB_PORT", "3306"))

    try:
        return pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as e:
        try:
            # Unknown database 'X'
            from pymysql.err import OperationalError  # type: ignore
        except Exception:
            raise
        if isinstance(e, OperationalError) and getattr(e, 'args', [None])[0] == 1049:
            # Conecta sem DB, cria o schema e reconecta
            tmp = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                charset="utf8mb4",
                autocommit=True,
            )
            try:
                with tmp.cursor() as cur:
                    cur.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
            finally:
                tmp.close()
            return pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
            )
        raise


def _mysql_ensure_schema(conn):
    with conn.cursor() as cur:
        for stmt in MYSQL_DDL:
            cur.execute(stmt)
    conn.commit()


def _mysql_save_run(conn, created_at_dt, source_file):
    created_at_str = created_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run (created_at, source_file) VALUES (%s, %s)",
            (created_at_str, source_file),
        )
        run_id = cur.lastrowid
    conn.commit()
    return run_id


def _mysql_upsert_league_and_season(conn, tournament_id, season_id, league_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO league (tournament_id, name)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE name=VALUES(name)
            """,
            (tournament_id, league_name),
        )
        cur.execute(
            """
            INSERT INTO season (season_id, tournament_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE tournament_id=VALUES(tournament_id)
            """,
            (season_id, tournament_id),
        )
    conn.commit()


def _mysql_insert_run_league(conn, run_id, tournament_id, season_id, league_name, status, error_msg):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO run_league (run_id, tournament_id, season_id, league_name, status, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE status=VALUES(status), error_msg=VALUES(error_msg), league_name=VALUES(league_name)
            """,
            (run_id, tournament_id, season_id, league_name, status, error_msg),
        )
    conn.commit()


def _mysql_upsert_team(conn, team_id, team_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team (team_id, name)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE name=VALUES(name)
            """,
            (team_id, team_name),
        )


def _mysql_insert_team_metrics(conn, run_id, tournament_id, season_id, league_name, row, rank_icj):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_metrics (
              run_id, tournament_id, season_id, league_name,
              team_id, team_name, posse_media, passes_por_jogo, precisao_passes, icj, ec, estilo_jogo, rank_icj
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              team_name=VALUES(team_name),
              posse_media=VALUES(posse_media),
              passes_por_jogo=VALUES(passes_por_jogo),
              precisao_passes=VALUES(precisao_passes),
              icj=VALUES(icj),
              ec=VALUES(ec),
              estilo_jogo=VALUES(estilo_jogo),
              rank_icj=VALUES(rank_icj)
            """,
            (
                run_id,
                tournament_id,
                season_id,
                league_name,
                int(row.get("team_id")),
                str(row.get("time")),
                float(row.get("posse_media")),
                float(row.get("passes_por_jogo")),
                float(row.get("precisao_passes")),
                float(row.get("icj")),
                float(row.get("ec")),
                str(row.get("estilo_jogo")),
                int(rank_icj),
            ),
        )


def save_to_mysql(resultado_final, ts, source_file):
    conn = _mysql_connect_from_env()
    try:
        _mysql_ensure_schema(conn)
        dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
        run_id = _mysql_save_run(conn, dt, source_file)

        for league_name, payload in resultado_final.items():
            tournament_id = int((payload or {}).get("tournament_id", 0) or 0)
            season_id = int((payload or {}).get("season_id", 0) or 0)

            if tournament_id and season_id:
                _mysql_upsert_league_and_season(conn, tournament_id, season_id, league_name)

            if isinstance(payload, dict) and payload.get("erro"):
                _mysql_insert_run_league(conn, run_id, tournament_id, season_id, league_name, "erro", str(payload.get("erro")))
                continue
            else:
                _mysql_insert_run_league(conn, run_id, tournament_id, season_id, league_name, "ok", None)

            rows = (payload or {}).get("times") or []
            for rank, row in enumerate(rows, start=1):
                if row.get("team_id") is not None:
                    _mysql_upsert_team(conn, int(row["team_id"]), str(row["time"]))
                _mysql_insert_team_metrics(conn, run_id, tournament_id, season_id, league_name, row, rank)

        conn.commit()
        print(f"[DB] Dados salvos com sucesso (run_id={run_id}).")
    finally:
        try:
            conn.close()
        except Exception:
            pass


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

                if not overall:
                    print(f"   ⚠️ {team_name}: sem dados ou bloqueado")
                    _sleep()
                    continue

                # Inclui o ID do time na saída para uso no banco de dados
                row = {"team_id": team_id, "time": team_name}
                row.update(analyze_team(overall))
                # �ltimos confrontos (forma recente)
                try:
                    ev_summary = get_last_events_summary(page, int(team_id))
                except Exception:
                    ev_summary = {"last5": {}, "last10": {}, "events": []}
                l5 = ev_summary.get("last5") or {}
                l10 = ev_summary.get("last10") or {}
                row.update({
                    "form5": l5.get("form"),
                    "w5": l5.get("w"), "d5": l5.get("d"), "l5": l5.get("l"),
                    "gf5": l5.get("gf"), "ga5": l5.get("ga"),
                    "form10": l10.get("form"),
                    "w10": l10.get("w"), "d10": l10.get("d"), "l10": l10.get("l"),
                    "gf10": l10.get("gf"), "ga10": l10.get("ga"),
                })
                # Salva JSON bruto dos eventos recentes por time (para consulta)
                try:
                    import os as _os, json as _json
                    _os.makedirs("last_events", exist_ok=True)
                    with open(_os.path.join("last_events", f"last_events_{team_id}.json"), "w", encoding="utf-8") as f:
                        _json.dump({"team_id": team_id, "team": team_name, **ev_summary}, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
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
    # Persistência no MySQL, se configurado via variáveis de ambiente
    try:
        save_to_mysql(resultado_final, ts, file_name)
    except Exception as e:
        print(f"[DB] Persistência MySQL não executada: {e}")

if __name__ == "__main__":
    main()
