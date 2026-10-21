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
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide",
)


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

def init_state(
    nome,
    valor,
):

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

    st.session_state.ultima_execucao_id = None


def iniciar_nova_rota():

    limpar_rota_atual()

    limpar_edicao()

    st.session_state.num_paradas = 1

    st.session_state.form_version += 1

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

    mensagem = (
        st.session_state.get(
            "mensagem_sistema"
        )
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
# CABEÇALHO
# ============================================================

col_titulo, col_nova, col_rotas, col_hist, col_usuario, col_sair = (
    st.columns(
        [
            4,
            1.1,
            1.25,
            1.15,
            2,
            0.7,
        ]
    )
)


with col_titulo:

    st.title(
        "🗺️ Otimizador de Rotas"
    )


with col_nova:

    st.write("")

    if st.button(
        "➕ Nova rota",
        use_container_width=True,
    ):

        iniciar_nova_rota()

        st.rerun()


with col_rotas:

    st.write("")

    if st.button(
        "📂 Minhas rotas",
        use_container_width=True,
    ):

        st.session_state.pagina_app = (
            "minhas"
        )

        st.rerun()


with col_hist:

    st.write("")

    if st.button(
        "📊 Histórico",
        use_container_width=True,
    ):

        st.session_state.pagina_app = (
            "historico"
        )

        st.rerun()


with col_usuario:

    st.write(
        f"👤 **{usuario_nome}**"
    )

    if usuario_email:

        st.caption(
            usuario_email
        )


with col_sair:

    st.write("")

    st.button(
        "Sair",
        on_click=st.logout,
        use_container_width=True,
    )


mostrar_mensagem_sistema()


# ============================================================
# SUPABASE - REQUISIÇÃO GENÉRICA
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
            f"(HTTP {response.status_code}). "
            f"{response.text[:300]}"
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
# JSON SEGURO
# ============================================================

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
# ROTAS - LISTAR
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


# ============================================================
# ROTAS - SALVAR
# ============================================================

def salvar_rota_banco(
    nome,
    dados,
):

    nome = (
        nome
        or ""
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


# ============================================================
# ROTAS - ATUALIZAR DADOS
# ============================================================

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


# ============================================================
# ROTAS - ATUALIZAR COMPLETA
# ============================================================

def atualizar_rota_completa_banco(
    rota_id,
    nome,
    dados,
):

    nome = (
        nome
        or ""
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


# ============================================================
# ROTAS - RENOMEAR
# ============================================================

def renomear_rota_banco(
    rota_id,
    novo_nome,
):

    novo_nome = (
        novo_nome
        or ""
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


# ============================================================
# ROTAS - EXCLUIR
# ============================================================

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
# HISTÓRICO - REGISTRAR EXECUÇÃO
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


# ============================================================
# HISTÓRICO - VINCULAR ROTA SALVA
# ============================================================

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


# ============================================================
# HISTÓRICO - LISTAR
# ============================================================

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
# DATA/HORA
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
# CACHE AUTOCOMPLETE
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


# ============================================================
# CACHE IMPORTAÇÃO
# ============================================================

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


# ============================================================
# CACHE MATRIX
# ============================================================

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


# ============================================================
# CACHE DIRECTIONS
# ============================================================

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


# ============================================================
# AUTOCOMPLETE
# ============================================================

def buscar_sugestoes(
    searchterm,
    focus_lat=None,
    focus_lon=None,
):

    texto = (
        searchterm
        or ""
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


# ============================================================
# CAMPO LOCAL
# ============================================================

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
# NORMALIZA TEXTO
# ============================================================

def normalizar_texto(
    texto,
):

    texto = unicodedata.normalize(
        "NFKD",
        str(
            texto
        ).strip().lower(),
    )


    return "".join(
        caractere
        for caractere
        in texto
        if not unicodedata.combining(
            caractere
        )
    )


# ============================================================
# COLUNA ENDEREÇO
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


# ============================================================
# CSV
# ============================================================

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


# ============================================================
# LEITURA CSV/XLSX
# ============================================================

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
# CONVERTE LOCAL
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
    }


# ============================================================
# VALIDA LOCAIS
# ============================================================

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
            f"Esta rota possui {len(locais)} locais. "
            f"O limite atual é {max_locais}."
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


# ============================================================
# CALCULAR ROTA
# ============================================================

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
            3,

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
# ASSINATURA DE CONFIGURAÇÃO
# ============================================================

def assinatura_configuracao(
    locais,
    voltar_inicio,
    objetivo,
    modo,
):

    payload = {
        "locais": [
            {
                "nome":
                    local.get(
                        "nome",
                        "",
                    ),

                "latitude":
                    round(
                        float(
                            local[
                                "latitude"
                            ]
                        ),
                        7,
                    ),

                "longitude":
                    round(
                        float(
                            local[
                                "longitude"
                            ]
                        ),
                        7,
                    ),
            }

            for local
            in locais
        ],

        "voltar_inicio":
            bool(
                voltar_inicio
            ),

        "objetivo":
            objetivo,

        "modo":
            modo,
    }


    texto = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
    )


    return (
        hashlib.sha256(
            texto.encode(
                "utf-8"
            )
        )
        .hexdigest()
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
                nome,
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


# ============================================================
# HTML DO MAPA
# ============================================================

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
        + urlencode(
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
# RENDERIZAR RESULTADO
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


    # ========================================================
    # SNAPSHOT DO HISTÓRICO
    # ========================================================

    if st.session_state.resultado_historico:

        st.info(
            "📊 Você está visualizando um resultado "
            "salvo no histórico. Este é um snapshot "
            "da execução realizada naquele momento."
        )


    # ========================================================
    # ROTA SALVA ABERTA
    # ========================================================

    if (
        st.session_state.rota_atual_id
        and
        st.session_state.rota_atual_nome
    ):

        st.info(
            "📂 Rota salva aberta: "
            f"**{st.session_state.rota_atual_nome}**"
        )


        col_recalcular, col_editar, col_atualizar = (
            st.columns(
                3
            )
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


                    try:

                        registrar_execucao(
                            dados=novo_resultado,
                            rota_id=(
                                st.session_state.rota_atual_id
                            ),
                            nome_rota=(
                                st.session_state.rota_atual_nome
                            ),
                        )

                    except Exception as erro_hist:

                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "warning",

                            "texto":
                                (
                                    "A rota foi recalculada, mas "
                                    "não foi possível registrar "
                                    "o histórico: "
                                    f"{erro_hist}"
                                ),
                        }


                    if not st.session_state.mensagem_sistema:

                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    "Rota recalculada. "
                                    "Clique em 'Atualizar rota salva' "
                                    "se quiser gravar a nova versão."
                                ),
                        }


                    st.rerun()


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


    # ========================================================
    # MÉTRICAS
    # ========================================================

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


    # ========================================================
    # STATUS SOLVER
    # ========================================================

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


    # ========================================================
    # ORDEM
    # ========================================================

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
    # SALVAR ROTA
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

                    registro = (
                        salvar_rota_banco(
                            nome_rota,
                            dados,
                        )
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
            file_name="mapa_rota_otimizada.html",
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


    # ========================================================
    # MAPA NA TELA
    # ========================================================

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
# PÁGINA HISTÓRICO
# ============================================================

def renderizar_historico():

    st.subheader(
        "📊 Histórico"
    )


    st.write(
        "Cada cálculo e recálculo realizado pelo usuário "
        "é registrado aqui como um snapshot independente."
    )


    if not supabase_configurado:

        st.error(
            "Supabase não configurado."
        )

        return


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


    dias = mapa_periodos[
        periodo
    ]


    try:

        with st.spinner(
            "Carregando histórico..."
        ):

            execucoes = (
                listar_execucoes_usuario(
                    dias=dias
                )
            )


            total_rotas_salvas = (
                contar_rotas_usuario()
            )


    except Exception as e:

        st.error(
            "Não foi possível carregar "
            f"o histórico: {e}"
        )

        return


    # ========================================================
    # PAINEL
    # ========================================================

    total_execucoes = len(
        execucoes
    )


    total_km = sum(
        float(
            item.get(
                "distancia_m",
                0,
            )
        )

        for item
        in execucoes
    ) / 1000


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


    st.subheader(
        "📈 Meu painel"
    )


    col1, col2, col3, col4 = (
        st.columns(
            4
        )
    )


    with col1:

        st.metric(
            "Rotas salvas",
            total_rotas_salvas,
        )


    with col2:

        st.metric(
            "Rotas calculadas",
            total_execucoes,
        )


    with col3:

        st.metric(
            "Km calculados",
            f"{total_km:,.1f} km".replace(
                ",",
                ".",
            ),
        )


    with col4:

        st.metric(
            "Locais roteados",
            total_locais,
        )


    st.caption(
        f"As métricas de cálculos, quilômetros e locais "
        f"consideram: **{periodo}**."
    )


    st.divider()


    # ========================================================
    # SEM EXECUÇÕES
    # ========================================================

    if not execucoes:

        st.info(
            "Nenhuma execução encontrada neste período."
        )

        return


    st.subheader(
        "🕘 Execuções"
    )


    # ========================================================
    # CARDS DO HISTÓRICO
    # ========================================================

    for execucao in execucoes:

        execucao_id = execucao[
            "id"
        ]


        nome_rota = (
            execucao.get(
                "nome_rota"
            )
            or
            "Rota não salva"
        )


        criado = formatar_data(
            execucao.get(
                "created_at"
            )
        )


        distancia_km = (
            float(
                execucao.get(
                    "distancia_m",
                    0,
                )
            )
            /
            1000
        )


        minutos_total = round(
            float(
                execucao.get(
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


        quantidade_locais = int(
            execucao.get(
                "quantidade_locais",
                0,
            )
        )


        objetivo = execucao.get(
            "objetivo",
            "—",
        )


        modo = execucao.get(
            "modo",
            "—",
        )


        fechada = bool(
            execucao.get(
                "voltar_inicio",
                False,
            )
        )


        with st.container(
            border=True
        ):

            st.markdown(
                f"### {nome_rota}"
            )


            if criado:

                st.caption(
                    criado
                )


            col_a, col_b, col_c, col_d = (
                st.columns(
                    4
                )
            )


            with col_a:

                st.metric(
                    "Locais",
                    quantidade_locais,
                )


            with col_b:

                st.metric(
                    "Distância",
                    f"{distancia_km:.2f} km",
                )


            with col_c:

                st.metric(
                    "Tempo",
                    tempo,
                )


            with col_d:

                st.metric(
                    "Tipo",
                    (
                        "Fechada"
                        if fechada
                        else
                        "Aberta"
                    ),
                )


            st.caption(
                f"Otimização: **{objetivo}** · "
                f"Modo: **{modo}**"
            )


            if st.button(
                "📂 Abrir resultado",
                key=(
                    f"abrir_historico_"
                    f"{execucao_id}"
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


                st.session_state.ultima_execucao_id = (
                    None
                )


                st.session_state.pagina_app = (
                    "nova"
                )


                st.rerun()


# ============================================================
# PÁGINA MINHAS ROTAS
# ============================================================

def renderizar_minhas_rotas():

    st.subheader(
        "📂 Minhas rotas"
    )


    st.write(
        "As rotas desta página pertencem "
        "à sua conta Google atualmente conectada."
    )


    if not supabase_configurado:

        st.error(
            "Supabase não configurado."
        )

        return


    try:

        with st.spinner(
            "Carregando suas rotas..."
        ):

            rotas = (
                listar_rotas_usuario()
            )


    except Exception as e:

        st.error(
            "Não foi possível carregar "
            f"as rotas: {e}"
        )

        return


    if not rotas:

        st.info(
            "Você ainda não possui rotas salvas."
        )

        return


    st.caption(
        f"{len(rotas)} rota(s) salva(s)."
    )


    for rota in rotas:

        rota_id = rota[
            "id"
        ]


        nome = rota.get(
            "nome",
            "Rota sem nome",
        )


        dados = (
            rota.get(
                "dados"
            )
            or
            {}
        )


        locais_base = (
            dados.get(
                "locais_base"
            )
            or
            []
        )


        objetivo = dados.get(
            "objetivo",
            "—",
        )


        modo = dados.get(
            "modo",
            "—",
        )


        voltar_inicio = bool(
            dados.get(
                "voltar_inicio",
                False,
            )
        )


        atualizado = formatar_data(
            rota.get(
                "updated_at"
            )
        )


        with st.container(
            border=True
        ):

            st.markdown(
                f"### {nome}"
            )


            col_a, col_b, col_c, col_d = (
                st.columns(
                    4
                )
            )


            with col_a:

                st.metric(
                    "Locais",
                    len(
                        locais_base
                    ),
                )


            with col_b:

                st.metric(
                    "Objetivo",
                    (
                        "Distância"

                        if objetivo
                        == "Menor distância"

                        else

                        "Tempo"

                        if objetivo
                        == "Menor tempo"

                        else

                        objetivo
                    ),
                )


            with col_c:

                st.metric(
                    "Modo",
                    modo,
                )


            with col_d:

                st.metric(
                    "Tipo",
                    (
                        "Fechada"

                        if voltar_inicio

                        else

                        "Aberta"
                    ),
                )


            if atualizado:

                st.caption(
                    "Última alteração: "
                    f"{atualizado}"
                )


            col_abrir, col_editar, col_recalcular = (
                st.columns(
                    3
                )
            )


            # =================================================
            # ABRIR
            # =================================================

            with col_abrir:

                if st.button(
                    "📂 Abrir",
                    key=(
                        f"abrir_"
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


                    st.session_state.ultima_execucao_id = (
                        None
                    )


                    st.session_state.pagina_app = (
                        "nova"
                    )


                    st.rerun()


            # =================================================
            # EDITAR
            # =================================================

            with col_editar:

                if st.button(
                    "✏️ Editar",
                    key=(
                        f"editar_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    iniciar_edicao(
                        rota
                    )

                    st.rerun()


            # =================================================
            # RECALCULAR
            # =================================================

            with col_recalcular:

                if st.button(
                    "🔄 Recalcular",
                    key=(
                        f"recalcular_"
                        f"{rota_id}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        if not locais_base:

                            raise ValueError(
                                "Esta rota não possui "
                                "os locais originais salvos."
                            )


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


                        try:

                            registrar_execucao(
                                dados=novo_resultado,
                                rota_id=rota_id,
                                nome_rota=nome,
                            )

                        except Exception as erro_hist:

                            st.session_state.mensagem_sistema = {
                                "tipo":
                                    "warning",

                                "texto":
                                    (
                                        "A rota foi recalculada, "
                                        "mas o histórico não pôde "
                                        "ser registrado: "
                                        f"{erro_hist}"
                                    ),
                            }


                        st.session_state.resultado_rota = (
                            novo_resultado
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


                        st.session_state.ultima_execucao_id = (
                            None
                        )


                        st.session_state.pagina_app = (
                            "nova"
                        )


                        if not st.session_state.mensagem_sistema:

                            st.session_state.mensagem_sistema = {
                                "tipo":
                                    "success",

                                "texto":
                                    (
                                        "Rota recalculada. "
                                        "Confira o resultado e clique "
                                        "em 'Atualizar rota salva' "
                                        "se quiser armazenar "
                                        "a nova versão."
                                    ),
                            }


                        st.rerun()


                    except Exception as e:

                        st.error(
                            "Não foi possível recalcular: "
                            f"{e}"
                        )


            # =================================================
            # GERENCIAR
            # =================================================

            with st.expander(
                "⚙️ Gerenciar rota"
            ):

                with st.form(
                    key=(
                        f"form_renomear_"
                        f"{rota_id}"
                    )
                ):

                    novo_nome = st.text_input(
                        "Novo nome",
                        value=nome,
                        key=(
                            f"nome_"
                            f"{rota_id}"
                        ),
                    )


                    confirmar_renomear = (
                        st.form_submit_button(
                            "✏️ Renomear rota",
                            use_container_width=True,
                        )
                    )


                    if confirmar_renomear:

                        try:

                            renomear_rota_banco(
                                rota_id,
                                novo_nome,
                            )


                            if (
                                st.session_state.rota_atual_id
                                == rota_id
                            ):

                                st.session_state.rota_atual_nome = (
                                    novo_nome.strip()
                                )


                            st.session_state.mensagem_sistema = {
                                "tipo":
                                    "success",

                                "texto":
                                    "Rota renomeada com sucesso.",
                            }


                            st.rerun()


                        except Exception as e:

                            st.error(
                                "Não foi possível renomear: "
                                f"{e}"
                            )


                st.divider()


                confirmar_exclusao = st.checkbox(
                    (
                        "Confirmo que desejo "
                        "excluir esta rota"
                    ),
                    key=(
                        f"confirmar_excluir_"
                        f"{rota_id}"
                    ),
                )


                if st.button(
                    "🗑️ Excluir definitivamente",
                    key=(
                        f"excluir_"
                        f"{rota_id}"
                    ),
                    disabled=(
                        not confirmar_exclusao
                    ),
                    use_container_width=True,
                ):

                    try:

                        excluir_rota_banco(
                            rota_id
                        )


                        if (
                            st.session_state.rota_atual_id
                            == rota_id
                        ):

                            limpar_rota_atual()


                        if (
                            st.session_state.edicao_rota
                            and
                            st.session_state.edicao_rota.get(
                                "id"
                            )
                            == rota_id
                        ):

                            limpar_edicao()


                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    "Rota excluída. "
                                    "O histórico de execuções "
                                    "foi preservado."
                                ),
                        }


                        st.rerun()


                    except Exception as e:

                        st.error(
                            "Não foi possível excluir: "
                            f"{e}"
                        )


# ============================================================
# EDIÇÃO DE ROTA
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

        st.session_state.pagina_app = (
            "minhas"
        )

        return


    rota_id = rota[
        "id"
    ]


    dados_originais = (
        rota.get(
            "dados"
        )
        or
        {}
    )


    locais_originais = (
        dados_originais.get(
            "locais_base"
        )
        or
        []
    )


    versao = (
        st.session_state.edicao_version
    )


    if not locais_originais:

        st.error(
            "Esta rota não possui os locais "
            "originais necessários para edição."
        )

        return


    # ========================================================
    # CABEÇALHO EDIÇÃO
    # ========================================================

    col_titulo_edicao, col_cancelar = (
        st.columns(
            [
                5,
                1,
            ]
        )
    )


    with col_titulo_edicao:

        st.subheader(
            "✏️ Editar rota — "
            f"{rota.get('nome', 'Rota sem nome')}"
        )


    with col_cancelar:

        if st.button(
            "Cancelar",
            key=(
                f"cancelar_edicao_"
                f"{versao}"
            ),
            use_container_width=True,
        ):

            limpar_edicao()

            st.session_state.pagina_app = (
                "minhas"
            )

            st.rerun()


    st.write(
        "Os locais já salvos usam diretamente as "
        "coordenadas armazenadas. Só os novos locais "
        "precisam passar pela busca de endereços."
    )


    nome_rota = st.text_input(
        "Nome da rota",
        value=(
            rota.get(
                "nome",
                "",
            )
        ),
        key=(
            f"edit_nome_rota_"
            f"{versao}"
        ),
    )


    # ========================================================
    # LOCAIS EXISTENTES
    # ========================================================

    st.subheader(
        "📍 Locais existentes"
    )


    st.caption(
        "Desmarque um local para removê-lo."
    )


    itens_ativos = []


    for indice, local in enumerate(
        locais_originais
    ):

        incluir = st.checkbox(
            (
                f"{indice + 1}. "
                f"{local.get('nome', 'Local sem nome')}"
            ),
            value=True,
            key=(
                f"edit_incluir_"
                f"{versao}_"
                f"{indice}"
            ),
        )


        if incluir:

            itens_ativos.append(
                {
                    "id":
                        f"old_{indice}",

                    "local":
                        local,
                }
            )


    # ========================================================
    # NOVOS LOCAIS
    # ========================================================

    st.subheader(
        "➕ Novos locais"
    )


    col_add, col_remove, _ = (
        st.columns(
            [
                1,
                1,
                4,
            ]
        )
    )


    with col_add:

        if st.button(
            "➕ Adicionar local",
            key=(
                f"edit_add_novo_"
                f"{versao}"
            ),
            use_container_width=True,
        ):

            st.session_state.edicao_num_novos += 1

            st.session_state.edicao_resultado = None

            st.session_state.edicao_assinatura = None

            st.rerun()


    with col_remove:

        if st.button(
            "➖ Remover campo",
            key=(
                f"edit_remove_novo_"
                f"{versao}"
            ),
            disabled=(
                st.session_state.edicao_num_novos
                <= 0
            ),
            use_container_width=True,
        ):

            st.session_state.edicao_num_novos -= 1

            st.session_state.edicao_resultado = None

            st.session_state.edicao_assinatura = None

            st.rerun()


    focus_edicao = (
        locais_originais[
            0
        ]

        if locais_originais

        else

        None
    )


    for indice in range(
        st.session_state.edicao_num_novos
    ):

        novo = campo_local(
            chave=(
                f"edit_novo_local_"
                f"{versao}_"
                f"{indice}"
            ),
            label=(
                f"Novo local {indice + 1}"
            ),
            placeholder=(
                "Digite um endereço "
                "ou estabelecimento..."
            ),
            focus=focus_edicao,
        )


        if novo:

            itens_ativos.append(
                {
                    "id":
                        f"new_{indice}",

                    "local":
                        converter_local(
                            novo,
                            "Parada",
                        ),
                }
            )


    if len(
        itens_ativos
    ) < 2:

        st.warning(
            "Mantenha ou adicione pelo menos "
            "dois locais."
        )

        return


    mapa_itens = {
        item[
            "id"
        ]:
            item[
                "local"
            ]

        for item
        in itens_ativos
    }


    ids_ativos = list(
        mapa_itens.keys()
    )


    # ========================================================
    # INÍCIO/FIM
    # ========================================================

    st.divider()

    st.subheader(
        "🚩 Início e fim"
    )


    start_key = (
        f"edit_inicio_"
        f"{versao}"
    )


    inicio_preferido = (
        "old_0"

        if "old_0"
        in ids_ativos

        else

        ids_ativos[
            0
        ]
    )


    if (
        st.session_state.get(
            start_key
        )
        not in ids_ativos
    ):

        st.session_state[
            start_key
        ] = inicio_preferido


    id_inicio = st.selectbox(
        "Ponto inicial",
        options=ids_ativos,
        key=start_key,
        format_func=(
            lambda item_id:
            mapa_itens[
                item_id
            ].get(
                "nome",
                "Local",
            )
        ),
    )


    voltar_inicio = st.checkbox(
        "🔄 Retornar ao ponto inicial",
        value=bool(
            dados_originais.get(
                "voltar_inicio",
                False,
            )
        ),
        key=(
            f"edit_voltar_inicio_"
            f"{versao}"
        ),
    )


    id_fim = None


    if voltar_inicio:

        st.info(
            "A rota terminará novamente em "
            f"**{mapa_itens[id_inicio].get('nome', 'ponto inicial')}**."
        )


    else:

        ids_fim = [
            item_id
            for item_id
            in ids_ativos
            if item_id
            != id_inicio
        ]


        fim_key = (
            f"edit_fim_"
            f"{versao}"
        )


        ultimo_original = (
            f"old_"
            f"{len(locais_originais) - 1}"
        )


        fim_preferido = (
            ultimo_original

            if ultimo_original
            in ids_fim

            else

            ids_fim[
                -1
            ]
        )


        if (
            st.session_state.get(
                fim_key
            )
            not in ids_fim
        ):

            st.session_state[
                fim_key
            ] = fim_preferido


        id_fim = st.selectbox(
            "Ponto final",
            options=ids_fim,
            key=fim_key,
            format_func=(
                lambda item_id:
                mapa_itens[
                    item_id
                ].get(
                    "nome",
                    "Local",
                )
            ),
        )


    intermediarios = [
        item_id
        for item_id
        in ids_ativos
        if (
            item_id
            != id_inicio
            and
            item_id
            != id_fim
        )
    ]


    locais_para_solver = [
        mapa_itens[
            id_inicio
        ]
    ]


    locais_para_solver.extend(
        mapa_itens[
            item_id
        ]

        for item_id
        in intermediarios
    )


    if (
        not voltar_inicio
        and
        id_fim is not None
    ):

        locais_para_solver.append(
            mapa_itens[
                id_fim
            ]
        )


    st.caption(
        f"Locais ativos: "
        f"**{len(locais_para_solver)}** · "
        f"Paradas intermediárias: "
        f"**{len(intermediarios)}**"
    )


    # ========================================================
    # CONFIGURAÇÃO EDIÇÃO
    # ========================================================

    st.divider()

    st.subheader(
        "⚙️ Configuração da otimização"
    )


    col_obj, col_modo = (
        st.columns(
            2
        )
    )


    objetivo_original = (
        dados_originais.get(
            "objetivo",
            "Menor distância",
        )
    )


    modo_original = (
        dados_originais.get(
            "modo",
            "Rápido",
        )
    )


    with col_obj:

        objetivo = st.radio(
            "Otimizar por",
            [
                "Menor distância",
                "Menor tempo",
            ],
            index=(
                0
                if objetivo_original
                == "Menor distância"
                else
                1
            ),
            horizontal=True,
            key=(
                f"edit_objetivo_"
                f"{versao}"
            ),
        )


    with col_modo:

        modo = st.radio(
            "Modo de otimização",
            [
                "Rápido",
                "Preciso",
            ],
            index=(
                0
                if modo_original
                == "Rápido"
                else
                1
            ),
            horizontal=True,
            key=(
                f"edit_modo_"
                f"{versao}"
            ),
        )


    assinatura_atual = (
        assinatura_configuracao(
            locais_para_solver,
            voltar_inicio,
            objetivo,
            modo,
        )
    )


    st.divider()


    # ========================================================
    # RECALCULAR EDIÇÃO
    # ========================================================

    if st.button(
        "🚗 Recalcular rota editada",
        type="primary",
        use_container_width=True,
        key=(
            f"edit_calcular_"
            f"{versao}"
        ),
    ):

        try:

            with st.spinner(
                "Calculando a nova versão da rota..."
            ):

                novo_resultado = (
                    calcular_rota_a_partir_de_locais(
                        locais=locais_para_solver,
                        voltar_inicio=voltar_inicio,
                        objetivo=objetivo,
                        modo=modo,
                    )
                )


            st.session_state.edicao_resultado = (
                novo_resultado
            )


            st.session_state.edicao_assinatura = (
                assinatura_atual
            )


            try:

                registrar_execucao(
                    dados=novo_resultado,
                    rota_id=rota_id,
                    nome_rota=nome_rota,
                )

            except Exception as erro_hist:

                st.warning(
                    "A rota foi calculada, mas "
                    "o histórico não pôde ser registrado: "
                    f"{erro_hist}"
                )


            st.success(
                "Nova versão da rota calculada."
            )


        except Exception as e:

            st.error(
                "Não foi possível recalcular: "
                f"{e}"
            )


    resultado_editado = (
        st.session_state.edicao_resultado
    )


    if not resultado_editado:

        return


    if (
        st.session_state.edicao_assinatura
        != assinatura_atual
    ):

        st.warning(
            "Você alterou os locais ou as configurações "
            "depois do último cálculo. Recalcule a rota "
            "antes de salvar."
        )

        return


    (
        distancia_km,
        tempo_texto,
    ) = dados_metricas(
        resultado_editado
    )


    locais_ordenados = (
        resultado_editado.get(
            "locais_ordenados"
        )
        or
        []
    )


    solver = (
        resultado_editado.get(
            "solver"
        )
        or
        {}
    )


    # ========================================================
    # PRÉVIA
    # ========================================================

    st.divider()

    st.subheader(
        "✅ Prévia da rota editada"
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
                resultado_editado.get(
                    "locais_base"
                )
                or
                []
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
            "Gap máximo restante: "
            f"{float(gap):.3f}%."
        )


    st.write(
        "#### Ordem proposta"
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
    # MAPA DA EDIÇÃO
    # ========================================================

    try:

        mapa_edicao = criar_mapa(
            geometry=(
                resultado_editado.get(
                    "geometry"
                )
                or
                []
            ),
            locais_ordenados=locais_ordenados,
            rota_fechada=voltar_inicio,
            distancia_km=distancia_km,
            tempo_texto=tempo_texto,
            objetivo=objetivo,
        )


        st_folium(
            mapa_edicao,
            height=500,
            use_container_width=True,
            returned_objects=[],
            key=(
                f"mapa_edicao_"
                f"{versao}"
            ),
        )


    except Exception as e:

        st.warning(
            "A prévia do mapa não pôde "
            f"ser exibida: {e}"
        )


    # ========================================================
    # SALVAR EDIÇÃO
    # ========================================================

    st.subheader(
        "💾 Salvar alterações"
    )


    col_atualizar, col_copia = (
        st.columns(
            2
        )
    )


    with col_atualizar:

        if st.button(
            "💾 Atualizar rota existente",
            type="primary",
            use_container_width=True,
            key=(
                f"edit_salvar_existente_"
                f"{versao}"
            ),
        ):

            try:

                registro = (
                    atualizar_rota_completa_banco(
                        rota_id,
                        nome_rota,
                        resultado_editado,
                    )
                )


                st.session_state.resultado_rota = (
                    resultado_editado
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


                st.session_state.pagina_app = (
                    "nova"
                )


                limpar_edicao()


                st.session_state.mensagem_sistema = {
                    "tipo":
                        "success",

                    "texto":
                        (
                            f"Rota "
                            f"'{registro['nome']}' "
                            f"atualizada com sucesso."
                        ),
                }


                st.rerun()


            except Exception as e:

                st.error(
                    "Não foi possível atualizar "
                    f"a rota: {e}"
                )


    with col_copia:

        with st.form(
            key=(
                f"edit_form_copia_"
                f"{versao}"
            )
        ):

            nome_copia = st.text_input(
                "Nome da cópia",
                value=(
                    f"{nome_rota} - cópia"

                    if nome_rota

                    else

                    ""
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
                            resultado_editado,
                        )
                    )


                    st.session_state.resultado_rota = (
                        resultado_editado
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


                    st.session_state.pagina_app = (
                        "nova"
                    )


                    limpar_edicao()


                    st.session_state.mensagem_sistema = {
                        "tipo":
                            "success",

                        "texto":
                            (
                                f"Nova rota "
                                f"'{registro['nome']}' "
                                f"criada com sucesso."
                            ),
                    }


                    st.rerun()


                except Exception as e:

                    st.error(
                        "Não foi possível salvar "
                        f"a cópia: {e}"
                    )


# ============================================================
# ROTEAMENTO DE PÁGINAS
# ============================================================

if (
    st.session_state.pagina_app
    == "minhas"
):

    renderizar_minhas_rotas()

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
# PÁGINA PRINCIPAL
# ============================================================

if not ors_configurado:

    st.error(
        "OpenRouteService não configurado."
    )


if not supabase_configurado:

    st.warning(
        "O cálculo de rotas continuará funcionando, "
        "mas não será possível salvar no Supabase."
    )


# ============================================================
# ROTA SALVA/HISTÓRICO ABERTO
# ============================================================

if (
    (
        st.session_state.rota_aberta_salva
        or
        st.session_state.resultado_historico
    )
    and
    st.session_state.resultado_rota
):

    if st.session_state.rota_aberta_salva:

        st.subheader(
            "📂 Rota salva"
        )


        st.write(
            "As coordenadas armazenadas serão utilizadas "
            "sem necessidade de geocodificar novamente."
        )


    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )


    st.stop()


# ============================================================
# NOVA ROTA
# ============================================================

st.write(
    "Informe os locais manualmente ou importe uma lista "
    "por CSV/Excel. O sistema calculará a melhor sequência."
)


st.subheader(
    "📍 Locais da rota"
)


modo_entrada = st.radio(
    "Como deseja informar os locais?",
    [
        "Digitar manualmente",
        "Importar CSV/Excel",
    ],
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
            f"manual_voltar_inicio_"
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
                f"busca_ponto_inicio_"
                f"{st.session_state.form_version}"
            ),
            label="📍 Ponto inicial",
            placeholder=(
                "Digite pelo menos 3 letras..."
            ),
        )


    with col_fim:

        if voltar_inicio:

            st.text_input(
                "🏁 Ponto final",
                value=(
                    ponto_inicio[
                        "label"
                    ]

                    if ponto_inicio

                    else

                    "Selecione primeiro "
                    "o ponto inicial"
                ),
                disabled=True,
                key=(
                    f"fim_desabilitado_"
                    f"{st.session_state.form_version}"
                ),
            )


            ponto_fim = (
                ponto_inicio
            )


        else:

            ponto_fim = campo_local(
                chave=(
                    f"busca_ponto_fim_"
                    f"{st.session_state.form_version}"
                ),
                label="🏁 Ponto final",
                placeholder=(
                    "Digite pelo menos 3 letras..."
                ),
                focus=ponto_inicio,
            )


    if ponto_inicio:

        st.caption(
            "📌 As demais buscas estão priorizando "
            "locais próximos de "
            f"**{ponto_inicio['label']}**."
        )


    st.subheader(
        "Paradas intermediárias"
    )


    col_adicionar, col_remover, _ = (
        st.columns(
            [
                1,
                1,
                4,
            ]
        )
    )


    with col_adicionar:

        if st.button(
            "➕ Adicionar parada",
            use_container_width=True,
            key=(
                f"adicionar_parada_"
                f"{st.session_state.form_version}"
            ),
        ):

            st.session_state.num_paradas += 1

            st.rerun()


    with col_remover:

        if st.button(
            "➖ Remover parada",
            disabled=(
                st.session_state.num_paradas
                <= 1
            ),
            use_container_width=True,
            key=(
                f"remover_parada_"
                f"{st.session_state.form_version}"
            ),
        ):

            st.session_state.num_paradas -= 1

            st.rerun()


    for i in range(
        st.session_state.num_paradas
    ):

        parada = campo_local(
            chave=(
                f"busca_parada_"
                f"{st.session_state.form_version}_"
                f"{i}"
            ),
            label=(
                f"Parada {i + 1}"
            ),
            placeholder=(
                "Digite um endereço "
                "ou estabelecimento..."
            ),
            focus=ponto_inicio,
        )


        paradas.append(
            parada
        )


# ============================================================
# IMPORTAÇÃO
# ============================================================

else:

    st.info(
        "O sistema localizará automaticamente cada endereço "
        "e utilizará o primeiro resultado encontrado. "
        "Se necessário, você poderá trocar algum resultado."
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
        "Selecione o arquivo",
        type=[
            "csv",
            "xlsx",
        ],
        key=(
            f"arquivo_importado_"
            f"{st.session_state.form_version}"
        ),
    )


    if arquivo is not None:

        try:

            (
                enderecos_importados,
                hash_arquivo,
            ) = ler_arquivo_paradas(
                arquivo
            )


        except Exception as e:

            st.error(
                str(
                    e
                )
            )

            st.stop()


        if len(
            enderecos_importados
        ) > 50:

            st.error(
                "O arquivo possui mais "
                "de 50 locais."
            )

            st.stop()


        st.success(
            f"{len(enderecos_importados)} "
            "endereços importados."
        )


        resultados_por_endereco = []


        with st.spinner(
            "Localizando automaticamente "
            "os endereços importados..."
        ):

            for endereco in enderecos_importados:

                try:

                    candidatos = (
                        buscar_endereco_importado_cache(
                            endereco,
                            ORS_KEY,
                        )
                    )

                except Exception:

                    candidatos = []


                resultados_por_endereco.append(
                    candidatos
                )


        nao_encontrados = [
            indice + 1

            for indice, candidatos
            in enumerate(
                resultados_por_endereco
            )

            if not candidatos
        ]


        if nao_encontrados:

            st.error(
                "Não foi possível localizar "
                "automaticamente os itens: "
                +
                ", ".join(
                    map(
                        str,
                        nao_encontrados,
                    )
                )
                +
                "."
            )


        st.subheader(
            "🔎 Endereços identificados"
        )


        st.caption(
            "O primeiro resultado encontrado já está selecionado. "
            "Só altere um item se o local estiver incorreto."
        )


        locais_confirmados = []


        for indice, (
            endereco_original,
            candidatos,
        ) in enumerate(
            zip(
                enderecos_importados,
                resultados_por_endereco,
            ),
            start=1,
        ):

            if not candidatos:

                st.error(
                    f"{indice}. "
                    f"{endereco_original} — "
                    "nenhum resultado encontrado."
                )


                locais_confirmados.append(
                    None
                )

                continue


            posicao = st.selectbox(
                (
                    f"{indice}. "
                    f"{endereco_original}"
                ),
                options=list(
                    range(
                        len(
                            candidatos
                        )
                    )
                ),
                index=0,
                key=(
                    f"import_result_"
                    f"{st.session_state.form_version}_"
                    f"{hash_arquivo}_"
                    f"{indice}"
                ),
                format_func=(
                    lambda posicao,
                    lista=candidatos:
                    lista[
                        posicao
                    ][
                        "label"
                    ]
                ),
            )


            locais_confirmados.append(
                candidatos[
                    posicao
                ]
            )


        if all(
            local is not None
            for local
            in locais_confirmados
        ):

            importacao_pronta = True


            st.success(
                "Todos os endereços foram identificados."
            )


            st.divider()


            st.subheader(
                "🚩 Defina o início "
                "e o fim da rota"
            )


            indices = list(
                range(
                    len(
                        locais_confirmados
                    )
                )
            )


            indice_inicio = st.selectbox(
                "Ponto inicial",
                options=indices,
                key=(
                    f"inicio_"
                    f"{st.session_state.form_version}_"
                    f"{hash_arquivo}"
                ),
                format_func=(
                    lambda indice:
                    (
                        f"{indice + 1}. "
                        f"{locais_confirmados[indice]['label']}"
                    )
                ),
            )


            ponto_inicio = (
                locais_confirmados[
                    indice_inicio
                ]
            )


            voltar_inicio = st.checkbox(
                "🔄 Retornar ao ponto inicial",
                key=(
                    f"voltar_"
                    f"{st.session_state.form_version}_"
                    f"{hash_arquivo}"
                ),
            )


            if voltar_inicio:

                ponto_fim = (
                    ponto_inicio
                )


                indice_fim = None


                st.info(
                    "A rota terminará novamente em "
                    f"**{ponto_inicio['label']}**."
                )


            else:

                opcoes_fim = [
                    indice

                    for indice
                    in indices

                    if indice
                    != indice_inicio
                ]


                indice_fim = st.selectbox(
                    "Ponto final",
                    options=opcoes_fim,
                    key=(
                        f"fim_"
                        f"{st.session_state.form_version}_"
                        f"{hash_arquivo}"
                    ),
                    format_func=(
                        lambda indice:
                        (
                            f"{indice + 1}. "
                            f"{locais_confirmados[indice]['label']}"
                        )
                    ),
                )


                ponto_fim = (
                    locais_confirmados[
                        indice_fim
                    ]
                )


            excluir = {
                indice_inicio
            }


            if (
                not voltar_inicio
                and
                indice_fim is not None
            ):

                excluir.add(
                    indice_fim
                )


            paradas = [
                local

                for indice, local
                in enumerate(
                    locais_confirmados
                )

                if indice
                not in excluir
            ]


            st.caption(
                "Paradas intermediárias: "
                f"**{len(paradas)}**"
            )


    else:

        st.caption(
            "Selecione um arquivo para continuar."
        )


# ============================================================
# ERRO AUTOCOMPLETE
# ============================================================

if st.session_state.erro_autocomplete:

    st.warning(
        st.session_state.erro_autocomplete
    )


# ============================================================
# CONFIGURAÇÃO DA OTIMIZAÇÃO
# ============================================================

st.divider()


st.subheader(
    "⚙️ Configuração da otimização"
)


col_objetivo, col_modo = (
    st.columns(
        2
    )
)


with col_objetivo:

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

    modo = st.radio(
        "Modo de otimização",
        [
            "Rápido",
            "Preciso",
        ],
        horizontal=True,
        key=(
            f"modo_"
            f"{st.session_state.form_version}"
        ),
    )


if objetivo == "Menor distância":

    st.info(
        "📏 O algoritmo minimizará "
        "a distância total percorrida."
    )


else:

    st.info(
        "⏱️ O algoritmo minimizará "
        "o tempo estimado de deslocamento."
    )


if modo == "Preciso":

    st.caption(
        "🎯 O modo Preciso executa várias estratégias "
        "e tenta melhorar ou comprovar a solução."
    )


else:

    st.caption(
        "⚡ O modo Rápido prioriza velocidade."
    )


# ============================================================
# CALCULAR
# ============================================================

st.divider()


calcular = st.button(
    "🚗 Calcular melhor rota",
    type="primary",
    use_container_width=True,
    key=(
        f"calcular_"
        f"{st.session_state.form_version}"
    ),
)


# ============================================================
# EXECUÇÃO
# ============================================================

if calcular:

    if not ors_configurado:

        st.error(
            "OpenRouteService não configurado."
        )

        st.stop()


    if (
        modo_entrada
        == "Importar CSV/Excel"
        and
        not importacao_pronta
    ):

        st.error(
            "Existem endereços importados "
            "que não foram localizados."
        )

        st.stop()


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
        if parada is not None
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


    # ========================================================
    # MONTA LISTA DOS LOCAIS
    # ========================================================

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


    # ========================================================
    # CALCULA
    # ========================================================

    try:

        validar_locais_base(
            locais,
            voltar_inicio,
        )


        with st.spinner(
            "Calculando distâncias, tempos "
            "e procurando a melhor rota..."
        ):

            resultado_rota = (
                calcular_rota_a_partir_de_locais(
                    locais=locais,
                    voltar_inicio=voltar_inicio,
                    objetivo=objetivo,
                    modo=modo,
                )
            )


        st.session_state.resultado_rota = (
            resultado_rota
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


        # ====================================================
        # REGISTRA NO HISTÓRICO
        # ====================================================

        try:

            execucao = registrar_execucao(
                dados=resultado_rota,
                rota_id=None,
                nome_rota=None,
            )


            st.session_state.ultima_execucao_id = (
                execucao[
                    "id"
                ]
            )


        except Exception as erro_hist:

            st.session_state.ultima_execucao_id = (
                None
            )


            st.warning(
                "A rota foi calculada, mas "
                "não foi possível registrar "
                "o histórico: "
                f"{erro_hist}"
            )


        st.success(
            "Rota calculada com sucesso."
        )


    except (
        ORSError,
        RouteOptimizationError,
        ValueError,
    ) as e:

        st.error(
            str(
                e
            )
        )

        st.stop()


    except Exception as e:

        st.error(
            "Ocorreu um erro inesperado "
            "ao calcular a rota."
        )


        st.exception(
            e
        )


        st.stop()


# ============================================================
# RESULTADO PERSISTIDO
# ============================================================

if st.session_state.resultado_rota:

    st.divider()


    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )
