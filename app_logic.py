from __future__ import annotations

from typing import Any


def mesma_coordenada(local_a: dict[str, Any] | None, local_b: dict[str, Any] | None) -> bool:
    if not local_a or not local_b:
        return False

    try:
        return (
            round(float(local_a["latitude"]), 6)
            == round(float(local_b["latitude"]), 6)
            and round(float(local_a["longitude"]), 6)
            == round(float(local_b["longitude"]), 6)
        )
    except (KeyError, TypeError, ValueError):
        return False


def locais_para_execucao(dados: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna somente as paradas operacionais da rota otimizada."""
    locais = list(dados.get("locais_ordenados") or [])

    if len(locais) < 2:
        return []

    operacionais = list(locais[1:])

    if (
        bool(dados.get("voltar_inicio", False))
        and operacionais
        and mesma_coordenada(locais[0], operacionais[-1])
    ):
        operacionais = operacionais[:-1]

    return operacionais


def dividir_rota_google_maps(
    locais: list[dict[str, Any]],
    max_pontos_por_trecho: int = 5,
) -> list[list[dict[str, Any]]]:
    """Divide a sequência preservando sobreposição entre trechos."""
    if max_pontos_por_trecho < 2:
        raise ValueError("Cada trecho precisa permitir pelo menos 2 pontos.")

    if len(locais) <= max_pontos_por_trecho:
        return [locais]

    trechos: list[list[dict[str, Any]]] = []
    inicio = 0

    while inicio < len(locais) - 1:
        fim = min(
            inicio + max_pontos_por_trecho - 1,
            len(locais) - 1,
        )
        trechos.append(locais[inicio : fim + 1])
        inicio = fim

    return trechos


def resolver_perfil_efetivo(
    perfil: dict[str, Any] | None,
    plano: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Combina plano e eventuais limites personalizados do usuário."""
    if not perfil:
        return None

    if not plano:
        raise ValueError("Plano não encontrado.")

    nome_plano = perfil.get("plano") or "Gratuito"
    personalizado = bool(perfil.get("usar_limites_personalizados", False))

    if personalizado:
        limite_rotas = int(perfil.get("limite_rotas_mes") or 0)
        limite_locais = int(perfil.get("limite_locais_rota") or 2)
        permite_preciso = bool(perfil.get("permite_preciso", False))
    else:
        limite_rotas = int(plano.get("limite_rotas_mes") or 0)
        limite_locais = int(plano.get("limite_locais_rota") or 2)
        permite_preciso = bool(plano.get("permite_preciso", False))

    return {
        "user_id": perfil["user_id"],
        "nome": perfil.get("nome"),
        "email": perfil.get("email"),
        "plano": nome_plano,
        "plano_descricao": plano.get("descricao"),
        "preco_mensal": float(plano.get("preco_mensal") or 0),
        "moeda": plano.get("moeda") or "BRL",
        "ativo": bool(perfil.get("ativo", True)),
        "is_admin": bool(perfil.get("is_admin", False)),
        "usar_limites_personalizados": personalizado,
        "limite_rotas_mes": limite_rotas,
        "limite_locais_rota": limite_locais,
        "permite_preciso": permite_preciso,
        "permite_recorrentes": bool(plano.get("permite_recorrentes", False)),
        "historico_dias": plano.get("historico_dias"),
        "permite_exportacoes": bool(plano.get("permite_exportacoes", False)),
        "plano_ativo": bool(plano.get("ativo", True)),
    }


def validar_regras_plano(
    efetivo: dict[str, Any] | None,
    quantidade_locais: int,
    modo: str,
    uso_atual: int | None = None,
) -> None:
    """Valida regras puras do plano; a reserva atômica continua no banco."""
    if not efetivo:
        raise ValueError("perfil_nao_encontrado")

    if not efetivo.get("ativo", True):
        raise ValueError("conta_inativa")

    limite_locais = int(efetivo.get("limite_locais_rota") or 0)
    if limite_locais > 0 and quantidade_locais > limite_locais:
        raise ValueError("limite_locais")

    if modo == "Preciso" and not efetivo.get("permite_preciso", False):
        raise ValueError("preciso_indisponivel")

    if uso_atual is not None:
        limite_mes = int(efetivo.get("limite_rotas_mes") or 0)
        if limite_mes > 0 and uso_atual >= limite_mes:
            raise ValueError("limite_mensal")
