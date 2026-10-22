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

# Tempo usado no modo rápido.
FAST_TIME_SECONDS = 5

# Tempo de cada metaheurística no modo preciso.
PRECISE_HEURISTIC_TIME_SECONDS = 8

# Tempo máximo adicional do CP-SAT.
PRECISE_CP_SAT_TIME_SECONDS = 60

# Limite técnico atual do sistema.
MAX_SUPPORTED_LOCATIONS = 100

# Acima deste número de locais, o modo Preciso continua
# executando as três metaheurísticas, mas não executa o CP-SAT.
#
# O CP-SAT cria aproximadamente N² variáveis booleanas.
# Em rotas muito grandes isso pode consumir muita memória e CPU,
# especialmente no Streamlit Community Cloud.
PRECISE_CP_SAT_MAX_LOCATIONS = 60

# Limita o número de CPUs para não sobrecarregar
# o servidor do Streamlit.
CPU_COUNT = max(
    1,
    min(
        os.cpu_count() or 1,
        8,
    ),
)


# ============================================================
# EXCEÇÃO PERSONALIZADA
# ============================================================

class RouteOptimizationError(Exception):
    pass


# ============================================================
# PREPARAÇÃO DA MATRIZ
# ============================================================

def _preparar_matriz(matrix):
    """
    Converte a matriz recebida para inteiros.

    A matriz pode representar:

    - metros, quando otimizamos distância;
    - segundos, quando otimizamos tempo.

    Para o algoritmo isso não faz diferença:
    ele simplesmente minimiza os valores.
    """

    try:

        matrix = np.asarray(
            matrix,
            dtype=float,
        )

    except Exception as e:

        raise RouteOptimizationError(
            "Não foi possível interpretar "
            "a matriz de otimização."
        ) from e


    # ========================================================
    # MATRIZ QUADRADA
    # ========================================================

    if (
        matrix.ndim != 2
        or
        matrix.shape[0] != matrix.shape[1]
    ):

        raise RouteOptimizationError(
            "A matriz precisa ser quadrada."
        )


    # ========================================================
    # MATRIZ VAZIA
    # ========================================================

    if matrix.shape[0] == 0:

        raise RouteOptimizationError(
            "A matriz de otimização está vazia."
        )


    # ========================================================
    # VALORES INVÁLIDOS
    # ========================================================

    if not np.all(
        np.isfinite(matrix)
    ):

        raise RouteOptimizationError(
            "A matriz possui trechos inválidos "
            "ou inacessíveis."
        )


    # ========================================================
    # VALORES NEGATIVOS
    # ========================================================

    if np.any(
        matrix < 0
    ):

        raise RouteOptimizationError(
            "A matriz possui valores negativos."
        )


    # ========================================================
    # CONVERSÃO
    # ========================================================

    # OR-Tools trabalha melhor com inteiros.
    return np.rint(
        matrix
    ).astype(
        np.int64
    )


# ============================================================
# CALCULA CUSTO DA ROTA
# ============================================================

def _custo_rota(
    route,
    matrix,
):
    """
    Soma o custo de todos os arcos da rota.
    """

    return sum(
        int(
            matrix[i, j]
        )
        for i, j
        in zip(
            route[:-1],
            route[1:],
        )
    )


# ============================================================
# VALIDA RESULTADO DA ROTA
# ============================================================

def _validar_rota_resultante(
    route,
    n,
    start_idx,
    end_idx,
):
    """
    Confirma que a solução contém todos os locais
    exatamente uma vez, respeitando início e fim.

    Em rota fechada, o ponto inicial aparece novamente
    no último elemento.
    """

    rota_fechada = (
        start_idx == end_idx
    )


    if rota_fechada:

        if len(route) != n + 1:

            raise RouteOptimizationError(
                "A rota fechada não contém "
                "a quantidade esperada de pontos."
            )

        if (
            route[0] != start_idx
            or
            route[-1] != start_idx
        ):

            raise RouteOptimizationError(
                "A rota fechada não começa e termina "
                "no ponto inicial."
            )

        if len(
            set(
                route[:-1]
            )
        ) != n:

            raise RouteOptimizationError(
                "A rota fechada não contém "
                "todos os locais."
            )


    else:

        if len(route) != n:

            raise RouteOptimizationError(
                "A rota aberta não contém "
                "a quantidade esperada de locais."
            )

        if route[0] != start_idx:

            raise RouteOptimizationError(
                "A rota não começa no ponto definido."
            )

        if route[-1] != end_idx:

            raise RouteOptimizationError(
                "A rota não termina no ponto definido."
            )

        if len(
            set(
                route
            )
        ) != n:

            raise RouteOptimizationError(
                "A rota não contém todos os locais."
            )


# ============================================================
# SOLVER ROUTING
# ============================================================

def _executar_metaheuristica(
    matrix,
    start_idx,
    end_idx,
    metaheuristica,
    nome,
    tempo,
):
    """
    Executa uma estratégia do OR-Tools Routing.
    """

    n = len(
        matrix
    )

    rota_fechada = (
        start_idx == end_idx
    )


    # ========================================================
    # ROUTING INDEX MANAGER
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
    # CALLBACK DE CUSTO
    # ========================================================

    def cost_callback(
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
            cost_callback
        )
    )


    routing.SetArcCostEvaluatorOfAllVehicles(
        callback_index
    )


    # ========================================================
    # PARÂMETROS DE BUSCA
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
    # EXECUÇÃO
    # ========================================================

    solution = (
        routing.SolveWithParameters(
            params
        )
    )


    if not solution:
        return None


    # ========================================================
    # RECONSTRUÇÃO DA ROTA
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


    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    _validar_rota_resultante(
        route=route,
        n=n,
        start_idx=start_idx,
        end_idx=end_idx,
    )


    # ========================================================
    # CUSTO
    # ========================================================

    custo = (
        _custo_rota(
            route,
            matrix,
        )
    )


    return {
        "route": route,
        "objective_cost": custo,
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
    """
    Usa Guided Local Search.

    É rápido e normalmente encontra soluções
    muito boas para uso cotidiano.
    """

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
            "Não foi possível encontrar "
            "uma rota válida."
        )


    resultado.update(
        {
            "status": "OTIMIZADA",
            "gap_percent": None,
            "lower_bound": None,
        }
    )


    return resultado


# ============================================================
# MODO PRECISO - HEURÍSTICAS
# ============================================================

def _melhor_heuristica_precisa(
    matrix,
    start_idx,
    end_idx,
):
    """
    Testa três metaheurísticas diferentes
    e mantém o melhor resultado.
    """

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
        key=lambda x: x[
            "objective_cost"
        ],
    )


# ============================================================
# RESULTADO PRECISO SEM CP-SAT
# ============================================================

def _resultado_preciso_heuristico(
    resultado,
):
    """
    Usado em rotas grandes.

    O modo Preciso continua comparando três metaheurísticas,
    mas não executa CP-SAT para evitar consumo excessivo
    de CPU e memória.
    """

    resultado_final = dict(
        resultado
    )


    resultado_final.update(
        {
            "status":
                "MELHOR HEURÍSTICA",

            "gap_percent":
                None,

            "lower_bound":
                None,
        }
    )


    return resultado_final


# ============================================================
# CP-SAT
# ============================================================

def _executar_cp_sat(
    matrix,
    start_idx,
    end_idx,
    resultado_inicial,
):
    """
    Usa o resultado heurístico como limite superior
    e tenta melhorar ou comprovar a solução.

    Essa etapa é utilizada apenas em rotas com tamanho
    compatível com PRECISE_CP_SAT_MAX_LOCATIONS.
    """

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
            "objective_cost"
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


            # Em rota aberta:
            #
            # START -> ... -> END
            #
            # não permitimos:
            #
            # algo -> START
            # END -> algo
            #
            # porque depois criaremos artificialmente:
            #
            # END -> START
            #
            # com custo zero.

            if rota_aberta:

                if j == start_idx:
                    continue

                if i == end_idx:
                    continue


            var = (
                model.NewBoolVar(
                    f"x_{i}_{j}"
                )
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
    # ARCO ARTIFICIAL PARA ROTA ABERTA
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
        ), var
        in arc_vars.items()
    )


    # Já temos uma solução válida.
    # Não aceitamos uma solução pior.
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

        if arco in arcos_iniciais:

            valor_hint = 1

        else:

            valor_hint = 0


        model.AddHint(
            var,
            valor_hint,
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


    status = (
        solver.Solve(
            model
        )
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
                "status":
                    "MELHOR HEURÍSTICA",

                "gap_percent":
                    None,

                "lower_bound":
                    None,
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
    # RECONSTRÓI ROTA ABERTA
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
                    "Foi detectado um ciclo inesperado."
                )


    # ========================================================
    # RECONSTRÓI ROTA FECHADA
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


    # ========================================================
    # VALIDA ROTA
    # ========================================================

    _validar_rota_resultante(
        route=route,
        n=n,
        start_idx=start_idx,
        end_idx=end_idx,
    )


    # ========================================================
    # RESULTADOS DO CP-SAT
    # ========================================================

    custo = float(
        solver.ObjectiveValue()
    )


    lower_bound = float(
        solver.BestObjectiveBound()
    )


    # ========================================================
    # GAP
    # ========================================================

    if custo > 0:

        gap = max(
            0.0,
            (
                custo
                - lower_bound
            )
            / custo
            * 100,
        )

    else:

        gap = 0.0


    # ========================================================
    # STATUS
    # ========================================================

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
        "objective_cost": custo,
        "metodo": "CP-SAT",
        "status": status_text,
        "gap_percent": gap,
        "lower_bound": lower_bound,
    }


# ============================================================
# FUNÇÃO PÚBLICA PRINCIPAL
# ============================================================

def otimizar_rota(
    matrix_m,
    start_idx,
    end_idx,
    modo="Rápido",
):
    """
    Função usada pelo app.py.

    matrix_m pode conter:

    - distâncias em metros;
    - durações em segundos.

    O algoritmo minimiza a matriz recebida.

    Atualmente suporta até 100 locais.

    Modos:

    Rápido:
        Guided Local Search.

    Preciso:
        compara Guided Local Search,
        Tabu Search e Simulated Annealing.

        Para rotas de até
        PRECISE_CP_SAT_MAX_LOCATIONS,
        tenta adicionalmente melhorar/comprovar
        a solução com CP-SAT.

        Para rotas maiores, retorna diretamente
        a melhor das três metaheurísticas.
    """

    matrix = (
        _preparar_matriz(
            matrix_m
        )
    )


    n = len(
        matrix
    )


    # ========================================================
    # VALIDAÇÕES
    # ========================================================

    if n < 2:

        raise RouteOptimizationError(
            "São necessários pelo menos "
            "dois locais."
        )


    if n > MAX_SUPPORTED_LOCATIONS:

        raise RouteOptimizationError(
            f"O sistema suporta atualmente até "
            f"{MAX_SUPPORTED_LOCATIONS} locais "
            "por rota."
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


    modo_normalizado = (
        str(
            modo
            or
            ""
        )
        .strip()
        .lower()
    )


    # Aceita também a versão sem acento,
    # caso algum dado antigo utilize "Rapido".
    if modo_normalizado in (
        "rápido",
        "rapido",
    ):

        return _modo_rapido(
            matrix,
            start_idx,
            end_idx,
        )


    if modo_normalizado not in (
        "preciso",
        "precisa",
    ):

        raise RouteOptimizationError(
            "Modo de otimização inválido."
        )


    # ========================================================
    # MODO PRECISO
    # ========================================================

    melhor = (
        _melhor_heuristica_precisa(
            matrix,
            start_idx,
            end_idx,
        )
    )


    # ========================================================
    # ROTAS GRANDES
    # ========================================================

    # Acima do limite configurado não executamos CP-SAT.
    #
    # Em uma rota de 100 locais o modelo completo teria
    # aproximadamente 9.900 arcos/variáveis possíveis.
    # No Streamlit isso pode gerar consumo desnecessário
    # de memória e CPU.
    #
    # As três metaheurísticas continuam sendo executadas,
    # portanto o modo Preciso continua sendo superior
    # ao modo Rápido nesses casos.
    if (
        n
        > PRECISE_CP_SAT_MAX_LOCATIONS
    ):

        return (
            _resultado_preciso_heuristico(
                melhor
            )
        )


    # ========================================================
    # CP-SAT PARA ROTAS MENORES
    # ========================================================

    return _executar_cp_sat(
        matrix,
        start_idx,
        end_idx,
        melhor,
    )
