import logging
import math
import random
import time
from urllib.parse import urlsplit

import requests


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

PELIAS_BASE_URL = "https://api.heigit.org/pelias/v1"
ORS_BASE_URL = "https://api.heigit.org/openrouteservice/v2"

# Compatibilidade com eventual código antigo que importe o nome incorreto.
PELIA_BASE_URL = PELIAS_BASE_URL

# requests aceita (connect_timeout, read_timeout).
AUTOCOMPLETE_TIMEOUT = (3, 6)
SEARCH_TIMEOUT = (4, 10)
ROUTING_TIMEOUT = (5, 25)

AUTOCOMPLETE_MAX_RETRIES = 0
SEARCH_MAX_RETRIES = 1
ROUTING_MAX_RETRIES = 2

INITIAL_BACKOFF = 1.0
MAX_RETRY_AFTER_SECONDS = 30.0

# Retentativas apenas para situações que normalmente são transitórias.
TRANSIENT_HTTP_STATUSES = {429, 502, 503, 504}

# Limites da API pública.
MATRIX_MAX_PAIRS_PER_REQUEST = 3500
MATRIX_BLOCK_SIZE = 50
DIRECTIONS_MAX_WAYPOINTS = 50

# Limite técnico atual do produto.
MAX_SUPPORTED_LOCATIONS = 100

# Autocomplete: reduz chamadas geradas durante a digitação.
AUTOCOMPLETE_MIN_CHARS = 4

# Simplificação local da geometria antes de devolvê-la ao app.
# Distância e duração continuam sendo os valores oficiais do ORS.
GEOMETRY_SIMPLIFY_TOLERANCE_M = 5.0


# ============================================================
# EXCEÇÃO
# ============================================================

class ORSError(Exception):
    pass


# ============================================================
# HELPERS DE LOG / ERRO
# ============================================================

def _endpoint_label(url):
    """Retorna apenas host + path, sem query string nem credenciais."""
    try:
        parsed = urlsplit(url)
        return f"{parsed.netloc}{parsed.path}"
    except Exception:
        return "endpoint-desconhecido"


def _extrair_erro_ors(response):
    """
    Extrai código e mensagem técnica retornados por ORS/HeiGIT.

    Nunca retorna a API key e não depende do texto bruto da resposta.
    """
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None, ""

    if not isinstance(data, dict):
        return None, ""

    error = data.get("error")

    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or ""
    elif error is not None:
        code = data.get("code")
        message = str(error)
    else:
        code = data.get("code")
        message = data.get("message") or ""

    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None

    return code, str(message or "")


def _mensagem_erro_ors(status, code):
    """Converte erros técnicos do ORS em mensagens úteis ao usuário."""
    if code in {2010, 6010, 8010}:
        return (
            "Um ou mais locais não ficam próximos de uma via roteável. "
            "Revise o endereço ou escolha um ponto mais próximo de uma rua acessível."
        )

    if code in {2009, 2016}:
        return (
            "Não foi possível encontrar uma rota entre alguns dos locais. "
            "Verifique se os pontos são acessíveis pelo perfil de veículo selecionado."
        )

    if code in {2004, 6004, 8004} or status == 413:
        return (
            "A consulta ultrapassou um limite do serviço de rotas. "
            "Reduza a quantidade ou a complexidade dos pontos e tente novamente."
        )

    if code in {2017, 6020}:
        return (
            "A rota ficou complexa demais para o serviço calcular. "
            "Revise os pontos ou divida a operação em rotas menores."
        )

    if status == 400:
        return "A consulta enviada ao serviço de rotas é inválida."

    if status == 401:
        return "A chave da API não foi aceita."

    if status == 403:
        return (
            "O serviço recusou a consulta. A chave pode estar sem permissão "
            "ou a cota disponível pode ter sido atingida."
        )

    if status == 404:
        return (
            "O serviço não encontrou resultado para a consulta. "
            "Revise os locais informados e tente novamente."
        )

    if status == 405:
        return "O método usado para consultar o serviço não é suportado."

    if status == 406:
        return (
            "O formato de resposta solicitado não foi aceito pelo serviço de rotas."
        )

    if status == 429:
        return (
            "O limite de consultas foi atingido ou há muitas requisições em sequência. "
            "Tente novamente em alguns instantes."
        )

    if status in {500, 501, 502, 503, 504}:
        return "O serviço de rotas está temporariamente indisponível."

    return "Não foi possível concluir a consulta ao serviço de rotas."


# ============================================================
# HEADERS
# ============================================================

def _headers(api_key, accept="application/json"):
    if not api_key:
        raise ORSError("A chave da API não foi informada.")

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "User-Agent": "otimizador-rotas/1.0",
    }

    if accept:
        headers["Accept"] = accept

    return headers


# ============================================================
# REQUEST COM RETRY CONTROLADO
# ============================================================

def _request_with_retries(
    method,
    url,
    *,
    headers=None,
    params=None,
    json=None,
    timeout=ROUTING_TIMEOUT,
    max_retries=ROUTING_MAX_RETRIES,
    retry_statuses=TRANSIENT_HTTP_STATUSES,
):
    tentativa = 0
    backoff = INITIAL_BACKOFF
    endpoint = _endpoint_label(url)

    while True:
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
            )

        except requests.Timeout as e:
            logger.warning(
                "Timeout ORS/HeiGIT endpoint=%s tentativa=%s/%s",
                endpoint,
                tentativa + 1,
                max_retries + 1,
            )

            if tentativa >= max_retries:
                raise ORSError(
                    "O serviço demorou demais para responder. Tente novamente."
                ) from e

            tentativa += 1
            delay = min(
                backoff + random.uniform(0, 0.5),
                MAX_RETRY_AFTER_SECONDS,
            )
            time.sleep(delay)
            backoff *= 2
            continue

        except requests.RequestException as e:
            logger.warning(
                "Falha de conexão ORS/HeiGIT endpoint=%s tentativa=%s/%s erro=%s",
                endpoint,
                tentativa + 1,
                max_retries + 1,
                type(e).__name__,
            )

            if tentativa >= max_retries:
                raise ORSError(
                    "Não foi possível conectar ao serviço de rotas. Tente novamente."
                ) from e

            tentativa += 1
            delay = min(
                backoff + random.uniform(0, 0.5),
                MAX_RETRY_AFTER_SECONDS,
            )
            time.sleep(delay)
            backoff *= 2
            continue

        if response.status_code < 400:
            return response

        status = response.status_code
        code, technical_message = _extrair_erro_ors(response)

        logger.warning(
            "Erro ORS/HeiGIT endpoint=%s status=%s code=%s tentativa=%s/%s mensagem=%s",
            endpoint,
            status,
            code,
            tentativa + 1,
            max_retries + 1,
            technical_message[:300],
        )

        if status in retry_statuses and tentativa < max_retries:
            tentativa += 1

            retry_after = response.headers.get("Retry-After")
            delay = None

            if retry_after:
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = None

            if delay is None:
                delay = backoff + random.uniform(0, 0.5)

            delay = max(0.0, min(delay, MAX_RETRY_AFTER_SECONDS))
            time.sleep(delay)
            backoff *= 2
            continue

        # O corpo técnico fica apenas no log; o usuário recebe mensagem segura.
        raise ORSError(_mensagem_erro_ors(status, code))


# ============================================================
# NORMALIZA RESULTADO DO PELIAS
# ============================================================

def _normalizar_feature(feature, texto_original=None):
    if not isinstance(feature, dict):
        return None

    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")

    if not coordinates or len(coordinates) < 2:
        return None

    longitude = coordinates[0]
    latitude = coordinates[1]
    properties = feature.get("properties") or {}

    label = (
        properties.get("label")
        or properties.get("name")
        or texto_original
        or "Local sem nome"
    )

    return {
        "label": str(label),
        "latitude": float(latitude),
        "longitude": float(longitude),
        "name": properties.get("name"),
        "street": properties.get("street"),
        "housenumber": properties.get("housenumber"),
        "locality": properties.get("locality"),
        "region": properties.get("region"),
        "country": properties.get("country"),
        "layer": properties.get("layer"),
    }


def _extrair_features_pelias(response):
    try:
        data = response.json()
    except ValueError as e:
        raise ORSError(
            "O serviço de busca retornou uma resposta inválida."
        ) from e

    if not isinstance(data, dict):
        raise ORSError(
            "O serviço de busca retornou uma resposta inválida."
        )

    features = data.get("features", [])

    if not isinstance(features, list):
        raise ORSError(
            "O serviço de busca retornou uma resposta inválida."
        )

    return features


# ============================================================
# AUTOCOMPLETE
# ============================================================

def autocomplete_enderecos(
    texto,
    api_key,
    limite=8,
    focus_lat=None,
    focus_lon=None,
    somente_brasil=True,
):
    texto = (texto or "").strip()

    # Reduz chamadas por digitação e consumo de quota.
    if len(texto) < AUTOCOMPLETE_MIN_CHARS:
        return []

    url = f"{PELIAS_BASE_URL}/autocomplete"

    params = {
        "text": texto,
        "size": int(limite),
        "layers": "address,venue,street,locality,neighbourhood",
    }

    if somente_brasil:
        params["boundary.country"] = "BR"

    if focus_lat is not None and focus_lon is not None:
        params["focus.point.lat"] = float(focus_lat)
        params["focus.point.lon"] = float(focus_lon)

    response = _request_with_retries(
        "GET",
        url,
        headers=_headers(api_key),
        params=params,
        timeout=AUTOCOMPLETE_TIMEOUT,
        max_retries=AUTOCOMPLETE_MAX_RETRIES,
    )

    resultados = []

    for feature in _extrair_features_pelias(response):
        resultado = _normalizar_feature(feature, texto)
        if resultado:
            resultados.append(resultado)

    return resultados


# ============================================================
# BUSCA NORMAL
# ============================================================

def buscar_enderecos(
    texto,
    api_key,
    limite=8,
    focus_lat=None,
    focus_lon=None,
    somente_brasil=True,
):
    texto = (texto or "").strip()

    if not texto:
        return []

    url = f"{PELIAS_BASE_URL}/search"

    params = {
        "text": texto,
        "size": int(limite),
        "layers": "address,venue,street,locality,neighbourhood",
    }

    if somente_brasil:
        params["boundary.country"] = "BR"

    if focus_lat is not None and focus_lon is not None:
        params["focus.point.lat"] = float(focus_lat)
        params["focus.point.lon"] = float(focus_lon)

    response = _request_with_retries(
        "GET",
        url,
        headers=_headers(api_key),
        params=params,
        timeout=SEARCH_TIMEOUT,
        max_retries=SEARCH_MAX_RETRIES,
    )

    resultados = []

    for feature in _extrair_features_pelias(response):
        resultado = _normalizar_feature(feature, texto)
        if resultado:
            resultados.append(resultado)

    return resultados


# ============================================================
# GEOCODIFICAÇÃO SIMPLES
# ============================================================

def geocodificar(
    texto,
    api_key,
    focus_lat=None,
    focus_lon=None,
    somente_brasil=True,
):
    resultados = buscar_enderecos(
        texto=texto,
        api_key=api_key,
        limite=1,
        focus_lat=focus_lat,
        focus_lon=focus_lon,
        somente_brasil=somente_brasil,
    )

    return resultados[0] if resultados else None


# ============================================================
# COORDENADAS
# ============================================================

def _normalizar_coordenadas(coordenadas):
    try:
        coordenadas = list(coordenadas)
    except TypeError as e:
        raise ORSError("As coordenadas informadas são inválidas.") from e

    normalizadas = []

    for indice, coordenada in enumerate(coordenadas, start=1):
        try:
            if coordenada is None or len(coordenada) < 2:
                raise ValueError

            lat = float(coordenada[0])
            lon = float(coordenada[1])
        except (TypeError, ValueError, IndexError) as e:
            raise ORSError(
                f"A coordenada do ponto {indice} é inválida."
            ) from e

        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not (-90 <= lat <= 90)
            or not (-180 <= lon <= 180)
        ):
            raise ORSError(
                f"A coordenada do ponto {indice} está fora dos limites válidos."
            )

        normalizadas.append((lat, lon))

    return normalizadas


# ============================================================
# MATRIX - VALIDAÇÃO
# ============================================================

def _validar_matriz(distancias, duracoes):
    if not isinstance(distancias, list) or not isinstance(duracoes, list):
        raise ORSError("A Matrix API retornou dados em formato inválido.")

    if len(distancias) != len(duracoes):
        raise ORSError("A Matrix API retornou dimensões inconsistentes.")

    for i, (linha_d, linha_t) in enumerate(zip(distancias, duracoes)):
        if not isinstance(linha_d, list) or not isinstance(linha_t, list):
            raise ORSError("A Matrix API retornou dados em formato inválido.")

        if len(linha_d) != len(linha_t):
            raise ORSError("A Matrix API retornou dimensões inconsistentes.")

        for j, (distancia, duracao) in enumerate(zip(linha_d, linha_t)):
            if distancia is None or duracao is None:
                raise ORSError(
                    f"Não foi possível encontrar uma rota entre os pontos "
                    f"{i + 1} e {j + 1}. Verifique se ambos ficam próximos "
                    "a vias acessíveis."
                )

            try:
                d = float(distancia)
                t = float(duracao)
            except (TypeError, ValueError) as e:
                raise ORSError(
                    "A Matrix API retornou um valor de distância/tempo inválido."
                ) from e

            if not math.isfinite(d) or not math.isfinite(t):
                raise ORSError(
                    f"Não foi possível calcular corretamente o trecho entre "
                    f"os pontos {i + 1} e {j + 1}."
                )


# ============================================================
# MATRIX - REQUISIÇÃO ÚNICA
# ============================================================

def _executar_matrix_request(
    coordenadas,
    api_key,
    profile,
    source_indices=None,
    destination_indices=None,
):
    url = f"{ORS_BASE_URL}/matrix/{profile}"

    locations = [
        [float(lon), float(lat)]
        for lat, lon in coordenadas
    ]

    payload = {
        "locations": locations,
        "metrics": ["distance", "duration"],
        "units": "m",
    }

    if source_indices is not None:
        payload["sources"] = [str(indice) for indice in source_indices]

    if destination_indices is not None:
        payload["destinations"] = [str(indice) for indice in destination_indices]

    response = _request_with_retries(
        "POST",
        url,
        headers=_headers(api_key, accept="application/json"),
        json=payload,
        timeout=ROUTING_TIMEOUT,
        max_retries=ROUTING_MAX_RETRIES,
    )

    try:
        data = response.json()
    except ValueError as e:
        raise ORSError("A Matrix API retornou uma resposta inválida.") from e

    if not isinstance(data, dict):
        raise ORSError("A Matrix API retornou uma resposta inválida.")

    distances = data.get("distances")
    durations = data.get("durations")

    if distances is None:
        raise ORSError("A Matrix API não retornou as distâncias.")

    if durations is None:
        raise ORSError("A Matrix API não retornou os tempos de deslocamento.")

    return {
        "distancias": distances,
        "duracoes": durations,
    }


# ============================================================
# MATRIX - BLOCO
# ============================================================

def _obter_bloco_matrix(
    coordenadas,
    source_indices,
    destination_indices,
    api_key,
    profile,
):
    indices_unicos = []
    indice_local = {}

    for indice_global in list(source_indices) + list(destination_indices):
        if indice_global not in indice_local:
            indice_local[indice_global] = len(indices_unicos)
            indices_unicos.append(indice_global)

    coordenadas_request = [
        coordenadas[indice]
        for indice in indices_unicos
    ]

    sources_request = [
        indice_local[indice]
        for indice in source_indices
    ]

    destinations_request = [
        indice_local[indice]
        for indice in destination_indices
    ]

    return _executar_matrix_request(
        coordenadas=coordenadas_request,
        api_key=api_key,
        profile=profile,
        source_indices=sources_request,
        destination_indices=destinations_request,
    )


# ============================================================
# MATRIX API
# ============================================================

def obter_matriz_distancias(
    coordenadas,
    api_key,
    profile="driving-car",
):
    coordenadas = _normalizar_coordenadas(coordenadas)

    if not coordenadas:
        raise ORSError("Nenhuma coordenada foi informada.")

    n = len(coordenadas)

    if n > MAX_SUPPORTED_LOCATIONS:
        raise ORSError(
            f"O sistema está preparado para até {MAX_SUPPORTED_LOCATIONS} locais por rota."
        )

    # Caminho de uma única requisição.
    if n * n <= MATRIX_MAX_PAIRS_PER_REQUEST:
        resultado = _executar_matrix_request(
            coordenadas=coordenadas,
            api_key=api_key,
            profile=profile,
        )
        _validar_matriz(resultado["distancias"], resultado["duracoes"])
        return resultado

    # Caminho em blocos.
    distancias = [[None for _ in range(n)] for _ in range(n)]
    duracoes = [[None for _ in range(n)] for _ in range(n)]

    blocos = [
        list(range(inicio, min(inicio + MATRIX_BLOCK_SIZE, n)))
        for inicio in range(0, n, MATRIX_BLOCK_SIZE)
    ]

    for source_indices in blocos:
        for destination_indices in blocos:
            quantidade_pares = len(source_indices) * len(destination_indices)

            if quantidade_pares > MATRIX_MAX_PAIRS_PER_REQUEST:
                raise ORSError(
                    "Um bloco da matriz ultrapassou o limite de combinações permitido."
                )

            bloco = _obter_bloco_matrix(
                coordenadas=coordenadas,
                source_indices=source_indices,
                destination_indices=destination_indices,
                api_key=api_key,
                profile=profile,
            )

            bloco_distancias = bloco["distancias"]
            bloco_duracoes = bloco["duracoes"]

            if (
                len(bloco_distancias) != len(source_indices)
                or len(bloco_duracoes) != len(source_indices)
            ):
                raise ORSError(
                    "A Matrix API retornou um bloco com dimensões inesperadas."
                )

            for linha_local, origem_global in enumerate(source_indices):
                linha_distancias = bloco_distancias[linha_local]
                linha_duracoes = bloco_duracoes[linha_local]

                if (
                    len(linha_distancias) != len(destination_indices)
                    or len(linha_duracoes) != len(destination_indices)
                ):
                    raise ORSError(
                        "A Matrix API retornou um bloco com dimensões inesperadas."
                    )

                for coluna_local, destino_global in enumerate(destination_indices):
                    distancias[origem_global][destino_global] = linha_distancias[coluna_local]
                    duracoes[origem_global][destino_global] = linha_duracoes[coluna_local]

    # A mesma validação é usada nos dois caminhos, inclusive para valores null.
    _validar_matriz(distancias, duracoes)

    return {
        "distancias": distancias,
        "duracoes": duracoes,
    }


# ============================================================
# GEOMETRIA - SIMPLIFICAÇÃO LOCAL
# ============================================================

def _projetar_ponto_metros(ponto, cos_lat0):
    lon, lat = float(ponto[0]), float(ponto[1])
    raio_terra = 6371008.8
    x = math.radians(lon) * raio_terra * cos_lat0
    y = math.radians(lat) * raio_terra
    return x, y


def _distancia_ponto_segmento_m(ponto, inicio, fim, cos_lat0):
    px, py = _projetar_ponto_metros(ponto, cos_lat0)
    ax, ay = _projetar_ponto_metros(inicio, cos_lat0)
    bx, by = _projetar_ponto_metros(fim, cos_lat0)

    dx = bx - ax
    dy = by - ay

    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))

    qx = ax + t * dx
    qy = ay + t * dy

    return math.hypot(px - qx, py - qy)


def _simplificar_geometry(geometry, tolerance_m=GEOMETRY_SIMPLIFY_TOLERANCE_M):
    """
    Douglas-Peucker iterativo em projeção local aproximada.

    Reduz o número de vértices que o app guarda/cacheia sem alterar
    distância ou duração calculadas pelo ORS.
    """
    if not geometry or len(geometry) <= 2 or tolerance_m <= 0:
        return geometry

    try:
        lat0 = sum(float(p[1]) for p in geometry) / len(geometry)
        cos_lat0 = max(abs(math.cos(math.radians(lat0))), 1e-6)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return geometry

    keep = [False] * len(geometry)
    keep[0] = True
    keep[-1] = True

    stack = [(0, len(geometry) - 1)]

    while stack:
        inicio_idx, fim_idx = stack.pop()

        if fim_idx <= inicio_idx + 1:
            continue

        maior_distancia = -1.0
        maior_indice = None

        inicio = geometry[inicio_idx]
        fim = geometry[fim_idx]

        for idx in range(inicio_idx + 1, fim_idx):
            distancia = _distancia_ponto_segmento_m(
                geometry[idx],
                inicio,
                fim,
                cos_lat0,
            )

            if distancia > maior_distancia:
                maior_distancia = distancia
                maior_indice = idx

        if maior_indice is not None and maior_distancia > tolerance_m:
            keep[maior_indice] = True
            stack.append((inicio_idx, maior_indice))
            stack.append((maior_indice, fim_idx))

    simplificada = [
        ponto
        for idx, ponto in enumerate(geometry)
        if keep[idx]
    ]

    # Defesa adicional: nunca devolver uma geometria inválida.
    if len(simplificada) < 2:
        return geometry

    logger.debug(
        "Geometria simplificada: %s -> %s vértices (tolerância %.1fm)",
        len(geometry),
        len(simplificada),
        tolerance_m,
    )

    return simplificada


# ============================================================
# DIRECTIONS - REQUISIÇÃO ÚNICA
# ============================================================

def _executar_directions_request(
    coordenadas,
    api_key,
    profile,
):
    if len(coordenadas) < 2:
        raise ORSError("Um trecho da rota precisa ter pelo menos dois pontos.")

    if len(coordenadas) > DIRECTIONS_MAX_WAYPOINTS:
        raise ORSError(
            "Um trecho da rota ultrapassou o limite de 50 pontos do Directions."
        )

    url = f"{ORS_BASE_URL}/directions/{profile}/geojson"

    coordinates = [
        [float(lon), float(lat)]
        for lat, lon in coordenadas
    ]

    payload = {
        "coordinates": coordinates,
        "instructions": False,
        "preference": "recommended",
    }

    response = _request_with_retries(
        "POST",
        url,
        headers=_headers(api_key, accept="application/geo+json"),
        json=payload,
        timeout=ROUTING_TIMEOUT,
        max_retries=ROUTING_MAX_RETRIES,
    )

    try:
        data = response.json()
    except ValueError as e:
        raise ORSError("A Directions API retornou uma resposta inválida.") from e

    try:
        feature = data["features"][0]
        summary = feature["properties"]["summary"]
        geometry = feature["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError) as e:
        raise ORSError(
            "A resposta do serviço de rota não contém os dados esperados."
        ) from e

    if not geometry:
        raise ORSError("A Directions API retornou uma rota sem geometria.")

    try:
        distancia_m = float(summary["distance"])
        duracao_s = float(summary["duration"])
    except (KeyError, TypeError, ValueError) as e:
        raise ORSError(
            "A Directions API retornou um resumo de rota inválido."
        ) from e

    # Não retornamos mais o GeoJSON bruto. O app não o utiliza e isso evita
    # serialização/cache desnecessários de estruturas grandes.
    return {
        "distancia_m": distancia_m,
        "duracao_s": duracao_s,
        "geometry": geometry,
    }


# ============================================================
# DIRECTIONS - JUNTA GEOMETRIAS
# ============================================================

def _juntar_geometry(geometry_total, geometry_trecho):
    if not geometry_trecho:
        return geometry_total

    if not geometry_total:
        geometry_total.extend(geometry_trecho)
        return geometry_total

    if geometry_total[-1] == geometry_trecho[0]:
        geometry_total.extend(geometry_trecho[1:])
    else:
        geometry_total.extend(geometry_trecho)

    return geometry_total


# ============================================================
# DIRECTIONS API
# ============================================================

def obter_rota_completa(
    coordenadas,
    api_key,
    profile="driving-car",
    simplificar_geometry=True,
    tolerance_m=GEOMETRY_SIMPLIFY_TOLERANCE_M,
):
    """
    Obtém distância, duração e geometria da rota completa.

    Rotas com mais de 50 pontos são divididas em trechos sobrepostos.
    A geometria final pode ser simplificada localmente para reduzir memória,
    cache e volume posteriormente salvo pelo app.
    """
    coordenadas = _normalizar_coordenadas(coordenadas)

    if len(coordenadas) < 2:
        raise ORSError("A rota precisa ter pelo menos dois pontos.")

    # Uma única chamada.
    if len(coordenadas) <= DIRECTIONS_MAX_WAYPOINTS:
        resultado = _executar_directions_request(
            coordenadas=coordenadas,
            api_key=api_key,
            profile=profile,
        )

        if simplificar_geometry:
            resultado["geometry"] = _simplificar_geometry(
                resultado["geometry"],
                tolerance_m=tolerance_m,
            )

        return resultado

    # Vários trechos.
    distancia_total = 0.0
    duracao_total = 0.0
    geometry_total = []

    inicio = 0
    total_pontos = len(coordenadas)

    while inicio < total_pontos - 1:
        fim_exclusivo = min(
            inicio + DIRECTIONS_MAX_WAYPOINTS,
            total_pontos,
        )

        trecho_coordenadas = coordenadas[inicio:fim_exclusivo]

        if len(trecho_coordenadas) < 2:
            break

        resultado_trecho = _executar_directions_request(
            coordenadas=trecho_coordenadas,
            api_key=api_key,
            profile=profile,
        )

        distancia_total += resultado_trecho["distancia_m"]
        duracao_total += resultado_trecho["duracao_s"]

        _juntar_geometry(
            geometry_total,
            resultado_trecho["geometry"],
        )

        if fim_exclusivo >= total_pontos:
            break

        # O último ponto do trecho anterior é o primeiro do próximo.
        inicio = fim_exclusivo - 1

    if not geometry_total:
        raise ORSError("Não foi possível montar a geometria completa da rota.")

    if simplificar_geometry:
        geometry_total = _simplificar_geometry(
            geometry_total,
            tolerance_m=tolerance_m,
        )

    return {
        "distancia_m": float(distancia_total),
        "duracao_s": float(duracao_total),
        "geometry": geometry_total,
    }
