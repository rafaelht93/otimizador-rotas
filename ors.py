import requests


# ============================================================
# ENDPOINTS
# ============================================================

PELIA_BASE_URL = "https://api.heigit.org/pelias/v1"


# ============================================================
# EXCEÇÃO PERSONALIZADA
# ============================================================

class ORSError(Exception):
    pass


# ============================================================
# GEOCODIFICAÇÃO
# ============================================================

def buscar_endereco(
    texto,
    api_key,
    limite=5,
    somente_brasil=True
):
    """
    Pesquisa um endereço/local pelo texto informado.

    Retorna uma lista no formato:

    [
        {
            "nome": "...",
            "latitude": ...,
            "longitude": ...
        }
    ]
    """

    texto = texto.strip()

    if not texto:
        return []

    url = f"{PELIA_BASE_URL}/search"

    params = {
        "text": texto,
        "size": limite,
    }

    # Restringe os resultados ao Brasil.
    # Isso melhora bastante pesquisas como:
    # "Havan Brusque"
    # "Rua João Bauer 100"
    if somente_brasil:
        params["boundary.country"] = "BR"

    headers = {
        "Authorization": api_key,
        "Accept": "application/json"
    }

    try:

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20
        )

    except requests.Timeout as e:

        raise ORSError(
            "O serviço de localização demorou "
            "demais para responder."
        ) from e

    except requests.RequestException as e:

        raise ORSError(
            "Não foi possível conectar ao "
            "serviço de localização."
        ) from e


    # ========================================================
    # ERROS HTTP
    # ========================================================

    if response.status_code == 401:

        raise ORSError(
            "A chave do OpenRouteService não foi aceita."
        )

    if response.status_code == 403:

        raise ORSError(
            "A chave do OpenRouteService não possui "
            "acesso ao serviço de geocodificação."
        )

    if response.status_code == 429:

        raise ORSError(
            "O limite de consultas do OpenRouteService "
            "foi atingido. Tente novamente mais tarde."
        )

    if response.status_code >= 400:

        raise ORSError(
            f"Erro do OpenRouteService "
            f"(HTTP {response.status_code})."
        )


    # ========================================================
    # JSON
    # ========================================================

    try:

        data = response.json()

    except ValueError as e:

        raise ORSError(
            "O serviço de localização retornou "
            "uma resposta inválida."
        ) from e


    # ========================================================
    # CONVERTE RESULTADOS
    # ========================================================

    resultados = []

    for feature in data.get(
        "features",
        []
    ):

        try:

            longitude, latitude = (
                feature[
                    "geometry"
                ][
                    "coordinates"
                ]
            )

        except (
            KeyError,
            ValueError,
            TypeError
        ):
            continue


        properties = feature.get(
            "properties",
            {}
        )


        # Pelias normalmente retorna um label bem completo.
        nome = (
            properties.get("label")
            or
            properties.get("name")
            or
            texto
        )


        resultados.append(
            {
                "nome": nome,
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        )


    return resultados


# ============================================================
# PEGAR MELHOR RESULTADO
# ============================================================

def geocodificar(
    texto,
    api_key,
    somente_brasil=True
):
    """
    Retorna o primeiro resultado encontrado.

    Exemplo:

    {
        "nome": "Havan, Brusque, SC, Brasil",
        "latitude": -27.09,
        "longitude": -48.90
    }

    Retorna None caso não encontre nada.
    """

    resultados = buscar_endereco(
        texto=texto,
        api_key=api_key,
        limite=1,
        somente_brasil=somente_brasil
    )

    if not resultados:
        return None

    return resultados[0]
