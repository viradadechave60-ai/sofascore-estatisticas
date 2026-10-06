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

SEASON = int(
    os.getenv(
        "API_FOOTBALL_SEASON",
        str(datetime.now().year)
    )
)

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
# REQUISIÇÃO À API-FOOTBALL
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
            timeout=15
        )

        if r.status_code == 429:
            raise HTTPException(
                503,
                "Limite de requisições da API-Football atingido. "
                "Tente novamente mais tarde."
            )

        if r.status_code in (401, 403):
            raise HTTPException(
                502,
                "A chave da API-Football foi recusada. "
                "Verifique API_FOOTBALL_KEY no Render."
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
# NORMALIZAÇÃO DE TEXTO
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
            "Digite pelo menos 3 caracteres do nome do jogador."
        )

    search_name = name.strip()

    data = get_json(
        "/players",
        {
            "search": search_name,
            "season": SEASON
        },
        600
    )

    players = data.get("response", [])

    # Caso a busca com temporada não encontre nada,
    # tenta novamente sem temporada.
    if not players:

        data = get_json(
            "/players",
            {
                "search": search_name
            },
            600
        )

        players = data.get("response", [])

    if not players:

        raise HTTPException(
            404,
            "Jogador não encontrado na API-Football."
        )

    wanted = normalize_name(search_name)

    # Primeiro tenta encontrar nome exatamente igual
    exact = []

    for item in players:

        player = item.get("player") or {}

        player_name = normalize_name(
            player.get("name", "")
        )

        if player_name == wanted:
            exact.append(item)

    if exact:
        return exact[0]

    # Caso contrário, retorna o primeiro resultado
    return players[0]


# ============================================================
# ESTATÍSTICAS DO JOGADOR NA TEMPORADA
# ============================================================

def player_statistics(player_id):

    all_stats = []

    page = 1

    while page <= 3:

        data = get_json(
            "/players",
            {
                "id": player_id,
                "season": SEASON,
                "page": page
            },
            300
        )

        response = data.get("response", [])

        for item in response:

            statistics_list = (
                item.get("statistics")
                or []
            )

            all_stats.extend(
                statistics_list
            )

        paging = data.get("paging") or {}

        total_pages = paging.get(
            "total",
            1
        )

        if page >= total_pages:
            break

        page += 1

    return all_stats


# ============================================================
# ÚLTIMOS JOGOS DO JOGADOR
# ============================================================

def player_history(player_id, team_id=None, n=10):

    stats = player_statistics(
        player_id
    )

    rows = []

    for item in stats:

        fixture = item.get("fixture") or {}
        games = item.get("games") or {}
        shots_data = item.get("shots") or {}

        fixture_id = fixture.get("id")

        if not fixture_id:
            continue

        minutes = games.get("minutes")

        if minutes is None:
            minutes = 0

        shots = shots_data.get("total")

        if shots is None:
            shots = 0

        substitute = games.get(
            "substitute",
            False
        )

        team = item.get("team") or {}

        current_team_id = team.get("id")

        if team_id and current_team_id != team_id:
            continue

        rows.append({
            "eventId": fixture_id,
            "date": fixture.get("date"),
            "minutes": int(minutes or 0),
            "shots": int(shots or 0),
            "starter": not bool(substitute),
            "teamId": current_team_id,
            "team": team.get("name"),
            "opponent": None,
            "home": None
        })

    # Ordenar do mais recente para o mais antigo
    rows.sort(
        key=lambda x: x.get("date") or "",
        reverse=True
    )

    # Remover partidas sem participação
    rows = [
        x for x in rows
        if x["minutes"] > 0
    ]

    return rows[:n]


# ============================================================
# DESCOBRIR ADVERSÁRIO / MANDO
# ============================================================

def enrich_fixture_data(rows, team_id):

    result = []

    for row in rows:

        fixture_id = row["eventId"]

        try:

            data = get_json(
                "/fixtures",
                {
                    "id": fixture_id
                },
                1800
            )

            fixtures = data.get(
                "response",
                []
            )

            if not fixtures:
                result.append(row)
                continue

            fixture = fixtures[0]

            teams = fixture.get(
                "teams"
            ) or {}

            home = teams.get(
                "home"
            ) or {}

            away = teams.get(
                "away"
            ) or {}

            home_id = home.get("id")

            away_id = away.get("id")

            if home_id == team_id:

                row["home"] = True
                row["opponent"] = away.get(
                    "name"
                )

            elif away_id == team_id:

                row["home"] = False
                row["opponent"] = home.get(
                    "name"
                )

            result.append(row)

        except HTTPException:

            result.append(row)

    return result


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
        180
    )

    fixtures = data.get(
        "response",
        []
    )

    if not fixtures:
        return None

    now = int(time.time())

    upcoming = []

    for fixture in fixtures:

        timestamp = (
            fixture
            .get("fixture", {})
            .get("timestamp")
        )

        if timestamp and timestamp > now:

            upcoming.append(
                fixture
            )

    if not upcoming:
        return None

    fixture = upcoming[0]

    fixture_info = fixture.get(
        "fixture"
    ) or {}

    teams = fixture.get(
        "teams"
    ) or {}

    league = fixture.get(
        "league"
    ) or {}

    return {
        "id": fixture_info.get("id"),
        "date": fixture_info.get("date"),
        "timestamp": fixture_info.get(
            "timestamp"
        ),
        "status": (
            fixture_info
            .get("status", {})
            .get("short")
        ),
        "homeTeam": (
            teams
            .get("home", {})
            .get("name")
        ),
        "awayTeam": (
            teams
            .get("away", {})
            .get("name")
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


# ============================================================
# ENDPOINT TESTE
# ============================================================

@app.get("/api/teste")
def teste():

    return {
        "status": "ok",
        "mensagem": "Servidor funcionando",
        "fonte": "API-Football",
        "season": SEASON
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

    p = item.get(
        "player"
    ) or {}

    statistics_list = (
        item.get("statistics")
        or []
    )

    team = {}

    if statistics_list:

        team = (
            statistics_list[0]
            .get("team")
            or {}
        )

    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "firstname": p.get("firstname"),
        "lastname": p.get("lastname"),
        "age": p.get("age"),
        "nationality": p.get(
            "nationality"
        ),
        "photo": p.get("photo"),
        "team": team
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

    p = item.get(
        "player"
    ) or {}

    player_id = p.get("id")

    if not player_id:

        raise HTTPException(
            404,
            "ID do jogador não encontrado."
        )

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    statistics_list = (
        item.get("statistics")
        or []
    )

    team = {}

    for stat in statistics_list:

        possible_team = (
            stat.get("team")
            or {}
        )

        if possible_team.get("id"):

            team = possible_team

            break

    team_id = team.get("id")

    if not team_id:

        # Tenta obter estatísticas novamente
        # para descobrir o clube.
        all_stats = player_statistics(
            player_id
        )

        if all_stats:

            team = (
                all_stats[0]
                .get("team")
                or {}
            )

            team_id = team.get("id")

    if not team_id:

        raise HTTPException(
            404,
            "Não foi possível identificar o clube atual do jogador."
        )

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
            "com minutos jogados para uma análise."
        )

    # --------------------------------------------------------
    # COMPLETAR ADVERSÁRIOS
    # --------------------------------------------------------

    hist = enrich_fixture_data(
        hist,
        team_id
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
    # CHUTES POR 90 MINUTOS
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
    # BASE PONDERADA
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
    # PRÓXIMO JOGO
    # --------------------------------------------------------

    fixture = next_fixture(
        team_id
    )

    venue = None

    if fixture:

        if fixture["homeTeam"] == team.get(
            "name"
        ):

            venue = "home"

        elif fixture["awayTeam"] == team.get(
            "name"
        ):

            venue = "away"

    # --------------------------------------------------------
    # FATOR CASA/FORA
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
    # TAXA DE TITULARIDADE
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

    score = max(
        0,
        min(
            100,
            0.30
            * hit5
            * 100

            + 0.20
            * hit10
            * 100

            + 0.20
            * min(
                1,
                exp_minutes / 90
            )
            * 100

            + 0.15
            * starter_rate
            * 100

            + 0.15
            * min(
                1,
                season90 / 3
            )
            * 100
        )
    )

    # --------------------------------------------------------
    # SINAL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RESULTADO
    # --------------------------------------------------------

    return {

        "player": {
            "id": p.get("id"),
            "name": p.get("name"),
            "firstname": p.get(
                "firstname"
            ),
            "lastname": p.g