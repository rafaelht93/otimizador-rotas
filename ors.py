import random
import time

import requests


# ============================================================
# CONFIGURAÇÕES
# ============================================================

PELIA_BASE_URL = "https://api.heigit.org/pelias/v1"

ORS_BASE_URL = (
    "https://api.heigit.org/openrouteservice/v2"
)

DEFAULT_TIMEOUT = 20
MAX_RETRIES = 4
INITIAL_BACKOFF = 1.0

# Limites usados para trabalhar com a API pública do ORS.
# A Matrix aceita até 3.500 combinações origem x destino
# por requisição. Usamos blocos conservadores de 50 x 50.
MATRIX_MAX_PAIRS_PER_REQUEST = 3500
MATRIX_BLOCK_SIZE = 50

# O Directions aceita no máximo 50 waypoints por requisição.
DIRECTIONS_MAX_WAYPOINTS = 50

# Limite técnico atual do produto.
# O app trabalha com até 100 locais únicos por rota.
MAX_SUPPORTED_LOCATIONS = 100


# ============================================================
# EXCEÇÃO
# ============================================================

class ORSError(Exception):
    pass


# ============================================================
# HEADERS
# ============================================================

def _headers(
    api_key,
    accept="application/json",
):

    if not api_key:
        raise ORSError(
            "A chave da API não foi informada."
        )

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "User-Agent": "otimizador-rotas/1.0",
    }

    if accept:
        headers["Accept"] = accept

    return headers


# ============================================================
# REQUEST COM RETRY
# ============================================================

def _request_with_retries(
    method,
    url,
    *,
    headers=None,
    params=None,
    json=None,
    timeout=DEFAULT_TIMEOUT,
    max_retries=MAX_RETRIES,
):

    tentativa = 0
    backoff = INITIAL_BACKOFF

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

            tentativa += 1

            if tentativa > max_retries:
                raise ORSError(
                    "O serviço demorou demais para responder."
                ) from e

            delay = (
                backoff
                + random.uniform(0, 0.5)
            )

            time.sleep(delay)
            backoff *= 2

            continue

        except requests.RequestException as e:

            tentativa += 1

            if tentativa > max_retries:
                raise ORSError(
                    "Não foi possível conectar ao serviço."
                ) from e

            delay = (
                backoff
                + random.uniform(0, 0.5)
            )

            time.sleep(delay)
            backoff *= 2

            continue


        # ====================================================
        # SUCESSO
        # ====================================================

        if response.status_code < 400:
            return response


        # ====================================================
        # ERROS TEMPORÁRIOS
        # ====================================================

        if (
            response.status_code == 429
            or
            500 <= response.status_code < 600
        ):

            tentativa += 1

            if tentativa > max_retries:
                break

            retry_after = response.headers.get(
                "Retry-After"
            )

            if retry_after:

                try:
                    delay = float(
                        retry_after
                    )

                except ValueError:
                    delay = (
                        backoff
                        + random.uniform(0, 0.5)
                    )

            else:
                delay = (
                    backoff
                    + random.uniform(0, 0.5)
                )

            time.sleep(delay)
            backoff *= 2

            continue

        break


    # ========================================================
    # ERROS HTTP
    # ========================================================

    status = response.status_code

    try:
        texto = response.text

    except Exception:
        texto = ""


    if status == 400:
        raise ORSError(
            "A consulta enviada ao serviço é inválida."
        )

    if status == 401:
        raise ORSError(
            "A chave da API não foi aceita."
        )

    if status == 403:
        raise ORSError(
            "A chave da API não possui acesso "
            "a este serviço."
        )

    if status == 404:
        raise ORSError(
            "O serviço solicitado não foi encontrado."
        )

    if status == 406:
        raise ORSError(
            "O formato de resposta solicitado não foi "
            "aceito pelo OpenRouteService."
        )

    if status == 429:
        raise ORSError(
            "O limite de consultas foi atingido. "
            "Tente novamente em alguns instantes."
        )

    if 500 <= status < 600:
        raise ORSError(
            "O serviço está temporariamente indisponível."
        )

    raise ORSError(
        f"Erro HTTP {status}: {texto[:300]}"
    )


# ============================================================
# NORMALIZA RESULTADO DO PELIAS
# ============================================================

def _normalizar_feature(
    feature,
    texto_original=None,
):

    geometry = (
        feature.get("geometry")
        or {}
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if (
        not coordinates
        or len(coordinates) < 2
    ):
        return None

    longitude = coordinates[0]
    latitude = coordinates[1]

    properties = (
        feature.get("properties")
        or {}
    )

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
        "housenumber": properties.get(
            "housenumber"
        ),
        "locality": properties.get("locality"),
        "region": properties.get("region"),
        "country": properties.get("country"),
        "layer": properties.get("layer"),
    }


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

    if len(texto) < 3:
        return []

    url = (
        f"{PELIA_BASE_URL}/autocomplete"
    )

    params = {
        "text": texto,
        "size": int(limite),
        "layers": (
            "address,"
            "venue,"
            "street,"
            "locality,"
            "neighbourhood"
        ),
    }

    if somente_brasil:
        params[
            "boundary.country"
        ] = "BR"

    if (
        focus_lat is not None
        and
        focus_lon is not None
    ):

        params[
            "focus.point.lat"
        ] = float(focus_lat)

        params[
            "focus.point.lon"
        ] = float(focus_lon)


    response = _request_with_retries(
        "GET",
        url,
        headers=_headers(api_key),
        params=params,
    )

    try:
        data = response.json()

    except ValueError as e:
        raise ORSError(
            "O serviço de busca retornou "
            "uma resposta inválida."
        ) from e


    resultados = []

    for feature in data.get(
        "features",
        []
    ):

        resultado = _normalizar_feature(
            feature,
            texto,
        )

        if resultado:
            resultados.append(
                resultado
            )

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

    url = (
        f"{PELIA_BASE_URL}/search"
    )

    params = {
        "text": texto,
        "size": int(limite),
        "layers": (
            "address,"
            "venue,"
            "street,"
            "locality,"
            "neighbourhood"
        ),
    }

    if somente_brasil:
        params[
            "boundary.country"
        ] = "BR"

    if (
        focus_lat is not None
        and
        focus_lon is not None
    ):

        params[
            "focus.point.lat"
        ] = float(focus_lat)

        params[
            "focus.point.lon"
        ] = float(focus_lon)


    response = _request_with_retries(
        "GET",
        url,
        headers=_headers(api_key),
        params=params,
    )

    try:
        data = response.json()

    except ValueError as e:
        raise ORSError(
            "O serviço de busca retornou "
            "uma resposta inválida."
        ) from e


    resultados = []

    for feature in data.get(
        "features",
        []
    ):

        resultado = _normalizar_feature(
            feature,
            texto,
        )

        if resultado:
            resultados.append(
                resultado
            )

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

    if not resultados:
        return None

    return resultados[0]


# ============================================================
# COORDENADAS
# ============================================================

def _normalizar_coordenadas(
    coordenadas,
):
    """
    Recebe coordenadas no formato usado pelo app:

        (latitude, longitude)

    e devolve uma lista de tuplas float no mesmo formato.
    """

    try:
        coordenadas = list(
            coordenadas
        )

    except TypeError as e:
        raise ORSError(
            "As coordenadas informadas são inválidas."
        ) from e

    normalizadas = []

    for indice, coordenada in enumerate(
        coordenadas,
        start=1,
    ):

        if (
            coordenada is None
            or len(coordenada) < 2
        ):
            raise ORSError(
                f"A coordenada do ponto {indice} é inválida."
            )

        try:
            lat = float(
                coordenada[0]
            )

            lon = float(
                coordenada[1]
            )

        except (
            TypeError,
            ValueError,
        ) as e:
            raise ORSError(
                f"A coordenada do ponto {indice} é inválida."
            ) from e

        if not (
            -90 <= lat <= 90
            and
            -180 <= lon <= 180
        ):
            raise ORSError(
                f"A coordenada do ponto {indice} está fora "
                "dos limites válidos de latitude/longitude."
            )

        normalizadas.append(
            (
                lat,
                lon,
            )
        )

    return normalizadas


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
    """
    Executa uma única chamada à Matrix API.

    coordenadas devem estar em (latitude, longitude).
    source_indices e destination_indices são índices relativos
    à lista coordenadas enviada nesta requisição.
    """

    url = (
        f"{ORS_BASE_URL}"
        f"/matrix/"
        f"{profile}"
    )

    locations = [
        [
            float(lon),
            float(lat),
        ]
        for lat, lon
        in coordenadas
    ]

    payload = {
        "locations": locations,
        "metrics": [
            "distance",
            "duration",
        ],
        "units": "m",
    }

    if source_indices is not None:
        payload["sources"] = [
            str(indice)
            for indice
            in source_indices
        ]

    if destination_indices is not None:
        payload["destinations"] = [
            str(indice)
            for indice
            in destination_indices
        ]

    response = _request_with_retries(
        "POST",
        url,
        headers=_headers(
            api_key,
            accept="application/json",
        ),
        json=payload,
    )

    try:
        data = response.json()

    except ValueError as e:
        raise ORSError(
            "A Matrix API retornou "
            "uma resposta inválida."
        ) from e

    distances = data.get(
        "distances"
    )

    durations = data.get(
        "durations"
    )

    if distances is None:
        raise ORSError(
            "A Matrix API não retornou "
            "as distâncias."
        )

    if durations is None:
        raise ORSError(
            "A Matrix API não retornou "
            "os tempos de deslocamento."
        )

    return {
        "distancias": distances,
        "duracoes": durations,
    }


# ============================================================
# MATRIX - MONTA BLOCO
# ============================================================

def _obter_bloco_matrix(
    coordenadas,
    source_indices,
    destination_indices,
    api_key,
    profile,
):
    """
    Monta uma requisição Matrix apenas com os pontos necessários
    para um bloco origem x destino.

    Isso permite montar matrizes maiores sem ultrapassar o limite
    de combinações por chamada da API pública.
    """

    indices_unicos = []
    indice_local = {}

    for indice_global in (
        list(source_indices)
        + list(destination_indices)
    ):

        if indice_global not in indice_local:
            indice_local[indice_global] = len(
                indices_unicos
            )

            indices_unicos.append(
                indice_global
            )

    coordenadas_request = [
        coordenadas[indice]
        for indice
        in indices_unicos
    ]

    sources_request = [
        indice_local[indice]
        for indice
        in source_indices
    ]

    destinations_request = [
        indice_local[indice]
        for indice
        in destination_indices
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
    """
    Retorna DUAS matrizes completas:

    distancias:
        metros

    duracoes:
        segundos

    Quando n x n permanece dentro do limite de
    3.500 combinações, a API é chamada uma única vez.

    Para matrizes maiores, o cálculo é dividido em blocos
    de até 50 origens x 50 destinos e os blocos são reunidos
    novamente em uma única matriz.

    O produto está preparado atualmente para até 100 locais.
    """

    coordenadas = _normalizar_coordenadas(
        coordenadas
    )

    if not coordenadas:
        raise ORSError(
            "Nenhuma coordenada foi informada."
        )

    n = len(
        coordenadas
    )

    if n > MAX_SUPPORTED_LOCATIONS:
        raise ORSError(
            f"O sistema está preparado para até "
            f"{MAX_SUPPORTED_LOCATIONS} locais por rota."
        )

    # ========================================================
    # MATRIZ CABE EM UMA ÚNICA REQUISIÇÃO
    # ========================================================

    if n * n <= MATRIX_MAX_PAIRS_PER_REQUEST:

        return _executar_matrix_request(
            coordenadas=coordenadas,
            api_key=api_key,
            profile=profile,
        )

    # ========================================================
    # MATRIZ EM BLOCOS
    # ========================================================

    distancias = [
        [None for _ in range(n)]
        for _ in range(n)
    ]

    duracoes = [
        [None for _ in range(n)]
        for _ in range(n)
    ]

    blocos = [
        list(
            range(
                inicio,
                min(
                    inicio + MATRIX_BLOCK_SIZE,
                    n,
                ),
            )
        )
        for inicio
        in range(
            0,
            n,
            MATRIX_BLOCK_SIZE,
        )
    ]

    for source_indices in blocos:

        for destination_indices in blocos:

            quantidade_pares = (
                len(source_indices)
                * len(destination_indices)
            )

            if (
                quantidade_pares
                > MATRIX_MAX_PAIRS_PER_REQUEST
            ):
                raise ORSError(
                    "Um bloco da matriz ultrapassou "
                    "o limite de combinações permitido."
                )

            bloco = _obter_bloco_matrix(
                coordenadas=coordenadas,
                source_indices=source_indices,
                destination_indices=destination_indices,
                api_key=api_key,
                profile=profile,
            )

            bloco_distancias = bloco[
                "distancias"
            ]

            bloco_duracoes = bloco[
                "duracoes"
            ]

            if (
                len(bloco_distancias)
                != len(source_indices)
                or
                len(bloco_duracoes)
                != len(source_indices)
            ):
                raise ORSError(
                    "A Matrix API retornou um bloco "
                    "com dimensões inesperadas."
                )

            for linha_local, origem_global in enumerate(
                source_indices
            ):

                linha_distancias = bloco_distancias[
                    linha_local
                ]

                linha_duracoes = bloco_duracoes[
                    linha_local
                ]

                if (
                    len(linha_distancias)
                    != len(destination_indices)
                    or
                    len(linha_duracoes)
                    != len(destination_indices)
                ):
                    raise ORSError(
                        "A Matrix API retornou um bloco "
                        "com dimensões inesperadas."
                    )

                for coluna_local, destino_global in enumerate(
                    destination_indices
                ):

                    distancias[
                        origem_global
                    ][
                        destino_global
                    ] = linha_distancias[
                        coluna_local
                    ]

                    duracoes[
                        origem_global
                    ][
                        destino_global
                    ] = linha_duracoes[
                        coluna_local
                    ]

    # ========================================================
    # VALIDA RESULTADO FINAL
    # ========================================================

    for i in range(n):

        for j in range(n):

            if (
                distancias[i][j] is None
                or
                duracoes[i][j] is None
            ):
                raise ORSError(
                    "Não foi possível calcular todos os "
                    "trechos da matriz da rota."
                )

    return {
        "distancias": distancias,
        "duracoes": duracoes,
    }


# ============================================================
# DIRECTIONS - REQUISIÇÃO ÚNICA
# ============================================================

def _executar_directions_request(
    coordenadas,
    api_key,
    profile,
):
    """
    Executa uma única chamada ao Directions.

    A lista deve conter entre 2 e 50 pontos.
    """

    if len(coordenadas) < 2:
        raise ORSError(
            "Um trecho da rota precisa ter pelo menos "
            "dois pontos."
        )

    if len(coordenadas) > DIRECTIONS_MAX_WAYPOINTS:
        raise ORSError(
            "Um trecho da rota ultrapassou o limite "
            "de 50 pontos do Directions."
        )

    url = (
        f"{ORS_BASE_URL}"
        f"/directions/"
        f"{profile}"
        f"/geojson"
    )

    coordinates = [
        [
            float(lon),
            float(lat),
        ]
        for lat, lon
        in coordenadas
    ]

    payload = {
        "coordinates": coordinates,
        "instructions": False,
        "preference": "recommended",
    }

    response = _request_with_retries(
        "POST",
        url,
        headers=_headers(
            api_key,
            accept="application/geo+json",
        ),
        json=payload,
    )

    try:
        data = response.json()

    except ValueError as e:
        raise ORSError(
            "A Directions API retornou "
            "uma resposta inválida."
        ) from e

    try:

        feature = data[
            "features"
        ][0]

        summary = feature[
            "properties"
        ][
            "summary"
        ]

        geometry = feature[
            "geometry"
        ][
            "coordinates"
        ]

    except (
        KeyError,
        IndexError,
        TypeError,
    ) as e:

        raise ORSError(
            "A resposta do serviço de rota "
            "não contém os dados esperados."
        ) from e

    if not geometry:
        raise ORSError(
            "A Directions API retornou uma rota "
            "sem geometria."
        )

    try:
        distancia_m = float(
            summary["distance"]
        )

        duracao_s = float(
            summary["duration"]
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        raise ORSError(
            "A Directions API retornou um resumo "
            "de rota inválido."
        ) from e

    return {
        "distancia_m": distancia_m,
        "duracao_s": duracao_s,
        "geometry": geometry,
        "raw": data,
    }


# ============================================================
# DIRECTIONS - JUNTA GEOMETRIAS
# ============================================================

def _juntar_geometry(
    geometry_total,
    geometry_trecho,
):

    if not geometry_trecho:
        return geometry_total

    if not geometry_total:
        geometry_total.extend(
            geometry_trecho
        )
        return geometry_total

    if (
        geometry_total[-1]
        == geometry_trecho[0]
    ):
        geometry_total.extend(
            geometry_trecho[1:]
        )

    else:
        geometry_total.extend(
            geometry_trecho
        )

    return geometry_total


# ============================================================
# DIRECTIONS API
# ============================================================

def obter_rota_completa(
    coordenadas,
    api_key,
    profile="driving-car",
):
    """
    Obtém a geometria, a distância e a duração da rota completa.

    O Directions público aceita até 50 pontos por chamada.

    Quando a sequência possui mais pontos, ela é dividida em
    trechos sobrepostos:

        trecho 1: pontos 1..50
        trecho 2: pontos 50..99
        trecho 3: pontos 99.....

    O último ponto de um trecho é repetido como primeiro ponto
    do trecho seguinte, garantindo continuidade da rota.

    Isso permite, por exemplo:

        - rota aberta com 100 locais;
        - rota fechada com 100 locais + retorno ao início
          (101 coordenadas na sequência final).
    """

    coordenadas = _normalizar_coordenadas(
        coordenadas
    )

    if len(coordenadas) < 2:
        raise ORSError(
            "A rota precisa ter pelo menos "
            "dois pontos."
        )

    # ========================================================
    # CABE EM UMA ÚNICA CHAMADA
    # ========================================================

    if (
        len(coordenadas)
        <= DIRECTIONS_MAX_WAYPOINTS
    ):

        return _executar_directions_request(
            coordenadas=coordenadas,
            api_key=api_key,
            profile=profile,
        )

    # ========================================================
    # ROTA EM TRECHOS
    # ========================================================

    distancia_total = 0.0
    duracao_total = 0.0
    geometry_total = []
    partes_raw = []

    inicio = 0

    total_pontos = len(
        coordenadas
    )

    while inicio < total_pontos - 1:

        fim_exclusivo = min(
            inicio + DIRECTIONS_MAX_WAYPOINTS,
            total_pontos,
        )

        trecho_coordenadas = coordenadas[
            inicio:
            fim_exclusivo
        ]

        if len(trecho_coordenadas) < 2:
            break

        resultado_trecho = (
            _executar_directions_request(
                coordenadas=trecho_coordenadas,
                api_key=api_key,
                profile=profile,
            )
        )

        distancia_total += resultado_trecho[
            "distancia_m"
        ]

        duracao_total += resultado_trecho[
            "duracao_s"
        ]

        _juntar_geometry(
            geometry_total,
            resultado_trecho[
                "geometry"
            ],
        )

        partes_raw.append(
            resultado_trecho[
                "raw"
            ]
        )

        if fim_exclusivo >= total_pontos:
            break

        # O último ponto do trecho anterior vira o primeiro
        # do próximo trecho para manter a continuidade.
        inicio = (
            fim_exclusivo - 1
        )

    if not geometry_total:
        raise ORSError(
            "Não foi possível montar a geometria "
            "completa da rota."
        )

    # Mantém uma estrutura semelhante ao GeoJSON original
    # para não quebrar eventual uso futuro do campo raw.
    raw_combinado = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "summary": {
                        "distance": distancia_total,
                        "duration": duracao_total,
                    },
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": geometry_total,
                },
            }
        ],
        "parts": partes_raw,
    }

    return {
        "distancia_m": float(
            distancia_total
        ),
        "duracao_s": float(
            duracao_total
        ),
        "geometry": geometry_total,
        "raw": raw_combinado,
    }
