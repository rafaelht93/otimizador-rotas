import requests


PELIA_BASE_URL = "https://api.heigit.org/pelias/v1"


class ORSError(Exception):
    pass


def autocomplete_enderecos(
    texto,
    api_key,
    limite=8,
    focus_lat=None,
    focus_lon=None
):
    """
    Retorna sugestões enquanto o usuário digita.

    Se focus_lat/focus_lon forem informados,
    os resultados próximos recebem prioridade.
    """

    texto = texto.strip()

    if len(texto) < 3:
        return []

    url = f"{PELIA_BASE_URL}/autocomplete"

    params = {
        "text": texto,
        "size": limite,
        "boundary.country": "BR",
        "layers": "address,venue,street,locality,neighbourhood"
    }

    if (
        focus_lat is not None
        and
        focus_lon is not None
    ):
        params["focus.point.lat"] = focus_lat
        params["focus.point.lon"] = focus_lon

    headers = {
        "Authorization": api_key,
        "Accept": "application/json"
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=15
        )

    except requests.Timeout as e:
        raise ORSError(
            "O serviço de busca demorou demais para responder."
        ) from e

    except requests.RequestException as e:
        raise ORSError(
            "Não foi possível conectar ao serviço de busca."
        ) from e

    if response.status_code == 401:
        raise ORSError(
            "A chave do OpenRouteService não foi aceita."
        )

    if response.status_code == 403:
        raise ORSError(
            "A chave não possui acesso ao serviço de geocodificação."
        )

    if response.status_code == 429:
        raise ORSError(
            "Limite de consultas do OpenRouteService atingido."
        )

    if response.status_code >= 400:
        raise ORSError(
            f"Erro OpenRouteService HTTP {response.status_code}."
        )

    try:
        data = response.json()

    except ValueError as e:
        raise ORSError(
            "Resposta inválida do serviço de busca."
        ) from e

    resultados = []

    for feature in data.get("features", []):

        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates")

        if not coordinates or len(coordinates) < 2:
            continue

        longitude, latitude = coordinates

        props = feature.get(
            "properties",
            {}
        )

        label = (
            props.get("label")
            or props.get("name")
            or texto
        )

        resultados.append(
            {
                "label": label,
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        )

    return resultados
