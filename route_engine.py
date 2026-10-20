import os

import numpy as np

from ortools.constraint_solver import (
    pywrapcp,
    routing_enums_pb2,
)

from ortools.sat.python import cp_model


# ============================================================
# CONFIGURAÇÕES
# ============================================================

# Modo rápido
FAST_TIME_SECONDS = 5

# Modo preciso:
# três heurísticas com este tempo para cada uma
PRECISE_HEURISTIC_TIME_SECONDS = 8

# Tempo máximo do CP-SAT
PRECISE_CP_SAT_TIME_SECONDS = 60

# Evita ocupar CPUs demais no servidor público
CPU_COUNT = max(
    1,
    min(
        os.cpu_count() or 1,
        8,
    ),
)


# ============================================================
# EXCEÇÃO
# ============================================================

class RouteOptimizationError(Exception):
    pass


# ============================================================
# MATRIZ
# ============================================================

def _preparar_matriz(matrix_m):

    try:
        matrix = np.asarray(
            matrix_m,
            dtype=float,
        )

    except Exception as e:
        raise RouteOptimizationError(
            "Não foi possível interpretar a matriz de distâncias."
        ) from e

    if (
        matrix.ndim != 2
        or matrix.shape[0] != matrix.shape[1]
    ):
        raise RouteOptimizationError(
            "A matriz de distâncias precisa ser quadrada."
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
            "A matriz possui distâncias negativas."
        )

    # OR-Tools trabalha melhor com inteiros
    return np.rint(
        matrix
    ).astype(
        np.int64
    )


# ============================================================
# CUSTO
# ============================================================

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


# ============================================================
# ROUTING SOLVER
# ============================================================

def _executar_metaheuristica(
    matrix,
    start_idx,
    end_idx,
    metaheuristica,
    nome,
    tempo,
):

    n = len(
        matrix
    )

    rota_fechada = (
        start_idx == end_idx
    )

    # ========================================================
    # MANAGER
    # ========================================================

    if rota_fechada:

        manager = (
            pywrapcp.RoutingIndexManager(
                n,
                1,
                start_idx,
            )
        )

    else:

        manager = (
            pywrapcp.RoutingIndexManager(
                n,
                1,
                [start_idx],
                [end_idx],
            )
        )

    routing = (
        pywrapcp.RoutingModel(
            manager
        )
    )

    # ========================================================
    # DISTÂNCIA
    # ========================================================

    def distance_callback(
        from_index,
        to_index,
    ):

        from_node = (
            manager.IndexToNode(
                from_index
            )
        )

        to_node = (
            manager.IndexToNode(
                to_index
            )
        )

        return int(
            matrix[
                from_node,
                to_node
            ]
        )

    callback_index = (
        routing.RegisterTransitCallback(
            distance_callback
        )
    )

    routing.SetArcCostEvaluatorOfAllVehicles(
        callback_index
    )

    # ========================================================
    # PARÂMETROS
    # ========================================================

    params = (
        pywrapcp.DefaultRoutingSearchParameters()
    )

    params.first_solution_strategy = (
        routing_enums_pb2
        .FirstSolutionStrategy
        .PATH_CHEAPEST_ARC
    )

    params.local_search_metaheuristic = (
        metaheuristica
    )

    params.time_limit.seconds = int(
        tempo
    )

    params.use_full_propagation = False

    params.log_search = False

    # ========================================================
    # SOLVER
    # ========================================================

    solution = (
        routing.SolveWithParameters(
            params
        )
    )

    if not solution:
        return None

    # ========================================================
    # RECONSTRUÇÃO
    # ========================================================

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

    custo_m = (
        _custo_rota(
            route,
            matrix,
        )
    )

    return {
        "route": route,
        "cost_m": custo_m,
        "distance_km": custo_m / 1000,
        "metodo": nome,
    }


# ============================================================
# MODO RÁPIDO
# ============================================================

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
            nome="Guided Local Search",
            tempo=FAST_TIME_SECONDS,
        )
    )

    if resultado is None:
        raise RouteOptimizationError(
            "Não foi possível encontrar uma rota válida."
        )

    resultado.update(
        {
            "status": "OTIMIZADA",
            "gap_percent": None,
            "lower_bound_km": None,
        }
    )

    return resultado


# ============================================================
# MELHOR HEURÍSTICA
# ============================================================

def _melhor_heuristica_precisa(
    matrix,
    start_idx,
    end_idx,
):

    estrategias = [
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

    resultados = []

    for nome, estrategia in estrategias:

        resultado = (
            _executar_metaheuristica(
                matrix=matrix,
                start_idx=start_idx,
                end_idx=end_idx,
                metaheuristica=estrategia,
                nome=nome,
                tempo=PRECISE_HEURISTIC_TIME_SECONDS,
            )
        )

        if resultado is not None:
            resultados.append(
                resultado
            )

    if not resultados:

        raise RouteOptimizationError(
            "Nenhuma estratégia conseguiu encontrar uma rota."
        )

    return min(
        resultados,
        key=lambda x: x["cost_m"],
    )


# ============================================================
# CP-SAT
# ============================================================

def _executar_cp_sat(
    matrix,
    start_idx,
    end_idx,
    resultado_inicial,
):

    n = len(
        matrix
    )

    rota_inicial = (
        resultado_inicial[
            "route"
        ]
    )

    custo_inicial = int(
        resultado_inicial[
            "cost_m"
        ]
    )

    rota_aberta = (
        start_idx != end_idx
    )

    # ========================================================
    # MODELO
    # ========================================================

    model = (
        cp_model.CpModel()
    )

    arc_vars = {}

    arcs = []

    # ========================================================
    # ARCOS REAIS
    # ========================================================

    for i in range(n):

        for j in range(n):

            if i == j:
                continue

            # Em uma rota aberta:
            #
            # START -> ... -> END
            #
            # nenhum arco real pode entrar em START
            # e nenhum arco real pode sair de END.
            if rota_aberta:

                if j == start_idx:
                    continue

                if i == end_idx:
                    continue

            var = model.NewBoolVar(
                f"x_{i}_{j}"
            )

            arc_vars[
                (i, j)
            ] = var

            arcs.append(
                (
                    i,
                    j,
                    var,
                )
            )

    # ========================================================
    # ROTA ABERTA
    # ========================================================
    #
    # AddCircuit trabalha com ciclos.
    #
    # Transformamos:
    #
    # START -> ... -> END
    #
    # em:
    #
    # START -> ... -> END -> START
    #
    # O último arco é artificial e possui custo zero.
    # ========================================================

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

    # ========================================================
    # CIRCUITO
    # ========================================================

    model.AddCircuit(
        arcs
    )

    # ========================================================
    # OBJETIVO
    # ========================================================

    objective = sum(
        int(
            matrix[i, j]
        ) * var
        for (
            i,
            j
        ), var in arc_vars.items()
    )

    # Já temos uma rota válida.
    # Não há motivo para aceitar algo pior.
    model.Add(
        objective <= custo_inicial
    )

    model.Minimize(
        objective
    )

    # ========================================================
    # HINT
    # ========================================================

    arcos_iniciais = set(
        zip(
            rota_inicial[:-1],
            rota_inicial[1:],
        )
    )

    for arco, var in arc_vars.items():

        model.AddHint(
            var,
            1
            if arco in arcos_iniciais
            else 0,
        )

    if arco_artificial is not None:

        model.AddHint(
            arco_artificial,
            1,
        )

    # ========================================================
    # SOLVER
    # ========================================================

    solver = (
        cp_model.CpSolver()
    )

    solver.parameters.max_time_in_seconds = (
        PRECISE_CP_SAT_TIME_SECONDS
    )

    solver.parameters.num_search_workers = (
        CPU_COUNT
    )

    solver.parameters.log_search_progress = (
        False
    )

    status = solver.Solve(
        model
    )

    # ========================================================
    # FALLBACK
    # ========================================================

    if status not in (
        cp_model.OPTIMAL,
        cp_model.FEASIBLE,
    ):

        resultado = dict(
            resultado_inicial
        )

        resultado.update(
            {
                "status": "MELHOR HEURÍSTICA",
                "gap_percent": None,
                "lower_bound_km": None,
            }
        )

        return resultado

    # ========================================================
    # ARCOS ESCOLHIDOS
    # ========================================================

    next_node = {}

    for (
        i,
        j
    ), var in arc_vars.items():

        if solver.BooleanValue(
            var
        ):

            next_node[i] = j

    # ========================================================
    # ROTA ABERTA
    # ========================================================

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
                    "Erro ao reconstruir a rota otimizada."
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
                    "Foi detectado um ciclo inesperado."
                )

        if (
            len(route) != n
            or
            len(set(route)) != n
        ):
            raise RouteOptimizationError(
                "A rota otimizada não contém todos os pontos."
            )

    # ========================================================
    # ROTA FECHADA
    # ========================================================

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
                    "Erro ao reconstruir o circuito."
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

        if (
            len(route) != n + 1
            or route[-1] != start_idx
            or len(set(route[:-1])) != n
        ):
            raise RouteOptimizationError(
                "O circuito não contém todos os pontos."
            )

    # ========================================================
    # RESULTADOS
    # ========================================================

    distancia_m = float(
        solver.ObjectiveValue()
    )

    lower_bound_m = float(
        solver.BestObjectiveBound()
    )

    distancia_km = (
        distancia_m / 1000
    )

    lower_bound_km = (
        lower_bound_m / 1000
    )

    if distancia_m > 0:

        gap = max(
            0.0,
            (
                distancia_m
                - lower_bound_m
            )
            / distancia_m
            * 100,
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
        "route": route,
        "cost_m": int(
            round(
                distancia_m
            )
        ),
        "distance_km": distancia_km,
        "metodo": "CP-SAT",
        "status": status_text,
        "gap_percent": gap,
        "lower_bound_km": lower_bound_km,
    }


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

def otimizar_rota(
    matrix_m,
    start_idx,
    end_idx,
    modo="Rápido",
):

    matrix = (
        _preparar_matriz(
            matrix_m
        )
    )

    n = len(
        matrix
    )

    if n < 2:

        raise RouteOptimizationError(
            "São necessários pelo menos dois locais."
        )

    if not (
        0 <= start_idx < n
    ):

        raise RouteOptimizationError(
            "Ponto inicial inválido."
        )

    if not (
        0 <= end_idx < n
    ):

        raise RouteOptimizationError(
            "Ponto final inválido."
        )

    # ========================================================
    # RÁPIDO
    # ========================================================

    if modo.lower() == "rápido":

        return _modo_rapido(
            matrix,
            start_idx,
            end_idx,
        )

    # ========================================================
    # PRECISO
    # ========================================================

    melhor = (
        _melhor_heuristica_precisa(
            matrix,
            start_idx,
            end_idx,
        )
    )

    return _executar_cp_sat(
        matrix,
        start_idx,
        end_idx,
        melhor,
    )
