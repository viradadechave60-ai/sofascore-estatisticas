from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import requests
import math
import time
import statistics
import os
from datetime import datetime


# ============================================================
# CONFIGURAÇÃO
# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")

BASE = "https://v3.football.api-sports.io"

# O plano gratuito informado pela API permite 2022-2024
SEARCH_SEASONS = [2024, 2023, 2022]

HEADERS = {
    "x-apisports-key": API_KEY or "",
    "Accept": "application/json"
}

app = FastAPI(
    title="Analisador de Chutes - API-Football"
)


# ============================================================
# ARQUIVOS ESTÁTICOS
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static"
)


# ============================================================
# SESSÃO / CACHE
# ============================================================

sess = requests.Session()
sess.headers.update(HEADERS)

_cache = {}


# ============================================================
# REQUISIÇÃO À API
# ============================================================

def get_json(endpoint, params=None, ttl=120):

    if not API_KEY:
        raise HTTPException(
            500,
            "API_FOOTBALL_KEY não configurada no Render."
        )

    params = params or {}

    cache_key = (
        endpoint,
        tuple(sorted(params.items()))
    )

    now = time.time()

    hit = _cache.get(cache_key)

    if hit and now - hit[0] < ttl:
        return hit[1]

    try:

        r = sess.get(
            BASE + endpoint,
            params=params,
            timeout=20
        )

        if r.status_code == 429:
            raise HTTPException(
                503,
                "Limite de requisições da API-Football atingido."
            )

        if r.status_code in (401, 403):
            raise HTTPException(
                502,
                "A chave da API-Football foi recusada."
            )

        r.raise_for_status()

        data = r.json()

        errors = data.get("errors")

        if errors:

            if isinstance(errors, dict):
                mensagem = "; ".join(
                    f"{k}: {v}"
                    for k, v in errors.items()
                )
            else:
                mensagem = str(errors)

            raise HTTPException(
                502,
                f"API-Football: {mensagem}"
            )

        _cache[cache_key] = (
            now,
            data
        )

        return data

    except HTTPException:
        raise

    except requests.RequestException as e:

        raise HTTPException(
            502,
            f"Falha ao consultar API-Football: {e}"
        )


# ============================================================
# POISSON
# ============================================================

def poisson_over(lam, line):

    k = math.floor(float(line)) + 1

    cdf = sum(
        math.exp(-lam)
        * lam ** i
        / math.factorial(i)
        for i in range(k)
    )

    return max(
        0,
        min(
            1,
            1 - cdf
        )
    )


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize_name(name):

    return (
        str(name)
        .strip()
        .lower()
        .replace(".", "")
    )


# ============================================================
# BUSCAR JOGADOR
# ============================================================

def find_player(name):

    if not name or len(name.strip()) < 3:

        raise HTTPException(
            400,
            "Digite pelo menos 3 caracteres."
        )

    search_name = name.strip()

    last_error = None

    # Procuramos nas temporadas permitidas
    # pelo plano gratuito.
    for season in SEARCH_SEASONS:

        try:

            data = get_json(
                "/players",
                {
                    "search": search_name,
                    "season": season
                },
                3600
            )

            players = data.get(
                "response",
                []
            )

            if not players:
                continue

            wanted = normalize_name(
                search_name
            )

            # Nome exatamente igual
            for item in players:

                player = (
                    item.get("player")
                    or {}
                )

                player_name = normalize_name(
                    player.get("name", "")
                )

                if player_name == wanted:
                    return item

            # Tenta primeiro resultado
            return players[0]

        except HTTPException as e:

            last_error = e

            # Se a temporada não estiver
            # disponível, tenta a anterior.
            continue

    if last_error:
        raise HTTPException(
            404,
            "Jogador não encontrado nas temporadas "
            "disponíveis do plano gratuito."
        )

    raise HTTPException(
        404,
        "Jogador não encontrado."
    )


# ============================================================
# DESCOBRIR CLUBES ASSOCIADOS AO JOGADOR
# ============================================================

def player_teams(player_id):

    data = get_json(
        "/players/squads",
        {
            "player": player_id
        },
        1800
    )

    response = data.get(
        "response",
        []
    )

    teams = []

    for item in response:

        team = item.get("team") or {}

        if team.get("id"):

            teams.append({
                "id": team.get("id"),
                "name": team.get("name"),
                "logo": team.get("logo")
            })

    return teams


# ============================================================
# ESCOLHER CLUBE
# ============================================================

def choose_current_team(teams):

    if not teams:
        return None

    # A API normalmente retorna o clube atual
    # junto dos clubes associados.
    return teams[0]


# ============================================================
# ESTATÍSTICAS DE UMA PARTIDA
# ============================================================

def fixture_player_stats(
    fixture_id,
    player_id
):

    data = get_json(
        "/fixtures/players",
        {
            "fixture": fixture_id
        },
        1800
    )

    response = data.get(
        "response",
        []
    )

    for team_block in response:

        players = (
            team_block.get("players")
            or []
        )

        for item in players:

            player = (
                item.get("player")
                or {}
            )

            if player.get("id") != player_id:
                continue

            statistics_list = (
                item.get("statistics")
                or []
            )

            if not statistics_list:
                continue

            stat = statistics_list[0]

            games = (
                stat.get("games")
                or {}
            )

            shots = (
                stat.get("shots")
                or {}
            )

            minutes = games.get(
                "minutes"
            )

            if minutes is None:
                minutes = 0

            total_shots = shots.get(
                "total"
            )

            if total_shots is None:
                total_shots = 0

            substitute = games.get(
                "substitute",
                False
            )

            return {
                "minutes": int(
                    minutes or 0
                ),
                "shots": int(
                    total_shots or 0
                ),
                "starter": not bool(
                    substitute
                )
            }

    return None


# ============================================================
# ÚLTIMOS JOGOS DO CLUBE
# ============================================================

def recent_fixtures(team_id, n=10):

    data = get_json(
        "/fixtures",
        {
            "team": team_id,
            "last": n
        },
        300
    )

    fixtures = data.get(
        "response",
        []
    )

    return fixtures


# ============================================================
# HISTÓRICO DO JOGADOR
# ============================================================

def player_history(
    player_id,
    team_id,
    n=10
):

    fixtures = recent_fixtures(
        team_id,
        n
    )

    rows = []

    for fixture in fixtures:

        fixture_info = (
            fixture.get("fixture")
            or {}
        )

        fixture_id = fixture_info.get(
            "id"
        )

        if not fixture_id:
            continue

        status = (
            fixture_info
            .get("status", {})
            .get("short")
        )

        # Só partidas encerradas
        if status not in (
            "FT",
            "AET",
            "PEN"
        ):
            continue

        teams = (
            fixture.get("teams")
            or {}
        )

        home = (
            teams.get("home")
            or {}
        )

        away = (
            teams.get("away")
            or {}
        )

        stats = fixture_player_stats(
            fixture_id,
            player_id
        )

        if not stats:
            continue

        if stats["minutes"] <= 0:
            continue

        if home.get("id") == team_id:

            is_home = True
            opponent = away.get(
                "name"
            )

        elif away.get("id") == team_id:

            is_home = False
            opponent = home.get(
                "name"
            )

        else:

            continue

        rows.append({
            "eventId": fixture_id,
            "date": fixture_info.get(
                "date"
            ),
            "minutes": stats[
                "minutes"
            ],
            "shots": stats[
                "shots"
            ],
            "starter": stats[
                "starter"
            ],
            "teamId": team_id,
            "opponent": opponent,
            "home": is_home
        })

    rows.sort(
        key=lambda x: x.get(
            "date"
        ) or "",
        reverse=True
    )

    return rows[:n]


# ============================================================
# PRÓXIMA PARTIDA
# ============================================================

def next_fixture(team_id):

    data = get_json(
        "/fixtures",
        {
            "team": team_id,
            "next": 5
        },
        300
    )

    fixtures = data.get(
        "response",
        []
    )

    now = int(time.time())

    for fixture in fixtures:

        fixture_info = (
            fixture.get("fixture")
            or {}
        )

        timestamp = fixture_info.get(
            "timestamp"
        )

        if not timestamp:
            continue

        if timestamp <= now:
            continue

        teams = (
            fixture.get("teams")
            or {}
        )

        home = (
            teams.get("home")
            or {}
        )

        away = (
            teams.get("away")
            or {}
        )

        league = (
            fixture.get("league")
            or {}
        )

        return {
            "id": fixture_info.get(
                "id"
            ),
            "date": fixture_info.get(
                "date"
            ),
            "timestamp": timestamp,
            "status": (
                fixture_info
                .get("status", {})
                .get("short")
            ),
            "homeTeam": home.get(
                "name"
            ),
            "awayTeam": away.get(
                "name"
            ),
            "league": league.get(
                "name"
            ),
            "venue": (
                fixture_info
                .get("venue", {})
                .get("name")
            )
        }

    return None


# ============================================================
# TESTE
# ============================================================

@app.get("/api/teste")
def teste():

    return {
        "status": "ok",
        "mensagem": "Servidor funcionando",
        "fonte": "API-Football",
        "temporadasPesquisa": SEARCH_SEASONS
    }


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.get("/")
def root():

    return FileResponse(
        "app/static/index.html"
    )


# ============================================================
# ENDPOINT PLAYER
# ============================================================

@app.get("/api/player")
def player(name: str):

    item = find_player(name)

    p = (
        item.get("player")
        or {}
    )

    player_id = p.get("id")

    if not player_id:

        raise HTTPException(
            404,
            "ID do jogador não encontrado."
        )

    teams = player_teams(
        player_id
    )

    team = choose_current_team(
        teams
    )

    return {
        "id": player_id,
        "name": p.get("name"),
        "firstname": p.get(
            "firstname"
        ),
        "lastname": p.get(
            "lastname"
        ),
        "age": p.get("age"),
        "nationality": p.get(
            "nationality"
        ),
        "photo": p.get("photo"),
        "team": team,
        "teams": teams
    }


# ============================================================
# ANÁLISE
# ============================================================

@app.get("/api/analyze")
def analyze(
    name: str,
    line: float,
    odd: float
):

    if odd <= 1:

        raise HTTPException(
            400,
            "Odd inválida."
        )

    if line < 0:

        raise HTTPException(
            400,
            "Linha inválida."
        )

    # --------------------------------------------------------
    # JOGADOR
    # --------------------------------------------------------

    item = find_player(name)

    p = (
        item.get("player")
        or {}
    )

    player_id = p.get("id")

    if not player_id:

        raise HTTPException(
            404,
            "ID do jogador não encontrado."
        )

    # --------------------------------------------------------
    # CLUBE
    # --------------------------------------------------------

    teams = player_teams(
        player_id
    )

    team = choose_current_team(
        teams
    )

    if not team:

        raise HTTPException(
            404,
            "Não foi possível identificar "
            "o clube do jogador."
        )

    team_id = team.get("id")

    # --------------------------------------------------------
    # HISTÓRICO
    # --------------------------------------------------------

    hist = player_history(
        player_id,
        team_id,
        10
    )

    if len(hist) < 5:

        raise HTTPException(
            422,
            "Não há pelo menos 5 jogos recentes "
            "com minutos jogados para este jogador."
        )

    # --------------------------------------------------------
    # VALORES
    # --------------------------------------------------------

    vals = [
        x["shots"]
        for x in hist
    ]

    mins = [
        x["minutes"]
        for x in hist
    ]

    # --------------------------------------------------------
    # CHUTES POR 90
    # --------------------------------------------------------

    s90 = [
        x["shots"] * 90 / x["minutes"]
        for x in hist
        if x["minutes"] >= 15
    ]

    if s90:

        season90 = statistics.mean(
            s90
        )

    else:

        season90 = statistics.mean(
            vals
        )

    # --------------------------------------------------------
    # MÉDIAS
    # --------------------------------------------------------

    last5 = statistics.mean(
        vals[:5]
    )

    last10 = statistics.mean(
        vals[:10]
    )

    # --------------------------------------------------------
    # BASE
    # --------------------------------------------------------

    base = (
        0.40 * season90
        + 0.35 * last5
        + 0.25 * last10
    )

    # --------------------------------------------------------
    # MINUTOS ESPERADOS
    # --------------------------------------------------------

    exp_minutes = statistics.mean(
        mins[:5]
    )

    exp_minutes = max(
        45,
        min(
            90,
            exp_minutes
        )
    )

    # --------------------------------------------------------
    # CHUTES ESPERADOS
    # --------------------------------------------------------

    lam = (
        base
        * exp_minutes
        / 90
    )

    # --------------------------------------------------------
    # PRÓXIMA PARTIDA
    # --------------------------------------------------------

    fixture = next_fixture(
        team_id
    )

    venue = None

    if fixture:

        if (
            fixture["homeTeam"]
            == team.get("name")
        ):

            venue = "home"

        elif (
            fixture["awayTeam"]
            == team.get("name")
        ):

            venue = "away"

    # --------------------------------------------------------
    # CASA / FORA
    # --------------------------------------------------------

    split = []

    if venue:

        expected_home = (
            venue == "home"
        )

        split = [
            x["shots"]
            for x in hist
            if x["home"] is not None
            and x["home"] == expected_home
        ]

    if len(split) >= 3:

        overall = (
            statistics.mean(vals)
            or 1
        )

        split_factor = max(
            0.85,
            min(
                1.15,
                statistics.mean(split)
                / overall
            )
        )

        lam *= split_factor

    # --------------------------------------------------------
    # PROBABILIDADE
    # --------------------------------------------------------

    prob = poisson_over(
        lam,
        line
    )

    # --------------------------------------------------------
    # PROBABILIDADE IMPLÍCITA
    # --------------------------------------------------------

    implied = 1 / odd

    # --------------------------------------------------------
    # EDGE
    # --------------------------------------------------------

    edge = (
        prob
        - implied
    )

    # --------------------------------------------------------
    # TAXA DE ACERTO
    # --------------------------------------------------------

    hit5 = (
        sum(
            x["shots"] > line
            for x in hist[:5]
        )
        / min(
            5,
            len(hist)
        )
    )

    hit10 = (
        sum(
            x["shots"] > line
            for x in hist[:10]
        )
        / len(hist)
    )

    # --------------------------------------------------------
    # TITULARIDADE
    # --------------------------------------------------------

    starter_rate = (
        sum(
            x["starter"]
            for x in hist
        )
        / len(hist)
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = round(
        min(
            100,
            0.30 * min(1, season90 / 3) * 
100
            + 0.20 * hit5 * 100
            + 0.20 * hit10 * 100
            + 0.20 * min(1, exp_minutes / 
90) * 100
            + 0.15 * starter_rate * 100
            + 0.15 * min(1, season90 / 3) * 
100
        )
    )

    if (
        prob >= 0.75
        and edge >= 0.06
        and score >= 75
    ):
        signal = "APROVAR"

    elif (
        prob >= 0.68
        and edge >= 0.03
        and score >= 65
    ):
        signal = "INTERESSANTE"

    else:
        signal = "EVITAR"

    return {
        "player": {
            "id": player_id,
            "name": p.get("name"),
            "firstname": 
p.get("firstname"),
            "lastname": p.get("lastname"),
            "age": p.get("age"),
            "nationality": 
p.get("nationality"),
            "photo": p.get("photo")
        },

        "team": team,

        "fixture": fixture,

        "sample": hist,

        "seasonShots90": season90,

        "last5": last5,

        "last10": last10,

        "expectedMinutes": exp_minutes,

        "expectedShots": lam,

        "probability": prob,

        "impliedProbability": implied,

        "edge": edge,

        "score": score,

        "signal": signal,

        "method": (
            "API-Football; jogador 
localizado "
            "nas temporadas disponíveis; "
            "clube via players/squads; "
            "histórico recente via fixtures 
+ "
            "fixtures/players; chutes por 
90; "
            "minutos esperados; fator casa/
fora; "
            "distribuição de Poisson."
        )
    }