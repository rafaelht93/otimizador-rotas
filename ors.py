import time
import random
import requests


# ============================================================
# CONFIGURAÇÕES GERAIS
# ============================================================

PELIA_BASE_URL = (
    "https://api.heigit.org/pelias/v1"
)

ORS_BASE_URL = (
    "https://api.heigit.org/openrouteservice/v2"
)

DEFAULT_TIMEOUT = 20

MAX_RETRIES = 4

INITIAL_BACKOFF = 1.0


# ============================================================
# EXCEÇÃO PERSONALIZADA
# ============================================================

class ORSError(Exception):
    """
    Exceção usada para erros relacionados
    ao OpenRouteService / Pelias.
    """

    pass


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
                + random.uniform(
                    0,
                    0.5
                )
            )

            time.sleep(
                delay
            )

            backoff *= 2

            continue

        except requests.RequestException as e:

            tentativa += 1

            if tentativa > max_retries:

                raise ORSError(
                    "Não foi possível conectar ao serviço de rotas."
                ) from e

            delay = (
                backoff
                + random.uniform(
                    0,
                    0.5
                )
            )

            time.sleep(
                delay
            )

            backoff *= 2

            continue


        # ====================================================
        # SUCESSO
        # ====================================================

        if response.status_code < 400:

            return response


        # ====================================================
        # RATE LIMIT / ERRO TEMPORÁRIO
        # ====================================================

        if (
            response.status_code == 429
            or
            500 <= response.status_code < 600
        ):

            tentativa += 1

            if tentativa > max_retries:

                break

            retry_after = (
                response.headers.get(
                    "Retry-After"
                )
            )

            if retry_after:

                try:

                    delay = float(
                        retry_after
                    )

                except ValueError:

                    delay = (
                        backoff
                        + random.uniform(
                            0,
                            0.5
                        )
                    )

            else:

                delay = (
                    backoff
                    + random.uniform(
                        0,
                        0.5
                    )
                )

            time.sleep(
                delay
            )

            backoff *= 2

            continue


        # ====================================================
        # ERROS DEFINITIVOS
        # ====================================================

        break


    # ========================================================
    # TRATAMENTO DE ERROS HTTP
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
# HEADERS
# ============================================================

def _headers(
    api_key,
    accept="application/json"
):
    """
    Monta os headers das requisições.

    accept:
        application/json
            -> Matrix, Pelias etc.

        application/geo+json
            -> Directions no formato GeoJSON
    """

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
# NORMALIZA RESULTADO DO PELIAS
# ============================================================

def _normalizar_feature(
    feature,
    texto_original=None,
):

    geometry = (
        feature.get(
            "geometry"
        )
        or {}
    )

    coordinates = (
        geometry.get(
            "coordinates"
        )
    )


    if (
        not coordinates
        or
        len(coordinates) < 2
    ):

        return None


    longitude = (
        coordinates[0]
    )

    latitude = (
        coordinates[1]
    )


    properties = (
        feature.get(
            "properties"
        )
        or {}
    )


    label = (

        properties.get(
            "label"
        )

        or

        properties.get(
            "name"
        )

        or

        texto_original

        or

        "Local sem nome"
    )


    return {

        "label":
            str(
                label
            ),

        "latitude":
            float(
                latitude
            ),

        "longitude":
            float(
                longitude
            ),

        "name":
            properties.get(
                "name"
            ),

        "street":
            properties.get(
                "street"
            ),

        "housenumber":
            properties.get(
                "housenumber"
            ),

        "locality":
            properties.get(
                "locality"
            ),

        "region":
            properties.get(
                "region"
            ),

        "country":
            properties.get(
                "country"
            ),

        "layer":
            properties.get(
                "layer"
            ),
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
    """
    Pesquisa sugestões de endereço/local
    conforme o usuário digita.

    Retorna:

    [
        {
            "label": "...",
            "latitude": ...,
            "longitude": ...
        }
    ]
    """

    texto = (
        texto or ""
    ).strip()


    # Evita consultas muito curtas
    if len(texto) < 3:

        return []


    url = (
        f"{PELIA_BASE_URL}"
        f"/autocomplete"
    )


    params = {

        "text":
            texto,

        "size":
            int(
                limite
            ),

        # Tipos úteis para um planejador de rotas
        "layers":
            (
                "address,"
                "venue,"
                "street,"
                "locality,"
                "neighbourhood"
            ),
    }


    # ========================================================
    # RESTRINGE AO BRASIL
    # ========================================================

    if somente_brasil:

        params[
            "boundary.country"
        ] = "BR"


    # ========================================================
    # FOCO GEOGRÁFICO
    # ========================================================
    #
    # Se o usuário escolheu um ponto inicial,
    # damos preferência a resultados próximos dele.
    #
    # Exemplo:
    #
    # início = Brusque
    #
    # busca = Limeira
    #
    # resultados próximos de Brusque ganham prioridade.
    # ========================================================

    if (
        focus_lat is not None
        and
        focus_lon is not None
    ):

        params[
            "focus.point.lat"
        ] = float(
            focus_lat
        )

        params[
            "focus.point.lon"
        ] = float(
            focus_lon
        )


    response = _request_with_retries(

        "GET",

        url,

        headers=_headers(
            api_key
        ),

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

        resultado = (
            _normalizar_feature(
                feature,
                texto
            )
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
    """
    Busca normal do Pelias.

    Diferença para autocomplete:
    esta função é mais adequada para
    pesquisas completas.
    """

    texto = (
        texto or ""
    ).strip()


    if not texto:

        return []


    url = (
        f"{PELIA_BASE_URL}"
        f"/search"
    )


    params = {

        "text":
            texto,

        "size":
            int(
                limite
            ),

        "layers":
            (
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
        ] = float(
            focus_lat
        )

        params[
            "focus.point.lon"
        ] = float(
            focus_lon
        )


response = _request_with_retries(

    "POST",

    url,

    headers=_headers(
        api_key,
        accept="application/geo+json"
    ),

    json=payload,
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

        resultado = (
            _normalizar_feature(
                feature,
                texto
            )
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
    """
    Retorna somente o melhor resultado.

    Esta função não é usada no autocomplete,
    mas pode ser útil futuramente para:

    - arquivos CSV
    - planilhas
    - importação em lote
    """

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
# MATRIX API
# ============================================================

def obter_matriz_distancias(
    coordenadas,
    api_key,
    profile="driving-car",
):
    """
    Retorna matriz de distâncias em metros.

    coordenadas:

    [
        (lat, lon),
        (lat, lon),
        ...
    ]
    """

    if not coordenadas:

        raise ORSError(
            "Nenhuma coordenada foi informada."
        )


    n = len(
        coordenadas
    )


    if n * n > 3500:

        raise ORSError(
            f"{n} pontos geram "
            f"{n*n} pares na matriz, "
            f"acima do limite público."
        )


    url = (

        f"{ORS_BASE_URL}"
        f"/matrix/"
        f"{profile}"
    )


    # ORS usa:
    #
    # longitude, latitude

    locations = [

        [
            float(
                lon
            ),
            float(
                lat
            ),
        ]

        for lat, lon
        in coordenadas
    ]


    payload = {

        "locations":
            locations,

        "metrics":
            [
                "distance"
            ],

        "units":
            "m",
    }


    response = _request_with_retries(

        "POST",

        url,

        headers=_headers(
            api_key
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


    distances = (
        data.get(
            "distances"
        )
    )


    if distances is None:

        raise ORSError(
            "A Matrix API não retornou "
            "as distâncias."
        )


    return distances


# ============================================================
# DIRECTIONS API
# ============================================================

def obter_rota_completa(
    coordenadas,
    api_key,
    profile="driving-car",
):
    """
    Recebe as coordenadas JÁ NA ORDEM
    em que devem ser percorridas.

    Retorna:

    {
        "distancia_m": ...,
        "duracao_s": ...,
        "geometry": [...]
    }
    """

    if len(
        coordenadas
    ) < 2:

        raise ORSError(
            "A rota precisa ter "
            "pelo menos dois pontos."
        )


    if len(
        coordenadas
    ) > 50:

        raise ORSError(
            "A rota possui mais de "
            "50 waypoints."
        )


    url = (

        f"{ORS_BASE_URL}"
        f"/directions/"
        f"{profile}"
        f"/geojson"
    )


    coordinates = [

        [
            float(
                lon
            ),
            float(
                lat
            ),
        ]

        for lat, lon
        in coordenadas
    ]


    payload = {

        "coordinates":
            coordinates,

        "instructions":
            False,

        "preference":
            "recommended",
    }


    response = _request_with_retries(

        "POST",

        url,

        headers=_headers(
            api_key
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

        feature = (
            data[
                "features"
            ][0]
        )


        summary = (
            feature[
                "properties"
            ][
                "summary"
            ]
        )


        geometry = (
            feature[
                "geometry"
            ][
                "coordinates"
            ]
        )


    except (
        KeyError,
        IndexError,
        TypeError
    ) as e:

        raise ORSError(
            "A resposta do serviço de rota "
            "não contém os dados esperados."
        ) from e


    return {

        "distancia_m":
            float(
                summary[
                    "distance"
                ]
            ),

        "duracao_s":
            float(
                summary[
                    "duration"
                ]
            ),

        "geometry":
            geometry,

        "raw":
            data,
    }
