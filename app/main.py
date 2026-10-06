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
            "API-Football; jogador localizado "
            "nas temporadas disponíveis; "
            "clube via players/squads; "
            "histórico recente via fixtures + "
            "fixtures/players; chutes por 90; "
            "minutos esperados; fator casa/fora; "
            "distribuição de Poisson."
        )
    }