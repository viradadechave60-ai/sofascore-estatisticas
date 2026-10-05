from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import requests
import math
import time
import statistics

BASE = 'https://www.sofascore.com/api/v1'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.sofascore.com/',
    'Origin': 'https://www.sofascore.com'
}

app = FastAPI(title='Analisador de Chutes - Sofascore')
app.mount('/static', StaticFiles(directory='app/static'), name='static')

sess = requests.Session()
sess.headers.update(HEADERS)

_cache = {}


def get_json(path, ttl=120):
    now = time.time()
    hit = _cache.get(path)

    if hit and now - hit[0] < ttl:
        return hit[1]

    try:
        r = sess.get(BASE + path, timeout=12)

        if r.status_code in (403, 429):
    raise HTTPException(
        503,
        f'Sofascore respondeu HTTP {r.status_code}.'
    )

        r.raise_for_status()
        data = r.json()
        _cache[path] = (now, data)
        return data

    except requests.RequestException as e:
        raise HTTPException(
            502,
            f'Falha ao consultar Sofascore: {e}'
        )


def poisson_over(lam, line):
    k = math.floor(float(line)) + 1
    cdf = sum(
        math.exp(-lam) * lam ** i / math.factorial(i)
        for i in range(k)
    )
    return max(0, min(1, 1 - cdf))


def find_player(name):
    d = get_json(
        '/search/all?q=' + requests.utils.quote(name),
        300
    )

    arr = d.get('results', [])
    players = [
        x for x in arr
        if x.get('entity', {}).get('type') == 'player'
    ]

    if not players:
        raise HTTPException(
            404,
            'Jogador não encontrado no Sofascore.'
        )

    return players[0]['entity']


def next_events(team_id):
    out = []

    for page in range(0, 2):
        d = get_json(
            f'/team/{team_id}/events/next/{page}',
            180
        )

        out += d.get('events', [])

        if not d.get('hasNextPage'):
            break

    return out


def last_events(team_id, pages=2):
    out = []

    for page in range(pages):
        d = get_json(
            f'/team/{team_id}/events/last/{page}',
            300
        )

        out += [
            e for e in d.get('events', [])
            if e.get('status', {}).get('type') == 'finished'
        ]

        if not d.get('hasNextPage'):
            break

    return out


def player_history(player_id, team_id, n=10):
    events = last_events(team_id, 4)
    rows = []

    for e in events[:25]:
        try:
            l = get_json(
                f"/event/{e['id']}/lineups",
                600
            )
        except HTTPException:
            continue

        found = None

        for side in ('home', 'away'):
            for p in l.get(side, {}).get('players', []):
                if p.get('player', {}).get('id') == player_id:
                    found = p
                    break

            if found:
                break

        if found:
            st = found.get('statistics') or {}

            minutes = st.get('minutesPlayed') or 0
            shots = st.get('totalShots')

            if shots is None:
                shots = st.get('shots', 0)

            rows.append({
                'eventId': e['id'],
                'date': e.get('startTimestamp'),
                'minutes': minutes or 0,
                'shots': shots or 0,
                'starter': bool(found.get('starter')),
                'home': e.get('homeTeam', {}).get('id') == team_id,
                'opponent':
                    e.get('awayTeam', {}).get('name')
                    if e.get('homeTeam', {}).get('id') == team_id
                    else e.get('homeTeam', {}).get('name')
            })

            if len(rows) >= n:
                break

    return rows


@app.get('/')
def root():
    return FileResponse('app/static/index.html')


@app.get('/api/player')
def player(name: str):
    p = find_player(name)
    return p


@app.get('/api/analyze')
def analyze(name: str, line: float, odd: float):

    if odd <= 1 or line < 0:
        raise HTTPException(
            400,
            'Linha/odd inválidas.'
        )

    p = find_player(name)

    team = p.get('team') or {}
    team_id = team.get('id')

    if not team_id:
        raise HTTPException(
            404,
            'Sofascore não retornou o clube atual do jogador.'
        )

    hist = player_history(
        p['id'],
        team_id,
        10
    )

    if len(hist) < 5:
        raise HTTPException(
            422,
            'Não há jogos recentes suficientes no Sofascore para uma análise confiável.'
        )

    vals = [
        x['shots']
        for x in hist
    ]

    mins = [
        x['minutes']
        for x in hist
    ]

    s90 = [
        x['shots'] * 90 / x['minutes']
        for x in hist
        if x['minutes'] >= 15
    ]

    season90 = (
        statistics.mean(s90)
        if s90
        else statistics.mean(vals)
    )

    last5 = statistics.mean(vals[:5])
    last10 = statistics.mean(vals[:10])

    base = (
        0.40 * season90
        + 0.35 * last5
        + 0.25 * last10
    )

    exp_minutes = statistics.mean(mins[:5])

    exp_minutes = max(
        45,
        min(90, exp_minutes)
    )

    lam = base * exp_minutes / 90

    current_events = next_events(team_id)

    upcoming = [
        e for e in current_events
        if e.get('status', {}).get('type') == 'notstarted'
    ]

    fixture = upcoming[0] if upcoming else None

    venue = None

    if fixture:
        venue = (
            'home'
            if fixture.get('homeTeam', {}).get('id') == team_id
            else 'away'
        )

    split = [
        x['shots']
        for x in hist
        if x['home'] == (venue == 'home')
    ] if venue else []

    if len(split) >= 3:
        overall = statistics.mean(vals) or 1

        split_factor = max(
            0.85,
            min(
                1.15,
                statistics.mean(split) / overall
            )
        )

        lam *= split_factor

    prob = poisson_over(
        lam,
        line
    )

    implied = 1 / odd
    edge = prob - implied

    hit5 = (
        sum(
            x['shots'] > line
            for x in hist[:5]
        )
        / min(5, len(hist))
    )

    hit10 = (
        sum(
            x['shots'] > line
            for x in hist[:10]
        )
        / len(hist)
    )

    starter_rate = (
        sum(
            x['starter']
            for x in hist
        )
        / len(hist)
    )

    score = max(
        0,
        min(
            100,
            0.30 * hit5 * 100
            + 0.20 * hit10 * 100
            + 0.20 * min(1, exp_minutes / 90) * 100
            + 0.15 * starter_rate * 100
            + 0.15 * min(1, season90 / 3) * 100
        )
    )

    if prob >= 0.75 and edge >= 0.06 and score >= 75:
        signal = 'APROVAR'
    elif prob >= 0.68 and edge >= 0.03 and score >= 65:
        signal = 'INTERESSANTE'
    else:
        signal = 'EVITAR'

    return {
        'player': p,
        'team': team,
        'fixture': fixture,
        'sample': hist,
        'seasonShots90': season90,
        'last5': last5,
        'last10': last10,
        'expectedMinutes': exp_minutes,
        'expectedShots': lam,
        'probability': prob,
        'impliedProbability': implied,
        'edge': edge,
        'score': score,
        'signal': signal,
        'method': 'Sofascore-only; weighted history + Poisson; context score is not a probability.'
    }