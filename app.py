import hashlib
import html
import io
import json
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import requests
import streamlit as st

from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

from ors import (
    ORSError,
    autocomplete_enderecos,
    obter_matriz_distancias,
    obter_rota_completa,
)

from route_engine import (
    RouteOptimizationError,
    otimizar_rota,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide",
)

APP_URL = (
    "https://otimizador-rotas-hpu3l5hwuf5c6id4jtwsnz."
    "streamlit.app"
)


class LimiteUsoError(Exception):
    pass


class PagamentoError(Exception):
    pass


# ============================================================
# LOGIN
# ============================================================

if not st.user.is_logged_in:

    st.title("🗺️ Otimizador de Rotas")

    st.write(
        "Entre com sua conta Google para acessar "
        "o Otimizador de Rotas."
    )

    st.button(
        "🔐 Entrar com Google",
        on_click=st.login,
        type="primary",
        use_container_width=True,
    )

    st.stop()


dados_usuario = st.user.to_dict()

usuario_id = dados_usuario.get("sub")

usuario_nome = (
    dados_usuario.get("name")
    or dados_usuario.get("given_name")
    or dados_usuario.get("email")
    or "Usuário"
)

usuario_email = (
    dados_usuario.get("email")
    or ""
)


if not usuario_id:

    st.error(
        "Não foi possível identificar sua conta Google."
    )

    st.stop()


# ============================================================
# SECRETS
# ============================================================

try:

    ORS_KEY = st.secrets["ORS_KEY"]
    ors_configurado = True

except Exception:

    ORS_KEY = None
    ors_configurado = False


try:

    SUPABASE_URL = str(
        st.secrets["SUPABASE_URL"]
    ).rstrip("/")

    SUPABASE_SECRET_KEY = str(
        st.secrets["SUPABASE_SECRET_KEY"]
    )

    supabase_configurado = True

except Exception:

    SUPABASE_URL = None
    SUPABASE_SECRET_KEY = None
    supabase_configurado = False


# ============================================================
# SESSION STATE
# ============================================================

def init_state(nome, valor):

    if nome not in st.session_state:
        st.session_state[nome] = valor


init_state("pagina_app", "nova")
init_state("num_paradas", 1)
init_state("form_version", 0)

init_state("resultado_rota", None)
init_state("rota_atual_id", None)
init_state("rota_atual_nome", None)
init_state("rota_aberta_salva", False)

init_state("resultado_historico", False)
init_state("resultado_direto", False)
init_state("resultado_direto_titulo", None)

init_state("rota_nome_sugerido", "")
init_state("ultima_execucao_id", None)
init_state("mensagem_sistema", None)
init_state("erro_autocomplete", None)

init_state("preset_local_salvo_ids", [])

init_state("local_form_version", 0)
init_state("local_cadastro_aberto", False)
init_state("local_endereco_editando_id", None)
init_state("local_endereco_edit_version", 0)

init_state("recorrente_cadastro_aberto", False)
init_state("recorrente_form_version", 0)
init_state("recorrente_gerar_id", None)
init_state("recorrente_gerar_version", 0)

init_state("edicao_rota", None)
init_state("edicao_version", 0)

init_state("admin_refresh", 0)

init_state("mp_checkout_url", None)
init_state("mp_checkout_plano", None)


# ============================================================
# UTILIDADES
# ============================================================

def normalizar_texto(texto):

    texto = unicodedata.normalize(
        "NFKD",
        str(texto).strip().lower(),
    )

    return "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )


def preparar_json(dados):

    return json.loads(
        json.dumps(
            dados,
            ensure_ascii=False,
            default=str,
        )
    )


def agora_iso():

    return (
        datetime
        .now()
        .astimezone()
        .isoformat()
    )


def formatar_data(valor):

    if not valor:
        return ""

    try:

        data = datetime.fromisoformat(
            str(valor).replace(
                "Z",
                "+00:00",
            )
        )

        data = data.astimezone(
            ZoneInfo(
                "America/Sao_Paulo"
            )
        )

        return data.strftime(
            "%d/%m/%Y às %H:%M"
        )

    except Exception:

        return str(valor)


def formatar_dinheiro(valor):

    try:

        numero = float(valor)

    except Exception:

        numero = 0.0

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def mostrar_mensagem_sistema():

    mensagem = st.session_state.get(
        "mensagem_sistema"
    )

    if not mensagem:
        return

    tipo = mensagem.get(
        "tipo",
        "info",
    )

    texto = mensagem.get(
        "texto",
        "",
    )

    if tipo == "success":
        st.success(texto)

    elif tipo == "warning":
        st.warning(texto)

    elif tipo == "error":
        st.error(texto)

    else:
        st.info(texto)

    st.session_state.mensagem_sistema = None


def limpar_rota_atual():

    st.session_state.resultado_rota = None
    st.session_state.rota_atual_id = None
    st.session_state.rota_atual_nome = None
    st.session_state.rota_aberta_salva = False
    st.session_state.resultado_historico = False
    st.session_state.resultado_direto = False
    st.session_state.resultado_direto_titulo = None
    st.session_state.rota_nome_sugerido = ""
    st.session_state.ultima_execucao_id = None


def iniciar_nova_rota(
    preset_ids=None,
):

    limpar_rota_atual()

    st.session_state.num_paradas = 1
    st.session_state.form_version += 1

    st.session_state.preset_local_salvo_ids = list(
        preset_ids
        or
        []
    )

    st.session_state.recorrente_gerar_id = None
    st.session_state.pagina_app = "nova"


# ============================================================
# SUPABASE
# ============================================================

def supabase_request(
    tabela,
    method,
    params=None,
    json_body=None,
    prefer=None,
):

    if not supabase_configurado:

        raise RuntimeError(
            "Supabase não configurado."
        )

    headers = {
        "apikey":
            SUPABASE_SECRET_KEY,

        "Content-Type":
            "application/json",
    }

    if prefer:

        headers["Prefer"] = prefer

    try:

        response = requests.request(
            method=method,
            url=(
                f"{SUPABASE_URL}"
                f"/rest/v1/{tabela}"
            ),
            headers=headers,
            params=params,
            json=json_body,
            timeout=20,
        )

    except requests.Timeout as e:

        raise RuntimeError(
            "O Supabase demorou demais para responder."
        ) from e

    except requests.RequestException as e:

        raise RuntimeError(
            "Não foi possível conectar ao Supabase."
        ) from e

    if response.status_code >= 400:

        raise RuntimeError(
            "Erro ao acessar o banco de dados "
            f"(HTTP {response.status_code})."
        )

    if (
        response.status_code == 204
        or
        not response.text.strip()
    ):

        return []

    try:

        return response.json()

    except ValueError:

        return []


# ============================================================
# MERCADO PAGO / EDGE FUNCTION
# ============================================================

def mercado_pago_backend(
    action,
    plan_name=None,
):

    if not supabase_configurado:

        raise PagamentoError(
            "O sistema de pagamentos está indisponível."
        )

    payload = {
        "action":
            action,

        "user_id":
            usuario_id,
    }

    if plan_name:

        payload["plan_name"] = plan_name

    try:

        response = requests.post(
            (
                f"{SUPABASE_URL}"
                "/functions/v1/"
                "mercado-pago-subscription"
            ),
            headers={
                "apikey":
                    SUPABASE_SECRET_KEY,

                "Content-Type":
                    "application/json",
            },
            json=payload,
            timeout=30,
        )

    except requests.Timeout as e:

        raise PagamentoError(
            "O sistema de pagamentos demorou "
            "demais para responder."
        ) from e

    except requests.RequestException as e:

        raise PagamentoError(
            "Não foi possível acessar "
            "o sistema de pagamentos."
        ) from e

    try:

        dados = response.json()

    except ValueError:

        dados = {}

    if response.status_code >= 400:

        codigo = dados.get(
            "error"
        )

        mensagem_tecnica = dados.get(
            "message",
            "",
        )

        mensagens = {
            "plan_price_not_configured":
                (
                    "O preço deste plano ainda "
                    "não foi configurado."
                ),

            "plan_not_found":
                (
                    "O plano solicitado não foi encontrado."
                ),

            "user_without_email":
                (
                    "Sua conta não possui um e-mail "
                    "disponível para criar a assinatura."
                ),

            "user_not_available":
                (
                    "Sua conta não está disponível "
                    "para contratação."
                ),

            "subscription_not_found":
                (
                    "Nenhuma assinatura foi encontrada."
                ),

            "invalid_paid_plan":
                (
                    "Este plano não requer assinatura."
                ),
        }

        if codigo in mensagens:

            raise PagamentoError(
                mensagens[codigo]
            )

        if (
            "credentials not configured"
            in mensagem_tecnica.lower()
        ):

            raise PagamentoError(
                "As credenciais do Mercado Pago "
                "ainda não foram configuradas "
                "no Supabase."
            )

        raise PagamentoError(
            "Não foi possível processar "
            "a solicitação no Mercado Pago."
        )

    return dados


def listar_assinaturas_usuario(
    user_id_alvo=None,
    limite=20,
):

    alvo = (
        user_id_alvo
        or
        usuario_id
    )

    return supabase_request(
        "assinaturas",
        "GET",
        params={
            "select":
                (
                    "id,user_id,plano,gateway,"
                    "gateway_subscription_id,"
                    "external_reference,status,"
                    "payer_email,amount,currency,"
                    "init_point,next_payment_date,"
                    "last_payment_id,"
                    "last_payment_status,"
                    "last_payment_at,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{alvo}",

            "order":
                "created_at.desc",

            "limit":
                str(limite),
        },
    )


def obter_assinatura_atual(
    user_id_alvo=None,
):

    assinaturas = listar_assinaturas_usuario(
        user_id_alvo=user_id_alvo,
        limite=1,
    )

    if assinaturas:
        return assinaturas[0]

    return None


def nome_status_assinatura(
    status,
):

    mapa = {
        "pending":
            "Aguardando pagamento",

        "authorized":
            "Ativa",

        "paused":
            "Pausada",

        "canceled":
            "Cancelada",

        "cancelled":
            "Cancelada",
    }

    return mapa.get(
        str(status or "").lower(),
        status or "Sem status",
    )


# ============================================================
# PLANOS
# ============================================================

def listar_planos(
    apenas_ativos=False,
):

    params = {
        "select":
            (
                "nome,ordem,descricao,"
                "limite_rotas_mes,"
                "limite_locais_rota,"
                "permite_preciso,"
                "permite_recorrentes,"
                "historico_dias,"
                "permite_exportacoes,"
                "preco_mensal,moeda,"
                "ativo,"
                "created_at,updated_at"
            ),

        "order":
            "ordem.asc",
    }

    if apenas_ativos:

        params["ativo"] = (
            "eq.true"
        )

    return supabase_request(
        "planos",
        "GET",
        params=params,
    )


def obter_plano(
    nome,
):

    registros = supabase_request(
        "planos",
        "GET",
        params={
            "select":
                (
                    "nome,ordem,descricao,"
                    "limite_rotas_mes,"
                    "limite_locais_rota,"
                    "permite_preciso,"
                    "permite_recorrentes,"
                    "historico_dias,"
                    "permite_exportacoes,"
                    "preco_mensal,moeda,"
                    "ativo"
                ),

            "nome":
                f"eq.{nome}",

            "limit":
                "1",
        },
    )

    if registros:

        return registros[0]

    return None


# ============================================================
# USUÁRIO
# ============================================================

def obter_usuario_app(
    user_id_alvo=None,
):

    user_id_consulta = (
        user_id_alvo
        or
        usuario_id
    )

    registros = supabase_request(
        "usuarios_app",
        "GET",
        params={
            "select":
                (
                    "user_id,nome,email,"
                    "plano,ativo,"
                    "limite_rotas_mes,"
                    "limite_locais_rota,"
                    "permite_preciso,"
                    "usar_limites_personalizados,"
                    "is_admin,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{user_id_consulta}",

            "limit":
                "1",
        },
    )

    if registros:

        return registros[0]

    return None


def garantir_usuario_app():

    perfil = obter_usuario_app()

    if perfil is None:

        registros = supabase_request(
            "usuarios_app",
            "POST",
            json_body={
                "user_id":
                    usuario_id,

                "nome":
                    usuario_nome,

                "email":
                    usuario_email
                    or
                    None,

                "plano":
                    "Gratuito",

                "ativo":
                    True,

                "usar_limites_personalizados":
                    False,

                "is_admin":
                    False,
            },
            prefer="return=representation",
        )

        if not registros:

            raise RuntimeError(
                "Não foi possível criar "
                "o perfil do usuário."
            )

        return registros[0]

    alteracoes = {}

    if (
        perfil.get("nome")
        or
        ""
    ) != usuario_nome:

        alteracoes["nome"] = (
            usuario_nome
        )

    if (
        perfil.get("email")
        or
        ""
    ) != usuario_email:

        alteracoes["email"] = (
            usuario_email
            or
            None
        )

    if alteracoes:

        alteracoes["updated_at"] = (
            agora_iso()
        )

        registros = supabase_request(
            "usuarios_app",
            "PATCH",
            params={
                "user_id":
                    f"eq.{usuario_id}",
            },
            json_body=alteracoes,
            prefer="return=representation",
        )

        if registros:

            perfil = registros[0]

    return perfil


def perfil_efetivo(
    perfil,
):

    if not perfil:
        return None

    nome_plano = (
        perfil.get("plano")
        or
        "Gratuito"
    )

    plano = obter_plano(
        nome_plano
    )

    if not plano:

        raise RuntimeError(
            f"O plano '{nome_plano}' "
            "não foi encontrado."
        )

    personalizado = bool(
        perfil.get(
            "usar_limites_personalizados",
            False,
        )
    )

    if personalizado:

        limite_rotas = int(
            perfil.get(
                "limite_rotas_mes"
            )
            or
            0
        )

        limite_locais = int(
            perfil.get(
                "limite_locais_rota"
            )
            or
            2
        )

        permite_preciso = bool(
            perfil.get(
                "permite_preciso",
                False,
            )
        )

    else:

        limite_rotas = int(
            plano.get(
                "limite_rotas_mes"
            )
            or
            0
        )

        limite_locais = int(
            plano.get(
                "limite_locais_rota"
            )
            or
            2
        )

        permite_preciso = bool(
            plano.get(
                "permite_preciso",
                False,
            )
        )

    return {
        "user_id":
            perfil["user_id"],

        "nome":
            perfil.get("nome"),

        "email":
            perfil.get("email"),

        "plano":
            nome_plano,

        "plano_descricao":
            plano.get("descricao"),

        "preco_mensal":
            float(
                plano.get(
                    "preco_mensal"
                )
                or
                0
            ),

        "moeda":
            plano.get("moeda")
            or
            "BRL",

        "ativo":
            bool(
                perfil.get(
                    "ativo",
                    True,
                )
            ),

        "is_admin":
            bool(
                perfil.get(
                    "is_admin",
                    False,
                )
            ),

        "usar_limites_personalizados":
            personalizado,

        "limite_rotas_mes":
            limite_rotas,

        "limite_locais_rota":
            limite_locais,

        "permite_preciso":
            permite_preciso,

        "permite_recorrentes":
            bool(
                plano.get(
                    "permite_recorrentes",
                    False,
                )
            ),

        "historico_dias":
            plano.get(
                "historico_dias"
            ),

        "permite_exportacoes":
            bool(
                plano.get(
                    "permite_exportacoes",
                    False,
                )
            ),

        "plano_ativo":
            bool(
                plano.get(
                    "ativo",
                    True,
                )
            ),
    }


def intervalo_mes_atual():

    agora = datetime.now(
        ZoneInfo(
            "America/Sao_Paulo"
        )
    )

    inicio = agora.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    if inicio.month == 12:

        fim = inicio.replace(
            year=(
                inicio.year
                + 1
            ),
            month=1,
        )

    else:

        fim = inicio.replace(
            month=(
                inicio.month
                + 1
            ),
        )

    return inicio, fim


def proximo_ciclo_texto():

    _, fim = intervalo_mes_atual()

    return fim.strftime(
        "%d/%m/%Y"
    )


def contar_execucoes_mes_usuario(
    user_id_alvo,
):

    inicio, fim = (
        intervalo_mes_atual()
    )

    registros = supabase_request(
        "execucoes_rotas",
        "GET",
        params={
            "select":
                "id",

            "user_id":
                f"eq.{user_id_alvo}",

            "and":
                (
                    "(created_at.gte."
                    f"{inicio.isoformat()},"
                    "created_at.lt."
                    f"{fim.isoformat()})"
                ),

            "limit":
                "10000",
        },
    )

    return len(registros)


def contar_execucoes_mes_atual():

    return contar_execucoes_mes_usuario(
        usuario_id
    )


perfil_usuario_app = None
perfil_usuario_efetivo = None
uso_rotas_mes = None
erro_perfil_usuario = None


if supabase_configurado:

    try:

        perfil_usuario_app = (
            garantir_usuario_app()
        )

        perfil_usuario_efetivo = (
            perfil_efetivo(
                perfil_usuario_app
            )
        )

        uso_rotas_mes = (
            contar_execucoes_mes_atual()
        )

    except Exception as e:

        erro_perfil_usuario = str(e)


usuario_e_admin = bool(
    perfil_usuario_efetivo
    and
    perfil_usuario_efetivo.get(
        "is_admin",
        False,
    )
)


# ============================================================
# CONTA INATIVA
# ============================================================

if (
    perfil_usuario_efetivo
    and
    not perfil_usuario_efetivo.get(
        "ativo",
        True,
    )
):

    st.title(
        "🗺️ Otimizador de Rotas"
    )

    st.error(
        "⛔ Sua conta está inativa."
    )

    st.write(
        "O acesso ao Otimizador de Rotas "
        "está temporariamente desabilitado."
    )

    if usuario_email:

        st.caption(
            f"Conta conectada: {usuario_email}"
        )

    st.button(
        "🚪 Sair",
        on_click=st.logout,
    )

    st.stop()


# ============================================================
# PERMISSÕES
# ============================================================

def limite_locais_usuario():

    if not perfil_usuario_efetivo:
        return 0

    return int(
        perfil_usuario_efetivo.get(
            "limite_locais_rota"
        )
        or
        0
    )


def permite_preciso_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_preciso",
            False,
        )
    )


def permite_recorrentes_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_recorrentes",
            False,
        )
    )


def permite_exportacoes_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_exportacoes",
            False,
        )
    )


def historico_dias_usuario():

    if not perfil_usuario_efetivo:
        return 7

    return perfil_usuario_efetivo.get(
        "historico_dias"
    )


def opcoes_modo_usuario():

    if permite_preciso_usuario():

        return [
            "Rápido",
            "Preciso",
        ]

    return [
        "Rápido",
    ]


def validar_quantidade_plano(
    quantidade_locais,
):

    if not perfil_usuario_efetivo:

        raise LimiteUsoError(
            "Não foi possível validar "
            "as regras da sua conta."
        )

    limite = int(
        perfil_usuario_efetivo.get(
            "limite_locais_rota"
        )
        or
        0
    )

    if (
        limite > 0
        and
        quantidade_locais > limite
    ):

        raise LimiteUsoError(
            f"Esta rota possui {quantidade_locais} locais, "
            f"mas seu plano permite até {limite}."
        )


def validar_permissao_calculo(
    quantidade_locais,
    modo,
):

    if not supabase_configurado:

        raise LimiteUsoError(
            "O controle de uso está indisponível. "
            "Nenhum cálculo foi iniciado."
        )

    try:

        perfil_atual = obter_usuario_app()

        efetivo = perfil_efetivo(
            perfil_atual
        )

    except Exception as e:

        raise LimiteUsoError(
            "Não foi possível validar seu plano. "
            "Nenhum cálculo foi iniciado."
        ) from e

    if not efetivo:

        raise LimiteUsoError(
            "Seu perfil não foi encontrado."
        )

    if not efetivo.get(
        "ativo",
        True,
    ):

        raise LimiteUsoError(
            "Sua conta está inativa."
        )

    limite_locais = int(
        efetivo.get(
            "limite_locais_rota"
        )
        or
        0
    )

    if (
        limite_locais > 0
        and
        quantidade_locais > limite_locais
    ):

        raise LimiteUsoError(
            "Seu plano permite até "
            f"{limite_locais} locais por rota."
        )

    if (
        modo == "Preciso"
        and
        not efetivo.get(
            "permite_preciso",
            False,
        )
    ):

        raise LimiteUsoError(
            "O modo Preciso não está disponível "
            "no seu plano."
        )

    limite_mes = int(
        efetivo.get(
            "limite_rotas_mes"
        )
        or
        0
    )

    if limite_mes > 0:

        try:

            uso = (
                contar_execucoes_mes_atual()
            )

        except Exception as e:

            raise LimiteUsoError(
                "Não foi possível verificar "
                "seu consumo mensal. "
                "Nenhum cálculo foi iniciado."
            ) from e

        if uso >= limite_mes:

            raise LimiteUsoError(
                "Você atingiu o limite mensal "
                f"de {limite_mes} cálculo(s). "
                "Seu próximo ciclo começa em "
                f"{proximo_ciclo_texto()}."
            )


# ============================================================
# MENU
# ============================================================

def botao_menu(
    label,
    pagina,
    ativo=False,
):

    if st.sidebar.button(
        label,
        use_container_width=True,
        type=(
            "primary"
            if ativo
            else
            "secondary"
        ),
        key=(
            f"menu_{pagina}"
        ),
    ):

        if pagina == "nova":

            iniciar_nova_rota()

        else:

            st.session_state.pagina_app = (
                pagina
            )

        st.rerun()


with st.sidebar:

    st.markdown(
        "## 🗺️ Menu"
    )

    st.write(
        f"👤 **{usuario_nome}**"
    )

    if usuario_email:

        st.caption(
            usuario_email
        )

    st.divider()


botao_menu(
    "➕ Nova rota",
    "nova",
    (
        st.session_state.pagina_app
        == "nova"
    ),
)

botao_menu(
    "📂 Minhas rotas",
    "minhas",
    (
        st.session_state.pagina_app
        in (
            "minhas",
            "editar",
        )
    ),
)

botao_menu(
    "📍 Locais",
    "locais",
    (
        st.session_state.pagina_app
        == "locais"
    ),
)

botao_menu(
    "🔁 Recorrentes",
    "recorrentes",
    (
        st.session_state.pagina_app
        == "recorrentes"
    ),
)

botao_menu(
    "📊 Histórico",
    "historico",
    (
        st.session_state.pagina_app
        == "historico"
    ),
)

botao_menu(
    "💎 Planos",
    "planos",
    (
        st.session_state.pagina_app
        == "planos"
    ),
)


if usuario_e_admin:

    with st.sidebar:

        st.divider()

        st.caption(
            "Administração"
        )

    botao_menu(
        "🛠️ Administração",
        "administracao",
        (
            st.session_state.pagina_app
            == "administracao"
        ),
    )


with st.sidebar:

    st.divider()

    st.markdown(
        "### 📦 Plano e uso"
    )

    if perfil_usuario_efetivo:

        plano = (
            perfil_usuario_efetivo[
                "plano"
            ]
        )

        limite_mes = int(
            perfil_usuario_efetivo.get(
                "limite_rotas_mes"
            )
            or
            0
        )

        limite_locais = int(
            perfil_usuario_efetivo.get(
                "limite_locais_rota"
            )
            or
            0
        )

        uso = int(
            uso_rotas_mes
            or
            0
        )

        st.write(
            f"**Plano:** {plano}"
        )

        if usuario_e_admin:

            st.caption(
                "🛡️ Administrador"
            )

        if perfil_usuario_efetivo.get(
            "usar_limites_personalizados"
        ):

            st.caption(
                "⚙️ Regras individuais ativas"
            )

        if limite_mes > 0:

            percentual = min(
                max(
                    uso / limite_mes,
                    0.0,
                ),
                1.0,
            )

            st.caption(
                f"Rotas este mês: "
                f"**{uso} / {limite_mes}**"
            )

            st.progress(
                percentual
            )

            if uso >= limite_mes:

                st.warning(
                    "⚠️ Limite mensal atingido."
                )

                st.caption(
                    "Novo ciclo em "
                    f"**{proximo_ciclo_texto()}**."
                )

            elif percentual >= 0.8:

                restantes = max(
                    limite_mes
                    -
                    uso,
                    0,
                )

                st.warning(
                    f"Restam {restantes} cálculo(s)."
                )

        else:

            st.caption(
                f"Rotas este mês: "
                f"**{uso} / ilimitadas**"
            )

        st.caption(
            "Limite: "
            f"**{limite_locais} locais/rota**"
        )

        st.caption(
            "Modo Preciso: "
            +
            (
                "**habilitado**"
                if permite_preciso_usuario()
                else
                "**não disponível**"
            )
        )

    elif erro_perfil_usuario:

        st.error(
            "Não foi possível validar "
            "os dados do plano."
        )

    st.divider()

    st.button(
        "🚪 Sair",
        on_click=st.logout,
        use_container_width=True,
    )


# ============================================================
# CABEÇALHO
# ============================================================

st.title(
    "🗺️ Otimizador de Rotas"
)

mostrar_mensagem_sistema()


# ============================================================
# ROTAS SALVAS
# ============================================================

def listar_rotas_usuario():

    return supabase_request(
        "rotas",
        "GET",
        params={
            "select":
                (
                    "id,nome,dados,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "updated_at.desc",
        },
    )


def contar_rotas_usuario():

    registros = supabase_request(
        "rotas",
        "GET",
        params={
            "select":
                "id",

            "user_id":
                f"eq.{usuario_id}",
        },
    )

    return len(registros)


def salvar_rota_banco(
    nome,
    dados,
):

    nome = (
        nome
        or
        ""
    ).strip()

    if not nome:

        raise ValueError(
            "Informe um nome para a rota."
        )

    resultado = supabase_request(
        "rotas",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome,

            "dados":
                preparar_json(
                    dados
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "O banco não retornou "
            "a rota criada."
        )

    return resultado[0]


def atualizar_rota_banco(
    rota_id,
    dados,
):

    resultado = supabase_request(
        "rotas",
        "PATCH",
        params={
            "id":
                f"eq.{rota_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "dados":
                preparar_json(
                    dados
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota não foi encontrada."
        )

    return resultado[0]


def atualizar_rota_completa_banco(
    rota_id,
    nome,
    dados,
):

    resultado = supabase_request(
        "rotas",
        "PATCH",
        params={
            "id":
                f"eq.{rota_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "nome":
                nome.strip(),

            "dados":
                preparar_json(
                    dados
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota não foi encontrada."
        )

    return resultado[0]


def renomear_rota_banco(
    rota_id,
    novo_nome,
):

    novo_nome = (
        novo_nome
        or
        ""
    ).strip()

    if not novo_nome:

        raise ValueError(
            "Informe o novo nome."
        )

    resultado = supabase_request(
        "rotas",
        "PATCH",
        params={
            "id":
                f"eq.{rota_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "nome":
                novo_nome,

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota não foi encontrada."
        )

    return resultado[0]


def excluir_rota_banco(
    rota_id,
):

    resultado = supabase_request(
        "rotas",
        "DELETE",
        params={
            "id":
                f"eq.{rota_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota não foi encontrada."
        )


# ============================================================
# HISTÓRICO
# ============================================================

def registrar_execucao(
    dados,
    rota_id=None,
    nome_rota=None,
):

    locais_base = (
        dados.get(
            "locais_base"
        )
        or
        []
    )

    resultado = supabase_request(
        "execucoes_rotas",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "rota_id":
                rota_id,

            "nome_rota":
                nome_rota,

            "quantidade_locais":
                len(
                    locais_base
                ),

            "distancia_m":
                float(
                    dados.get(
                        "distancia_m",
                        0,
                    )
                ),

            "duracao_s":
                float(
                    dados.get(
                        "duracao_s",
                        0,
                    )
                ),

            "objetivo":
                dados.get(
                    "objetivo",
                    "Menor distância",
                ),

            "modo":
                dados.get(
                    "modo",
                    "Rápido",
                ),

            "voltar_inicio":
                bool(
                    dados.get(
                        "voltar_inicio",
                        False,
                    )
                ),

            "dados":
                preparar_json(
                    dados
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Não foi possível registrar "
            "a execução."
        )

    return resultado[0]


def registrar_execucao_seguro(
    dados,
    rota_id=None,
    nome_rota=None,
):

    try:

        return (
            registrar_execucao(
                dados,
                rota_id,
                nome_rota,
            ),
            None,
        )

    except Exception as e:

        return None, str(e)


def vincular_execucao_rota(
    execucao_id,
    rota_id,
    nome_rota,
):

    if not execucao_id:
        return

    supabase_request(
        "execucoes_rotas",
        "PATCH",
        params={
            "id":
                f"eq.{execucao_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "rota_id":
                rota_id,

            "nome_rota":
                nome_rota,
        },
        prefer="return=representation",
    )


def listar_execucoes_usuario(
    dias=None,
    limite=500,
):

    params = {
        "select":
            (
                "id,rota_id,nome_rota,"
                "quantidade_locais,"
                "distancia_m,duracao_s,"
                "objetivo,modo,"
                "voltar_inicio,dados,"
                "created_at"
            ),

        "user_id":
            f"eq.{usuario_id}",

        "order":
            "created_at.desc",

        "limit":
            str(limite),
    }

    if dias is not None:

        data_limite = (
            datetime.now(
                ZoneInfo(
                    "UTC"
                )
            )
            -
            timedelta(
                days=int(dias)
            )
        )

        params["created_at"] = (
            f"gte.{data_limite.isoformat()}"
        )

    return supabase_request(
        "execucoes_rotas",
        "GET",
        params=params,
    )


# ============================================================
# LOCAIS SALVOS
# ============================================================

def listar_locais_salvos_usuario():

    return supabase_request(
        "locais_salvos",
        "GET",
        params={
            "select":
                (
                    "id,nome,endereco,"
                    "latitude,longitude,"
                    "observacao,dados,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "nome.asc",
        },
    )


def contar_locais_salvos_usuario():

    registros = supabase_request(
        "locais_salvos",
        "GET",
        params={
            "select":
                "id",

            "user_id":
                f"eq.{usuario_id}",
        },
    )

    return len(registros)


def nome_padrao_local(
    origem,
):

    return (
        origem.get("name")
        or
        origem.get("label")
        or
        origem.get("nome")
        or
        "Local"
    )


def salvar_local_banco(
    nome,
    origem,
    observacao="",
):

    nome = (
        nome
        or
        ""
    ).strip()

    if not nome:

        raise ValueError(
            "Informe um nome para o local."
        )

    if not origem:

        raise ValueError(
            "Selecione um endereço."
        )

    endereco = (
        origem.get("label")
        or
        origem.get("endereco")
        or
        origem.get("nome")
        or
        nome
    )

    resultado = supabase_request(
        "locais_salvos",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome,

            "endereco":
                str(
                    endereco
                ),

            "latitude":
                float(
                    origem[
                        "latitude"
                    ]
                ),

            "longitude":
                float(
                    origem[
                        "longitude"
                    ]
                ),

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),

            "dados":
                preparar_json(
                    origem
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Não foi possível salvar o local."
        )

    return resultado[0]


def atualizar_local_banco(
    local_id,
    nome,
    observacao,
    nova_origem=None,
):

    payload = {
        "nome":
            (
                nome
                or
                ""
            ).strip(),

        "observacao":
            (
                (
                    observacao
                    or
                    ""
                ).strip()
                or
                None
            ),

        "updated_at":
            agora_iso(),
    }

    if nova_origem:

        payload.update(
            {
                "endereco":
                    str(
                        nova_origem.get(
                            "label"
                        )
                        or
                        nova_origem.get(
                            "endereco"
                        )
                        or
                        nome
                    ),

                "latitude":
                    float(
                        nova_origem[
                            "latitude"
                        ]
                    ),

                "longitude":
                    float(
                        nova_origem[
                            "longitude"
                        ]
                    ),

                "dados":
                    preparar_json(
                        nova_origem
                    ),
            }
        )

    resultado = supabase_request(
        "locais_salvos",
        "PATCH",
        params={
            "id":
                f"eq.{local_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body=payload,
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "O local não foi encontrado."
        )

    return resultado[0]


def excluir_local_banco(
    local_id,
):

    resultado = supabase_request(
        "locais_salvos",
        "DELETE",
        params={
            "id":
                f"eq.{local_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "O local não foi encontrado."
        )


def salvar_locais_em_lote(
    origens,
):

    existentes = (
        listar_locais_salvos_usuario()
    )

    coordenadas_existentes = {
        (
            round(
                float(
                    item[
                        "latitude"
                    ]
                ),
                6,
            ),

            round(
                float(
                    item[
                        "longitude"
                    ]
                ),
                6,
            ),
        )

        for item
        in existentes
    }

    novos = []
    ignorados = 0

    for origem in origens:

        chave = (
            round(
                float(
                    origem[
                        "latitude"
                    ]
                ),
                6,
            ),

            round(
                float(
                    origem[
                        "longitude"
                    ]
                ),
                6,
            ),
        )

        if chave in coordenadas_existentes:

            ignorados += 1
            continue

        nome = nome_padrao_local(
            origem
        )

        novos.append(
            {
                "user_id":
                    usuario_id,

                "nome":
                    str(
                        nome
                    ),

                "endereco":
                    str(
                        origem.get(
                            "label"
                        )
                        or
                        nome
                    ),

                "latitude":
                    float(
                        origem[
                            "latitude"
                        ]
                    ),

                "longitude":
                    float(
                        origem[
                            "longitude"
                        ]
                    ),

                "observacao":
                    None,

                "dados":
                    preparar_json(
                        origem
                    ),
            }
        )

        coordenadas_existentes.add(
            chave
        )

    if not novos:

        return 0, ignorados

    resultado = supabase_request(
        "locais_salvos",
        "POST",
        json_body=novos,
        prefer="return=representation",
    )

    return (
        len(
            resultado
        ),
        ignorados,
    )


def local_salvo_para_origem(
    registro,
):

    dados = (
        registro.get(
            "dados"
        )
        or
        {}
    )

    return {
        "label":
            (
                registro.get(
                    "nome"
                )
                or
                registro.get(
                    "endereco"
                )
                or
                "Local"
            ),

        "latitude":
            float(
                registro[
                    "latitude"
                ]
            ),

        "longitude":
            float(
                registro[
                    "longitude"
                ]
            ),

        "name":
            dados.get("name"),

        "street":
            dados.get("street"),

        "housenumber":
            dados.get(
                "housenumber"
            ),

        "locality":
            dados.get(
                "locality"
            ),

        "region":
            dados.get(
                "region"
            ),

        "country":
            dados.get(
                "country"
            ),

        "layer":
            dados.get(
                "layer"
            ),

        "endereco_salvo":
            registro.get(
                "endereco"
            ),

        "local_salvo_id":
            registro.get(
                "id"
            ),

        "observacao":
            registro.get(
                "observacao"
            ),
    }


# ============================================================
# RECORRENTES
# ============================================================

def listar_recorrentes_usuario():

    return supabase_request(
        "rotas_recorrentes",
        "GET",
        params={
            "select":
                (
                    "id,nome,local_ids,"
                    "inicio_local_id,"
                    "fim_local_id,"
                    "voltar_inicio,"
                    "objetivo,modo,"
                    "observacao,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "updated_at.desc",
        },
    )


def validar_acesso_recorrentes():

    if not permite_recorrentes_usuario():

        raise LimiteUsoError(
            "Rotas recorrentes são um "
            "recurso do plano Pro."
        )


def salvar_recorrente_banco(
    nome,
    local_ids,
    inicio_local_id,
    fim_local_id,
    voltar_inicio,
    objetivo,
    modo,
    observacao="",
):

    validar_acesso_recorrentes()

    validar_quantidade_plano(
        len(
            local_ids
        )
    )

    if len(
        local_ids
    ) < 2:

        raise ValueError(
            "Selecione pelo menos dois locais."
        )

    if inicio_local_id not in local_ids:

        raise ValueError(
            "Ponto inicial inválido."
        )

    if (
        not voltar_inicio
        and
        (
            not fim_local_id
            or
            fim_local_id not in local_ids
            or
            fim_local_id == inicio_local_id
        )
    ):

        raise ValueError(
            "Selecione um ponto final válido."
        )

    if (
        modo == "Preciso"
        and
        not permite_preciso_usuario()
    ):

        raise LimiteUsoError(
            "O modo Preciso não está "
            "disponível no seu plano."
        )

    resultado = supabase_request(
        "rotas_recorrentes",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome.strip(),

            "local_ids":
                preparar_json(
                    local_ids
                ),

            "inicio_local_id":
                inicio_local_id,

            "fim_local_id":
                (
                    None
                    if voltar_inicio
                    else
                    fim_local_id
                ),

            "voltar_inicio":
                bool(
                    voltar_inicio
                ),

            "objetivo":
                objetivo,

            "modo":
                modo,

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Não foi possível salvar "
            "a rota recorrente."
        )

    return resultado[0]


def atualizar_recorrente_banco(
    recorrente_id,
    nome,
    local_ids,
    inicio_local_id,
    fim_local_id,
    voltar_inicio,
    objetivo,
    modo,
    observacao="",
):

    validar_acesso_recorrentes()

    validar_quantidade_plano(
        len(
            local_ids
        )
    )

    resultado = supabase_request(
        "rotas_recorrentes",
        "PATCH",
        params={
            "id":
                f"eq.{recorrente_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "nome":
                nome.strip(),

            "local_ids":
                preparar_json(
                    local_ids
                ),

            "inicio_local_id":
                inicio_local_id,

            "fim_local_id":
                (
                    None
                    if voltar_inicio
                    else
                    fim_local_id
                ),

            "voltar_inicio":
                bool(
                    voltar_inicio
                ),

            "objetivo":
                objetivo,

            "modo":
                modo,

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota recorrente "
            "não foi encontrada."
        )

    return resultado[0]


def duplicar_recorrente_banco(
    recorrente,
):

    return salvar_recorrente_banco(
        nome=(
            f"{recorrente['nome']} - cópia"
        ),

        local_ids=(
            recorrente.get(
                "local_ids"
            )
            or
            []
        ),

        inicio_local_id=(
            recorrente.get(
                "inicio_local_id"
            )
        ),

        fim_local_id=(
            recorrente.get(
                "fim_local_id"
            )
        ),

        voltar_inicio=bool(
            recorrente.get(
                "voltar_inicio",
                True,
            )
        ),

        objetivo=(
            recorrente.get(
                "objetivo",
                "Menor tempo",
            )
        ),

        modo=(
            recorrente.get(
                "modo",
                "Rápido",
            )
        ),

        observacao=(
            recorrente.get(
                "observacao"
            )
            or
            ""
        ),
    )


def excluir_recorrente_banco(
    recorrente_id,
):

    validar_acesso_recorrentes()

    resultado = supabase_request(
        "rotas_recorrentes",
        "DELETE",
        params={
            "id":
                f"eq.{recorrente_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota recorrente "
            "não foi encontrada."
        )


# ============================================================
# ORS / AUTOCOMPLETE
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def buscar_sugestoes_cache(
    texto,
    focus_lat,
    focus_lon,
    _api_key,
):

    return autocomplete_enderecos(
        texto=texto,
        api_key=_api_key,
        limite=8,
        focus_lat=focus_lat,
        focus_lon=focus_lon,
    )


@st.cache_data(
    ttl=3600,
    show_spinner=False,
)
def buscar_endereco_importado_cache(
    texto,
    _api_key,
):

    return autocomplete_enderecos(
        texto=texto,
        api_key=_api_key,
        limite=6,
    )


@st.cache_data(
    ttl=3600,
    show_spinner=False,
)
def obter_matrix_cache_v2(
    coordenadas,
    _api_key,
    cache_version="matrix-v2",
):

    return obter_matriz_distancias(
        coordenadas=list(
            coordenadas
        ),
        api_key=_api_key,
    )


@st.cache_data(
    ttl=3600,
    show_spinner=False,
)
def obter_directions_cache(
    coordenadas,
    _api_key,
):

    return obter_rota_completa(
        coordenadas=list(
            coordenadas
        ),
        api_key=_api_key,
    )


def buscar_sugestoes(
    searchterm,
    focus_lat=None,
    focus_lon=None,
):

    texto = (
        searchterm
        or
        ""
    ).strip()

    if (
        len(
            texto
        ) < 3
        or
        not ors_configurado
    ):

        return []

    try:

        resultados = (
            buscar_sugestoes_cache(
                texto,
                focus_lat,
                focus_lon,
                ORS_KEY,
            )
        )

        st.session_state.erro_autocomplete = None

    except Exception as e:

        st.session_state.erro_autocomplete = str(e)

        return []

    return [
        (
            resultado["label"],
            resultado,
        )

        for resultado
        in resultados
    ]


def campo_local(
    chave,
    label,
    placeholder,
    focus=None,
):

    if focus:

        focus_lat = (
            focus.get(
                "latitude"
            )
        )

        focus_lon = (
            focus.get(
                "longitude"
            )
        )

    else:

        focus_lat = None
        focus_lon = None

    return st_searchbox(
        buscar_sugestoes,
        key=chave,
        label=label,
        placeholder=placeholder,
        debounce=400,
        clear_on_submit=False,
        edit_after_submit="option",
        focus_lat=focus_lat,
        focus_lon=focus_lon,
    )


# ============================================================
# IMPORTAÇÃO
# ============================================================

def localizar_coluna_endereco(
    dataframe,
):

    for coluna in dataframe.columns:

        if normalizar_texto(
            coluna
        ) == "endereco":

            return coluna

    return None


def decodificar_csv(
    bytes_arquivo,
):

    try:

        return bytes_arquivo.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError:

        return bytes_arquivo.decode(
            "latin1"
        )


def limpar_linha_csv_simples(
    linha,
):

    valor = linha.strip()

    if (
        len(
            valor
        ) >= 2
        and
        (
            (
                valor.startswith(
                    '"'
                )
                and
                valor.endswith(
                    '"'
                )
            )
            or
            (
                valor.startswith(
                    "'"
                )
                and
                valor.endswith(
                    "'"
                )
            )
        )
    ):

        valor = (
            valor[
                1:-1
            ]
            .replace(
                '""',
                '"',
            )
        )

    return valor.strip()


def ler_csv_robusto(
    bytes_arquivo,
):

    texto = decodificar_csv(
        bytes_arquivo
    )

    linhas = [
        linha

        for linha
        in texto.splitlines()

        if linha.strip()
    ]

    if not linhas:

        raise ValueError(
            "O arquivo CSV está vazio."
        )

    cabecalho = normalizar_texto(
        linhas[0]
        .strip()
        .strip('"')
        .strip("'")
    )

    if cabecalho == "endereco":

        enderecos = []

        for linha in linhas[1:]:

            endereco = (
                limpar_linha_csv_simples(
                    linha
                )
            )

            if endereco:

                enderecos.append(
                    endereco
                )

        return pd.DataFrame(
            {
                "endereco":
                    enderecos
            }
        )

    for separador in (
        ";",
        ",",
        "\t",
    ):

        try:

            dataframe = pd.read_csv(
                io.StringIO(
                    texto
                ),
                sep=separador,
            )

        except Exception:

            continue

        if localizar_coluna_endereco(
            dataframe
        ) is not None:

            return dataframe

    raise ValueError(
        "Não foi possível identificar "
        "a coluna 'endereco'."
    )


def ler_arquivo_paradas(
    arquivo,
):

    bytes_arquivo = (
        arquivo.getvalue()
    )

    nome = (
        arquivo.name.lower()
    )

    if nome.endswith(
        ".csv"
    ):

        dataframe = (
            ler_csv_robusto(
                bytes_arquivo
            )
        )

    elif nome.endswith(
        ".xlsx"
    ):

        dataframe = pd.read_excel(
            io.BytesIO(
                bytes_arquivo
            ),
            engine="openpyxl",
        )

    else:

        raise ValueError(
            "Utilize CSV ou XLSX."
        )

    coluna = localizar_coluna_endereco(
        dataframe
    )

    if coluna is None:

        raise ValueError(
            "O arquivo precisa possuir "
            "a coluna 'endereco'."
        )

    enderecos = []

    for valor in dataframe[
        coluna
    ].tolist():

        if pd.isna(
            valor
        ):

            continue

        endereco = str(
            valor
        ).strip()

        if endereco:

            enderecos.append(
                endereco
            )

    if not enderecos:

        raise ValueError(
            "Nenhum endereço válido."
        )

    hash_arquivo = (
        hashlib.sha256(
            bytes_arquivo
        )
        .hexdigest()[:12]
    )

    return (
        enderecos,
        hash_arquivo,
    )


# ============================================================
# MOTOR DA ROTA
# ============================================================

def converter_local(
    origem,
    tipo,
):

    return {
        "nome":
            origem.get(
                "label",
                origem.get(
                    "nome",
                    "",
                ),
            ),

        "latitude":
            float(
                origem[
                    "latitude"
                ]
            ),

        "longitude":
            float(
                origem[
                    "longitude"
                ]
            ),

        "tipo":
            tipo,

        "name":
            origem.get(
                "name"
            ),

        "street":
            origem.get(
                "street"
            ),

        "housenumber":
            origem.get(
                "housenumber"
            ),

        "locality":
            origem.get(
                "locality"
            ),

        "region":
            origem.get(
                "region"
            ),

        "country":
            origem.get(
                "country"
            ),

        "layer":
            origem.get(
                "layer"
            ),

        "endereco_salvo":
            origem.get(
                "endereco_salvo"
            ),

        "local_salvo_id":
            origem.get(
                "local_salvo_id"
            ),

        "observacao":
            origem.get(
                "observacao"
            ),
    }


def validar_locais_base(
    locais,
    voltar_inicio,
):

    if len(
        locais
    ) < 2:

        raise ValueError(
            "São necessários pelo menos "
            "dois locais."
        )

    limite_tecnico = (
        49
        if voltar_inicio
        else
        50
    )

    if len(
        locais
    ) > limite_tecnico:

        raise ValueError(
            "O limite técnico desta rota "
            f"é de {limite_tecnico} locais."
        )

    coordenadas = set()

    for local in locais:

        chave = (
            round(
                float(
                    local[
                        "latitude"
                    ]
                ),
                6,
            ),

            round(
                float(
                    local[
                        "longitude"
                    ]
                ),
                6,
            ),
        )

        if chave in coordenadas:

            raise ValueError(
                "O mesmo local foi selecionado "
                "mais de uma vez."
            )

        coordenadas.add(
            chave
        )


def calcular_rota_a_partir_de_locais(
    locais,
    voltar_inicio,
    objetivo,
    modo,
):

    if not ors_configurado:

        raise ORSError(
            "OpenRouteService não configurado."
        )

    validar_permissao_calculo(
        quantidade_locais=len(
            locais
        ),
        modo=modo,
    )

    validar_locais_base(
        locais,
        voltar_inicio,
    )

    coordenadas = tuple(
        (
            local[
                "latitude"
            ],
            local[
                "longitude"
            ],
        )

        for local
        in locais
    )

    start_idx = 0

    end_idx = (
        0
        if voltar_inicio
        else
        len(
            locais
        ) - 1
    )

    matrix_data = (
        obter_matrix_cache_v2(
            coordenadas,
            ORS_KEY,
        )
    )

    matrix_otimizacao = (
        matrix_data[
            "distancias"
        ]
        if objetivo
        == "Menor distância"
        else
        matrix_data[
            "duracoes"
        ]
    )

    resultado_solver = (
        otimizar_rota(
            matrix_m=matrix_otimizacao,
            start_idx=start_idx,
            end_idx=end_idx,
            modo=modo,
        )
    )

    locais_ordenados = [
        locais[
            indice
        ]

        for indice
        in resultado_solver[
            "route"
        ]
    ]

    coordenadas_ordenadas = tuple(
        (
            local[
                "latitude"
            ],
            local[
                "longitude"
            ],
        )

        for local
        in locais_ordenados
    )

    rota_real = (
        obter_directions_cache(
            coordenadas_ordenadas,
            ORS_KEY,
        )
    )

    gap = resultado_solver.get(
        "gap_percent"
    )

    if gap is not None:

        gap = float(
            gap
        )

    return {
        "versao":
            12,

        "voltar_inicio":
            bool(
                voltar_inicio
            ),

        "objetivo":
            objetivo,

        "modo":
            modo,

        "locais_base":
            preparar_json(
                locais
            ),

        "locais_ordenados":
            preparar_json(
                locais_ordenados
            ),

        "distancia_m":
            float(
                rota_real[
                    "distancia_m"
                ]
            ),

        "duracao_s":
            float(
                rota_real[
                    "duracao_s"
                ]
            ),

        "geometry":
            preparar_json(
                rota_real[
                    "geometry"
                ]
            ),

        "solver": {
            "status":
                str(
                    resultado_solver.get(
                        "status",
                        "",
                    )
                ),

            "metodo":
                str(
                    resultado_solver.get(
                        "metodo",
                        "",
                    )
                ),

            "gap_percent":
                gap,
        },
    }


# ============================================================
# MAPA / GOOGLE MAPS / EXPORTAÇÃO
# ============================================================

def dados_metricas(
    dados,
):

    distancia_km = (
        float(
            dados.get(
                "distancia_m",
                0,
            )
        )
        /
        1000
    )

    minutos_total = round(
        float(
            dados.get(
                "duracao_s",
                0,
            )
        )
        /
        60
    )

    horas, minutos = divmod(
        minutos_total,
        60,
    )

    tempo = (
        f"{horas}h {minutos:02d}min"
        if horas
        else
        f"{minutos} min"
    )

    return distancia_km, tempo


def criar_mapa(
    geometry,
    locais_ordenados,
    rota_fechada,
):

    path = [
        (
            lat,
            lon,
        )

        for lon, lat
        in geometry
    ]

    if not path:

        raise ValueError(
            "A rota não possui geometria."
        )

    centro = [
        sum(
            p[0]
            for p in path
        )
        /
        len(
            path
        ),

        sum(
            p[1]
            for p in path
        )
        /
        len(
            path
        ),
    ]

    mapa = folium.Map(
        location=centro,
        zoom_start=13,
        control_scale=True,
    )

    folium.PolyLine(
        path,
        weight=5,
        opacity=0.8,
    ).add_to(
        mapa
    )

    marcadores = (
        locais_ordenados[:-1]
        if rota_fechada
        else
        locais_ordenados
    )

    for numero, local in enumerate(
        marcadores,
        start=1,
    ):

        nome = html.escape(
            str(
                local[
                    "nome"
                ]
            )
        )

        icone = f"""
        <div style="
            background:#1e3a8a;
            color:#fff;
            width:30px;
            height:30px;
            border-radius:50%;
            display:flex;
            align-items:center;
            justify-content:center;
            font-weight:700;
            border:2px solid white;
        ">{numero}</div>
        """

        folium.Marker(
            location=[
                local[
                    "latitude"
                ],
                local[
                    "longitude"
                ],
            ],
            tooltip=(
                f"{numero}. {nome}"
            ),
            icon=folium.DivIcon(
                html=icone
            ),
        ).add_to(
            mapa
        )

    mapa.fit_bounds(
        [
            [
                min(
                    p[0]
                    for p in path
                ),
                min(
                    p[1]
                    for p in path
                ),
            ],
            [
                max(
                    p[0]
                    for p in path
                ),
                max(
                    p[1]
                    for p in path
                ),
            ],
        ]
    )

    return mapa


def montar_referencia_google(
    local,
):

    if local.get(
        "endereco_salvo"
    ):

        return str(
            local[
                "endereco_salvo"
            ]
        )

    componentes = []

    nome = (
        local.get(
            "name"
        )
        or
        local.get(
            "nome"
        )
    )

    rua = local.get(
        "street"
    )

    numero = local.get(
        "housenumber"
    )

    if nome:

        componentes.append(
            str(
                nome
            )
        )

    if rua:

        endereco = str(
            rua
        )

        if numero:

            endereco += (
                f", {numero}"
            )

        componentes.append(
            endereco
        )

    for valor in (
        local.get(
            "locality"
        ),
        local.get(
            "region"
        ),
        local.get(
            "country"
        )
        or
        "Brazil",
    ):

        if valor:

            valor = str(
                valor
            )

            if valor not in componentes:

                componentes.append(
                    valor
                )

    return ", ".join(
        componentes
    )


def coordenada_google_maps(
    local,
):

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


def gerar_url_google_maps(
    locais,
    usar_coordenadas=False,
):

    if len(
        locais
    ) < 2:

        return None

    if usar_coordenadas:

        referencias = [
            coordenada_google_maps(
                local
            )

            for local
            in locais
        ]

    else:

        referencias = [
            montar_referencia_google(
                local
            )

            for local
            in locais
        ]

    params = {
        "api":
            "1",

        "origin":
            referencias[0],

        "destination":
            referencias[-1],

        "travelmode":
            "driving",

        "dir_action":
            "navigate",
    }

    if len(
        referencias
    ) > 2:

        params["waypoints"] = "|".join(
            referencias[1:-1]
        )

    return (
        "https://www.google.com/maps/dir/?"
        +
        urlencode(
            params
        )
    )


def dividir_rota_google_maps(
    locais,
    max_pontos_por_trecho=5,
):

    if len(
        locais
    ) <= max_pontos_por_trecho:

        return [
            locais
        ]

    trechos = []
    inicio = 0

    while inicio < len(
        locais
    ) - 1:

        fim = min(
            inicio
            +
            max_pontos_por_trecho
            -
            1,
            len(
                locais
            ) - 1,
        )

        trechos.append(
            locais[
                inicio:
                fim + 1
            ]
        )

        inicio = fim

    return trechos


def dataframe_rota_otimizada(
    locais,
    rota_fechada,
):

    linhas = []

    for ordem, local in enumerate(
        locais,
        start=1,
    ):

        if ordem == 1:

            tipo = "Início"

        elif (
            rota_fechada
            and
            ordem == len(
                locais
            )
        ):

            tipo = "Retorno"

        elif ordem == len(
            locais
        ):

            tipo = "Fim"

        else:

            tipo = "Parada"

        linhas.append(
            {
                "ordem":
                    ordem,

                "tipo":
                    tipo,

                "local":
                    local[
                        "nome"
                    ],

                "endereco":
                    montar_referencia_google(
                        local
                    ),

                "observacao":
                    local.get(
                        "observacao"
                    )
                    or
                    "",

                "latitude":
                    local[
                        "latitude"
                    ],

                "longitude":
                    local[
                        "longitude"
                    ],
            }
        )

    return pd.DataFrame(
        linhas
    )


def gerar_excel_rota(
    dataframe,
    dados,
):

    buffer = io.BytesIO()

    distancia_km, tempo = (
        dados_metricas(
            dados
        )
    )

    resumo = pd.DataFrame(
        [
            {
                "Distância total (km)":
                    round(
                        distancia_km,
                        2,
                    ),

                "Tempo estimado":
                    tempo,

                "Objetivo":
                    dados.get(
                        "objetivo"
                    ),

                "Modo":
                    dados.get(
                        "modo"
                    ),
            }
        ]
    )

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl",
    ) as writer:

        resumo.to_excel(
            writer,
            sheet_name="Resumo",
            index=False,
        )

        dataframe.to_excel(
            writer,
            sheet_name="Rota",
            index=False,
        )

    return buffer.getvalue()


# ============================================================
# RESULTADO
# ============================================================

def renderizar_resultado_rota(
    dados,
):

    if not dados:
        return

    locais = (
        dados.get(
            "locais_ordenados"
        )
        or
        []
    )

    locais_base = (
        dados.get(
            "locais_base"
        )
        or
        []
    )

    voltar_inicio = bool(
        dados.get(
            "voltar_inicio",
            False,
        )
    )

    distancia_km, tempo = (
        dados_metricas(
            dados
        )
    )

    if st.session_state.resultado_historico:

        st.info(
            "📊 Snapshot do histórico."
        )

    if st.session_state.resultado_direto_titulo:

        st.info(
            st.session_state
            .resultado_direto_titulo
        )

    if (
        st.session_state.rota_atual_id
        and
        st.session_state.rota_atual_nome
    ):

        st.info(
            "📂 Rota salva: "
            f"**{st.session_state.rota_atual_nome}**"
        )

        col1, col2, col3 = (
            st.columns(
                3
            )
        )

        with col1:

            if st.button(
                "🔄 Recalcular",
                use_container_width=True,
            ):

                try:

                    novo = (
                        calcular_rota_a_partir_de_locais(
                            locais_base,
                            voltar_inicio,
                            dados.get(
                                "objetivo",
                                "Menor distância",
                            ),
                            dados.get(
                                "modo",
                                "Rápido",
                            ),
                        )
                    )

                    registrar_execucao_seguro(
                        novo,
                        st.session_state
                        .rota_atual_id,
                        st.session_state
                        .rota_atual_nome,
                    )

                    st.session_state.resultado_rota = (
                        novo
                    )

                    st.rerun()

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

        with col2:

            if st.button(
                "✏️ Editar locais",
                use_container_width=True,
            ):

                st.session_state.edicao_rota = {
                    "id":
                        st.session_state
                        .rota_atual_id,

                    "nome":
                        st.session_state
                        .rota_atual_nome,

                    "dados":
                        dados,
                }

                st.session_state.edicao_version += 1
                st.session_state.pagina_app = "editar"

                st.rerun()

        with col3:

            if st.button(
                "💾 Atualizar rota salva",
                use_container_width=True,
            ):

                try:

                    atualizar_rota_banco(
                        st.session_state
                        .rota_atual_id,
                        dados,
                    )

                    st.success(
                        "Rota atualizada."
                    )

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

    st.subheader(
        "✅ Resultado da rota"
    )

    c1, c2, c3, c4 = (
        st.columns(
            4
        )
    )

    c1.metric(
        "Distância",
        f"{distancia_km:.2f} km",
    )

    c2.metric(
        "Tempo",
        tempo,
    )

    c3.metric(
        "Locais",
        len(
            locais_base
        ),
    )

    c4.metric(
        "Modo",
        dados.get(
            "modo",
            "—",
        ),
    )

    st.subheader(
        "📋 Ordem da rota"
    )

    exibir = (
        locais[:-1]
        if voltar_inicio
        else
        locais
    )

    for numero, local in enumerate(
        exibir,
        start=1,
    ):

        st.write(
            f"**{numero}.** "
            f"{local['nome']}"
        )

        if local.get(
            "observacao"
        ):

            st.caption(
                f"📝 {local['observacao']}"
            )

    if (
        voltar_inicio
        and
        locais
    ):

        st.write(
            f"**{len(exibir) + 1}.** "
            f"{locais[-1]['nome']} "
            "_(retorno)_"
        )

    st.subheader(
        "☁️ Salvar rota"
    )

    if not st.session_state.rota_atual_id:

        with st.form(
            "salvar_rota_form"
        ):

            nome = st.text_input(
                "Nome da rota",
                value=(
                    st.session_state
                    .rota_nome_sugerido
                ),
            )

            salvar = (
                st.form_submit_button(
                    "💾 Salvar em Minhas rotas",
                    use_container_width=True,
                )
            )

            if salvar:

                try:

                    registro = (
                        salvar_rota_banco(
                            nome,
                            dados,
                        )
                    )

                    if (
                        st.session_state
                        .ultima_execucao_id
                    ):

                        vincular_execucao_rota(
                            st.session_state
                            .ultima_execucao_id,
                            registro[
                                "id"
                            ],
                            registro[
                                "nome"
                            ],
                        )

                    st.session_state.rota_atual_id = (
                        registro[
                            "id"
                        ]
                    )

                    st.session_state.rota_atual_nome = (
                        registro[
                            "nome"
                        ]
                    )

                    st.session_state.rota_aberta_salva = True

                    st.rerun()

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

    else:

        st.success(
            "Esta rota já está salva."
        )

    try:

        mapa = criar_mapa(
            dados.get(
                "geometry"
            )
            or
            [],
            locais,
            voltar_inicio,
        )

    except Exception as e:

        st.error(
            "Não foi possível montar o mapa: "
            f"{e}"
        )

        return

    st.subheader(
        "💾 Exportar rota"
    )

    if permite_exportacoes_usuario():

        dataframe = (
            dataframe_rota_otimizada(
                locais,
                voltar_inicio,
            )
        )

        csv_bytes = (
            dataframe
            .to_csv(
                index=False,
                sep=";",
            )
            .encode(
                "utf-8-sig"
            )
        )

        excel_bytes = (
            gerar_excel_rota(
                dataframe,
                dados,
            )
        )

        html_bytes = (
            mapa.get_root()
            .render()
            .encode(
                "utf-8"
            )
        )

        c1, c2, c3 = (
            st.columns(
                3
            )
        )

        with c1:

            st.download_button(
                "⬇️ CSV",
                data=csv_bytes,
                file_name="rota.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with c2:

            st.download_button(
                "⬇️ Excel",
                data=excel_bytes,
                file_name="rota.xlsx",
                use_container_width=True,
            )

        with c3:

            st.download_button(
                "🗺️ Mapa HTML",
                data=html_bytes,
                file_name="mapa.html",
                mime="text/html",
                use_container_width=True,
            )

    else:

        st.info(
            "🔒 Exportação em CSV, Excel e "
            "HTML está disponível no plano Pro."
        )

        if st.button(
            "💎 Ver plano Pro",
            key="resultado_ver_pro",
        ):

            st.session_state.pagina_app = (
                "planos"
            )

            st.rerun()

    st.subheader(
        "🧭 Navegação no Google Maps"
    )

    trechos = (
        dividir_rota_google_maps(
            locais,
            5,
        )
    )

    if len(
        trechos
    ) == 1:

        st.link_button(
            "🧭 Abrir no Google Maps",
            gerar_url_google_maps(
                trechos[0]
            ),
            use_container_width=True,
        )

    else:

        for numero, trecho in enumerate(
            trechos,
            start=1,
        ):

            with st.expander(
                f"Trecho {numero}: "
                f"{trecho[0]['nome']} → "
                f"{trecho[-1]['nome']}"
            ):

                st.link_button(
                    f"🧭 Abrir trecho {numero}",
                    gerar_url_google_maps(
                        trecho
                    ),
                    use_container_width=True,
                )

    st.subheader(
        "🗺️ Mapa"
    )

    st_folium(
        mapa,
        height=600,
        use_container_width=True,
        returned_objects=[],
    )


# ============================================================
# PÁGINA PLANOS / MERCADO PAGO
# ============================================================

def renderizar_assinatura_atual():

    try:

        assinatura = (
            obter_assinatura_atual()
        )

    except Exception as e:

        st.warning(
            "Não foi possível consultar "
            f"a assinatura: {e}"
        )

        return

    if not assinatura:

        if (
            perfil_usuario_efetivo
            and
            perfil_usuario_efetivo.get(
                "plano"
            ) == "Pro"
        ):

            if usuario_e_admin:

                st.info(
                    "🛡️ Sua conta possui acesso Pro "
                    "administrativo. Não há uma "
                    "assinatura Mercado Pago vinculada."
                )

            else:

                st.info(
                    "Seu acesso Pro foi concedido "
                    "administrativamente."
                )

        return

    status = assinatura.get(
        "status"
    )

    st.subheader(
        "💳 Sua assinatura"
    )

    with st.container(
        border=True
    ):

        c1, c2, c3 = (
            st.columns(
                3
            )
        )

        with c1:

            st.metric(
                "Plano",
                assinatura.get(
                    "plano",
                    "—",
                ),
            )

        with c2:

            st.metric(
                "Status",
                nome_status_assinatura(
                    status
                ),
            )

        with c3:

            st.metric(
                "Valor",
                (
                    formatar_dinheiro(
                        assinatura.get(
                            "amount"
                        )
                    )
                    + "/mês"
                ),
            )

        if assinatura.get(
            "next_payment_date"
        ):

            st.caption(
                "Próxima cobrança: "
                f"**{formatar_data(assinatura['next_payment_date'])}**"
            )

        if assinatura.get(
            "last_payment_status"
        ):

            st.caption(
                "Último pagamento: "
                f"**{assinatura['last_payment_status']}**"
            )

        c1, c2 = (
            st.columns(
                2
            )
        )

        with c1:

            if st.button(
                "🔄 Atualizar status",
                use_container_width=True,
                key="mp_sync_assinatura",
            ):

                try:

                    with st.spinner(
                        "Consultando Mercado Pago..."
                    ):

                        mercado_pago_backend(
                            "sync"
                        )

                    st.session_state.mensagem_sistema = {
                        "tipo":
                            "success",

                        "texto":
                            (
                                "Status da assinatura "
                                "atualizado."
                            ),
                    }

                    st.rerun()

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

        with c2:

            if str(
                status
            ).lower() in (
                "authorized",
                "pending",
                "paused",
            ):

                confirmar = st.checkbox(
                    "Confirmo o cancelamento",
                    key="mp_confirmar_cancelamento",
                )

                if st.button(
                    "Cancelar assinatura",
                    use_container_width=True,
                    disabled=(
                        not confirmar
                    ),
                    key="mp_cancelar_assinatura",
                ):

                    try:

                        with st.spinner(
                            "Cancelando assinatura..."
                        ):

                            mercado_pago_backend(
                                "cancel"
                            )

                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    "Assinatura cancelada."
                                ),
                        }

                        st.rerun()

                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


def renderizar_planos():

    st.subheader(
        "💎 Planos"
    )

    st.write(
        "Escolha o nível de recursos adequado "
        "ao seu uso do Otimizador de Rotas."
    )

    try:

        planos = listar_planos(
            apenas_ativos=True
        )

    except Exception as e:

        st.error(
            str(
                e
            )
        )

        return

    plano_atual = (
        perfil_usuario_efetivo.get(
            "plano"
        )
        if perfil_usuario_efetivo
        else
        "Gratuito"
    )

    st.info(
        f"Seu plano atual: **{plano_atual}**"
    )

    colunas = st.columns(
        len(
            planos
        )
    )

    for coluna, plano in zip(
        colunas,
        planos,
    ):

        with coluna:

            with st.container(
                border=True
            ):

                st.markdown(
                    f"## {plano['nome']}"
                )

                preco = float(
                    plano.get(
                        "preco_mensal"
                    )
                    or
                    0
                )

                if plano[
                    "nome"
                ] == "Gratuito":

                    st.markdown(
                        "### Grátis"
                    )

                elif preco > 0:

                    st.markdown(
                        "### "
                        f"{formatar_dinheiro(preco)}"
                        "/mês"
                    )

                else:

                    st.markdown(
                        "### Preço a definir"
                    )

                if plano.get(
                    "descricao"
                ):

                    st.write(
                        plano[
                            "descricao"
                        ]
                    )

                limite_mes = int(
                    plano[
                        "limite_rotas_mes"
                    ]
                )

                if limite_mes > 0:

                    st.write(
                        f"✓ **{limite_mes}** "
                        "cálculos/mês"
                    )

                else:

                    st.write(
                        "✓ Cálculos ilimitados"
                    )

                st.write(
                    "✓ Até "
                    f"**{plano['limite_locais_rota']}** "
                    "locais por rota"
                )

                st.write(
                    "✓ Modo Rápido"
                )

                if plano[
                    "permite_preciso"
                ]:

                    st.write(
                        "✓ Modo Preciso"
                    )

                else:

                    st.write(
                        "— Modo Preciso"
                    )

                if plano[
                    "permite_recorrentes"
                ]:

                    st.write(
                        "✓ Rotas recorrentes"
                    )

                else:

                    st.write(
                        "— Rotas recorrentes"
                    )

                if plano[
                    "historico_dias"
                ] is None:

                    st.write(
                        "✓ Histórico completo"
                    )

                else:

                    st.write(
                        "✓ Histórico dos últimos "
                        f"**{plano['historico_dias']} dias**"
                    )

                if plano[
                    "permite_exportacoes"
                ]:

                    st.write(
                        "✓ Exportação CSV, Excel e HTML"
                    )

                else:

                    st.write(
                        "— Exportações avançadas"
                    )

                if plano[
                    "nome"
                ] == plano_atual:

                    st.success(
                        "✓ Seu plano atual"
                    )

                elif plano[
                    "nome"
                ] == "Pro":

                    if preco <= 0:

                        st.info(
                            "Preço ainda não definido "
                            "pelo administrador."
                        )

                    elif st.button(
                        "💳 Assinar Pro",
                        type="primary",
                        use_container_width=True,
                        key="assinar_plano_pro",
                    ):

                        try:

                            with st.spinner(
                                "Preparando checkout "
                                "no Mercado Pago..."
                            ):

                                resposta = (
                                    mercado_pago_backend(
                                        "create",
                                        "Pro",
                                    )
                                )

                            if resposta.get(
                                "already_active"
                            ):

                                st.success(
                                    "Sua assinatura Pro "
                                    "já está ativa."
                                )

                            else:

                                checkout_url = (
                                    resposta.get(
                                        "checkout_url"
                                    )
                                )

                                if checkout_url:

                                    st.session_state.mp_checkout_url = (
                                        checkout_url
                                    )

                                    st.session_state.mp_checkout_plano = (
                                        "Pro"
                                    )

                                else:

                                    st.warning(
                                        "O Mercado Pago não "
                                        "retornou um link "
                                        "de pagamento."
                                    )

                        except Exception as e:

                            st.error(
                                str(
                                    e
                                )
                            )

    if st.session_state.mp_checkout_url:

        st.divider()

        st.success(
            "Checkout criado. "
            "Continue o pagamento no Mercado Pago."
        )

        st.link_button(
            "💳 Continuar no Mercado Pago",
            st.session_state.mp_checkout_url,
            type="primary",
            use_container_width=True,
        )

        st.caption(
            "Depois de concluir o pagamento, "
            "retorne ao aplicativo. O plano será "
            "liberado automaticamente pelo webhook; "
            "você também pode usar "
            "'Atualizar status'."
        )

    st.divider()

    renderizar_assinatura_atual()


# ============================================================
# HISTÓRICO
# ============================================================

def renderizar_historico():

    st.subheader(
        "📊 Histórico"
    )

    limite_historico = (
        historico_dias_usuario()
    )

    if limite_historico is None:

        periodo = st.selectbox(
            "Período",
            [
                "Últimos 7 dias",
                "Últimos 30 dias",
                "Últimos 90 dias",
                "Todo o histórico",
            ],
        )

        mapa = {
            "Últimos 7 dias":
                7,

            "Últimos 30 dias":
                30,

            "Últimos 90 dias":
                90,

            "Todo o histórico":
                None,
        }

        dias = mapa[
            periodo
        ]

    else:

        dias = int(
            limite_historico
        )

        st.info(
            "Seu plano permite consultar "
            f"os últimos **{dias} dias** "
            "de histórico."
        )

    try:

        execucoes = (
            listar_execucoes_usuario(
                dias=dias
            )
        )

        total_rotas = (
            contar_rotas_usuario()
        )

        total_locais = (
            contar_locais_salvos_usuario()
        )

    except Exception as e:

        st.error(
            str(
                e
            )
        )

        return

    total_km = (
        sum(
            float(
                item.get(
                    "distancia_m",
                    0,
                )
            )

            for item
            in execucoes
        )
        /
        1000
    )

    total_pontos = sum(
        int(
            item.get(
                "quantidade_locais",
                0,
            )
        )

        for item
        in execucoes
    )

    c1, c2, c3, c4, c5 = (
        st.columns(
            5
        )
    )

    c1.metric(
        "Rotas salvas",
        total_rotas,
    )

    c2.metric(
        "Locais salvos",
        total_locais,
    )

    c3.metric(
        "Cálculos",
        len(
            execucoes
        ),
    )

    c4.metric(
        "Km",
        f"{total_km:.1f}",
    )

    c5.metric(
        "Locais roteados",
        total_pontos,
    )

    st.divider()

    for execucao in execucoes:

        with st.container(
            border=True
        ):

            st.markdown(
                "### "
                +
                (
                    execucao.get(
                        "nome_rota"
                    )
                    or
                    "Rota não salva"
                )
            )

            st.caption(
                formatar_data(
                    execucao.get(
                        "created_at"
                    )
                )
            )

            if st.button(
                "📂 Abrir resultado",
                key=(
                    "abrir_hist_"
                    f"{execucao['id']}"
                ),
                use_container_width=True,
            ):

                st.session_state.resultado_rota = (
                    execucao.get(
                        "dados"
                    )
                    or
                    {}
                )

                st.session_state.resultado_historico = True
                st.session_state.rota_aberta_salva = False
                st.session_state.resultado_direto = False
                st.session_state.pagina_app = "nova"

                st.rerun()


# ============================================================
# LOCAIS
# ============================================================

def renderizar_locais_salvos():

    st.subheader(
        "📍 Locais salvos"
    )

    try:

        locais = (
            listar_locais_salvos_usuario()
        )

    except Exception as e:

        st.error(
            str(
                e
            )
        )

        return

    editando_endereco = None

    if (
        st.session_state
        .local_endereco_editando_id
    ):

        editando_endereco = next(
            (
                item

                for item
                in locais

                if item[
                    "id"
                ]
                ==
                st.session_state
                .local_endereco_editando_id
            ),
            None,
        )

    if editando_endereco:

        with st.container(
            border=True
        ):

            st.markdown(
                "### 📍 Alterar endereço"
            )

            st.write(
                editando_endereco[
                    "endereco"
                ]
            )

            novo = campo_local(
                chave=(
                    "editar_endereco_"
                    f"{st.session_state.local_endereco_edit_version}"
                ),
                label="Novo endereço",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
            )

            c1, c2 = st.columns(
                2
            )

            with c1:

                if st.button(
                    "💾 Salvar endereço",
                    type="primary",
                    use_container_width=True,
                ):

                    if not novo:

                        st.error(
                            "Selecione o endereço."
                        )

                    else:

                        atualizar_local_banco(
                            editando_endereco[
                                "id"
                            ],
                            editando_endereco[
                                "nome"
                            ],
                            editando_endereco.get(
                                "observacao",
                                "",
                            ),
                            novo,
                        )

                        st.session_state.local_endereco_editando_id = None
                        st.session_state.local_endereco_edit_version += 1

                        st.rerun()

            with c2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                ):

                    st.session_state.local_endereco_editando_id = None
                    st.rerun()

    elif (
        st.session_state
        .local_cadastro_aberto
    ):

        versao = (
            st.session_state
            .local_form_version
        )

        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Cadastrar novo local"
            )

            nome = st.text_input(
                "Nome do cliente/local",
                key=(
                    f"novo_nome_{versao}"
                ),
            )

            endereco = campo_local(
                chave=(
                    f"novo_endereco_{versao}"
                ),
                label="Endereço",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
            )

            observacao = st.text_area(
                "Observação",
                key=(
                    f"nova_obs_{versao}"
                ),
            )

            c1, c2 = st.columns(
                2
            )

            with c1:

                if st.button(
                    "💾 Salvar local",
                    type="primary",
                    use_container_width=True,
                ):

                    if not endereco:

                        st.error(
                            "Selecione um endereço."
                        )

                    else:

                        salvar_local_banco(
                            (
                                nome.strip()
                                or
                                nome_padrao_local(
                                    endereco
                                )
                            ),
                            endereco,
                            observacao,
                        )

                        st.session_state.local_cadastro_aberto = False
                        st.session_state.local_form_version += 1

                        st.rerun()

            with c2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                ):

                    st.session_state.local_cadastro_aberto = False
                    st.rerun()

    else:

        if st.button(
            "➕ Cadastrar novo local",
            type="primary",
            use_container_width=True,
        ):

            st.session_state.local_cadastro_aberto = True
            st.session_state.local_form_version += 1

            st.rerun()

    st.divider()

    filtro = normalizar_texto(
        st.text_input(
            "🔎 Buscar local"
        )
    )

    for local in locais:

        texto = normalizar_texto(
            " ".join(
                [
                    str(
                        local.get(
                            "nome"
                        )
                        or
                        ""
                    ),

                    str(
                        local.get(
                            "endereco"
                        )
                        or
                        ""
                    ),

                    str(
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                ]
            )
        )

        if (
            filtro
            and
            filtro not in texto
        ):

            continue

        with st.container(
            border=True
        ):

            st.markdown(
                f"### {local['nome']}"
            )

            st.write(
                f"📍 {local['endereco']}"
            )

            if local.get(
                "observacao"
            ):

                st.caption(
                    f"📝 {local['observacao']}"
                )

            c1, c2 = (
                st.columns(
                    2
                )
            )

            with c1:

                if st.button(
                    "🚗 Usar na rota",
                    key=(
                        f"usar_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    iniciar_nova_rota(
                        [
                            local[
                                "id"
                            ]
                        ]
                    )

                    st.rerun()

            with c2:

                if st.button(
                    "📍 Alterar endereço",
                    key=(
                        f"end_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.local_endereco_editando_id = (
                        local[
                            "id"
                        ]
                    )

                    st.session_state.local_endereco_edit_version += 1

                    st.rerun()

            with st.expander(
                "⚙️ Editar / excluir"
            ):

                nome = st.text_input(
                    "Nome",
                    value=local[
                        "nome"
                    ],
                    key=(
                        f"nome_{local['id']}"
                    ),
                )

                observacao = st.text_area(
                    "Observação",
                    value=(
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                    key=(
                        f"obs_{local['id']}"
                    ),
                )

                if st.button(
                    "💾 Salvar",
                    key=(
                        f"salvar_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    atualizar_local_banco(
                        local[
                            "id"
                        ],
                        nome,
                        observacao,
                    )

                    st.rerun()

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"conf_{local['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"excluir_{local['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    excluir_local_banco(
                        local[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# MINHAS ROTAS
# ============================================================

def renderizar_minhas_rotas():

    st.subheader(
        "📂 Minhas rotas"
    )

    try:

        rotas = (
            listar_rotas_usuario()
        )

    except Exception as e:

        st.error(
            str(
                e
            )
        )

        return

    if not rotas:

        st.info(
            "Você ainda não possui rotas salvas."
        )

        return

    for rota in rotas:

        dados = (
            rota.get(
                "dados"
            )
            or
            {}
        )

        with st.container(
            border=True
        ):

            st.markdown(
                f"### {rota['nome']}"
            )

            st.caption(
                "Última alteração: "
                f"{formatar_data(rota['updated_at'])}"
            )

            st.write(
                f"{len(dados.get('locais_base') or [])} "
                "local(is)"
            )

            c1, c2, c3 = (
                st.columns(
                    3
                )
            )

            with c1:

                if st.button(
                    "📂 Abrir",
                    key=(
                        f"abrir_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.resultado_rota = (
                        dados
                    )

                    st.session_state.rota_atual_id = (
                        rota[
                            "id"
                        ]
                    )

                    st.session_state.rota_atual_nome = (
                        rota[
                            "nome"
                        ]
                    )

                    st.session_state.rota_aberta_salva = True
                    st.session_state.resultado_historico = False
                    st.session_state.resultado_direto = False
                    st.session_state.pagina_app = "nova"

                    st.rerun()

            with c2:

                if st.button(
                    "✏️ Editar",
                    key=(
                        f"editar_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.edicao_rota = (
                        rota
                    )

                    st.session_state.edicao_version += 1
                    st.session_state.pagina_app = "editar"

                    st.rerun()

            with c3:

                if st.button(
                    "🔄 Recalcular",
                    key=(
                        f"recalc_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        resultado = (
                            calcular_rota_a_partir_de_locais(
                                (
                                    dados.get(
                                        "locais_base"
                                    )
                                    or
                                    []
                                ),
                                bool(
                                    dados.get(
                                        "voltar_inicio",
                                        False,
                                    )
                                ),
                                dados.get(
                                    "objetivo",
                                    "Menor distância",
                                ),
                                dados.get(
                                    "modo",
                                    "Rápido",
                                ),
                            )
                        )

                        registrar_execucao_seguro(
                            resultado,
                            rota[
                                "id"
                            ],
                            rota[
                                "nome"
                            ],
                        )

                        st.session_state.resultado_rota = (
                            resultado
                        )

                        st.session_state.rota_atual_id = (
                            rota[
                                "id"
                            ]
                        )

                        st.session_state.rota_atual_nome = (
                            rota[
                                "nome"
                            ]
                        )

                        st.session_state.rota_aberta_salva = True
                        st.session_state.pagina_app = "nova"

                        st.rerun()

                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )

            with st.expander(
                "⚙️ Gerenciar"
            ):

                novo_nome = (
                    st.text_input(
                        "Nome",
                        value=rota[
                            "nome"
                        ],
                        key=(
                            f"rota_nome_{rota['id']}"
                        ),
                    )
                )

                if st.button(
                    "💾 Renomear",
                    key=(
                        f"renomear_{rota['id']}"
                    ),
                ):

                    renomear_rota_banco(
                        rota[
                            "id"
                        ],
                        novo_nome,
                    )

                    st.rerun()

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"rota_conf_{rota['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"rota_excluir_{rota['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                ):

                    excluir_rota_banco(
                        rota[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# RECORRENTES
# ============================================================

def renderizar_recorrentes():

    st.subheader(
        "🔁 Rotas recorrentes"
    )

    if not permite_recorrentes_usuario():

        st.warning(
            "🔒 Rotas recorrentes estão "
            "disponíveis no plano Pro."
        )

        st.write(
            "No Pro você pode manter modelos "
            "de rotas e selecionar somente os "
            "clientes que serão atendidos no dia."
        )

        if st.button(
            "💎 Conhecer o plano Pro",
            type="primary",
        ):

            st.session_state.pagina_app = "planos"
            st.rerun()

        return

    locais = (
        listar_locais_salvos_usuario()
    )

    recorrentes = (
        listar_recorrentes_usuario()
    )

    mapa_locais = {
        local["id"]:
            local

        for local
        in locais
    }

    ids_locais = list(
        mapa_locais.keys()
    )

    recorrente_id = (
        st.session_state
        .recorrente_gerar_id
    )

    recorrente = next(
        (
            item

            for item
            in recorrentes

            if item[
                "id"
            ] == recorrente_id
        ),
        None,
    )

    if recorrente:

        ids_validos = [
            x

            for x
            in (
                recorrente.get(
                    "local_ids"
                )
                or
                []
            )

            if x in mapa_locais
        ]

        with st.container(
            border=True
        ):

            st.markdown(
                "### 🚗 Rota de hoje"
            )

            selecionados = (
                st.multiselect(
                    "Locais atendidos hoje",
                    options=ids_validos,
                    default=ids_validos,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )
            )

            if len(
                selecionados
            ) >= 2:

                inicio = st.selectbox(
                    "Ponto inicial",
                    selecionados,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )

                voltar = st.checkbox(
                    "Retornar ao início",
                    value=bool(
                        recorrente.get(
                            "voltar_inicio",
                            True,
                        )
                    ),
                )

                fim = None

                if not voltar:

                    fim = st.selectbox(
                        "Ponto final",
                        [
                            x

                            for x
                            in selecionados

                            if x != inicio
                        ],
                        format_func=(
                            lambda x:
                            mapa_locais[
                                x
                            ][
                                "nome"
                            ]
                        ),
                    )

                objetivo = st.radio(
                    "Otimizar por",
                    [
                        "Menor distância",
                        "Menor tempo",
                    ],
                    index=(
                        0
                        if recorrente.get(
                            "objetivo"
                        )
                        ==
                        "Menor distância"
                        else
                        1
                    ),
                    horizontal=True,
                )

                modos = (
                    opcoes_modo_usuario()
                )

                modo_original = (
                    recorrente.get(
                        "modo",
                        "Rápido",
                    )
                )

                modo_default = (
                    modo_original
                    if modo_original
                    in modos
                    else
                    "Rápido"
                )

                modo = st.radio(
                    "Modo",
                    modos,
                    index=modos.index(
                        modo_default
                    ),
                    horizontal=True,
                )

                if st.button(
                    "🚗 Calcular rota de hoje",
                    type="primary",
                    use_container_width=True,
                ):

                    intermediarios = [
                        x

                        for x
                        in selecionados

                        if x not in (
                            inicio,
                            fim,
                        )
                    ]

                    locais_solver = [
                        converter_local(
                            local_salvo_para_origem(
                                mapa_locais[
                                    inicio
                                ]
                            ),
                            "Início",
                        )
                    ]

                    for i, item_id in enumerate(
                        intermediarios,
                        start=1,
                    ):

                        locais_solver.append(
                            converter_local(
                                local_salvo_para_origem(
                                    mapa_locais[
                                        item_id
                                    ]
                                ),
                                f"Parada {i}",
                            )
                        )

                    if not voltar:

                        locais_solver.append(
                            converter_local(
                                local_salvo_para_origem(
                                    mapa_locais[
                                        fim
                                    ]
                                ),
                                "Fim",
                            )
                        )

                    try:

                        resultado = (
                            calcular_rota_a_partir_de_locais(
                                locais_solver,
                                voltar,
                                objetivo,
                                modo,
                            )
                        )

                        execucao, _ = (
                            registrar_execucao_seguro(
                                resultado,
                                nome_rota=(
                                    recorrente[
                                        "nome"
                                    ]
                                ),
                            )
                        )

                        st.session_state.resultado_rota = (
                            resultado
                        )

                        st.session_state.resultado_direto = True

                        st.session_state.resultado_direto_titulo = (
                            "🔁 Gerada a partir de "
                            f"**{recorrente['nome']}**"
                        )

                        st.session_state.ultima_execucao_id = (
                            execucao[
                                "id"
                            ]
                            if execucao
                            else
                            None
                        )

                        st.session_state.rota_nome_sugerido = (
                            recorrente[
                                "nome"
                            ]
                            +
                            " - "
                            +
                            datetime.now(
                                ZoneInfo(
                                    "America/Sao_Paulo"
                                )
                            ).strftime(
                                "%d-%m-%Y"
                            )
                        )

                        st.session_state.recorrente_gerar_id = None
                        st.session_state.pagina_app = "nova"

                        st.rerun()

                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )

    if st.button(
        "➕ Criar rota recorrente",
        type="primary",
        use_container_width=True,
    ):

        st.session_state.recorrente_cadastro_aberto = True
        st.session_state.recorrente_form_version += 1

        st.rerun()

    if (
        st.session_state
        .recorrente_cadastro_aberto
    ):

        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Nova recorrente"
            )

            nome = st.text_input(
                "Nome da recorrente"
            )

            selecionados = st.multiselect(
                "Locais",
                ids_locais,
                format_func=(
                    lambda x:
                    mapa_locais[
                        x
                    ][
                        "nome"
                    ]
                ),
            )

            inicio = None
            fim = None
            voltar = True

            if len(
                selecionados
            ) >= 2:

                inicio = st.selectbox(
                    "Ponto inicial",
                    selecionados,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )

                voltar = st.checkbox(
                    "Retornar ao início",
                    value=True,
                )

                if not voltar:

                    fim = st.selectbox(
                        "Ponto final",
                        [
                            x

                            for x
                            in selecionados

                            if x != inicio
                        ],
                        format_func=(
                            lambda x:
                            mapa_locais[
                                x
                            ][
                                "nome"
                            ]
                        ),
                    )

            objetivo = st.radio(
                "Otimizar por",
                [
                    "Menor distância",
                    "Menor tempo",
                ],
                index=1,
                horizontal=True,
            )

            modo = st.radio(
                "Modo",
                opcoes_modo_usuario(),
                horizontal=True,
            )

            observacao = st.text_area(
                "Observação"
            )

            if st.button(
                "💾 Salvar recorrente",
                type="primary",
                use_container_width=True,
            ):

                try:

                    salvar_recorrente_banco(
                        nome,
                        selecionados,
                        inicio,
                        fim,
                        voltar,
                        objetivo,
                        modo,
                        observacao,
                    )

                    st.session_state.recorrente_cadastro_aberto = False

                    st.rerun()

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

    st.divider()

    for item in recorrentes:

        ids_validos = [
            x

            for x
            in (
                item.get(
                    "local_ids"
                )
                or
                []
            )

            if x in mapa_locais
        ]

        with st.container(
            border=True
        ):

            st.markdown(
                f"### 🔁 {item['nome']}"
            )

            st.caption(
                f"{len(ids_validos)} locais · "
                f"{item['objetivo']} · "
                f"{item['modo']}"
            )

            c1, c2 = (
                st.columns(
                    2
                )
            )

            with c1:

                if st.button(
                    "🚗 Gerar rota de hoje",
                    key=(
                        f"gerar_rec_{item['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.recorrente_gerar_id = (
                        item[
                            "id"
                        ]
                    )

                    st.session_state.recorrente_gerar_version += 1

                    st.rerun()

            with c2:

                if st.button(
                    "📋 Duplicar",
                    key=(
                        f"dup_rec_{item['id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        duplicar_recorrente_banco(
                            item
                        )

                        st.rerun()

                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )

            with st.expander(
                "🗑️ Excluir"
            ):

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"conf_rec_{item['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir recorrente",
                    key=(
                        f"del_rec_{item['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    excluir_recorrente_banco(
                        item[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# EDIÇÃO DE ROTA
# ============================================================

def renderizar_edicao_rota():

    rota = (
        st.session_state
        .edicao_rota
    )

    if not rota:

        st.warning(
            "Nenhuma rota selecionada."
        )

        return

    dados = (
        rota.get(
            "dados"
        )
        or
        {}
    )

    locais = (
        dados.get(
            "locais_base"
        )
        or
        []
    )

    st.subheader(
        f"✏️ Editar rota — {rota['nome']}"
    )

    ativos = []

    for i, local in enumerate(
        locais
    ):

        if st.checkbox(
            local[
                "nome"
            ],
            value=True,
            key=(
                f"edit_"
                f"{st.session_state.edicao_version}_"
                f"{i}"
            ),
        ):

            ativos.append(
                local
            )

    if len(
        ativos
    ) < 2:

        st.warning(
            "Mantenha pelo menos dois locais."
        )

        return

    validar_limite_visual = (
        limite_locais_usuario()
    )

    if (
        validar_limite_visual > 0
        and
        len(
            ativos
        ) > validar_limite_visual
    ):

        st.warning(
            "Seu plano permite "
            f"{validar_limite_visual} locais."
        )

    modo = dados.get(
        "modo",
        "Rápido",
    )

    if (
        modo == "Preciso"
        and
        not permite_preciso_usuario()
    ):

        st.warning(
            "A rota foi salva em modo Preciso, "
            "mas seu plano atual não possui "
            "essa modalidade. O recálculo "
            "será feito no modo Rápido."
        )

        modo = "Rápido"

    if st.button(
        "🚗 Recalcular rota editada",
        type="primary",
        use_container_width=True,
    ):

        try:

            resultado = (
                calcular_rota_a_partir_de_locais(
                    ativos,
                    bool(
                        dados.get(
                            "voltar_inicio",
                            False,
                        )
                    ),
                    dados.get(
                        "objetivo",
                        "Menor distância",
                    ),
                    modo,
                )
            )

            atualizar_rota_completa_banco(
                rota[
                    "id"
                ],
                rota[
                    "nome"
                ],
                resultado,
            )

            registrar_execucao_seguro(
                resultado,
                rota[
                    "id"
                ],
                rota[
                    "nome"
                ],
            )

            st.session_state.resultado_rota = resultado
            st.session_state.rota_atual_id = rota["id"]
            st.session_state.rota_atual_nome = rota["nome"]
            st.session_state.rota_aberta_salva = True
            st.session_state.pagina_app = "nova"
            st.session_state.edicao_rota = None

            st.rerun()

        except Exception as e:

            st.error(
                str(
                    e
                )
            )


# ============================================================
# ADMINISTRAÇÃO
# ============================================================

def listar_usuarios_admin():

    if not usuario_e_admin:

        raise PermissionError(
            "Acesso não autorizado."
        )

    return supabase_request(
        "usuarios_app",
        "GET",
        params={
            "select":
                (
                    "user_id,nome,email,plano,"
                    "ativo,limite_rotas_mes,"
                    "limite_locais_rota,"
                    "permite_preciso,"
                    "usar_limites_personalizados,"
                    "is_admin,"
                    "created_at,updated_at"
                ),

            "order":
                "created_at.desc",
        },
    )


def atualizar_usuario_admin(
    user_id_alvo,
    plano,
    ativo,
    personalizado,
    limite_rotas,
    limite_locais,
    permite_preciso,
):

    if not usuario_e_admin:

        raise PermissionError(
            "Acesso não autorizado."
        )

    resultado = supabase_request(
        "usuarios_app",
        "PATCH",
        params={
            "user_id":
                f"eq.{user_id_alvo}",
        },
        json_body={
            "plano":
                plano,

            "ativo":
                bool(
                    ativo
                ),

            "usar_limites_personalizados":
                bool(
                    personalizado
                ),

            "limite_rotas_mes":
                int(
                    limite_rotas
                ),

            "limite_locais_rota":
                int(
                    limite_locais
                ),

            "permite_preciso":
                bool(
                    permite_preciso
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Usuário não encontrado."
        )

    return resultado[0]


def atualizar_plano_admin(
    nome,
    limite_rotas,
    limite_locais,
    permite_preciso,
    permite_recorrentes,
    historico_completo,
    historico_dias,
    permite_exportacoes,
    preco_mensal,
):

    if not usuario_e_admin:

        raise PermissionError(
            "Acesso não autorizado."
        )

    if nome == "Gratuito":

        preco_mensal = 0

    resultado = supabase_request(
        "planos",
        "PATCH",
        params={
            "nome":
                f"eq.{nome}",
        },
        json_body={
            "limite_rotas_mes":
                int(
                    limite_rotas
                ),

            "limite_locais_rota":
                int(
                    limite_locais
                ),

            "permite_preciso":
                bool(
                    permite_preciso
                ),

            "permite_recorrentes":
                bool(
                    permite_recorrentes
                ),

            "historico_dias":
                (
                    None
                    if historico_completo
                    else
                    int(
                        historico_dias
                    )
                ),

            "permite_exportacoes":
                bool(
                    permite_exportacoes
                ),

            "preco_mensal":
                float(
                    preco_mensal
                ),

            "moeda":
                "BRL",

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Plano não encontrado."
        )

    return resultado[0]


def renderizar_administracao():

    if not usuario_e_admin:

        st.error(
            "Acesso não autorizado."
        )

        return

    st.subheader(
        "🛠️ Administração"
    )

    try:

        usuarios = (
            listar_usuarios_admin()
        )

        planos = (
            listar_planos()
        )

    except Exception as e:

        st.error(
            str(
                e
            )
        )

        return

    total_uso = 0

    for usuario in usuarios:

        try:

            total_uso += (
                contar_execucoes_mes_usuario(
                    usuario[
                        "user_id"
                    ]
                )
            )

        except Exception:

            pass

    c1, c2, c3 = (
        st.columns(
            3
        )
    )

    c1.metric(
        "Usuários",
        len(
            usuarios
        ),
    )

    c2.metric(
        "Ativos",
        sum(
            1

            for u
            in usuarios

            if u.get(
                "ativo",
                True,
            )
        ),
    )

    c3.metric(
        "Cálculos este mês",
        total_uso,
    )

    st.divider()

    st.subheader(
        "💎 Configuração dos planos"
    )

    st.caption(
        "Alterações aqui afetam automaticamente "
        "os usuários que não possuem "
        "regras individuais."
    )

    for plano in planos:

        with st.expander(
            f"💎 {plano['nome']}"
        ):

            if plano[
                "nome"
            ] == "Gratuito":

                st.text_input(
                    "Preço mensal",
                    value="Grátis",
                    disabled=True,
                    key=(
                        f"preco_gratis_{plano['nome']}"
                    ),
                )

                preco_mensal = 0.0

            else:

                preco_mensal = (
                    st.number_input(
                        "Preço mensal (R$)",
                        min_value=0.0,
                        max_value=99999.0,
                        value=float(
                            plano.get(
                                "preco_mensal"
                            )
                            or
                            0
                        ),
                        step=1.0,
                        format="%.2f",
                        key=(
                            f"plano_preco_{plano['nome']}"
                        ),
                    )
                )

                if preco_mensal <= 0:

                    st.warning(
                        "Enquanto o preço estiver em "
                        "R$ 0,00, o checkout deste "
                        "plano ficará desabilitado."
                    )

            limite_rotas = st.number_input(
                "Cálculos por mês",
                min_value=0,
                max_value=100000,
                value=int(
                    plano[
                        "limite_rotas_mes"
                    ]
                ),
                step=10,
                key=(
                    f"plano_rotas_{plano['nome']}"
                ),
            )

            limite_locais = st.number_input(
                "Locais por rota",
                min_value=2,
                max_value=50,
                value=int(
                    plano[
                        "limite_locais_rota"
                    ]
                ),
                key=(
                    f"plano_locais_{plano['nome']}"
                ),
            )

            permite_preciso = (
                st.checkbox(
                    "Permitir modo Preciso",
                    value=bool(
                        plano[
                            "permite_preciso"
                        ]
                    ),
                    key=(
                        f"plano_preciso_{plano['nome']}"
                    ),
                )
            )

            permite_recorrentes = (
                st.checkbox(
                    "Permitir rotas recorrentes",
                    value=bool(
                        plano[
                            "permite_recorrentes"
                        ]
                    ),
                    key=(
                        f"plano_rec_{plano['nome']}"
                    ),
                )
            )

            permite_exportacoes = (
                st.checkbox(
                    "Permitir exportações",
                    value=bool(
                        plano[
                            "permite_exportacoes"
                        ]
                    ),
                    key=(
                        f"plano_exp_{plano['nome']}"
                    ),
                )
            )

            historico_completo = (
                st.checkbox(
                    "Histórico completo",
                    value=(
                        plano[
                            "historico_dias"
                        ]
                        is None
                    ),
                    key=(
                        f"plano_hist_full_{plano['nome']}"
                    ),
                )
            )

            historico_dias = (
                st.number_input(
                    "Dias de histórico",
                    min_value=1,
                    max_value=3650,
                    value=int(
                        plano[
                            "historico_dias"
                        ]
                        or
                        30
                    ),
                    disabled=historico_completo,
                    key=(
                        f"plano_hist_{plano['nome']}"
                    ),
                )
            )

            if st.button(
                "💾 Salvar configuração do plano",
                key=(
                    f"salvar_plano_{plano['nome']}"
                ),
                use_container_width=True,
            ):

                try:

                    atualizar_plano_admin(
                        plano[
                            "nome"
                        ],
                        limite_rotas,
                        limite_locais,
                        permite_preciso,
                        permite_recorrentes,
                        historico_completo,
                        historico_dias,
                        permite_exportacoes,
                        preco_mensal,
                    )

                    st.success(
                        "Plano atualizado."
                    )

                    st.rerun()

                except Exception as e:

                    st.error(
                        str(
                            e
                        )
                    )

    st.divider()

    st.subheader(
        "👥 Usuários"
    )

    filtro = normalizar_texto(
        st.text_input(
            "🔎 Buscar usuário"
        )
    )

    nomes_planos = [
        p[
            "nome"
        ]

        for p
        in planos

        if p.get(
            "ativo",
            True,
        )
    ]

    for usuario in usuarios:

        texto = normalizar_texto(
            (
                str(
                    usuario.get(
                        "nome"
                    )
                    or
                    ""
                )
                +
                " "
                +
                str(
                    usuario.get(
                        "email"
                    )
                    or
                    ""
                )
            )
        )

        if (
            filtro
            and
            filtro not in texto
        ):

            continue

        efetivo = perfil_efetivo(
            usuario
        )

        uso = (
            contar_execucoes_mes_usuario(
                usuario[
                    "user_id"
                ]
            )
        )

        assinatura = None

        try:

            assinatura = (
                obter_assinatura_atual(
                    usuario[
                        "user_id"
                    ]
                )
            )

        except Exception:

            pass

        titulo = (
            usuario.get(
                "nome"
            )
            or
            "Usuário"
        )

        if usuario.get(
            "is_admin"
        ):

            titulo += " 🛡️"

        with st.container(
            border=True
        ):

            st.markdown(
                f"### {titulo}"
            )

            st.caption(
                usuario.get(
                    "email"
                )
                or
                ""
            )

            c1, c2, c3, c4 = (
                st.columns(
                    4
                )
            )

            c1.metric(
                "Plano",
                efetivo[
                    "plano"
                ],
            )

            limite_mes = (
                efetivo[
                    "limite_rotas_mes"
                ]
            )

            c2.metric(
                "Uso mensal",
                (
                    f"{uso} / {limite_mes}"
                    if limite_mes > 0
                    else
                    f"{uso} / ∞"
                ),
            )

            c3.metric(
                "Locais/rota",
                efetivo[
                    "limite_locais_rota"
                ],
            )

            c4.metric(
                "Status",
                (
                    "Ativo"
                    if efetivo[
                        "ativo"
                    ]
                    else
                    "Inativo"
                ),
            )

            if assinatura:

                st.caption(
                    "💳 Mercado Pago: "
                    f"**{nome_status_assinatura(assinatura['status'])}**"
                    " · "
                    f"{formatar_dinheiro(assinatura['amount'])}/mês"
                )

            with st.expander(
                "⚙️ Gerenciar usuário"
            ):

                plano_atual = (
                    usuario.get(
                        "plano",
                        "Gratuito",
                    )
                )

                if plano_atual not in nomes_planos:

                    opcoes_planos = (
                        nomes_planos
                        +
                        [
                            plano_atual
                        ]
                    )

                else:

                    opcoes_planos = (
                        nomes_planos
                    )

                plano = st.selectbox(
                    "Plano",
                    opcoes_planos,
                    index=opcoes_planos.index(
                        plano_atual
                    ),
                    key=(
                        f"admin_plano_{usuario['user_id']}"
                    ),
                )

                ativo = st.checkbox(
                    "Conta ativa",
                    value=bool(
                        usuario.get(
                            "ativo",
                            True,
                        )
                    ),
                    key=(
                        f"admin_ativo_{usuario['user_id']}"
                    ),
                )

                personalizado = st.checkbox(
                    "Usar limites personalizados",
                    value=bool(
                        usuario.get(
                            "usar_limites_personalizados",
                            False,
                        )
                    ),
                    key=(
                        f"admin_personalizado_{usuario['user_id']}"
                    ),
                )

                plano_selecionado = (
                    obter_plano(
                        plano
                    )
                )

                if personalizado:

                    limite_rotas = (
                        st.number_input(
                            "Limite mensal individual",
                            min_value=0,
                            max_value=100000,
                            value=int(
                                usuario.get(
                                    "limite_rotas_mes"
                                )
                                or
                                0
                            ),
                            key=(
                                f"admin_rotas_{usuario['user_id']}"
                            ),
                        )
                    )

                    limite_locais = (
                        st.number_input(
                            "Locais/rota individual",
                            min_value=2,
                            max_value=50,
                            value=int(
                                usuario.get(
                                    "limite_locais_rota"
                                )
                                or
                                2
                            ),
                            key=(
                                f"admin_locais_{usuario['user_id']}"
                            ),
                        )
                    )

                    preciso = (
                        st.checkbox(
                            "Permitir modo Preciso individualmente",
                            value=bool(
                                usuario.get(
                                    "permite_preciso",
                                    False,
                                )
                            ),
                            key=(
                                f"admin_preciso_{usuario['user_id']}"
                            ),
                        )
                    )

                else:

                    limite_rotas = int(
                        plano_selecionado[
                            "limite_rotas_mes"
                        ]
                    )

                    limite_locais = int(
                        plano_selecionado[
                            "limite_locais_rota"
                        ]
                    )

                    preciso = bool(
                        plano_selecionado[
                            "permite_preciso"
                        ]
                    )

                    st.info(
                        "Este usuário seguirá "
                        "automaticamente as regras "
                        f"do plano **{plano}**."
                    )

                if st.button(
                    "💾 Salvar alterações",
                    key=(
                        f"admin_salvar_{usuario['user_id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        atualizar_usuario_admin(
                            usuario[
                                "user_id"
                            ],
                            plano,
                            ativo,
                            personalizado,
                            limite_rotas,
                            limite_locais,
                            preciso,
                        )

                        st.session_state.admin_refresh += 1

                        st.success(
                            "Usuário atualizado."
                        )

                        st.rerun()

                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


# ============================================================
# PÁGINAS
# ============================================================

if (
    st.session_state.pagina_app
    == "planos"
):

    renderizar_planos()
    st.stop()


if (
    st.session_state.pagina_app
    == "administracao"
):

    renderizar_administracao()
    st.stop()


if (
    st.session_state.pagina_app
    == "minhas"
):

    renderizar_minhas_rotas()
    st.stop()


if (
    st.session_state.pagina_app
    == "locais"
):

    renderizar_locais_salvos()
    st.stop()


if (
    st.session_state.pagina_app
    == "recorrentes"
):

    renderizar_recorrentes()
    st.stop()


if (
    st.session_state.pagina_app
    == "historico"
):

    renderizar_historico()
    st.stop()


if (
    st.session_state.pagina_app
    == "editar"
):

    renderizar_edicao_rota()
    st.stop()


if (
    st.session_state.resultado_rota
    and
    (
        st.session_state.resultado_direto
        or
        st.session_state.rota_aberta_salva
        or
        st.session_state.resultado_historico
    )
):

    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )

    st.stop()


# ============================================================
# NOVA ROTA
# ============================================================

st.subheader(
    "➕ Nova rota"
)

limite_locais = (
    limite_locais_usuario()
)

if limite_locais:

    st.caption(
        "Seu plano permite até "
        f"**{limite_locais} locais por rota**."
    )

modo_entrada = st.radio(
    "Como deseja informar os locais?",
    [
        "Digitar manualmente",
        "Importar CSV/Excel",
        "Selecionar locais salvos",
    ],
    index=(
        2
        if
        st.session_state
        .preset_local_salvo_ids
        else
        0
    ),
    horizontal=True,
    key=(
        f"modo_entrada_"
        f"{st.session_state.form_version}"
    ),
)

ponto_inicio = None
ponto_fim = None
paradas = []
voltar_inicio = False


# ============================================================
# MANUAL
# ============================================================

if (
    modo_entrada
    ==
    "Digitar manualmente"
):

    voltar_inicio = st.checkbox(
        "🔄 Retornar ao ponto inicial"
    )

    c1, c2 = (
        st.columns(
            2
        )
    )

    with c1:

        ponto_inicio = campo_local(
            chave=(
                f"inicio_"
                f"{st.session_state.form_version}"
            ),
            label="Ponto inicial",
            placeholder=(
                "Digite pelo menos 3 letras..."
            ),
        )

    with c2:

        if voltar_inicio:

            ponto_fim = ponto_inicio

            st.text_input(
                "Ponto final",
                value=(
                    ponto_inicio[
                        "label"
                    ]
                    if ponto_inicio
                    else
                    ""
                ),
                disabled=True,
            )

        else:

            ponto_fim = campo_local(
                chave=(
                    f"fim_"
                    f"{st.session_state.form_version}"
                ),
                label="Ponto final",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
                focus=ponto_inicio,
            )

    c1, c2 = (
        st.columns(
            2
        )
    )

    with c1:

        if st.button(
            "➕ Adicionar parada",
            use_container_width=True,
        ):

            st.session_state.num_paradas += 1

            st.rerun()

    with c2:

        if st.button(
            "➖ Remover parada",
            disabled=(
                st.session_state.num_paradas
                <= 1
            ),
            use_container_width=True,
        ):

            st.session_state.num_paradas -= 1

            st.rerun()

    for i in range(
        st.session_state.num_paradas
    ):

        paradas.append(
            campo_local(
                chave=(
                    f"parada_"
                    f"{st.session_state.form_version}_"
                    f"{i}"
                ),
                label=(
                    f"Parada {i + 1}"
                ),
                placeholder=(
                    "Digite um endereço..."
                ),
                focus=ponto_inicio,
            )
        )


# ============================================================
# LOCAIS SALVOS
# ============================================================

elif (
    modo_entrada
    ==
    "Selecionar locais salvos"
):

    locais_salvos = (
        listar_locais_salvos_usuario()
    )

    mapa = {
        item[
            "id"
        ]:
            item

        for item
        in locais_salvos
    }

    selecionados = st.multiselect(
        "Locais",
        list(
            mapa.keys()
        ),
        default=[
            x

            for x
            in st.session_state
            .preset_local_salvo_ids

            if x in mapa
        ],
        format_func=(
            lambda x:
            mapa[
                x
            ][
                "nome"
            ]
        ),
    )

    if (
        limite_locais > 0
        and
        len(
            selecionados
        ) > limite_locais
    ):

        st.warning(
            "Seu plano permite "
            f"{limite_locais} locais."
        )

    if len(
        selecionados
    ) >= 2:

        inicio_id = st.selectbox(
            "Ponto inicial",
            selecionados,
            format_func=(
                lambda x:
                mapa[
                    x
                ][
                    "nome"
                ]
            ),
        )

        ponto_inicio = (
            local_salvo_para_origem(
                mapa[
                    inicio_id
                ]
            )
        )

        voltar_inicio = st.checkbox(
            "🔄 Retornar ao ponto inicial"
        )

        fim_id = None

        if voltar_inicio:

            ponto_fim = ponto_inicio

        else:

            fim_id = st.selectbox(
                "Ponto final",
                [
                    x

                    for x
                    in selecionados

                    if x != inicio_id
                ],
                format_func=(
                    lambda x:
                    mapa[
                        x
                    ][
                        "nome"
                    ]
                ),
            )

            ponto_fim = (
                local_salvo_para_origem(
                    mapa[
                        fim_id
                    ]
                )
            )

        paradas = [
            local_salvo_para_origem(
                mapa[
                    x
                ]
            )

            for x
            in selecionados

            if x not in (
                inicio_id,
                fim_id,
            )
        ]


# ============================================================
# IMPORTAÇÃO
# ============================================================

else:

    modelo = (
        'endereco\n'
        '"Havan, Brusque, SC"\n'
        '"Prefeitura Municipal de Brusque, Brusque, SC"\n'
    )

    st.download_button(
        "⬇️ Baixar modelo CSV",
        data=modelo.encode(
            "utf-8-sig"
        ),
        file_name="modelo.csv",
    )

    arquivo = st.file_uploader(
        "CSV ou XLSX",
        type=[
            "csv",
            "xlsx",
        ],
    )

    if arquivo:

        try:

            enderecos, hash_arquivo = (
                ler_arquivo_paradas(
                    arquivo
                )
            )

            if (
                limite_locais > 0
                and
                len(
                    enderecos
                ) > limite_locais
            ):

                st.warning(
                    "O arquivo possui "
                    f"{len(enderecos)} locais, "
                    "mas seu plano permite "
                    f"{limite_locais}."
                )

            confirmados = []

            with st.spinner(
                "Localizando endereços..."
            ):

                resultados = [
                    buscar_endereco_importado_cache(
                        endereco,
                        ORS_KEY,
                    )

                    for endereco
                    in enderecos
                ]

            for i, (
                endereco,
                candidatos,
            ) in enumerate(
                zip(
                    enderecos,
                    resultados,
                )
            ):

                if not candidatos:

                    st.error(
                        "Não encontrado: "
                        f"{endereco}"
                    )

                    continue

                pos = st.selectbox(
                    endereco,
                    list(
                        range(
                            len(
                                candidatos
                            )
                        )
                    ),
                    key=(
                        f"imp_{hash_arquivo}_{i}"
                    ),
                    format_func=(
                        lambda x,
                        c=candidatos:
                        c[
                            x
                        ][
                            "label"
                        ]
                    ),
                )

                confirmados.append(
                    candidatos[
                        pos
                    ]
                )

            if (
                len(
                    confirmados
                )
                ==
                len(
                    enderecos
                )
                and
                len(
                    confirmados
                ) >= 2
            ):

                indices = list(
                    range(
                        len(
                            confirmados
                        )
                    )
                )

                indice_inicio = st.selectbox(
                    "Ponto inicial",
                    indices,
                    format_func=(
                        lambda x:
                        confirmados[
                            x
                        ][
                            "label"
                        ]
                    ),
                )

                ponto_inicio = (
                    confirmados[
                        indice_inicio
                    ]
                )

                voltar_inicio = st.checkbox(
                    "🔄 Retornar ao ponto inicial"
                )

                indice_fim = None

                if voltar_inicio:

                    ponto_fim = (
                        ponto_inicio
                    )

                else:

                    indice_fim = st.selectbox(
                        "Ponto final",
                        [
                            x

                            for x
                            in indices

                            if x != indice_inicio
                        ],
                        format_func=(
                            lambda x:
                            confirmados[
                                x
                            ][
                                "label"
                            ]
                        ),
                    )

                    ponto_fim = (
                        confirmados[
                            indice_fim
                        ]
                    )

                paradas = [
                    local

                    for i, local
                    in enumerate(
                        confirmados
                    )

                    if i not in (
                        indice_inicio,
                        indice_fim,
                    )
                ]

                if st.button(
                    "📍 Salvar endereços em Locais",
                    use_container_width=True,
                ):

                    inseridos, ignorados = (
                        salvar_locais_em_lote(
                            confirmados
                        )
                    )

                    st.success(
                        f"{inseridos} salvo(s). "
                        f"{ignorados} ignorado(s)."
                    )

        except Exception as e:

            st.error(
                str(
                    e
                )
            )


# ============================================================
# CONFIGURAÇÃO DA OTIMIZAÇÃO
# ============================================================

st.divider()

st.subheader(
    "⚙️ Configuração da otimização"
)

c1, c2 = (
    st.columns(
        2
    )
)

with c1:

    objetivo = st.radio(
        "Otimizar por",
        [
            "Menor distância",
            "Menor tempo",
        ],
        horizontal=True,
    )

with c2:

    modos = (
        opcoes_modo_usuario()
    )

    modo = st.radio(
        "Modo",
        modos,
        horizontal=True,
    )

    if not permite_preciso_usuario():

        st.caption(
            "🔒 Modo Preciso disponível "
            "no plano Pro."
        )


# ============================================================
# CALCULAR
# ============================================================

st.divider()

if st.button(
    "🚗 Calcular melhor rota",
    type="primary",
    use_container_width=True,
):

    if not ponto_inicio:

        st.error(
            "Selecione o ponto inicial."
        )

        st.stop()

    if (
        not voltar_inicio
        and
        not ponto_fim
    ):

        st.error(
            "Selecione o ponto final."
        )

        st.stop()

    paradas_validas = [
        item

        for item
        in paradas

        if item
    ]

    if (
        voltar_inicio
        and
        not paradas_validas
    ):

        st.error(
            "Adicione pelo menos uma parada."
        )

        st.stop()

    locais = [
        converter_local(
            ponto_inicio,
            "Início",
        )
    ]

    for i, parada in enumerate(
        paradas_validas,
        start=1,
    ):

        locais.append(
            converter_local(
                parada,
                f"Parada {i}",
            )
        )

    if not voltar_inicio:

        locais.append(
            converter_local(
                ponto_fim,
                "Fim",
            )
        )

    try:

        with st.spinner(
            "Calculando a melhor rota..."
        ):

            resultado = (
                calcular_rota_a_partir_de_locais(
                    locais,
                    voltar_inicio,
                    objetivo,
                    modo,
                )
            )

        st.session_state.resultado_rota = (
            resultado
        )

        st.session_state.rota_atual_id = None
        st.session_state.rota_atual_nome = None
        st.session_state.rota_aberta_salva = False
        st.session_state.resultado_historico = False
        st.session_state.resultado_direto = False

        execucao, erro = (
            registrar_execucao_seguro(
                resultado
            )
        )

        st.session_state.ultima_execucao_id = (
            execucao[
                "id"
            ]
            if execucao
            else
            None
        )

        if erro:

            st.warning(
                "A rota foi calculada, "
                "mas o histórico não pôde "
                f"ser registrado: {erro}"
            )

    except (
        LimiteUsoError,
        ORSError,
        RouteOptimizationError,
        ValueError,
    ) as e:

        st.error(
            str(
                e
            )
        )

    except Exception as e:

        st.error(
            "Não foi possível calcular: "
            f"{e}"
        )


if st.session_state.resultado_rota:

    st.divider()

    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )
