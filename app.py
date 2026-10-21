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


# ============================================================
# EXCEÇÕES
# ============================================================

class LimiteUsoError(Exception):
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

usuario_id = dados_usuario.get(
    "sub"
)

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

    ORS_KEY = st.secrets[
        "ORS_KEY"
    ]

    ors_configurado = True

except Exception:

    ORS_KEY = None
    ors_configurado = False


try:

    SUPABASE_URL = str(
        st.secrets[
            "SUPABASE_URL"
        ]
    ).rstrip("/")

    SUPABASE_SECRET_KEY = str(
        st.secrets[
            "SUPABASE_SECRET_KEY"
        ]
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

        st.session_state[
            nome
        ] = valor


init_state(
    "num_paradas",
    1,
)

init_state(
    "erro_autocomplete",
    None,
)

init_state(
    "pagina_app",
    "nova",
)

init_state(
    "resultado_rota",
    None,
)

init_state(
    "rota_atual_id",
    None,
)

init_state(
    "rota_atual_nome",
    None,
)

init_state(
    "rota_aberta_salva",
    False,
)

init_state(
    "resultado_historico",
    False,
)

init_state(
    "resultado_direto",
    False,
)

init_state(
    "resultado_direto_titulo",
    None,
)

init_state(
    "rota_nome_sugerido",
    "",
)

init_state(
    "form_version",
    0,
)

init_state(
    "mensagem_sistema",
    None,
)

init_state(
    "edicao_rota",
    None,
)

init_state(
    "edicao_resultado",
    None,
)

init_state(
    "edicao_assinatura",
    None,
)

init_state(
    "edicao_version",
    0,
)

init_state(
    "edicao_num_novos",
    0,
)

init_state(
    "ultima_execucao_id",
    None,
)

init_state(
    "preset_local_salvo_ids",
    [],
)

init_state(
    "local_form_version",
    0,
)

init_state(
    "local_cadastro_aberto",
    False,
)

init_state(
    "local_endereco_editando_id",
    None,
)

init_state(
    "local_endereco_edit_version",
    0,
)

init_state(
    "recorrente_cadastro_aberto",
    False,
)

init_state(
    "recorrente_form_version",
    0,
)

init_state(
    "recorrente_gerar_id",
    None,
)

init_state(
    "recorrente_gerar_version",
    0,
)

init_state(
    "admin_usuario_aberto",
    None,
)

init_state(
    "admin_refresh",
    0,
)


# ============================================================
# CONTROLE DE ESTADO
# ============================================================

def limpar_edicao():

    st.session_state.edicao_rota = None

    st.session_state.edicao_resultado = None

    st.session_state.edicao_assinatura = None

    st.session_state.edicao_num_novos = 0


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

    limpar_edicao()

    st.session_state.num_paradas = 1

    st.session_state.form_version += 1

    st.session_state.preset_local_salvo_ids = list(
        preset_ids
        or
        []
    )

    st.session_state.recorrente_gerar_id = None

    st.session_state.pagina_app = (
        "nova"
    )


def iniciar_edicao(
    rota,
):

    st.session_state.edicao_version += 1

    st.session_state.edicao_rota = {
        "id":
            rota[
                "id"
            ],

        "nome":
            rota.get(
                "nome",
                "Rota sem nome",
            ),

        "dados":
            rota.get(
                "dados"
            )
            or {},
    }

    st.session_state.edicao_resultado = None

    st.session_state.edicao_assinatura = None

    st.session_state.edicao_num_novos = 0

    st.session_state.pagina_app = (
        "editar"
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

        st.success(
            texto
        )

    elif tipo == "warning":

        st.warning(
            texto
        )

    elif tipo == "error":

        st.error(
            texto
        )

    else:

        st.info(
            texto
        )


    st.session_state.mensagem_sistema = (
        None
    )


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

        headers[
            "Prefer"
        ] = prefer


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


def preparar_json(
    dados,
):

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


# ============================================================
# USUÁRIO / PLANO / LIMITES
# ============================================================

def obter_usuario_app():

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
                    "is_admin,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "limit":
                "1",
        },
    )


    if registros:

        return registros[
            0
        ]


    return None


def garantir_usuario_app():

    perfil = (
        obter_usuario_app()
    )


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
                    (
                        usuario_email
                        or
                        None
                    ),

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


        return registros[
            0
        ]


    nome_atual = (
        perfil.get(
            "nome"
        )
        or
        ""
    )


    email_atual = (
        perfil.get(
            "email"
        )
        or
        ""
    )


    if (
        nome_atual
        != usuario_nome
        or
        email_atual
        != usuario_email
    ):

        registros = supabase_request(
            "usuarios_app",
            "PATCH",
            params={
                "user_id":
                    f"eq.{usuario_id}",
            },
            json_body={
                "nome":
                    usuario_nome,

                "email":
                    (
                        usuario_email
                        or
                        None
                    ),

                "updated_at":
                    agora_iso(),
            },
            prefer="return=representation",
        )


        if registros:

            perfil = registros[
                0
            ]


    return perfil


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


    return (
        inicio,
        fim,
    )


def proximo_ciclo_texto():

    _, fim = (
        intervalo_mes_atual()
    )


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


    return len(
        registros
    )


def contar_execucoes_mes_atual():

    return contar_execucoes_mes_usuario(
        usuario_id
    )


def limite_locais_perfil(
    perfil,
):

    if not perfil:

        return 0


    try:

        return int(
            perfil.get(
                "limite_locais_rota"
            )
            or
            0
        )

    except Exception:

        return 0


def permite_preciso_perfil(
    perfil,
):

    if not perfil:

        return False


    return bool(
        perfil.get(
            "permite_preciso",
            False,
        )
    )


def opcoes_modo_usuario():

    if permite_preciso_perfil(
        perfil_usuario_app
    ):

        return [
            "Rápido",
            "Preciso",
        ]


    return [
        "Rápido",
    ]


def validar_quantidade_plano(
    quantidade_locais,
    perfil=None,
):

    perfil = (
        perfil
        or
        obter_usuario_app()
    )


    if not perfil:

        raise LimiteUsoError(
            "Não foi possível validar "
            "as regras da sua conta."
        )


    limite = limite_locais_perfil(
        perfil
    )


    if (
        limite > 0
        and
        quantidade_locais > limite
    ):

        raise LimiteUsoError(
            "Esta rota possui "
            f"{quantidade_locais} locais, "
            "mas seu limite atual é de "
            f"{limite} locais por rota."
        )


def validar_permissao_calculo(
    quantidade_locais,
    modo,
):

    if not supabase_configurado:

        raise LimiteUsoError(
            "Não foi possível validar seu plano "
            "porque o controle de uso está indisponível."
        )


    try:

        perfil = obter_usuario_app()

    except Exception as e:

        raise LimiteUsoError(
            "Não foi possível validar seu plano "
            "neste momento. Tente novamente."
        ) from e


    if not perfil:

        raise LimiteUsoError(
            "Seu perfil de uso não foi encontrado."
        )


    if not bool(
        perfil.get(
            "ativo",
            True,
        )
    ):

        raise LimiteUsoError(
            "Sua conta está inativa."
        )


    validar_quantidade_plano(
        quantidade_locais,
        perfil,
    )


    if (
        modo == "Preciso"
        and
        not bool(
            perfil.get(
                "permite_preciso",
                False,
            )
        )
    ):

        raise LimiteUsoError(
            "O modo Preciso não está disponível "
            "para sua conta."
        )


    limite_mes = int(
        perfil.get(
            "limite_rotas_mes"
        )
        or
        0
    )


    if limite_mes > 0:

        try:

            uso_mes = (
                contar_execucoes_mes_atual()
            )

        except Exception as e:

            raise LimiteUsoError(
                "Não foi possível verificar "
                "seu consumo mensal. "
                "Nenhum cálculo foi iniciado."
            ) from e


        if uso_mes >= limite_mes:

            raise LimiteUsoError(
                "Você atingiu o limite mensal "
                f"de {limite_mes} cálculo(s) de rota. "
                "Seu próximo ciclo começa em "
                f"{proximo_ciclo_texto()}."
            )


    return perfil


# ============================================================
# CARREGA PERFIL
# ============================================================

perfil_usuario_app = None

uso_rotas_mes = None

erro_perfil_usuario = None


if supabase_configurado:

    try:

        perfil_usuario_app = (
            garantir_usuario_app()
        )

        uso_rotas_mes = (
            contar_execucoes_mes_atual()
        )


    except Exception as e:

        erro_perfil_usuario = str(
            e
        )


usuario_e_admin = bool(
    perfil_usuario_app
    and
    perfil_usuario_app.get(
        "is_admin",
        False,
    )
)


modo_preciso_permitido = (
    permite_preciso_perfil(
        perfil_usuario_app
    )
)


# ============================================================
# CONTA INATIVA
# ============================================================

if (
    perfil_usuario_app
    and
    not bool(
        perfil_usuario_app.get(
            "ativo",
            True,
        )
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


    st.caption(
        "Se você acredita que isso ocorreu "
        "por engano, entre em contato com "
        "o administrador do aplicativo."
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
# ADMINISTRAÇÃO
# ============================================================

def listar_usuarios_admin():

    if not usuario_e_admin:

        raise PermissionError(
            "Acesso administrativo não autorizado."
        )


    return supabase_request(
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
    limite_rotas_mes,
    limite_locais_rota,
    permite_preciso,
):

    if not usuario_e_admin:

        raise PermissionError(
            "Acesso administrativo não autorizado."
        )


    plano = (
        plano
        or
        "Gratuito"
    ).strip()


    limite_rotas_mes = int(
        limite_rotas_mes
    )


    limite_locais_rota = int(
        limite_locais_rota
    )


    if limite_rotas_mes < 0:

        raise ValueError(
            "O limite mensal não pode ser negativo."
        )


    if limite_locais_rota < 2:

        raise ValueError(
            "O limite de locais por rota "
            "deve ser pelo menos 2."
        )


    registros = supabase_request(
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

            "limite_rotas_mes":
                limite_rotas_mes,

            "limite_locais_rota":
                limite_locais_rota,

            "permite_preciso":
                bool(
                    permite_preciso
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )


    if not registros:

        raise RuntimeError(
            "O usuário não foi encontrado."
        )


    return registros[
        0
    ]


def contar_total_tabela(
    tabela,
):

    registros = supabase_request(
        tabela,
        "GET",
        params={
            "select":
                "id",

            "limit":
                "10000",
        },
    )


    return len(
        registros
    )


def contar_usuarios_ativos_admin(
    usuarios,
):

    return sum(
        1

        for usuario
        in usuarios

        if usuario.get(
            "ativo",
            True,
        )
    )


def total_rotas_mes_admin(
    usuarios,
):

    total = 0


    for usuario in usuarios:

        total += contar_execucoes_mes_usuario(
            usuario[
                "user_id"
            ]
        )


    return total


# ============================================================
# NORMALIZAÇÃO DE TEXTO
# ============================================================

def normalizar_texto(
    texto,
):

    texto = unicodedata.normalize(
        "NFKD",
        str(
            texto
        )
        .strip()
        .lower(),
    )


    return "".join(
        caractere

        for caractere
        in texto

        if not unicodedata.combining(
            caractere
        )
    )


def renderizar_administracao():

    if not usuario_e_admin:

        st.error(
            "Você não possui permissão "
            "para acessar esta área."
        )

        return


    st.subheader(
        "🛠️ Administração"
    )


    st.write(
        "Gerencie usuários, planos, limites "
        "e permissões do aplicativo."
    )


    st.success(
        "✅ Os limites configurados nesta área "
        "já estão sendo aplicados aos cálculos."
    )


    try:

        usuarios = (
            listar_usuarios_admin()
        )


        total_locais = (
            contar_total_tabela(
                "locais_salvos"
            )
        )


        total_recorrentes = (
            contar_total_tabela(
                "rotas_recorrentes"
            )
        )


        total_rotas_mes = (
            total_rotas_mes_admin(
                usuarios
            )
        )


    except Exception as e:

        st.error(
            "Não foi possível carregar "
            f"o painel administrativo: {e}"
        )

        return


    st.subheader(
        "📊 Resumo"
    )


    (
        col1,
        col2,
        col3,
        col4,
        col5,
    ) = st.columns(
        5
    )


    with col1:

        st.metric(
            "Usuários",
            len(
                usuarios
            ),
        )


    with col2:

        st.metric(
            "Ativos",
            contar_usuarios_ativos_admin(
                usuarios
            ),
        )


    with col3:

        st.metric(
            "Rotas este mês",
            total_rotas_mes,
        )


    with col4:

        st.metric(
            "Locais cadastrados",
            total_locais,
        )


    with col5:

        st.metric(
            "Recorrentes",
            total_recorrentes,
        )


    st.divider()


    filtro = st.text_input(
        "🔎 Buscar usuário",
        placeholder=(
            "Nome ou e-mail..."
        ),
        key=(
            f"admin_busca_"
            f"{st.session_state.admin_refresh}"
        ),
    )


    filtro_norm = (
        normalizar_texto(
            filtro
        )

        if filtro

        else

        ""
    )


    usuarios_filtrados = []


    for usuario in usuarios:

        texto = " ".join(
            [
                str(
                    usuario.get(
                        "nome"
                    )
                    or
                    ""
                ),

                str(
                    usuario.get(
                        "email"
                    )
                    or
                    ""
                ),

                str(
                    usuario.get(
                        "plano"
                    )
                    or
                    ""
                ),
            ]
        )


        if (
            not filtro_norm
            or
            filtro_norm
            in
            normalizar_texto(
                texto
            )
        ):

            usuarios_filtrados.append(
                usuario
            )


    st.caption(
        f"{len(usuarios_filtrados)} de "
        f"{len(usuarios)} usuário(s)."
    )


    for usuario in usuarios_filtrados:

        user_id_alvo = usuario[
            "user_id"
        ]


        nome = (
            usuario.get(
                "nome"
            )
            or
            "Usuário"
        )


        email = (
            usuario.get(
                "email"
            )
            or
            ""
        )


        plano_atual = (
            usuario.get(
                "plano"
            )
            or
            "Gratuito"
        )


        ativo_atual = bool(
            usuario.get(
                "ativo",
                True,
            )
        )


        limite_mes_atual = int(
            usuario.get(
                "limite_rotas_mes"
            )
            or
            0
        )


        limite_locais_atual = int(
            usuario.get(
                "limite_locais_rota"
            )
            or
            50
        )


        permite_preciso_atual = bool(
            usuario.get(
                "permite_preciso",
                True,
            )
        )


        is_admin = bool(
            usuario.get(
                "is_admin",
                False,
            )
        )


        try:

            uso_mes = (
                contar_execucoes_mes_usuario(
                    user_id_alvo
                )
            )

        except Exception:

            uso_mes = 0


        with st.container(
            border=True
        ):

            titulo = nome


            if is_admin:

                titulo += " 🛡️"


            st.markdown(
                f"### {titulo}"
            )


            if email:

                st.caption(
                    email
                )


            (
                col_a,
                col_b,
                col_c,
                col_d,
            ) = st.columns(
                4
            )


            with col_a:

                st.metric(
                    "Plano",
                    plano_atual,
                )


            with col_b:

                st.metric(
                    "Uso mensal",
                    (
                        f"{uso_mes} / "
                        f"{limite_mes_atual}"

                        if limite_mes_atual > 0

                        else

                        f"{uso_mes} / ∞"
                    ),
                )


            with col_c:

                st.metric(
                    "Locais/rota",
                    limite_locais_atual,
                )


            with col_d:

                st.metric(
                    "Status",
                    (
                        "Ativo"

                        if ativo_atual

                        else

                        "Inativo"
                    ),
                )


            if (
                limite_mes_atual > 0
                and
                uso_mes >= limite_mes_atual
            ):

                st.warning(
                    "⚠️ Este usuário atingiu "
                    "o limite mensal."
                )


            with st.expander(
                "⚙️ Gerenciar usuário"
            ):

                planos = [
                    "Gratuito",
                    "Pro",
                ]


                if plano_atual not in planos:

                    planos.append(
                        plano_atual
                    )


                plano_edit = st.selectbox(
                    "Plano",
                    options=planos,
                    index=planos.index(
                        plano_atual
                    ),
                    key=(
                        f"admin_plano_"
                        f"{user_id_alvo}_"
                        f"{st.session_state.admin_refresh}"
                    ),
                )


                ativo_edit = st.checkbox(
                    "Conta ativa",
                    value=ativo_atual,
                    key=(
                        f"admin_ativo_"
                        f"{user_id_alvo}_"
                        f"{st.session_state.admin_refresh}"
                    ),
                )


                limite_mes_edit = st.number_input(
                    "Limite de rotas por mês",
                    min_value=0,
                    max_value=100000,
                    value=limite_mes_atual,
                    step=10,
                    help=(
                        "Use 0 para representar "
                        "um limite ilimitado."
                    ),
                    key=(
                        f"admin_limite_mes_"
                        f"{user_id_alvo}_"
                        f"{st.session_state.admin_refresh}"
                    ),
                )


                limite_locais_edit = st.number_input(
                    "Limite de locais por rota",
                    min_value=2,
                    max_value=50,
                    value=min(
                        max(
                            limite_locais_atual,
                            2,
                        ),
                        50,
                    ),
                    step=1,
                    key=(
                        f"admin_limite_locais_"
                        f"{user_id_alvo}_"
                        f"{st.session_state.admin_refresh}"
                    ),
                )


                permite_preciso_edit = st.checkbox(
                    "Permitir modo Preciso",
                    value=permite_preciso_atual,
                    key=(
                        f"admin_preciso_"
                        f"{user_id_alvo}_"
                        f"{st.session_state.admin_refresh}"
                    ),
                )


                if is_admin:

                    st.info(
                        "🛡️ Este usuário possui "
                        "permissão administrativa."
                    )


                if (
                    user_id_alvo
                    == usuario_id
                ):

                    st.warning(
                        "⚠️ Se você desativar sua própria "
                        "conta, também perderá acesso ao "
                        "painel administrativo. Para reativá-la, "
                        "será necessário alterar o registro "
                        "diretamente no Supabase."
                    )


                if st.button(
                    "💾 Salvar alterações",
                    type="primary",
                    use_container_width=True,
                    key=(
                        f"admin_salvar_"
                        f"{user_id_alvo}"
                    ),
                ):

                    try:

                        atualizar_usuario_admin(
                            user_id_alvo=user_id_alvo,
                            plano=plano_edit,
                            ativo=ativo_edit,
                            limite_rotas_mes=(
                                limite_mes_edit
                            ),
                            limite_locais_rota=(
                                limite_locais_edit
                            ),
                            permite_preciso=(
                                permite_preciso_edit
                            ),
                        )


                        st.session_state.admin_refresh += 1


                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    f"Usuário "
                                    f"'{nome}' "
                                    "atualizado com sucesso."
                                ),
                        }


                        st.rerun()


                    except Exception as e:

                        st.error(
                            "Não foi possível atualizar "
                            f"o usuário: {e}"
                        )


# ============================================================
# MENU LATERAL
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


    if perfil_usuario_app:

        plano = (
            perfil_usuario_app.get(
                "plano"
            )
            or
            "Gratuito"
        )


        limite_mes = int(
            perfil_usuario_app.get(
                "limite_rotas_mes"
            )
            or
            0
        )


        limite_locais = int(
            perfil_usuario_app.get(
                "limite_locais_rota"
            )
            or
            0
        )


        permite_preciso = bool(
            perfil_usuario_app.get(
                "permite_preciso",
                False,
            )
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


        if limite_mes > 0:

            percentual = min(
                max(
                    uso
                    /
                    limite_mes,
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
                    f"Restam {restantes} cálculo(s) "
                    "neste ciclo."
                )


        else:

            st.caption(
                f"Rotas este mês: "
                f"**{uso} / ilimitadas**"
            )


        if limite_locais > 0:

            st.caption(
                "Limite: "
                f"**{limite_locais} locais/rota**"
            )


        st.caption(
            "Modo Preciso: "
            +
            (
                "**habilitado**"

                if permite_preciso

                else

                "**não disponível**"
            )
        )


        st.caption(
            "✅ Limites de uso ativos."
        )


    elif erro_perfil_usuario:

        st.error(
            "Não foi possível validar "
            "os dados do plano."
        )


        st.caption(
            "Os cálculos ficam bloqueados "
            "enquanto a validação estiver indisponível."
        )


    elif not supabase_configurado:

        st.error(
            "Controle de uso indisponível."
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
# ROTAS
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


    return len(
        registros
    )


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


    return resultado[
        0
    ]


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
            "A rota não foi encontrada "
            "ou não pertence ao usuário."
        )


    return resultado[
        0
    ]


def atualizar_rota_completa_banco(
    rota_id,
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
        "PATCH",
        params={
            "id":
                f"eq.{rota_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "nome":
                nome,

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


    return resultado[
        0
    ]


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
            "Informe o novo nome da rota."
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


    return resultado[
        0
    ]


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


    return True


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
            "a execução no histórico."
        )


    return resultado[
        0
    ]


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

        return (
            None,
            str(
                e
            ),
        )


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
            str(
                limite
            ),
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
                days=dias
            )
        )


        params[
            "created_at"
        ] = (
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


    return len(
        registros
    )


def nome_padrao_local(
    origem,
):

    return (
        origem.get(
            "name"
        )
        or
        origem.get(
            "label"
        )
        or
        origem.get(
            "nome"
        )
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
            "Selecione um endereço para o local."
        )


    endereco = (
        origem.get(
            "label"
        )
        or
        origem.get(
            "endereco"
        )
        or
        origem.get(
            "nome"
        )
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
                    )
                    .strip()
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
            "O banco não retornou "
            "o local criado."
        )


    return resultado[
        0
    ]


def atualizar_local_banco(
    local_id,
    nome,
    observacao,
    nova_origem=None,
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


    payload = {
        "nome":
            nome,

        "observacao":
            (
                (
                    observacao
                    or
                    ""
                )
                .strip()
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
                        nova_origem.get(
                            "nome"
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


    return resultado[
        0
    ]


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


    return True


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

        if not origem:

            continue


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


        endereco = (
            origem.get(
                "label"
            )
            or
            nome
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

        return (
            0,
            ignorados,
        )


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
            dados.get(
                "name"
            ),

        "street":
            dados.get(
                "street"
            ),

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
# ROTAS RECORRENTES
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

    nome = (
        nome
        or
        ""
    ).strip()


    if not nome:

        raise ValueError(
            "Informe um nome para a rota recorrente."
        )


    if len(
        local_ids
    ) < 2:

        raise ValueError(
            "Selecione pelo menos dois locais."
        )


    validar_quantidade_plano(
        len(
            local_ids
        )
    )


    if (
        modo == "Preciso"
        and
        not modo_preciso_permitido
    ):

        raise LimiteUsoError(
            "O modo Preciso não está disponível "
            "para sua conta."
        )


    if (
        inicio_local_id
        not in
        local_ids
    ):

        raise ValueError(
            "Selecione um ponto inicial válido."
        )


    if (
        not voltar_inicio
        and
        (
            not fim_local_id
            or
            fim_local_id
            not in
            local_ids
            or
            fim_local_id
            ==
            inicio_local_id
        )
    ):

        raise ValueError(
            "Selecione um ponto final diferente "
            "do ponto inicial."
        )


    resultado = supabase_request(
        "rotas_recorrentes",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome,

            "local_ids":
                preparar_json(
                    list(
                        local_ids
                    )
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
                    )
                    .strip()
                    or
                    None
                ),
        },
        prefer="return=representation",
    )


    if not resultado:

        raise RuntimeError(
            "O banco não retornou "
            "a recorrente criada."
        )


    return resultado[
        0
    ]


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

    nome = (
        nome
        or
        ""
    ).strip()


    if not nome:

        raise ValueError(
            "Informe um nome para a rota recorrente."
        )


    if len(
        local_ids
    ) < 2:

        raise ValueError(
            "Selecione pelo menos dois locais."
        )


    validar_quantidade_plano(
        len(
            local_ids
        )
    )


    if (
        modo == "Preciso"
        and
        not modo_preciso_permitido
    ):

        raise LimiteUsoError(
            "O modo Preciso não está disponível "
            "para sua conta."
        )


    if (
        inicio_local_id
        not in
        local_ids
    ):

        raise ValueError(
            "Selecione um ponto inicial válido."
        )


    if (
        not voltar_inicio
        and
        (
            not fim_local_id
            or
            fim_local_id
            not in
            local_ids
            or
            fim_local_id
            ==
            inicio_local_id
        )
    ):

        raise ValueError(
            "Selecione um ponto final diferente "
            "do ponto inicial."
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
                nome,

            "local_ids":
                preparar_json(
                    list(
                        local_ids
                    )
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
                    )
                    .strip()
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
            "A rota recorrente não foi encontrada."
        )


    return resultado[
        0
    ]


def duplicar_recorrente_banco(
    recorrente,
):

    return salvar_recorrente_banco(
        nome=(
            f"{recorrente.get('nome', 'Recorrente')} "
            "- cópia"
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
            "A rota recorrente não foi encontrada."
        )


    return True


# ============================================================
# DATA / HORA
# ============================================================

def formatar_data(
    valor,
):

    if not valor:

        return ""


    try:

        data = datetime.fromisoformat(
            str(
                valor
            ).replace(
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

        return str(
            valor
        )


# ============================================================
# ORS
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
        focus_lat=None,
        focus_lon=None,
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


        st.session_state.erro_autocomplete = (
            None
        )


    except ORSError as e:

        st.session_state.erro_autocomplete = (
            str(
                e
            )
        )

        return []


    except Exception:

        st.session_state.erro_autocomplete = (
            "Não foi possível pesquisar "
            "o local neste momento."
        )

        return []


    return [
        (
            resultado[
                "label"
            ],
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
# CSV / EXCEL
# ============================================================

def localizar_coluna_endereco(
    dataframe,
):

    for coluna in dataframe.columns:

        if (
            normalizar_texto(
                coluna
            )
            == "endereco"
        ):

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

        valor = valor[
            1:-1
        ]

        valor = valor.replace(
            '""',
            '"',
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
        linhas[
            0
        ]
        .strip()
        .strip('"')
        .strip("'")
    )


    if cabecalho == "endereco":

        enderecos = []


        for linha in linhas[
            1:
        ]:

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


        if (
            localizar_coluna_endereco(
                dataframe
            )
            is not None
        ):

            return dataframe


    raise ValueError(
        "Não foi possível identificar "
        "a coluna 'endereco' no arquivo CSV."
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
            "Formato não suportado. "
            "Utilize CSV ou XLSX."
        )


    if dataframe.empty:

        raise ValueError(
            "O arquivo não possui dados."
        )


    coluna = (
        localizar_coluna_endereco(
            dataframe
        )
    )


    if coluna is None:

        raise ValueError(
            "O arquivo precisa possuir "
            "uma coluna chamada "
            "'endereco' ou 'endereço'."
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
            "Nenhum endereço válido "
            "foi encontrado."
        )


    hash_arquivo = (
        hashlib.sha256(
            bytes_arquivo
        )
        .hexdigest()[
            :12
        ]
    )


    return (
        enderecos,
        hash_arquivo,
    )


# ============================================================
# ROTA
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
            "São necessários pelo menos dois locais."
        )


    max_locais = (
        49
        if voltar_inicio
        else
        50
    )


    if len(
        locais
    ) > max_locais:

        raise ValueError(
            f"Esta rota possui "
            f"{len(locais)} locais. "
            f"O limite técnico atual é "
            f"{max_locais}."
        )


    if (
        len(
            locais
        )
        *
        len(
            locais
        )
        > 3500
    ):

        raise ValueError(
            "A quantidade de locais ultrapassa "
            "o limite atual da Matrix API."
        )


    coordenadas_unicas = set()


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


        if chave in coordenadas_unicas:

            raise ValueError(
                "O mesmo local foi selecionado "
                "mais de uma vez."
            )


        coordenadas_unicas.add(
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


    # --------------------------------------------------------
    # IMPORTANTE:
    # TODOS os caminhos de cálculo passam por esta função.
    # Os limites são verificados antes da chamada à Matrix API.
    # --------------------------------------------------------

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


    if not isinstance(
        matrix_data,
        dict,
    ):

        raise RouteOptimizationError(
            "A Matrix API retornou "
            "um formato incompatível."
        )


    if (
        "distancias"
        not in matrix_data
        or
        "duracoes"
        not in matrix_data
    ):

        raise RouteOptimizationError(
            "A Matrix API não retornou "
            "distâncias e tempos."
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
            10,

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
# MÉTRICAS
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


    tempo_texto = (
        f"{horas}h "
        f"{minutos:02d}min"

        if horas

        else

        f"{minutos} min"
    )


    return (
        distancia_km,
        tempo_texto,
    )


# ============================================================
# MAPA
# ============================================================

def criar_mapa(
    geometry,
    locais_ordenados,
    rota_fechada,
    distancia_km,
    tempo_texto,
    objetivo,
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
            ponto[
                0
            ]

            for ponto
            in path
        )
        /
        len(
            path
        ),

        sum(
            ponto[
                1
            ]

            for ponto
            in path
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
        tooltip="Rota otimizada",
    ).add_to(
        mapa
    )


    locais_marcadores = (
        locais_ordenados[
            :-1
        ]

        if rota_fechada

        else

        locais_ordenados
    )


    for numero, local in enumerate(
        locais_marcadores,
        start=1,
    ):

        nome = html.escape(
            str(
                local[
                    "nome"
                ]
            )
        )


        marcador_html = f"""
        <div style="
            background:#1e3a8a;
            color:white;
            border-radius:50%;
            width:30px;
            height:30px;
            display:flex;
            align-items:center;
            justify-content:center;
            font-size:12px;
            font-weight:700;
            border:2px solid white;
            box-shadow:0 0 6px rgba(0,0,0,.4);
        ">
            {numero}
        </div>
        """


        popup = nome


        if local.get(
            "observacao"
        ):

            popup += (
                "<br><br>"
                +
                html.escape(
                    str(
                        local[
                            "observacao"
                        ]
                    )
                )
            )


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
                f"{numero}. "
                f"{nome}"
            ),

            popup=folium.Popup(
                popup,
                max_width=350,
            ),

            icon=folium.DivIcon(
                html=marcador_html
            ),
        ).add_to(
            mapa
        )


    mapa.fit_bounds(
        [
            [
                min(
                    ponto[
                        0
                    ]

                    for ponto
                    in path
                ),

                min(
                    ponto[
                        1
                    ]

                    for ponto
                    in path
                ),
            ],

            [
                max(
                    ponto[
                        0
                    ]

                    for ponto
                    in path
                ),

                max(
                    ponto[
                        1
                    ]

                    for ponto
                    in path
                ),
            ],
        ]
    )


    objetivo_texto = (
        "Distância"

        if objetivo
        == "Menor distância"

        else

        "Tempo"
    )


    painel = f"""
    <div style="
        position:fixed;
        top:10px;
        right:10px;
        z-index:9999;
        background:rgba(255,255,255,.95);
        padding:12px 14px;
        border-radius:8px;
        border:1px solid #ccc;
        box-shadow:0 2px 8px rgba(0,0,0,.18);
        font-family:Arial,sans-serif;
        font-size:13px;
        max-width:280px;
    ">
        <strong>Rota otimizada</strong><br>
        Distância: {distancia_km:.2f} km<br>
        Tempo estimado: {html.escape(str(tempo_texto))}<br>
        Otimizado por: {objetivo_texto}<br>
        Locais: {len(locais_marcadores)}
    </div>
    """


    mapa.get_root().html.add_child(
        folium.Element(
            painel
        )
    )


    return mapa


def gerar_html_mapa(
    mapa,
):

    return (
        mapa
        .get_root()
        .render()
        .encode(
            "utf-8"
        )
    )


# ============================================================
# GOOGLE MAPS
# ============================================================

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


    street = local.get(
        "street"
    )


    housenumber = local.get(
        "housenumber"
    )


    locality = local.get(
        "locality"
    )


    region = local.get(
        "region"
    )


    country = (
        local.get(
            "country"
        )
        or
        "Brazil"
    )


    if nome:

        componentes.append(
            str(
                nome
            ).strip()
        )


    if street:

        endereco = str(
            street
        ).strip()


        if housenumber:

            endereco += (
                f", {housenumber}"
            )


        if endereco not in componentes:

            componentes.append(
                endereco
            )


    for item in (
        locality,
        region,
        country,
    ):

        if item:

            item = str(
                item
            ).strip()


            if item not in componentes:

                componentes.append(
                    item
                )


    return (
        ", ".join(
            componentes
        )
        or
        local.get(
            "nome",
            "",
        )
    )


def coordenada_google_maps(
    local,
):

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


def gerar_url_google_maps(
    locais_trecho,
    usar_coordenadas=False,
):

    if len(
        locais_trecho
    ) < 2:

        return None


    if usar_coordenadas:

        referencias = [
            coordenada_google_maps(
                local
            )

            for local
            in locais_trecho
        ]


    else:

        referencias = [
            montar_referencia_google(
                local
            )

            for local
            in locais_trecho
        ]


    params = {
        "api":
            "1",

        "origin":
            referencias[
                0
            ],

        "destination":
            referencias[
                -1
            ],

        "travelmode":
            "driving",

        "dir_action":
            "navigate",
    }


    if len(
        referencias
    ) > 2:

        params[
            "waypoints"
        ] = "|".join(
            referencias[
                1:-1
            ]
        )


    return (
        "https://www.google.com/maps/dir/?"
        +
        urlencode(
            params
        )
    )


def dividir_rota_google_maps(
    locais_ordenados,
    max_pontos_por_trecho=5,
):

    if (
        len(
            locais_ordenados
        )
        <= max_pontos_por_trecho
    ):

        return [
            locais_ordenados
        ]


    trechos = []

    inicio = 0


    while inicio < (
        len(
            locais_ordenados
        ) - 1
    ):

        fim = min(
            inicio
            + max_pontos_por_trecho
            - 1,

            len(
                locais_ordenados
            ) - 1,
        )


        trechos.append(
            locais_ordenados[
                inicio:
                fim + 1
            ]
        )


        inicio = fim


    return trechos


# ============================================================
# EXPORTAÇÃO
# ============================================================

def dataframe_rota_otimizada(
    locais_ordenados,
    rota_fechada,
):

    linhas = []

    total = len(
        locais_ordenados
    )


    for ordem, local in enumerate(
        locais_ordenados,
        start=1,
    ):

        if ordem == 1:

            tipo = "Início"


        elif (
            rota_fechada
            and
            ordem == total
        ):

            tipo = "Retorno"


        elif (
            not rota_fechada
            and
            ordem == total
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
                    (
                        local.get(
                            "endereco_salvo"
                        )
                        or
                        montar_referencia_google(
                            local
                        )
                    ),

                "observacao":
                    (
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),

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


def gerar_csv_rota(
    dataframe,
):

    return (
        dataframe
        .to_csv(
            index=False,
            sep=";",
        )
        .encode(
            "utf-8-sig"
        )
    )


def gerar_excel_rota(
    dataframe,
    distancia_km,
    tempo_texto,
    objetivo,
    modo,
    voltar_inicio,
):

    buffer = io.BytesIO()


    resumo = pd.DataFrame(
        [
            {
                "Distância total (km)":
                    round(
                        distancia_km,
                        2,
                    ),

                "Tempo estimado":
                    tempo_texto,

                "Otimizado por":
                    (
                        "Distância"

                        if objetivo
                        == "Menor distância"

                        else

                        "Tempo"
                    ),

                "Modo":
                    modo,

                "Tipo de rota":
                    (
                        "Fechada"

                        if voltar_inicio

                        else

                        "Aberta"
                    ),

                "Quantidade de pontos":
                    len(
                        dataframe
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
            sheet_name="Rota otimizada",
            index=False,
        )


    buffer.seek(
        0
    )


    return buffer.getvalue()


# ============================================================
# RESULTADO DA ROTA
# ============================================================

def renderizar_resultado_rota(
    dados,
):

    if not dados:

        return


    locais_ordenados = (
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


    geometry = (
        dados.get(
            "geometry"
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


    objetivo = dados.get(
        "objetivo",
        "Menor distância",
    )


    modo = dados.get(
        "modo",
        "Rápido",
    )


    solver = (
        dados.get(
            "solver"
        )
        or
        {}
    )


    (
        distancia_km,
        tempo_texto,
    ) = dados_metricas(
        dados
    )


    if st.session_state.resultado_historico:

        st.info(
            "📊 Você está visualizando "
            "um snapshot do histórico."
        )


    if st.session_state.resultado_direto_titulo:

        st.info(
            st.session_state.resultado_direto_titulo
        )


    if (
        st.session_state.rota_atual_id
        and
        st.session_state.rota_atual_nome
    ):

        st.info(
            "📂 Rota salva aberta: "
            f"**{st.session_state.rota_atual_nome}**"
        )


        (
            col_recalcular,
            col_editar,
            col_atualizar,
        ) = st.columns(
            3
        )


        with col_recalcular:

            if st.button(
                "🔄 Recalcular",
                use_container_width=True,
                key="recalcular_rota_aberta",
            ):

                try:

                    with st.spinner(
                        "Recalculando a rota..."
                    ):

                        novo_resultado = (
                            calcular_rota_a_partir_de_locais(
                                locais=locais_base,
                                voltar_inicio=voltar_inicio,
                                objetivo=objetivo,
                                modo=modo,
                            )
                        )


                    st.session_state.resultado_rota = (
                        novo_resultado
                    )


                    (
                        _,
                        erro_hist,
                    ) = registrar_execucao_seguro(
                        novo_resultado,
                        st.session_state.rota_atual_id,
                        st.session_state.rota_atual_nome,
                    )


                    st.session_state.mensagem_sistema = {
                        "tipo":
                            (
                                "warning"
                                if erro_hist
                                else
                                "success"
                            ),

                        "texto":
                            (
                                (
                                    "A rota foi recalculada, "
                                    "mas o histórico não pôde "
                                    f"ser registrado: {erro_hist}"
                                )

                                if erro_hist

                                else

                                (
                                    "Rota recalculada. Clique em "
                                    "'Atualizar rota salva' se quiser "
                                    "gravar a nova versão."
                                )
                            ),
                    }


                    st.rerun()


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
                        "Não foi possível recalcular: "
                        f"{e}"
                    )


        with col_editar:

            if st.button(
                "✏️ Editar locais",
                use_container_width=True,
                key="editar_rota_aberta",
            ):

                iniciar_edicao(
                    {
                        "id":
                            st.session_state.rota_atual_id,

                        "nome":
                            st.session_state.rota_atual_nome,

                        "dados":
                            dados,
                    }
                )


                st.rerun()


        with col_atualizar:

            if st.button(
                "💾 Atualizar rota salva",
                type="primary",
                use_container_width=True,
                key="atualizar_rota_aberta",
            ):

                try:

                    atualizar_rota_banco(
                        st.session_state.rota_atual_id,
                        dados,
                    )


                    st.success(
                        "Rota salva atualizada."
                    )


                except Exception as e:

                    st.error(
                        "Não foi possível atualizar: "
                        f"{e}"
                    )


    st.subheader(
        "✅ Resultado da rota"
    )


    col1, col2, col3, col4 = (
        st.columns(
            4
        )
    )


    with col1:

        st.metric(
            "Distância total",
            f"{distancia_km:.2f} km",
        )


    with col2:

        st.metric(
            "Tempo estimado",
            tempo_texto,
        )


    with col3:

        st.metric(
            "Locais",
            len(
                locais_base
            ),
        )


    with col4:

        st.metric(
            "Otimizado por",
            (
                "Distância"

                if objetivo
                == "Menor distância"

                else

                "Tempo"
            ),
        )


    st.caption(
        f"Modo utilizado: **{modo}**"
    )


    status_solver = solver.get(
        "status"
    )


    gap = solver.get(
        "gap_percent"
    )


    if status_solver == "ÓTIMA COMPROVADA":

        st.success(
            "🏆 Rota ótima "
            "matematicamente comprovada."
        )


    elif gap is not None:

        st.info(
            "🎯 Melhor rota encontrada. "
            f"Gap máximo restante: "
            f"{float(gap):.3f}%."
        )


    elif status_solver:

        st.info(
            "⚡ Status da otimização: "
            f"**{status_solver}**."
        )


    st.subheader(
        "📋 Ordem da rota"
    )


    lista_exibir = (
        locais_ordenados[
            :-1
        ]

        if voltar_inicio

        else

        locais_ordenados
    )


    for numero, local in enumerate(
        lista_exibir,
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
                "📝 "
                f"{local['observacao']}"
            )


    if (
        voltar_inicio
        and
        locais_ordenados
    ):

        st.write(
            f"**{len(lista_exibir) + 1}.** "
            f"{locais_ordenados[-1]['nome']} "
            f"_(retorno)_"
        )


    # ========================================================
    # SALVAR
    # ========================================================

    st.subheader(
        "☁️ Salvar rota"
    )


    if not supabase_configurado:

        st.warning(
            "Supabase não está configurado."
        )


    elif not st.session_state.rota_atual_id:

        with st.form(
            "form_salvar_rota"
        ):

            nome_rota = st.text_input(
                "Nome da rota",
                value=(
                    st.session_state.rota_nome_sugerido
                    or
                    ""
                ),
                placeholder=(
                    "Ex.: Clientes terça-feira"
                ),
            )


            salvar = st.form_submit_button(
                "💾 Salvar em Minhas rotas",
                type="primary",
                use_container_width=True,
            )


            if salvar:

                try:

                    registro = salvar_rota_banco(
                        nome_rota,
                        dados,
                    )


                    if st.session_state.ultima_execucao_id:

                        try:

                            vincular_execucao_rota(
                                st.session_state.ultima_execucao_id,
                                registro[
                                    "id"
                                ],
                                registro[
                                    "nome"
                                ],
                            )

                        except Exception:

                            pass


                    st.session_state.ultima_execucao_id = (
                        None
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


                    st.session_state.rota_aberta_salva = (
                        True
                    )


                    st.session_state.resultado_historico = (
                        False
                    )


                    st.session_state.resultado_direto = (
                        False
                    )


                    st.session_state.rota_nome_sugerido = (
                        ""
                    )


                    st.session_state.mensagem_sistema = {
                        "tipo":
                            "success",

                        "texto":
                            (
                                f"Rota "
                                f"'{registro['nome']}' "
                                f"salva com sucesso."
                            ),
                    }


                    st.rerun()


                except Exception as e:

                    st.error(
                        "Não foi possível salvar: "
                        f"{e}"
                    )


    else:

        st.success(
            "Esta rota já está salva como "
            f"**{st.session_state.rota_atual_nome}**."
        )


        with st.expander(
            "Salvar uma cópia com outro nome"
        ):

            with st.form(
                "form_salvar_copia"
            ):

                nome_copia = st.text_input(
                    "Nome da nova rota",
                    placeholder=(
                        "Ex.: Clientes quarta-feira"
                    ),
                )


                salvar_copia = (
                    st.form_submit_button(
                        "💾 Salvar como nova rota",
                        use_container_width=True,
                    )
                )


                if salvar_copia:

                    try:

                        registro = (
                            salvar_rota_banco(
                                nome_copia,
                                dados,
                            )
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


                        st.session_state.rota_aberta_salva = (
                            True
                        )


                        st.session_state.resultado_historico = (
                            False
                        )


                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    f"Cópia salva como "
                                    f"'{registro['nome']}'."
                                ),
                        }


                        st.rerun()


                    except Exception as e:

                        st.error(
                            "Não foi possível salvar "
                            f"a cópia: {e}"
                        )


    # ========================================================
    # MAPA
    # ========================================================

    try:

        mapa = criar_mapa(
            geometry=geometry,
            locais_ordenados=locais_ordenados,
            rota_fechada=voltar_inicio,
            distancia_km=distancia_km,
            tempo_texto=tempo_texto,
            objetivo=objetivo,
        )


    except Exception as e:

        st.error(
            "Não foi possível montar o mapa: "
            f"{e}"
        )

        return


    # ========================================================
    # EXPORTAÇÃO
    # ========================================================

    st.subheader(
        "💾 Exportar rota"
    )


    dataframe_exportacao = (
        dataframe_rota_otimizada(
            locais_ordenados,
            voltar_inicio,
        )
    )


    csv_exportacao = (
        gerar_csv_rota(
            dataframe_exportacao
        )
    )


    excel_exportacao = (
        gerar_excel_rota(
            dataframe_exportacao,
            distancia_km,
            tempo_texto,
            objetivo,
            modo,
            voltar_inicio,
        )
    )


    html_mapa = (
        gerar_html_mapa(
            mapa
        )
    )


    col_csv, col_excel, col_mapa = (
        st.columns(
            3
        )
    )


    with col_csv:

        st.download_button(
            "⬇️ Baixar rota em CSV",
            data=csv_exportacao,
            file_name="rota_otimizada.csv",
            mime="text/csv",
            use_container_width=True,
        )


    with col_excel:

        st.download_button(
            "⬇️ Baixar relatório em Excel",
            data=excel_exportacao,
            file_name="rota_otimizada.xlsx",
            mime=(
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            ),
            use_container_width=True,
        )


    with col_mapa:

        st.download_button(
            "🗺️ Baixar mapa interativo",
            data=html_mapa,
            file_name=(
                "mapa_rota_otimizada.html"
            ),
            mime="text/html",
            use_container_width=True,
        )


    # ========================================================
    # GOOGLE MAPS
    # ========================================================

    st.subheader(
        "🧭 Navegação no Google Maps"
    )


    st.caption(
        "A ordem foi calculada pelo Otimizador de Rotas. "
        "O Google Maps é utilizado apenas para navegação."
    )


    trechos_google = (
        dividir_rota_google_maps(
            locais_ordenados,
            max_pontos_por_trecho=5,
        )
    )


    if len(
        trechos_google
    ) == 1:

        st.link_button(
            "🧭 Abrir rota no Google Maps",
            gerar_url_google_maps(
                trechos_google[
                    0
                ]
            ),
            use_container_width=True,
        )


        with st.expander(
            "Opções avançadas de navegação"
        ):

            st.link_button(
                "📍 Abrir usando coordenadas",
                gerar_url_google_maps(
                    trechos_google[
                        0
                    ],
                    usar_coordenadas=True,
                ),
                use_container_width=True,
            )


    else:

        st.info(
            f"A rota foi dividida em "
            f"{len(trechos_google)} trechos."
        )


        for numero_trecho, trecho in enumerate(
            trechos_google,
            start=1,
        ):

            with st.expander(
                (
                    f"Trecho {numero_trecho}: "
                    f"{trecho[0]['nome']} → "
                    f"{trecho[-1]['nome']}"
                ),
                expanded=(
                    numero_trecho
                    == 1
                ),
            ):

                for numero_local, local in enumerate(
                    trecho,
                    start=1,
                ):

                    st.write(
                        f"**{numero_local}.** "
                        f"{local['nome']}"
                    )


                st.link_button(
                    (
                        f"🧭 Abrir trecho "
                        f"{numero_trecho} "
                        f"no Google Maps"
                    ),
                    gerar_url_google_maps(
                        trecho
                    ),
                    use_container_width=True,
                )


                st.link_button(
                    "📍 Alternativa por coordenadas",
                    gerar_url_google_maps(
                        trecho,
                        usar_coordenadas=True,
                    ),
                    use_container_width=True,
                )


    st.subheader(
        "🗺️ Mapa da rota"
    )


    st_folium(
        mapa,
        height=600,
        use_container_width=True,
        returned_objects=[],
    )


# ============================================================
# HISTÓRICO
# ============================================================

def renderizar_historico():

    st.subheader(
        "📊 Histórico"
    )


    st.write(
        "Cada cálculo e recálculo é registrado "
        "como um snapshot independente."
    )


    periodo = st.selectbox(
        "Período",
        [
            "Últimos 7 dias",
            "Últimos 30 dias",
            "Últimos 90 dias",
            "Todo o histórico",
        ],
    )


    mapa_periodos = {
        "Últimos 7 dias":
            7,

        "Últimos 30 dias":
            30,

        "Últimos 90 dias":
            90,

        "Todo o histórico":
            None,
    }


    try:

        execucoes = (
            listar_execucoes_usuario(
                dias=mapa_periodos[
                    periodo
                ]
            )
        )


        total_rotas_salvas = (
            contar_rotas_usuario()
        )


        total_locais_salvos = (
            contar_locais_salvos_usuario()
        )


    except Exception as e:

        st.error(
            f"Erro ao carregar histórico: {e}"
        )

        return


    total_execucoes = len(
        execucoes
    )


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


    total_locais = sum(
        int(
            item.get(
                "quantidade_locais",
                0,
            )
        )

        for item
        in execucoes
    )


    (
        col1,
        col2,
        col3,
        col4,
        col5,
    ) = st.columns(
        5
    )


    with col1:

        st.metric(
            "Rotas salvas",
            total_rotas_salvas,
        )


    with col2:

        st.metric(
            "Locais salvos",
            total_locais_salvos,
        )


    with col3:

        st.metric(
            "Rotas calculadas",
            total_execucoes,
        )


    with col4:

        st.metric(
            "Km calculados",
            f"{total_km:.1f}",
        )


    with col5:

        st.metric(
            "Locais roteados",
            total_locais,
        )


    st.divider()


    if not execucoes:

        st.info(
            "Nenhuma execução encontrada "
            "neste período."
        )

        return


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
                    f"hist_abrir_"
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


                st.session_state.rota_atual_id = (
                    None
                )


                st.session_state.rota_atual_nome = (
                    None
                )


                st.session_state.rota_aberta_salva = (
                    False
                )


                st.session_state.resultado_historico = (
                    True
                )


                st.session_state.resultado_direto = (
                    False
                )


                st.session_state.pagina_app = (
                    "nova"
                )


                st.rerun()


# ============================================================
# LOCAIS
# ============================================================

def renderizar_locais_salvos():

    st.subheader(
        "📍 Locais salvos"
    )


    st.write(
        "Cadastre clientes, estabelecimentos e outros "
        "endereços recorrentes."
    )


    try:

        locais = (
            listar_locais_salvos_usuario()
        )


    except Exception as e:

        st.error(
            f"Erro ao carregar locais: {e}"
        )

        return


    local_endereco_id = (
        st.session_state.local_endereco_editando_id
    )


    local_endereco = None


    if local_endereco_id:

        for item in locais:

            if item.get(
                "id"
            ) == local_endereco_id:

                local_endereco = item

                break


    if local_endereco:

        with st.container(
            border=True
        ):

            st.markdown(
                "### 📍 Alterar endereço"
            )


            st.write(
                local_endereco.get(
                    "endereco",
                    "",
                )
            )


            novo_endereco = campo_local(
                chave=(
                    "alterar_endereco_local_"
                    f"{st.session_state.local_endereco_edit_version}"
                ),
                label="Novo endereço",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
            )


            col1, col2 = (
                st.columns(
                    2
                )
            )


            with col1:

                if st.button(
                    "💾 Salvar novo endereço",
                    type="primary",
                    use_container_width=True,
                ):

                    if not novo_endereco:

                        st.error(
                            "Selecione um endereço."
                        )


                    else:

                        try:

                            atualizar_local_banco(
                                local_endereco[
                                    "id"
                                ],
                                local_endereco[
                                    "nome"
                                ],
                                local_endereco.get(
                                    "observacao",
                                    "",
                                ),
                                novo_endereco,
                            )


                            st.session_state.local_endereco_editando_id = (
                                None
                            )


                            st.session_state.local_endereco_edit_version += 1


                            st.rerun()


                        except Exception as e:

                            st.error(
                                str(
                                    e
                                )
                            )


            with col2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                ):

                    st.session_state.local_endereco_editando_id = (
                        None
                    )


                    st.session_state.local_endereco_edit_version += 1


                    st.rerun()


    elif st.session_state.local_cadastro_aberto:

        versao = (
            st.session_state.local_form_version
        )


        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Cadastrar novo local"
            )


            nome_novo = st.text_input(
                "Nome",
                key=(
                    f"local_novo_nome_"
                    f"{versao}"
                ),
            )


            endereco_novo = campo_local(
                chave=(
                    f"local_novo_endereco_"
                    f"{versao}"
                ),
                label="Endereço",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
            )


            observacao_nova = st.text_area(
                "Observação",
                key=(
                    f"local_novo_obs_"
                    f"{versao}"
                ),
            )


            col1, col2 = (
                st.columns(
                    2
                )
            )


            with col1:

                if st.button(
                    "💾 Salvar local",
                    type="primary",
                    use_container_width=True,
                    key=(
                        f"salvar_local_novo_"
                        f"{versao}"
                    ),
                ):

                    if not endereco_novo:

                        st.error(
                            "Selecione um endereço."
                        )


                    else:

                        try:

                            registro = (
                                salvar_local_banco(
                                    (
                                        nome_novo.strip()
                                        or
                                        nome_padrao_local(
                                            endereco_novo
                                        )
                                    ),
                                    endereco_novo,
                                    observacao_nova,
                                )
                            )


                            st.session_state.local_cadastro_aberto = (
                                False
                            )


                            st.session_state.local_form_version += 1


                            st.session_state.mensagem_sistema = {
                                "tipo":
                                    "success",

                                "texto":
                                    (
                                        f"Local "
                                        f"'{registro['nome']}' "
                                        "salvo."
                                    ),
                            }


                            st.rerun()


                        except Exception as e:

                            st.error(
                                str(
                                    e
                                )
                            )


            with col2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                    key=(
                        f"cancelar_local_novo_"
                        f"{versao}"
                    ),
                ):

                    st.session_state.local_cadastro_aberto = (
                        False
                    )


                    st.session_state.local_form_version += 1


                    st.rerun()


    else:

        if st.button(
            "➕ Cadastrar novo local",
            type="primary",
            use_container_width=True,
        ):

            st.session_state.local_cadastro_aberto = (
                True
            )


            st.session_state.local_form_version += 1


            st.rerun()


    if not locais:

        st.info(
            "Você ainda não possui locais salvos."
        )

        return


    st.divider()


    filtro = st.text_input(
        "🔎 Buscar",
    )


    filtro_norm = (
        normalizar_texto(
            filtro
        )

        if filtro

        else

        ""
    )


    for local in locais:

        texto = " ".join(
            [
                str(
                    local.get(
                        "nome",
                        "",
                    )
                ),

                str(
                    local.get(
                        "endereco",
                        "",
                    )
                ),

                str(
                    local.get(
                        "observacao",
                        "",
                    )
                    or
                    ""
                ),
            ]
        )


        if (
            filtro_norm
            and
            filtro_norm
            not in
            normalizar_texto(
                texto
            )
        ):

            continue


        local_id = local[
            "id"
        ]


        with st.container(
            border=True
        ):

            st.markdown(
                f"### {local['nome']}"
            )


            st.write(
                f"📍 {local.get('endereco', '')}"
            )


            if local.get(
                "observacao"
            ):

                st.caption(
                    f"📝 {local['observacao']}"
                )


            col1, col2 = (
                st.columns(
                    2
                )
            )


            with col1:

                if st.button(
                    "🚗 Usar na rota",
                    key=(
                        f"local_usar_"
                        f"{local_id}"
                    ),
                    use_container_width=True,
                ):

                    iniciar_nova_rota(
                        [
                            local_id
                        ]
                    )


                    st.rerun()


            with col2:

                if st.button(
                    "📍 Alterar endereço",
                    key=(
                        f"local_end_"
                        f"{local_id}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.local_cadastro_aberto = (
                        False
                    )


                    st.session_state.local_endereco_editando_id = (
                        local_id
                    )


                    st.session_state.local_endereco_edit_version += 1


                    st.rerun()


            with st.expander(
                "⚙️ Editar / excluir"
            ):

                nome_edit = st.text_input(
                    "Nome",
                    value=local[
                        "nome"
                    ],
                    key=(
                        f"local_edit_nome_"
                        f"{local_id}"
                    ),
                )


                obs_edit = st.text_area(
                    "Observação",
                    value=(
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                    key=(
                        f"local_edit_obs_"
                        f"{local_id}"
                    ),
                )


                if st.button(
                    "💾 Salvar",
                    key=(
                        f"local_edit_salvar_"
                        f"{local_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        atualizar_local_banco(
                            local_id,
                            nome_edit,
                            obs_edit,
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"local_conf_"
                        f"{local_id}"
                    ),
                )


                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"local_excluir_"
                        f"{local_id}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    try:

                        excluir_local_banco(
                            local_id
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


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
            f"Erro ao carregar rotas: {e}"
        )

        return


    if not rotas:

        st.info(
            "Você ainda não possui rotas salvas."
        )

        return


    for rota in rotas:

        rota_id = rota[
            "id"
        ]


        dados = (
            rota.get(
                "dados"
            )
            or
            {}
        )


        nome = rota.get(
            "nome",
            "Rota sem nome",
        )


        locais_base = (
            dados.get(
                "locais_base"
            )
            or
            []
        )


        with st.container(
            border=True
        ):

            st.markdown(
                f"### {nome}"
            )


            st.caption(
                "Última alteração: "
                f"{formatar_data(rota.get('updated_at'))}"
            )


            st.caption(
                f"{len(locais_base)} local(is) · "
                f"{dados.get('objetivo', '—')} · "
                f"{dados.get('modo', '—')}"
            )


            col1, col2, col3 = (
                st.columns(
                    3
                )
            )


            with col1:

                if st.button(
                    "📂 Abrir",
                    key=(
                        f"rota_abrir_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.resultado_rota = (
                        dados
                    )


                    st.session_state.rota_atual_id = (
                        rota_id
                    )


                    st.session_state.rota_atual_nome = (
                        nome
                    )


                    st.session_state.rota_aberta_salva = (
                        True
                    )


                    st.session_state.resultado_historico = (
                        False
                    )


                    st.session_state.resultado_direto = (
                        False
                    )


                    st.session_state.pagina_app = (
                        "nova"
                    )


                    st.rerun()


            with col2:

                if st.button(
                    "✏️ Editar",
                    key=(
                        f"rota_editar_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    iniciar_edicao(
                        rota
                    )


                    st.rerun()


            with col3:

                if st.button(
                    "🔄 Recalcular",
                    key=(
                        f"rota_recalc_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        novo = (
                            calcular_rota_a_partir_de_locais(
                                locais=locais_base,
                                voltar_inicio=bool(
                                    dados.get(
                                        "voltar_inicio",
                                        False,
                                    )
                                ),
                                objetivo=dados.get(
                                    "objetivo",
                                    "Menor distância",
                                ),
                                modo=dados.get(
                                    "modo",
                                    "Rápido",
                                ),
                            )
                        )


                        registrar_execucao_seguro(
                            novo,
                            rota_id,
                            nome,
                        )


                        st.session_state.resultado_rota = (
                            novo
                        )


                        st.session_state.rota_atual_id = (
                            rota_id
                        )


                        st.session_state.rota_atual_nome = (
                            nome
                        )


                        st.session_state.rota_aberta_salva = (
                            True
                        )


                        st.session_state.resultado_historico = (
                            False
                        )


                        st.session_state.resultado_direto = (
                            False
                        )


                        st.session_state.pagina_app = (
                            "nova"
                        )


                        st.rerun()


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
                            f"Erro ao recalcular: {e}"
                        )


            with st.expander(
                "⚙️ Gerenciar"
            ):

                novo_nome = st.text_input(
                    "Nome",
                    value=nome,
                    key=(
                        f"rota_nome_"
                        f"{rota_id}"
                    ),
                )


                if st.button(
                    "💾 Renomear",
                    key=(
                        f"rota_renomear_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        renomear_rota_banco(
                            rota_id,
                            novo_nome,
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"rota_conf_"
                        f"{rota_id}"
                    ),
                )


                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"rota_excluir_"
                        f"{rota_id}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    try:

                        excluir_rota_banco(
                            rota_id
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


# ============================================================
# RECORRENTES
# ============================================================

def renderizar_recorrentes():

    st.subheader(
        "🔁 Rotas recorrentes"
    )


    st.write(
        "Crie modelos com seus locais habituais "
        "e gere a rota novamente sempre que precisar."
    )


    try:

        locais = (
            listar_locais_salvos_usuario()
        )


        recorrentes = (
            listar_recorrentes_usuario()
        )


    except Exception as e:

        st.error(
            f"Erro ao carregar dados: {e}"
        )

        return


    mapa_locais = {
        item[
            "id"
        ]:
            item

        for item
        in locais
    }


    ids_locais = list(
        mapa_locais.keys()
    )


    limite_locais_atual = (
        limite_locais_perfil(
            perfil_usuario_app
        )
    )


    if limite_locais_atual > 0:

        st.caption(
            "Seu limite atual é de "
            f"**{limite_locais_atual} locais por rota**."
        )


    # ========================================================
    # GERAR ROTA
    # ========================================================

    gerar_id = (
        st.session_state.recorrente_gerar_id
    )


    recorrente_gerar = next(
        (
            item

            for item
            in recorrentes

            if item[
                "id"
            ]
            == gerar_id
        ),
        None,
    )


    if recorrente_gerar:

        versao = (
            st.session_state.recorrente_gerar_version
        )


        ids_validos = [
            item_id

            for item_id
            in (
                recorrente_gerar.get(
                    "local_ids"
                )
                or
                []
            )

            if item_id
            in mapa_locais
        ]


        with st.container(
            border=True
        ):

            st.markdown(
                "### 🚗 Rota de hoje"
            )


            st.write(
                f"Modelo: **{recorrente_gerar['nome']}**"
            )


            selecionados = st.multiselect(
                "Locais atendidos hoje",
                options=ids_validos,
                default=ids_validos,
                key=(
                    f"hoje_locais_"
                    f"{versao}"
                ),
                format_func=(
                    lambda item_id:
                    mapa_locais[
                        item_id
                    ][
                        "nome"
                    ]
                ),
            )


            if (
                limite_locais_atual > 0
                and
                len(
                    selecionados
                ) > limite_locais_atual
            ):

                st.warning(
                    "Você selecionou "
                    f"{len(selecionados)} locais. "
                    "Seu limite atual é de "
                    f"{limite_locais_atual}."
                )


            if len(
                selecionados
            ) >= 2:

                inicio_salvo = (
                    recorrente_gerar.get(
                        "inicio_local_id"
                    )
                )


                inicio_default = (
                    inicio_salvo

                    if inicio_salvo
                    in selecionados

                    else

                    selecionados[
                        0
                    ]
                )


                inicio_id = st.selectbox(
                    "Ponto inicial",
                    options=selecionados,
                    index=selecionados.index(
                        inicio_default
                    ),
                    key=(
                        f"hoje_inicio_"
                        f"{versao}"
                    ),
                    format_func=(
                        lambda item_id:
                        mapa_locais[
                            item_id
                        ][
                            "nome"
                        ]
                    ),
                )


                voltar = st.checkbox(
                    "Retornar ao início",
                    value=bool(
                        recorrente_gerar.get(
                            "voltar_inicio",
                            True,
                        )
                    ),
                    key=(
                        f"hoje_voltar_"
                        f"{versao}"
                    ),
                )


                fim_id = None


                if not voltar:

                    opcoes_fim = [
                        x

                        for x
                        in selecionados

                        if x
                        != inicio_id
                    ]


                    fim_salvo = (
                        recorrente_gerar.get(
                            "fim_local_id"
                        )
                    )


                    fim_default = (
                        fim_salvo

                        if fim_salvo
                        in opcoes_fim

                        else

                        opcoes_fim[
                            -1
                        ]
                    )


                    fim_id = st.selectbox(
                        "Ponto final",
                        options=opcoes_fim,
                        index=opcoes_fim.index(
                            fim_default
                        ),
                        key=(
                            f"hoje_fim_"
                            f"{versao}"
                        ),
                        format_func=(
                            lambda item_id:
                            mapa_locais[
                                item_id
                            ][
                                "nome"
                            ]
                        ),
                    )


                col_obj, col_modo = (
                    st.columns(
                        2
                    )
                )


                objetivo_salvo = (
                    recorrente_gerar.get(
                        "objetivo",
                        "Menor tempo",
                    )
                )


                modo_salvo = (
                    recorrente_gerar.get(
                        "modo",
                        "Rápido",
                    )
                )


                with col_obj:

                    objetivo_hoje = st.radio(
                        "Otimizar por",
                        [
                            "Menor distância",
                            "Menor tempo",
                        ],
                        index=(
                            0

                            if objetivo_salvo
                            == "Menor distância"

                            else

                            1
                        ),
                        horizontal=True,
                        key=(
                            f"hoje_obj_"
                            f"{versao}"
                        ),
                    )


                with col_modo:

                    modos_disponiveis = (
                        opcoes_modo_usuario()
                    )


                    modo_default = (
                        modo_salvo

                        if modo_salvo
                        in modos_disponiveis

                        else

                        "Rápido"
                    )


                    modo_hoje = st.radio(
                        "Modo",
                        modos_disponiveis,
                        index=modos_disponiveis.index(
                            modo_default
                        ),
                        horizontal=True,
                        key=(
                            f"hoje_modo_"
                            f"{versao}"
                        ),
                    )


                if (
                    modo_salvo == "Preciso"
                    and
                    not modo_preciso_permitido
                ):

                    st.info(
                        "🔒 Este modelo foi salvo originalmente "
                        "com modo Preciso. Como sua conta não "
                        "possui essa permissão atualmente, "
                        "a geração de hoje usará o modo Rápido."
                    )


                col_calcular, col_cancelar = (
                    st.columns(
                        2
                    )
                )


                with col_calcular:

                    if st.button(
                        "🚗 Calcular rota de hoje",
                        type="primary",
                        use_container_width=True,
                        key=(
                            f"hoje_calcular_"
                            f"{versao}"
                        ),
                    ):

                        try:

                            intermediarios = [
                                x

                                for x
                                in selecionados

                                if (
                                    x
                                    != inicio_id
                                    and
                                    x
                                    != fim_id
                                )
                            ]


                            locais_solver = [
                                converter_local(
                                    local_salvo_para_origem(
                                        mapa_locais[
                                            inicio_id
                                        ]
                                    ),
                                    "Início",
                                )
                            ]


                            for numero, item_id in enumerate(
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
                                        f"Parada {numero}",
                                    )
                                )


                            if not voltar:

                                locais_solver.append(
                                    converter_local(
                                        local_salvo_para_origem(
                                            mapa_locais[
                                                fim_id
                                            ]
                                        ),
                                        "Fim",
                                    )
                                )


                            with st.spinner(
                                "Calculando a rota de hoje..."
                            ):

                                resultado = (
                                    calcular_rota_a_partir_de_locais(
                                        locais_solver,
                                        voltar,
                                        objetivo_hoje,
                                        modo_hoje,
                                    )
                                )


                            execucao, erro_hist = (
                                registrar_execucao_seguro(
                                    resultado,
                                    nome_rota=(
                                        recorrente_gerar[
                                            "nome"
                                        ]
                                    ),
                                )
                            )


                            st.session_state.resultado_rota = (
                                resultado
                            )


                            st.session_state.resultado_direto = (
                                True
                            )


                            st.session_state.resultado_historico = (
                                False
                            )


                            st.session_state.rota_aberta_salva = (
                                False
                            )


                            st.session_state.resultado_direto_titulo = (
                                "🔁 Rota gerada a partir de "
                                f"**{recorrente_gerar['nome']}**."
                            )


                            st.session_state.ultima_execucao_id = (
                                execucao[
                                    "id"
                                ]

                                if execucao

                                else

                                None
                            )


                            hoje = datetime.now(
                                ZoneInfo(
                                    "America/Sao_Paulo"
                                )
                            )


                            st.session_state.rota_nome_sugerido = (
                                f"{recorrente_gerar['nome']} - "
                                f"{hoje.strftime('%d-%m-%Y')}"
                            )


                            st.session_state.recorrente_gerar_id = (
                                None
                            )


                            st.session_state.pagina_app = (
                                "nova"
                            )


                            if erro_hist:

                                st.session_state.mensagem_sistema = {
                                    "tipo":
                                        "warning",

                                    "texto":
                                        (
                                            "A rota foi calculada, "
                                            "mas o histórico não pôde "
                                            f"ser registrado: {erro_hist}"
                                        ),
                                }


                            st.rerun()


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
                                "Não foi possível calcular "
                                f"a rota: {e}"
                            )


                with col_cancelar:

                    if st.button(
                        "Cancelar",
                        use_container_width=True,
                        key=(
                            f"hoje_cancelar_"
                            f"{versao}"
                        ),
                    ):

                        st.session_state.recorrente_gerar_id = (
                            None
                        )


                        st.session_state.recorrente_gerar_version += 1


                        st.rerun()


    # ========================================================
    # CRIAR RECORRENTE
    # ========================================================

    if st.button(
        "➕ Criar rota recorrente",
        type="primary",
        use_container_width=True,
        key="abrir_nova_recorrente",
    ):

        st.session_state.recorrente_cadastro_aberto = (
            True
        )


        st.session_state.recorrente_form_version += 1


        st.rerun()


    if st.session_state.recorrente_cadastro_aberto:

        versao = (
            st.session_state.recorrente_form_version
        )


        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Nova rota recorrente"
            )


            nome = st.text_input(
                "Nome",
                key=(
                    f"rec_nome_"
                    f"{versao}"
                ),
            )


            local_ids = st.multiselect(
                "Locais",
                options=ids_locais,
                key=(
                    f"rec_locais_"
                    f"{versao}"
                ),
                format_func=(
                    lambda item_id:
                    mapa_locais[
                        item_id
                    ][
                        "nome"
                    ]
                ),
            )


            if (
                limite_locais_atual > 0
                and
                len(
                    local_ids
                ) > limite_locais_atual
            ):

                st.warning(
                    "Seu plano permite no máximo "
                    f"{limite_locais_atual} locais por rota."
                )


            inicio_id = None

            fim_id = None

            voltar = True


            if len(
                local_ids
            ) >= 2:

                inicio_id = st.selectbox(
                    "Ponto inicial",
                    options=local_ids,
                    key=(
                        f"rec_inicio_"
                        f"{versao}"
                    ),
                    format_func=(
                        lambda item_id:
                        mapa_locais[
                            item_id
                        ][
                            "nome"
                        ]
                    ),
                )


                voltar = st.checkbox(
                    "Retornar ao início",
                    value=True,
                    key=(
                        f"rec_voltar_"
                        f"{versao}"
                    ),
                )


                if not voltar:

                    fim_id = st.selectbox(
                        "Ponto final",
                        options=[
                            x

                            for x
                            in local_ids

                            if x
                            != inicio_id
                        ],
                        key=(
                            f"rec_fim_"
                            f"{versao}"
                        ),
                        format_func=(
                            lambda item_id:
                            mapa_locais[
                                item_id
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
                key=(
                    f"rec_obj_"
                    f"{versao}"
                ),
            )


            modos_recorrente = (
                opcoes_modo_usuario()
            )


            modo = st.radio(
                "Modo",
                modos_recorrente,
                horizontal=True,
                key=(
                    f"rec_modo_"
                    f"{versao}"
                ),
            )


            if not modo_preciso_permitido:

                st.caption(
                    "🔒 O modo Preciso não está "
                    "disponível para sua conta."
                )


            observacao = st.text_area(
                "Observação",
                key=(
                    f"rec_obs_"
                    f"{versao}"
                ),
            )


            col_salvar, col_cancelar = (
                st.columns(
                    2
                )
            )


            with col_salvar:

                if st.button(
                    "💾 Salvar recorrente",
                    type="primary",
                    use_container_width=True,
                    key=(
                        f"rec_salvar_"
                        f"{versao}"
                    ),
                ):

                    try:

                        salvar_recorrente_banco(
                            nome,
                            local_ids,
                            inicio_id,
                            fim_id,
                            voltar,
                            objetivo,
                            modo,
                            observacao,
                        )


                        st.session_state.recorrente_cadastro_aberto = (
                            False
                        )


                        st.session_state.recorrente_form_version += 1


                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                "Rota recorrente salva.",
                        }


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


            with col_cancelar:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                    key=(
                        f"rec_cancelar_"
                        f"{versao}"
                    ),
                ):

                    st.session_state.recorrente_cadastro_aberto = (
                        False
                    )


                    st.session_state.recorrente_form_version += 1


                    st.rerun()


    st.divider()


    # ========================================================
    # LISTA
    # ========================================================

    if not recorrentes:

        st.info(
            "Você ainda não possui rotas recorrentes."
        )

        return


    for recorrente in recorrentes:

        recorrente_id = (
            recorrente[
                "id"
            ]
        )


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

            if x
            in mapa_locais
        ]


        with st.container(
            border=True
        ):

            st.markdown(
                f"### 🔁 {recorrente['nome']}"
            )


            st.caption(
                f"{len(ids_validos)} local(is) · "
                f"{recorrente.get('objetivo')} · "
                f"{recorrente.get('modo')}"
            )


            if (
                limite_locais_atual > 0
                and
                len(
                    ids_validos
                ) > limite_locais_atual
            ):

                st.warning(
                    "⚠️ Este modelo possui mais locais "
                    "do que seu limite atual. "
                    "Reduza os locais antes de calcular."
                )


            if (
                recorrente.get(
                    "modo"
                )
                == "Preciso"
                and
                not modo_preciso_permitido
            ):

                st.info(
                    "🔒 Este modelo foi salvo no modo Preciso, "
                    "mas sua conta atualmente possui apenas "
                    "o modo Rápido."
                )


            col1, col2 = (
                st.columns(
                    2
                )
            )


            with col1:

                if st.button(
                    "🚗 Gerar rota de hoje",
                    key=(
                        f"rec_gerar_"
                        f"{recorrente_id}"
                    ),
                    use_container_width=True,
                    disabled=(
                        len(
                            ids_validos
                        ) < 2
                    ),
                ):

                    st.session_state.recorrente_gerar_id = (
                        recorrente_id
                    )


                    st.session_state.recorrente_gerar_version += 1


                    st.rerun()


            with col2:

                if st.button(
                    "📋 Duplicar",
                    key=(
                        f"rec_dup_"
                        f"{recorrente_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        duplicar_recorrente_banco(
                            recorrente
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


            with st.expander(
                "✏️ Editar / excluir"
            ):

                nome_edit = st.text_input(
                    "Nome",
                    value=recorrente[
                        "nome"
                    ],
                    key=(
                        f"rec_edit_nome_"
                        f"{recorrente_id}"
                    ),
                )


                locais_edit = st.multiselect(
                    "Locais",
                    options=ids_locais,
                    default=ids_validos,
                    key=(
                        f"rec_edit_locais_"
                        f"{recorrente_id}"
                    ),
                    format_func=(
                        lambda item_id:
                        mapa_locais[
                            item_id
                        ][
                            "nome"
                        ]
                    ),
                )


                inicio_edit = None

                fim_edit = None

                voltar_edit = bool(
                    recorrente.get(
                        "voltar_inicio",
                        True,
                    )
                )


                if len(
                    locais_edit
                ) >= 2:

                    inicio_original = (
                        recorrente.get(
                            "inicio_local_id"
                        )
                    )


                    inicio_default = (
                        inicio_original

                        if inicio_original
                        in locais_edit

                        else

                        locais_edit[
                            0
                        ]
                    )


                    inicio_edit = st.selectbox(
                        "Ponto inicial",
                        options=locais_edit,
                        index=locais_edit.index(
                            inicio_default
                        ),
                        key=(
                            f"rec_edit_inicio_"
                            f"{recorrente_id}"
                        ),
                        format_func=(
                            lambda item_id:
                            mapa_locais[
                                item_id
                            ][
                                "nome"
                            ]
                        ),
                    )


                    voltar_edit = st.checkbox(
                        "Retornar ao início",
                        value=bool(
                            recorrente.get(
                                "voltar_inicio",
                                True,
                            )
                        ),
                        key=(
                            f"rec_edit_voltar_"
                            f"{recorrente_id}"
                        ),
                    )


                    if not voltar_edit:

                        opcoes_fim = [
                            x

                            for x
                            in locais_edit

                            if x
                            != inicio_edit
                        ]


                        fim_original = (
                            recorrente.get(
                                "fim_local_id"
                            )
                        )


                        fim_default = (
                            fim_original

                            if fim_original
                            in opcoes_fim

                            else

                            opcoes_fim[
                                -1
                            ]
                        )


                        fim_edit = st.selectbox(
                            "Ponto final",
                            options=opcoes_fim,
                            index=opcoes_fim.index(
                                fim_default
                            ),
                            key=(
                                f"rec_edit_fim_"
                                f"{recorrente_id}"
                            ),
                            format_func=(
                                lambda item_id:
                                mapa_locais[
                                    item_id
                                ][
                                    "nome"
                                ]
                            ),
                        )


                objetivo_atual = (
                    recorrente.get(
                        "objetivo",
                        "Menor tempo",
                    )
                )


                objetivo_edit = st.radio(
                    "Otimizar por",
                    [
                        "Menor distância",
                        "Menor tempo",
                    ],
                    index=(
                        0

                        if objetivo_atual
                        == "Menor distância"

                        else

                        1
                    ),
                    horizontal=True,
                    key=(
                        f"rec_edit_obj_"
                        f"{recorrente_id}"
                    ),
                )


                modos_editaveis = (
                    opcoes_modo_usuario()
                )


                modo_atual = (
                    recorrente.get(
                        "modo",
                        "Rápido",
                    )
                )


                modo_edit_default = (
                    modo_atual

                    if modo_atual
                    in modos_editaveis

                    else

                    "Rápido"
                )


                modo_edit = st.radio(
                    "Modo",
                    modos_editaveis,
                    index=modos_editaveis.index(
                        modo_edit_default
                    ),
                    horizontal=True,
                    key=(
                        f"rec_edit_modo_"
                        f"{recorrente_id}"
                    ),
                )


                observacao_edit = st.text_area(
                    "Observação",
                    value=(
                        recorrente.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                    key=(
                        f"rec_edit_obs_"
                        f"{recorrente_id}"
                    ),
                )


                if st.button(
                    "💾 Salvar alterações",
                    key=(
                        f"rec_edit_salvar_"
                        f"{recorrente_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        atualizar_recorrente_banco(
                            recorrente_id,
                            nome_edit,
                            locais_edit,
                            inicio_edit,
                            fim_edit,
                            voltar_edit,
                            objetivo_edit,
                            modo_edit,
                            observacao_edit,
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"rec_conf_"
                        f"{recorrente_id}"
                    ),
                )


                if st.button(
                    "🗑️ Excluir recorrente",
                    key=(
                        f"rec_excluir_"
                        f"{recorrente_id}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    try:

                        excluir_recorrente_banco(
                            recorrente_id
                        )


                        st.rerun()


                    except Exception as e:

                        st.error(
                            str(
                                e
                            )
                        )


# ============================================================
# EDIÇÃO DE ROTA SALVA
# ============================================================

def renderizar_edicao_rota():

    rota = (
        st.session_state.edicao_rota
    )


    if not rota:

        st.warning(
            "Nenhuma rota foi selecionada "
            "para edição."
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


    limite_locais = (
        limite_locais_perfil(
            perfil_usuario_app
        )
    )


    if limite_locais > 0:

        st.caption(
            "Seu limite atual é de "
            f"**{limite_locais} locais por rota**."
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
                f"editar_local_"
                f"{i}_"
                f"{st.session_state.edicao_version}"
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


    if (
        limite_locais > 0
        and
        len(
            ativos
        ) > limite_locais
    ):

        st.warning(
            "A rota editada possui "
            f"{len(ativos)} locais, "
            "acima do seu limite atual de "
            f"{limite_locais}."
        )


    modo_rota = dados.get(
        "modo",
        "Rápido",
    )


    if (
        modo_rota == "Preciso"
        and
        not modo_preciso_permitido
    ):

        st.warning(
            "🔒 Esta rota foi salva no modo Preciso, "
            "mas essa modalidade não está disponível "
            "para sua conta. Para recalculá-la, "
            "será necessário utilizar o modo Rápido."
        )


        modo_recalculo = "Rápido"


    else:

        modo_recalculo = modo_rota


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
                    modo_recalculo,
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


            st.session_state.rota_aberta_salva = (
                True
            )


            st.session_state.resultado_historico = (
                False
            )


            st.session_state.resultado_direto = (
                False
            )


            st.session_state.pagina_app = (
                "nova"
            )


            limpar_edicao()


            st.rerun()


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
                "Não foi possível recalcular: "
                f"{e}"
            )


# ============================================================
# ROTEAMENTO
# ============================================================

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


# ============================================================
# RESULTADOS ABERTOS
# ============================================================

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


st.write(
    "Informe os locais manualmente, importe uma lista "
    "ou reutilize seus locais salvos."
)


limite_locais_atual = (
    limite_locais_perfil(
        perfil_usuario_app
    )
)


if limite_locais_atual > 0:

    st.caption(
        "Seu plano permite até "
        f"**{limite_locais_atual} locais por rota**."
    )


st.subheader(
    "📍 Locais da rota"
)


opcoes_modo = [
    "Digitar manualmente",
    "Importar CSV/Excel",
    "Selecionar locais salvos",
]


indice_padrao = (
    2

    if st.session_state.preset_local_salvo_ids

    else

    0
)


modo_entrada = st.radio(
    "Como deseja informar os locais?",
    opcoes_modo,
    index=indice_padrao,
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

importacao_pronta = False


# ============================================================
# MANUAL
# ============================================================

if modo_entrada == "Digitar manualmente":

    voltar_inicio = st.checkbox(
        "🔄 Retornar ao ponto inicial",
        key=(
            f"voltar_manual_"
            f"{st.session_state.form_version}"
        ),
    )


    col_inicio, col_fim = (
        st.columns(
            2
        )
    )


    with col_inicio:

        ponto_inicio = campo_local(
            chave=(
                f"inicio_manual_"
                f"{st.session_state.form_version}"
            ),
            label="Ponto inicial",
            placeholder=(
                "Digite pelo menos 3 letras..."
            ),
        )


    with col_fim:

        if voltar_inicio:

            ponto_fim = (
                ponto_inicio
            )


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
                key=(
                    f"fim_manual_desabilitado_"
                    f"{st.session_state.form_version}"
                ),
            )


        else:

            ponto_fim = campo_local(
                chave=(
                    f"fim_manual_"
                    f"{st.session_state.form_version}"
                ),
                label="Ponto final",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
                focus=ponto_inicio,
            )


    col_add, col_rem = (
        st.columns(
            2
        )
    )


    with col_add:

        if st.button(
            "➕ Adicionar parada",
            use_container_width=True,
            key=(
                f"add_parada_"
                f"{st.session_state.form_version}"
            ),
        ):

            st.session_state.num_paradas += 1

            st.rerun()


    with col_rem:

        if st.button(
            "➖ Remover parada",
            disabled=(
                st.session_state.num_paradas
                <= 1
            ),
            use_container_width=True,
            key=(
                f"rem_parada_"
                f"{st.session_state.form_version}"
            ),
        ):

            st.session_state.num_paradas -= 1

            st.rerun()


    for i in range(
        st.session_state.num_paradas
    ):

        paradas.append(
            campo_local(
                chave=(
                    f"parada_manual_"
                    f"{st.session_state.form_version}_"
                    f"{i}"
                ),
                label=(
                    f"Parada {i + 1}"
                ),
                placeholder=(
                    "Digite endereço ou estabelecimento..."
                ),
                focus=ponto_inicio,
            )
        )


# ============================================================
# LOCAIS SALVOS
# ============================================================

elif modo_entrada == "Selecionar locais salvos":

    try:

        locais_salvos = (
            listar_locais_salvos_usuario()
        )


    except Exception as e:

        st.error(
            f"Não foi possível carregar os locais: {e}"
        )

        locais_salvos = []


    mapa_salvos = {
        item[
            "id"
        ]:
            item

        for item
        in locais_salvos
    }


    selecionados = st.multiselect(
        "Locais",
        options=list(
            mapa_salvos.keys()
        ),
        default=[
            x

            for x
            in st.session_state.preset_local_salvo_ids

            if x
            in mapa_salvos
        ],
        key=(
            f"selecionar_salvos_"
            f"{st.session_state.form_version}"
        ),
        format_func=(
            lambda item_id:
            mapa_salvos[
                item_id
            ][
                "nome"
            ]
        ),
    )


    if (
        limite_locais_atual > 0
        and
        len(
            selecionados
        ) > limite_locais_atual
    ):

        st.warning(
            "Você selecionou "
            f"{len(selecionados)} locais. "
            "Seu limite atual é de "
            f"{limite_locais_atual}."
        )


    if len(
        selecionados
    ) >= 2:

        inicio_id = st.selectbox(
            "Ponto inicial",
            options=selecionados,
            key=(
                f"salvos_inicio_"
                f"{st.session_state.form_version}"
            ),
            format_func=(
                lambda item_id:
                mapa_salvos[
                    item_id
                ][
                    "nome"
                ]
            ),
        )


        ponto_inicio = (
            local_salvo_para_origem(
                mapa_salvos[
                    inicio_id
                ]
            )
        )


        voltar_inicio = st.checkbox(
            "🔄 Retornar ao ponto inicial",
            key=(
                f"salvos_voltar_"
                f"{st.session_state.form_version}"
            ),
        )


        fim_id = None


        if voltar_inicio:

            ponto_fim = (
                ponto_inicio
            )


        else:

            fim_id = st.selectbox(
                "Ponto final",
                options=[
                    x

                    for x
                    in selecionados

                    if x
                    != inicio_id
                ],
                key=(
                    f"salvos_fim_"
                    f"{st.session_state.form_version}"
                ),
                format_func=(
                    lambda item_id:
                    mapa_salvos[
                        item_id
                    ][
                        "nome"
                    ]
                ),
            )


            ponto_fim = (
                local_salvo_para_origem(
                    mapa_salvos[
                        fim_id
                    ]
                )
            )


        paradas = [
            local_salvo_para_origem(
                mapa_salvos[
                    x
                ]
            )

            for x
            in selecionados

            if (
                x
                != inicio_id
                and
                x
                != fim_id
            )
        ]


# ============================================================
# IMPORTAÇÃO
# ============================================================

else:

    st.info(
        "O sistema localizará automaticamente "
        "cada endereço importado."
    )


    modelo_csv = (
        'endereco;observacao\n'
        '"Havan, Brusque, SC";teste\n'
        '"Prefeitura Municipal de Brusque, Brusque, SC";teste\n'
        '"UNIFEBE, Brusque, SC";teste\n'
    )


    st.download_button(
        "⬇️ Baixar modelo CSV",
        data=modelo_csv.encode(
            "utf-8-sig"
        ),
        file_name=(
            "modelo_paradas.csv"
        ),
        mime="text/csv",
        key=(
            f"download_modelo_"
            f"{st.session_state.form_version}"
        ),
    )


    arquivo = st.file_uploader(
        "Arquivo CSV ou Excel",
        type=[
            "csv",
            "xlsx",
        ],
        key=(
            f"arquivo_importado_"
            f"{st.session_state.form_version}"
        ),
    )


    if arquivo:

        try:

            enderecos, hash_arquivo = (
                ler_arquivo_paradas(
                    arquivo
                )
            )


            if (
                limite_locais_atual > 0
                and
                len(
                    enderecos
                ) > limite_locais_atual
            ):

                st.warning(
                    "O arquivo contém "
                    f"{len(enderecos)} locais, "
                    "mas seu plano permite "
                    f"{limite_locais_atual} por rota."
                )


            confirmados = []


            for i, endereco in enumerate(
                enderecos
            ):

                candidatos = (
                    buscar_endereco_importado_cache(
                        endereco,
                        ORS_KEY,
                    )
                )


                if candidatos:

                    posicao = st.selectbox(
                        endereco,
                        options=list(
                            range(
                                len(
                                    candidatos
                                )
                            )
                        ),
                        key=(
                            f"imp_"
                            f"{hash_arquivo}_"
                            f"{i}"
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
                            posicao
                        ]
                    )


                else:

                    st.error(
                        f"Nenhum resultado encontrado para: "
                        f"{endereco}"
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
                )
                >= 2
            ):

                importacao_pronta = (
                    True
                )


                indices = list(
                    range(
                        len(
                            confirmados
                        )
                    )
                )


                indice_inicio = st.selectbox(
                    "Ponto inicial",
                    options=indices,
                    key=(
                        f"import_inicio_"
                        f"{hash_arquivo}"
                    ),
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
                    "🔄 Retornar ao ponto inicial",
                    key=(
                        f"import_voltar_"
                        f"{hash_arquivo}"
                    ),
                )


                indice_fim = None


                if voltar_inicio:

                    ponto_fim = (
                        ponto_inicio
                    )


                else:

                    indice_fim = st.selectbox(
                        "Ponto final",
                        options=[
                            x

                            for x
                            in indices

                            if x
                            != indice_inicio
                        ],
                        key=(
                            f"import_fim_"
                            f"{hash_arquivo}"
                        ),
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

                    if (
                        i
                        != indice_inicio
                        and
                        i
                        != indice_fim
                    )
                ]


                if supabase_configurado:

                    if st.button(
                        "📍 Salvar estes endereços em Meus locais",
                        use_container_width=True,
                        key=(
                            f"salvar_importados_"
                            f"{hash_arquivo}"
                        ),
                    ):

                        try:

                            inseridos, ignorados = (
                                salvar_locais_em_lote(
                                    confirmados
                                )
                            )


                            if inseridos:

                                st.success(
                                    f"{inseridos} local(is) salvos."
                                )


                            if ignorados:

                                st.info(
                                    f"{ignorados} local(is) "
                                    "já existiam."
                                )


                        except Exception as e:

                            st.error(
                                str(
                                    e
                                )
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


col_obj, col_modo = (
    st.columns(
        2
    )
)


with col_obj:

    objetivo = st.radio(
        "Otimizar por",
        [
            "Menor distância",
            "Menor tempo",
        ],
        horizontal=True,
        key=(
            f"objetivo_"
            f"{st.session_state.form_version}"
        ),
    )


with col_modo:

    modos_disponiveis = (
        opcoes_modo_usuario()
    )


    modo = st.radio(
        "Modo",
        modos_disponiveis,
        horizontal=True,
        key=(
            f"modo_"
            f"{st.session_state.form_version}"
        ),
    )


if not modo_preciso_permitido:

    st.caption(
        "🔒 O modo Preciso não está disponível "
        "para sua conta."
    )


# ============================================================
# CALCULAR
# ============================================================

st.divider()


if st.button(
    "🚗 Calcular melhor rota",
    type="primary",
    use_container_width=True,
    key=(
        f"calcular_"
        f"{st.session_state.form_version}"
    ),
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
        parada

        for parada
        in paradas

        if parada
    ]


    if (
        voltar_inicio
        and
        not paradas_validas
    ):

        st.error(
            "A rota precisa possuir "
            "pelo menos uma parada."
        )

        st.stop()


    locais = [
        converter_local(
            ponto_inicio,
            "Início",
        )
    ]


    for numero, parada in enumerate(
        paradas_validas,
        start=1,
    ):

        locais.append(
            converter_local(
                parada,
                f"Parada {numero}",
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
            "Calculando distâncias, tempos "
            "e procurando a melhor rota..."
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


        st.session_state.rota_atual_id = (
            None
        )


        st.session_state.rota_atual_nome = (
            None
        )


        st.session_state.rota_aberta_salva = (
            False
        )


        st.session_state.resultado_historico = (
            False
        )


        st.session_state.resultado_direto = (
            False
        )


        st.session_state.resultado_direto_titulo = (
            None
        )


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
                "A rota foi calculada, mas "
                f"o histórico falhou: {erro}"
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
            "Não foi possível calcular a rota: "
            f"{e}"
        )


# ============================================================
# RESULTADO
# ============================================================

if st.session_state.resultado_rota:

    st.divider()


    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )
