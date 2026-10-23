"""Extensões do Rotaki. Não executa rede nem banco ao importar.

A integração com APIs permanece no app.py; algoritmos são testáveis isoladamente.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

import requests

_COORD_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*[,;]\s*(-?\d+(?:[.,]\d+)?)\s*$")
_HORA_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validar_coordenadas(lat: Any, lon: Any) -> tuple[float, float]:
    lat, lon = float(lat), float(lon)
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude/longitude inválidas.")
    return lat, lon


def geoapify_buscar(texto, api_key, *, autocomplete=True, limite=8, lat=None, lon=None):
    """Geoapify opcional, campos compatíveis com o Pelias de ors.py."""
    if not api_key:
        return []
    endpoint = 'autocomplete' if autocomplete else 'search'
    parametros = {
        'text': str(texto).strip(), 'apiKey': api_key, 'format': 'json',
        'limit': min(max(int(limite), 1), 15), 'filter': 'countrycode:br', 'lang': 'pt',
    }
    if lat is not None and lon is not None:
        validar_coordenadas(lat, lon)
        parametros['bias'] = f'proximity:{float(lon)},{float(lat)}'
    resposta = requests.get(f'https://api.geoapify.com/v1/geocode/{endpoint}',
                            params=parametros, timeout=(3, 7))
    resposta.raise_for_status()
    encontrados = resposta.json().get('results') or []
    saida = []
    for r in encontrados:
        try:
            la, lo = validar_coordenadas(r['lat'], r['lon'])
        except (ValueError, TypeError, KeyError):
            continue
        rotulo = str(r.get('formatted') or r.get('address_line1') or texto).strip()
        saida.append({'label': rotulo, 'latitude': la, 'longitude': lo,
                      'nome': rotulo, 'endereco': rotulo, 'origem_geocoder': 'geoapify'})
    return saida


def minutos_hora(hora: str) -> int:
    if not isinstance(hora, str) or not _HORA_RE.fullmatch(hora):
        raise ValueError(f'Horário inválido: {hora!r}. Use HH:MM.')
    hh, mm = map(int, hora.split(':'))
    return 60 * hh + mm


def janela_local(local: dict) -> tuple[int, int, int]:
    inicio = str(local.get('janela_inicio') or '').strip()
    fim = str(local.get('janela_fim') or '').strip()
    servico = int(local.get('servico_min') or 0)
    if servico < 0 or servico > 480:
        raise ValueError('Tempo de atendimento deve estar entre 0 e 480 minutos.')
    if bool(inicio) != bool(fim):
        raise ValueError(f'Informe início e fim da janela de {local.get("nome") or "um local"}.')
    if not inicio:
        return 0, 48 * 60, servico
    a, b = minutos_hora(inicio), minutos_hora(fim)
    if b < a:
        raise ValueError('A janela de atendimento não pode terminar antes de começar.')
    return a, b, servico


def otimizar_janelas_locais(locais, custo_matrix, duracoes, *, voltar_inicio, partida='08:00', limite_s=15):
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    n = len(locais)
    if n < 2 or any(len(row) != n for row in custo_matrix) or any(len(row) != n for row in duracoes):
        raise ValueError('Matriz inválida para janelas de atendimento.')
    for row in duracoes:
        if any(v is None or not math.isfinite(float(v)) for v in row):
            raise ValueError('Existem trechos sem tempo de deslocamento conhecido.')
    fim = 0 if voltar_inicio else n - 1
    gerenciador = pywrapcp.RoutingIndexManager(n, 1, [0], [fim])
    solver = pywrapcp.RoutingModel(gerenciador)
    custos = [[max(0, round(float(x))) for x in row] for row in custo_matrix]
    minutos = [[max(0, math.ceil(float(x)/60)) for x in row] for row in duracoes]
    janelas = [janela_local(x) for x in locais]
    saida_min = minutos_hora(partida)

    def custo(i, j):
        return custos[gerenciador.IndexToNode(i)][gerenciador.IndexToNode(j)]
    ci = solver.RegisterTransitCallback(custo)
    solver.SetArcCostEvaluatorOfAllVehicles(ci)

    def transito(i, j):
        a, b = gerenciador.IndexToNode(i), gerenciador.IndexToNode(j)
        return minutos[a][b] + janelas[a][2]
    ti = solver.RegisterTransitCallback(transito)
    solver.AddDimension(ti, 48 * 60, 48 * 60, False, 'Tempo')
    tempo = solver.GetDimensionOrDie('Tempo')
    tempo.CumulVar(solver.Start(0)).SetRange(saida_min, saida_min)
    for pos in range(solver.Size()):
        node = gerenciador.IndexToNode(pos)
        if node == 0:
            continue
        a, b, _ = janelas[node]
        tempo.CumulVar(pos).SetRange(a, b)
    # Caso a rota termine no início, a janela de retorno não é a janela de saída.
    if not voltar_inicio:
        a, b, _ = janelas[fim]
        tempo.CumulVar(solver.End(0)).SetRange(a, b)
    else:
        tempo.CumulVar(solver.End(0)).SetRange(0, 48 * 60)
    parametros = pywrapcp.DefaultRoutingSearchParameters()
    parametros.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parametros.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parametros.time_limit.seconds = int(limite_s)
    solucao = solver.SolveWithParameters(parametros)
    if not solucao:
        raise ValueError('Não existe rota viável com as janelas de atendimento informadas. Ajuste horários ou ponto inicial.')
    rota, chegadas = [], []
    indice = solver.Start(0)
    while not solver.IsEnd(indice):
        rota.append(gerenciador.IndexToNode(indice))
        chegadas.append(solucao.Value(tempo.CumulVar(indice)))
        indice = solucao.Value(solver.NextVar(indice))
    rota.append(gerenciador.IndexToNode(indice))
    chegadas.append(solucao.Value(tempo.CumulVar(indice)))
    return {'route': rota, 'chegadas_min': chegadas, 'status': 'JANELAS_VIAVEIS',
            'metodo': 'OR-Tools Routing Time Windows', 'gap_percent': None}


def previsoes_ordem(locais, rota_indices, duracoes, partida='08:00', chegadas=None):
    """Agenda em ordem da rota; tempos estimados não equivalem a GPS ao vivo."""
    if len(locais) == 0 or len(rota_indices) == 0:
        return []
    t = minutos_hora(partida)
    resultado = []
    for pos, idx in enumerate(rota_indices):
        loc = locais[idx]
        if chegadas is not None:
            t = int(chegadas[pos])
        elif pos:
            anterior = rota_indices[pos - 1]
            servico = janela_local(locais[anterior])[2]
            t += math.ceil(float(duracoes[anterior][idx])/60) + servico
            a, _, _ = janela_local(loc)
            t = max(t, a)
        hh = (t // 60) % 24
        mm = t % 60
        resultado.append({'ordem': pos + 1, 'nome': str(loc.get('nome') or loc.get('label') or 'Local'),
                          'chegada': f'{hh:02d}:{mm:02d}', 'dia_adicional': t // 1440,
                          'minuto_absoluto': t})
    return resultado


def inverter_locais(locais, voltar_inicio):
    if voltar_inicio:
        raise ValueError('Para inverter uma rota, desative primeiro o retorno ao início.')
    return list(reversed(locais))


def nova_chave_compartilhamento():
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    return token, digest


def validar_ordem_ids(ids_antes, ids_depois):
    if len(ids_antes) != len(ids_depois) or set(ids_antes) != set(ids_depois):
        raise ValueError('A nova ordenação não corresponde às paradas pendentes.')
    if len(ids_depois) != len(set(ids_depois)):
        raise ValueError('Uma parada aparece mais de uma vez.')
    return True
