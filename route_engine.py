import os
from time import perf_counter

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.sat.python import cp_model


MAX_SUPPORTED_LOCATIONS = 100
HELD_KARP_MAX_LOCATIONS = 12
PRECISE_CP_SAT_MAX_LOCATIONS = 40

FAST_TIME_SECONDS = 5
PRECISE_HEURISTIC_TIME_SECONDS = 8
PRECISE_CP_SAT_TIME_SECONDS = 30
PRECISE_CP_SAT_RELATIVE_GAP_LIMIT = 0.005

BENCHMARK_HEURISTIC_TIME_SECONDS = 3


def _cpus_disponiveis():
    """Retorna uma quantidade conservadora de CPUs para o CP-SAT."""
    try:
        quantidade = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        quantidade = os.cpu_count() or 1

    return max(
        1,
        min(
            int(quantidade),
            4,
        ),
    )


CPU_COUNT = _cpus_disponiveis()


class RouteOptimizationError(Exception):
    pass


def _preparar_matriz(matrix):
    """
    Converte a matriz recebida para inteiros.

    A matriz pode representar metros ou segundos.
    O motor simplesmente minimiza os valores informados.
    """
    try:
        matrix = np.asarray(
            matrix,
            dtype=float,
        )
    except Exception as e:
        raise RouteOptimizationError(
            "Não foi possível interpretar a matriz de otimização."
        ) from e

    if (
        matrix.ndim != 2
        or matrix.shape[0] != matrix.shape[1]
    ):
        raise RouteOptimizationError(
            "A matriz precisa ser quadrada."
        )

    if not np.all(
        np.isfinite(matrix)
    ):
        raise RouteOptimizationError(
            "A matriz possui trechos inválidos ou inacessíveis."
        )

    if np.any(
        matrix < 0
    ):
        raise RouteOptimizationError(
            "A matriz possui valores negativos."
        )

    return np.rint(
        matrix
    ).astype(
        np.int64
    )


def _validar_indices(
    n,
    start_idx,
    end_idx,
):
    if n < 2:
        raise RouteOptimizationError(
            "São necessários pelo menos dois locais."
        )

    if n > MAX_SUPPORTED_LOCATIONS:
        raise RouteOptimizationError(
            f"O motor suporta no máximo "
            f"{MAX_SUPPORTED_LOCATIONS} locais por rota."
        )

    if not 0 <= start_idx < n:
        raise RouteOptimizationError(
            "Ponto inicial inválido."
        )

    if not 0 <= end_idx < n:
        raise RouteOptimizationError(
            "Ponto final inválido."
        )


def _validar_rota_resultante(
    route,
    n,
    start_idx,
    end_idx,
):
    """Confere se cada local aparece exatamente uma vez na rota."""

    if not route:
        raise RouteOptimizationError(
            "O otimizador retornou uma rota vazia."
        )

    rota_fechada = (
        start_idx == end_idx
    )

    if rota_fechada:
        if (
            len(route) != n + 1
            or route[0] != start_idx
            or route[-1] != start_idx
            or len(set(route[:-1])) != n
            or set(route[:-1]) != set(range(n))
        ):
            raise RouteOptimizationError(
                "A rota otimizada não contém "
                "todos os pontos corretamente."
            )

    elif (
        len(route) != n
        or route[0] != start_idx
        or route[-1] != end_idx
        or len(set(route)) != n
        or set(route) != set(range(n))
    ):
        raise RouteOptimizationError(
            "A rota otimizada não contém "
            "todos os pontos corretamente."
        )


def _custo_rota(
    route,
    matrix,
):
    return sum(
        int(
            matrix[i, j]
        )
        for i, j in zip(
            route[:-1],
            route[1:],
        )
    )


def _held_karp(
    matrix,
    start_idx,
    end_idx,
):
    """
    Resolve exatamente o TSP/caminho Hamiltoniano para rotas pequenas.

    Em rota fechada, start_idx == end_idx e a rota retornada termina
    no próprio ponto inicial.

    Em rota aberta, start e end permanecem fixos.
    """

    n = len(
        matrix
    )

    rota_fechada = (
        start_idx == end_idx
    )

    destino = (
        start_idx
        if rota_fechada
        else end_idx
    )

    intermediarios = [
        i
        for i in range(n)
        if i != start_idx
        and (
            rota_fechada
            or i != end_idx
        )
    ]

    m = len(
        intermediarios
    )

    if m == 0:
        route = [
            start_idx,
            destino,
        ]

        custo = int(
            matrix[
                start_idx,
                destino,
            ]
        )

        _validar_rota_resultante(
            route,
            n,
            start_idx,
            end_idx,
        )

        return {
            "route":
                route,

            "objective_cost":
                custo,

            "metodo":
                "Held-Karp",

            "status":
                "ÓTIMA COMPROVADA",

            "gap_percent":
                0.0,

            "lower_bound":
                float(custo),
        }

    quantidade_estados = (
        1 << m
    )

    dp = np.full(
        (
            quantidade_estados,
            m,
        ),
        np.inf,
        dtype=float,
    )

    pai = np.full(
        (
            quantidade_estados,
            m,
        ),
        -1,
        dtype=np.int16,
    )

    for k, no in enumerate(
        intermediarios
    ):
        dp[
            1 << k,
            k,
        ] = float(
            matrix[
                start_idx,
                no,
            ]
        )

    for mask in range(
        1,
        quantidade_estados,
    ):
        for k in range(m):
            if not (
                mask
                &
                1 << k
            ):
                continue

            custo_atual = dp[
                mask,
                k,
            ]

            if not np.isfinite(
                custo_atual
            ):
                continue

            no_atual = (
                intermediarios[k]
            )

            for proximo in range(m):
                if (
                    mask
                    &
                    1 << proximo
                ):
                    continue

                novo_mask = (
                    mask
                    |
                    1 << proximo
                )

                novo_custo = (
                    custo_atual
                    +
                    float(
                        matrix[
                            no_atual,
                            intermediarios[
                                proximo
                            ],
                        ]
                    )
                )

                if (
                    novo_custo
                    <
                    dp[
                        novo_mask,
                        proximo,
                    ]
                ):
                    dp[
                        novo_mask,
                        proximo,
                    ] = novo_custo

                    pai[
                        novo_mask,
                        proximo,
                    ] = k

    cheio = (
        quantidade_estados
        -
        1
    )

    melhor_ultimo = None
    melhor_custo = np.inf

    for k, no in enumerate(
        intermediarios
    ):
        custo_total = (
            dp[
                cheio,
                k,
            ]
            +
            float(
                matrix[
                    no,
                    destino,
                ]
            )
        )

        if custo_total < melhor_custo:
            melhor_custo = (
                custo_total
            )

            melhor_ultimo = k

    if (
        melhor_ultimo is None
        or
        not np.isfinite(
            melhor_custo
        )
    ):
        raise RouteOptimizationError(
            "Não foi possível encontrar "
            "uma rota exata válida."
        )

    sequencia_reversa = []

    mask = cheio
    k = melhor_ultimo

    while k != -1:
        sequencia_reversa.append(
            intermediarios[k]
        )

        anterior = int(
            pai[
                mask,
                k,
            ]
        )

        mask ^= (
            1 << k
        )

        k = anterior

    route = (
        [
            start_idx
        ]
        +
        list(
            reversed(
                sequencia_reversa
            )
        )
        +
        [
            destino
        ]
    )

    custo = int(
        round(
            melhor_custo
        )
    )

    _validar_rota_resultante(
        route,
        n,
        start_idx,
        end_idx,
    )

    return {
        "route":
            route,

        "objective_cost":
            custo,

        "metodo":
            "Held-Karp",

        "status":
            "ÓTIMA COMPROVADA",

        "gap_percent":
            0.0,

        "lower_bound":
            float(custo),
    }


def _executar_metaheuristica(
    matrix,
    start_idx,
    end_idx,
    metaheuristica,
    nome,
    tempo,
    first_solution_strategy=None,
):
    """Executa uma estratégia do OR-Tools Routing."""

    n = len(
        matrix
    )

    rota_fechada = (
        start_idx == end_idx
    )

    if rota_fechada:
        manager = (
            pywrapcp
            .RoutingIndexManager(
                n,
                1,
                start_idx,
            )
        )

    else:
        manager = (
            pywrapcp
            .RoutingIndexManager(
                n,
                1,
                [
                    start_idx
                ],
                [
                    end_idx
                ],
            )
        )

    routing = (
        pywrapcp
        .RoutingModel(
            manager
        )
    )

    def cost_callback(
        from_index,
        to_index,
    ):
        from_node = (
            manager
            .IndexToNode(
                from_index
            )
        )

        to_node = (
            manager
            .IndexToNode(
                to_index
            )
        )

        return int(
            matrix[
                from_node,
                to_node,
            ]
        )

    callback_index = (
        routing
        .RegisterTransitCallback(
            cost_callback
        )
    )

    routing.SetArcCostEvaluatorOfAllVehicles(
        callback_index
    )

    params = (
        pywrapcp
        .DefaultRoutingSearchParameters()
    )

    params.first_solution_strategy = (
        first_solution_strategy
        if first_solution_strategy
        is not None
        else
        routing_enums_pb2
        .FirstSolutionStrategy
        .PATH_CHEAPEST_ARC
    )

    params.local_search_metaheuristic = (
        metaheuristica
    )

    params.time_limit.seconds = max(
        1,
        int(
            tempo
        ),
    )

    params.log_search = False

    solution = (
        routing
        .SolveWithParameters(
            params
        )
    )

    if not solution:
        return None

    route = []

    index = routing.Start(
        0
    )

    while not routing.IsEnd(
        index
    ):
        route.append(
            manager.IndexToNode(
                index
            )
        )

        index = solution.Value(
            routing.NextVar(
                index
            )
        )

    route.append(
        manager.IndexToNode(
            index
        )
    )

    _validar_rota_resultante(
        route,
        n,
        start_idx,
        end_idx,
    )

    custo = _custo_rota(
        route,
        matrix,
    )

    return {
        "route":
            route,

        "objective_cost":
            int(
                custo
            ),

        "metodo":
            nome,
    }


def _modo_rapido(
    matrix,
    start_idx,
    end_idx,
):
    resultado = (
        _executar_metaheuristica(
            matrix=matrix,
            start_idx=start_idx,
            end_idx=end_idx,
            metaheuristica=(
                routing_enums_pb2
                .LocalSearchMetaheuristic
                .GUIDED_LOCAL_SEARCH
            ),
            nome=(
                "Guided Local Search"
            ),
            tempo=FAST_TIME_SECONDS,
        )
    )

    if resultado is None:
        raise RouteOptimizationError(
            "Não foi possível encontrar "
            "uma rota válida."
        )

    resultado.update(
        {
            "status":
                "OTIMIZADA",

            "gap_percent":
                None,

            "lower_bound":
                None,
        }
    )

    return resultado


def _estrategias_precisas():
    return [
        (
            "Guided Local Search",
            routing_enums_pb2
            .LocalSearchMetaheuristic
            .GUIDED_LOCAL_SEARCH,
        ),
        (
            "Tabu Search",
            routing_enums_pb2
            .LocalSearchMetaheuristic
            .TABU_SEARCH,
        ),
        (
            "Simulated Annealing",
            routing_enums_pb2
            .LocalSearchMetaheuristic
            .SIMULATED_ANNEALING,
        ),
    ]


def _melhor_heuristica_precisa(
    matrix,
    start_idx,
    end_idx,
):
    resultados = []

    for nome, estrategia in (
        _estrategias_precisas()
    ):
        resultado = (
            _executar_metaheuristica(
                matrix=matrix,
                start_idx=start_idx,
                end_idx=end_idx,
                metaheuristica=estrategia,
                nome=nome,
                tempo=(
                    PRECISE_HEURISTIC_TIME_SECONDS
                ),
            )
        )

        if resultado is not None:
            resultados.append(
                resultado
            )

    if not resultados:
        raise RouteOptimizationError(
            "Nenhuma estratégia conseguiu "
            "encontrar uma rota."
        )

    return min(
        resultados,
        key=lambda x:
            x[
                "objective_cost"
            ],
    )


def benchmark_metaheuristicas(
    matrix_m,
    start_idx,
    end_idx,
    tempo_por_estrategia=(
        BENCHMARK_HEURISTIC_TIME_SECONDS
    ),
):
    """
    Compara GLS, Tabu Search e Simulated Annealing.

    Esta função não é usada automaticamente pelo app.

    Retorna custo, tempo e diferença percentual em relação
    à melhor heurística daquele ensaio.

    Em matrizes com até 12 locais também calcula o gap
    em relação ao ótimo comprovado pelo Held-Karp.
    """

    matrix = _preparar_matriz(
        matrix_m
    )

    n = len(
        matrix
    )

    _validar_indices(
        n,
        start_idx,
        end_idx,
    )

    resultados = []

    for nome, estrategia in (
        _estrategias_precisas()
    ):
        inicio = perf_counter()

        resultado = (
            _executar_metaheuristica(
                matrix=matrix,
                start_idx=start_idx,
                end_idx=end_idx,
                metaheuristica=estrategia,
                nome=nome,
                tempo=tempo_por_estrategia,
            )
        )

        tempo_s = (
            perf_counter()
            -
            inicio
        )

        if resultado is None:
            resultados.append(
                {
                    "metodo":
                        nome,

                    "encontrou_solucao":
                        False,

                    "objective_cost":
                        None,

                    "tempo_s":
                        round(
                            tempo_s,
                            6,
                        ),

                    "diferenca_melhor_percent":
                        None,

                    "gap_otimo_percent":
                        None,
                }
            )

            continue

        resultados.append(
            {
                "metodo":
                    nome,

                "encontrou_solucao":
                    True,

                "objective_cost":
                    int(
                        resultado[
                            "objective_cost"
                        ]
                    ),

                "tempo_s":
                    round(
                        tempo_s,
                        6,
                    ),

                "diferenca_melhor_percent":
                    None,

                "gap_otimo_percent":
                    None,
            }
        )

    validos = [
        item
        for item
        in resultados
        if item[
            "encontrou_solucao"
        ]
    ]

    if not validos:
        return resultados

    melhor_custo = min(
        item[
            "objective_cost"
        ]
        for item
        in validos
    )

    custo_otimo = None

    if (
        n
        <=
        HELD_KARP_MAX_LOCATIONS
    ):
        custo_otimo = (
            _held_karp(
                matrix,
                start_idx,
                end_idx,
            )[
                "objective_cost"
            ]
        )

    for item in validos:
        if melhor_custo > 0:
            item[
                "diferenca_melhor_percent"
            ] = round(
                (
                    item[
                        "objective_cost"
                    ]
                    -
                    melhor_custo
                )
                /
                melhor_custo
                *
                100,
                6,
            )

        else:
            item[
                "diferenca_melhor_percent"
            ] = 0.0

        if custo_otimo is not None:
            if custo_otimo > 0:
                item[
                    "gap_otimo_percent"
                ] = round(
                    (
                        item[
                            "objective_cost"
                        ]
                        -
                        custo_otimo
                    )
                    /
                    custo_otimo
                    *
                    100,
                    6,
                )

            else:
                item[
                    "gap_otimo_percent"
                ] = 0.0

    return resultados


def _executar_cp_sat(
    matrix,
    start_idx,
    end_idx,
    resultado_inicial,
):
    """
    Usa o resultado heurístico como hint e limite superior.

    O CP-SAT só é utilizado até PRECISE_CP_SAT_MAX_LOCATIONS.
    """

    n = len(
        matrix
    )

    if (
        n
        >
        PRECISE_CP_SAT_MAX_LOCATIONS
    ):
        resultado = dict(
            resultado_inicial
        )

        resultado.update(
            {
                "status":
                    "MELHOR HEURÍSTICA",

                "gap_percent":
                    None,

                "lower_bound":
                    None,
            }
        )

        return resultado

    rota_inicial = (
        resultado_inicial[
            "route"
        ]
    )

    custo_inicial = int(
        resultado_inicial[
            "objective_cost"
        ]
    )

    rota_aberta = (
        start_idx != end_idx
    )

    model = (
        cp_model
        .CpModel()
    )

    arc_vars = {}
    arcs = []

    for i in range(n):
        for j in range(n):
            if i == j:
                continue

            if rota_aberta:
                if j == start_idx:
                    continue

                if i == end_idx:
                    continue

            var = model.NewBoolVar(
                f"x_{i}_{j}"
            )

            arc_vars[
                i,
                j,
            ] = var

            arcs.append(
                (
                    i,
                    j,
                    var,
                )
            )

    arco_artificial = None

    if rota_aberta:
        arco_artificial = (
            model.NewBoolVar(
                "artificial_end_start"
            )
        )

        model.Add(
            arco_artificial == 1
        )

        arcs.append(
            (
                end_idx,
                start_idx,
                arco_artificial,
            )
        )

    model.AddCircuit(
        arcs
    )

    objective = sum(
        int(
            matrix[
                i,
                j,
            ]
        )
        *
        var
        for (
            i,
            j
        ), var
        in arc_vars.items()
    )

    model.Add(
        objective
        <=
        custo_inicial
    )

    model.Minimize(
        objective
    )

    arcos_iniciais = set(
        zip(
            rota_inicial[:-1],
            rota_inicial[1:],
        )
    )

    for arco, var in (
        arc_vars.items()
    ):
        model.AddHint(
            var,
            (
                1
                if arco
                in arcos_iniciais
                else
                0
            ),
        )

    if arco_artificial is not None:
        model.AddHint(
            arco_artificial,
            1,
        )

    solver = (
        cp_model
        .CpSolver()
    )

    solver.parameters.max_time_in_seconds = (
        PRECISE_CP_SAT_TIME_SECONDS
    )

    solver.parameters.relative_gap_limit = (
        PRECISE_CP_SAT_RELATIVE_GAP_LIMIT
    )

    solver.parameters.num_search_workers = (
        CPU_COUNT
    )

    solver.parameters.log_search_progress = (
        False
    )

    status = (
        solver.Solve(
            model
        )
    )

    if status not in (
        cp_model.OPTIMAL,
        cp_model.FEASIBLE,
    ):
        resultado = dict(
            resultado_inicial
        )

        resultado.update(
            {
                "status":
                    "MELHOR HEURÍSTICA",

                "gap_percent":
                    None,

                "lower_bound":
                    None,
            }
        )

        return resultado

    next_node = {}

    for (
        i,
        j
    ), var in (
        arc_vars.items()
    ):
        if solver.BooleanValue(
            var
        ):
            next_node[
                i
            ] = j

    if rota_aberta:
        route = [
            start_idx
        ]

        atual = (
            start_idx
        )

        while atual != end_idx:
            if atual not in next_node:
                raise RouteOptimizationError(
                    "Erro ao reconstruir "
                    "a rota otimizada."
                )

            atual = (
                next_node[
                    atual
                ]
            )

            route.append(
                atual
            )

            if len(route) > n:
                raise RouteOptimizationError(
                    "Foi detectado um "
                    "ciclo inesperado."
                )

    else:
        route = [
            start_idx
        ]

        atual = (
            start_idx
        )

        for _ in range(n):
            if atual not in next_node:
                raise RouteOptimizationError(
                    "Erro ao reconstruir "
                    "o circuito."
                )

            atual = (
                next_node[
                    atual
                ]
            )

            route.append(
                atual
            )

            if atual == start_idx:
                break

    _validar_rota_resultante(
        route,
        n,
        start_idx,
        end_idx,
    )

    custo = int(
        round(
            solver.ObjectiveValue()
        )
    )

    lower_bound = float(
        solver.BestObjectiveBound()
    )

    if custo > 0:
        gap = max(
            0.0,
            (
                custo
                -
                lower_bound
            )
            /
            custo
            *
            100,
        )

    else:
        gap = 0.0

    if status == cp_model.OPTIMAL:
        status_text = (
            "ÓTIMA COMPROVADA"
        )

        gap = 0.0

    else:
        status_text = (
            "MELHOR ENCONTRADA"
        )

    return {
        "route":
            route,

        "objective_cost":
            custo,

        "metodo":
            (
                "CP-SAT + "
                f"{resultado_inicial.get('metodo', 'heurística')}"
            ),

        "status":
            status_text,

        "gap_percent":
            float(
                gap
            ),

        "lower_bound":
            lower_bound,
    }


def otimizar_rota(
    matrix_m,
    start_idx,
    end_idx,
    modo="Rápido",
):
    """
    Otimiza a matriz recebida.

    Estratégia:

    - 2 a 12 locais:
      Held-Karp exato em qualquer modo.

    - acima de 12, modo Rápido:
      Guided Local Search.

    - 13 a 40, modo Preciso:
      três heurísticas + CP-SAT.

    - 41 a 100, modo Preciso:
      melhor das três heurísticas.
    """

    matrix = (
        _preparar_matriz(
            matrix_m
        )
    )

    n = len(
        matrix
    )

    _validar_indices(
        n,
        start_idx,
        end_idx,
    )

    if (
        n
        <=
        HELD_KARP_MAX_LOCATIONS
    ):
        return _held_karp(
            matrix,
            start_idx,
            end_idx,
        )

    modo_normalizado = str(
        modo
        or
        "Rápido"
    ).strip().lower()

    if modo_normalizado in (
        "rápido",
        "rapido",
    ):
        return _modo_rapido(
            matrix,
            start_idx,
            end_idx,
        )

    if (
        modo_normalizado
        !=
        "preciso"
    ):
        raise RouteOptimizationError(
            "Modo de otimização inválido."
        )

    melhor = (
        _melhor_heuristica_precisa(
            matrix,
            start_idx,
            end_idx,
        )
    )

    if (
        n
        >
        PRECISE_CP_SAT_MAX_LOCATIONS
    ):
        melhor = dict(
            melhor
        )

        melhor.update(
            {
                "status":
                    "MELHOR HEURÍSTICA",

                "gap_percent":
                    None,

                "lower_bound":
                    None,
            }
        )

        return melhor

    return _executar_cp_sat(
        matrix,
        start_idx,
        end_idx,
        melhor,
    )
