import hashlib
import io
import unicodedata
from urllib.parse import urlencode

import folium
import pandas as pd
import streamlit as st

from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

from ors import (
    autocomplete_enderecos,
    obter_matriz_distancias,
    obter_rota_completa,
    ORSError,
)

from route_engine import (
    otimizar_rota,
    RouteOptimizationError,
)


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ Otimizador de Rotas")

st.write(
    "Informe o ponto inicial, o ponto final e as paradas. "
    "Você pode digitar as paradas manualmente ou importá-las "
    "de um arquivo CSV/Excel."
)


# ============================================================
# OPENROUTESERVICE
# ============================================================

try:
    ORS_KEY = st.secrets["ORS_KEY"]
    ors_configurado = True

except Exception:
    ORS_KEY = None
    ors_configurado = False


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("Configuração")

    if ors_configurado:
        st.success("OpenRouteService configurado")

    else:
        st.error("OpenRouteService não configurado")

    st.divider()

    st.caption(
        "Depois que o ponto inicial for escolhido, "
        "as demais pesquisas priorizam locais próximos dele."
    )


# ============================================================
# SESSION STATE
# ============================================================

if "num_paradas" not in st.session_state:
    st.session_state.num_paradas = 1

if "erro_autocomplete" not in st.session_state:
    st.session_state.erro_autocomplete = None


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
        coordenadas=list(coordenadas),
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
        coordenadas=list(coordenadas),
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

    texto = (searchterm or "").strip()

    if len(texto) < 3:
        return []

    if not ors_configurado:
        return []

    try:

        resultados = buscar_sugestoes_cache(
            texto,
            focus_lat,
            focus_lon,
            ORS_KEY,
        )

        st.session_state.erro_autocomplete = None

    except ORSError as e:

        st.session_state.erro_autocomplete = str(e)
        return []

    except Exception:

        st.session_state.erro_autocomplete = (
            "Não foi possível pesquisar "
            "o local neste momento."
        )

        return []

    return [
        (
            resultado["label"],
            resultado,
        )
        for resultado in resultados
    ]


# ============================================================
# CAMPO DE LOCAL
# ============================================================

def campo_local(
    chave,
    label,
    placeholder,
    focus=None,
    default_searchterm="",
):

    if focus:

        focus_lat = focus.get("latitude")
        focus_lon = focus.get("longitude")

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
        default_searchterm=default_searchterm,
        focus_lat=focus_lat,
        focus_lon=focus_lon,
    )


# ============================================================
# NORMALIZA TEXTO
# ============================================================

def normalizar_texto(
    texto,
):

    texto = str(texto).strip().lower()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(
            caractere
        )
    )

    return texto


# ============================================================
# LOCALIZA COLUNA ENDEREÇO
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


# ============================================================
# DECODIFICA CSV
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


# ============================================================
# REMOVE ASPAS EXTERNAS
# ============================================================

def limpar_linha_csv_simples(
    linha,
):

    valor = linha.strip()

    if (
        len(valor) >= 2
        and
        (
            (
                valor.startswith('"')
                and
                valor.endswith('"')
            )
            or
            (
                valor.startswith("'")
                and
                valor.endswith("'")
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


# ============================================================
# LEITURA ROBUSTA DE CSV
# ============================================================

def ler_csv_robusto(
    bytes_arquivo,
):
    """
    Aceita, entre outros:

    1) CSV de uma única coluna:

       endereco
       Havan, Brusque, SC
       Prefeitura Municipal de Brusque, Brusque, SC

       Nesse caso as vírgulas pertencem ao endereço e NÃO
       devem ser interpretadas como separadores.

    2) CSV separado por ponto e vírgula:

       endereco;observacao
       Havan, Brusque, SC;cliente 1

    3) CSV convencional separado por vírgula:

       endereco,observacao
       "Havan, Brusque, SC","cliente 1"
    """

    texto = decodificar_csv(
        bytes_arquivo
    )

    linhas = [
        linha
        for linha in texto.splitlines()
        if linha.strip()
    ]

    if not linhas:

        raise ValueError(
            "O arquivo CSV está vazio."
        )

    # ========================================================
    # CASO ESPECIAL:
    # CABEÇALHO ÚNICO "endereco"
    # ========================================================
    #
    # Esse tratamento é importante porque um detector
    # automático de delimitador pode confundir as vírgulas
    # existentes dentro dos próprios endereços.
    # ========================================================

    cabecalho_simples = (
        normalizar_texto(
            linhas[0]
            .strip()
            .strip('"')
            .strip("'")
        )
    )

    if cabecalho_simples == "endereco":

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

    # ========================================================
    # CSV COM MAIS DE UMA COLUNA
    # ========================================================

    erros = []

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

        except Exception as e:

            erros.append(
                str(e)
            )

            continue

        if (
            localizar_coluna_endereco(
                dataframe
            )
            is not None
        ):

            return dataframe

    raise ValueError(
        "Não foi possível identificar a coluna "
        "'endereco' no CSV. Use o modelo fornecido "
        "pelo aplicativo."
    )


# ============================================================
# LEITURA CSV / EXCEL
# ============================================================

def ler_arquivo_paradas(
    arquivo,
):

    bytes_arquivo = (
        arquivo.getvalue()
    )

    nome_arquivo = (
        arquivo.name.lower()
    )

    # ========================================================
    # CSV
    # ========================================================

    if nome_arquivo.endswith(
        ".csv"
    ):

        dataframe = ler_csv_robusto(
            bytes_arquivo
        )

    # ========================================================
    # EXCEL
    # ========================================================

    elif nome_arquivo.endswith(
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

    # ========================================================
    # COLUNA ENDEREÇO
    # ========================================================

    coluna_endereco = (
        localizar_coluna_endereco(
            dataframe
        )
    )

    if coluna_endereco is None:

        raise ValueError(
            "O arquivo precisa possuir "
            "uma coluna chamada 'endereco' "
            "ou 'endereço'."
        )

    # ========================================================
    # REMOVE VAZIOS
    # ========================================================

    enderecos = []

    for valor in dataframe[
        coluna_endereco
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
            "Nenhum endereço válido foi "
            "encontrado na coluna."
        )

    # ========================================================
    # HASH DO ARQUIVO
    # ========================================================

    hash_arquivo = hashlib.sha256(
        bytes_arquivo
    ).hexdigest()[:12]

    return (
        enderecos,
        hash_arquivo,
    )


# ============================================================
# MAPA
# ============================================================

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
        for lon, lat in geometry
    ]

    if not path:

        raise ValueError(
            "A rota não possui geometria."
        )

    centro = [
        sum(
            ponto[0]
            for ponto in path
        ) / len(path),

        sum(
            ponto[1]
            for ponto in path
        ) / len(path),
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

    if rota_fechada:

        locais_marcadores = (
            locais_ordenados[:-1]
        )

    else:

        locais_marcadores = (
            locais_ordenados
        )

    for numero, local in enumerate(
        locais_marcadores,
        start=1,
    ):

        html = f"""
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
                local["latitude"],
                local["longitude"],
            ],
            tooltip=(
                f"{numero}. "
                f"{local['nome']}"
            ),
            popup=local["nome"],
            icon=folium.DivIcon(
                html=html
            ),
        ).add_to(
            mapa
        )

    mapa.fit_bounds(
        [
            [
                min(
                    ponto[0]
                    for ponto in path
                ),
                min(
                    ponto[1]
                    for ponto in path
                ),
            ],
            [
                max(
                    ponto[0]
                    for ponto in path
                ),
                max(
                    ponto[1]
                    for ponto in path
                ),
            ],
        ]
    )

    return mapa


# ============================================================
# REFERÊNCIA GOOGLE MAPS
# ============================================================

def montar_referencia_google(
    local,
):

    nome = (
        local.get("name")
        or local.get("nome")
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
        local.get("country")
        or "Brazil"
    )

    componentes = []

    if nome:

        componentes.append(
            str(nome).strip()
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

    if locality:

        locality = str(
            locality
        ).strip()

        if locality not in componentes:

            componentes.append(
                locality
            )

    if region:

        region = str(
            region
        ).strip()

        if region not in componentes:

            componentes.append(
                region
            )

    if country:

        country = str(
            country
        ).strip()

        if country not in componentes:

            componentes.append(
                country
            )

    referencia = ", ".join(
        componentes
    )

    if not referencia:

        referencia = local.get(
            "nome",
            ""
        )

    return referencia


# ============================================================
# COORDENADA GOOGLE
# ============================================================

def coordenada_google_maps(
    local,
):

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


# ============================================================
# URL GOOGLE MAPS
# ============================================================

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
            for local in locais_trecho
        ]

    else:

        referencias = [
            montar_referencia_google(
                local
            )
            for local in locais_trecho
        ]

    origem = (
        referencias[0]
    )

    destino = (
        referencias[-1]
    )

    params = {
        "api": "1",
        "origin": origem,
        "destination": destino,
        "travelmode": "driving",
        "dir_action": "navigate",
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


# ============================================================
# DIVISÃO EM TRECHOS
# ============================================================

def dividir_rota_google_maps(
    locais_ordenados,
    max_pontos_por_trecho=5,
):

    if (
        len(locais_ordenados)
        <= max_pontos_por_trecho
    ):

        return [
            locais_ordenados
        ]

    trechos = []
    inicio = 0

    while inicio < (
        len(locais_ordenados) - 1
    ):

        fim = min(
            inicio
            + max_pontos_por_trecho
            - 1,
            len(locais_ordenados) - 1,
        )

        trecho = (
            locais_ordenados[
                inicio:
                fim + 1
            ]
        )

        trechos.append(
            trecho
        )

        inicio = fim

    return trechos


# ============================================================
# CONVERTE RESULTADO DO PELIAS
# ============================================================

def converter_local(
    origem,
    tipo,
):

    return {
        "nome":
            origem.get(
                "label",
                ""
            ),

        "latitude":
            origem[
                "latitude"
            ],

        "longitude":
            origem[
                "longitude"
            ],

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
# ROTA FECHADA
# ============================================================

voltar_inicio = st.checkbox(
    "🔄 Retornar ao ponto inicial",
    help=(
        "Quando marcado, a rota começa "
        "e termina no mesmo local."
    ),
)


# ============================================================
# INÍCIO / FIM
# ============================================================

col_inicio, col_fim = (
    st.columns(2)
)


with col_inicio:

    ponto_inicio = campo_local(
        chave="busca_ponto_inicio",
        label="📍 Ponto inicial",
        placeholder=(
            "Digite pelo menos 3 letras..."
        ),
    )


with col_fim:

    if voltar_inicio:

        if ponto_inicio:

            st.text_input(
                "🏁 Ponto final",
                value=ponto_inicio[
                    "label"
                ],
                disabled=True,
            )

        else:

            st.text_input(
                "🏁 Ponto final",
                value=(
                    "Selecione primeiro "
                    "o ponto inicial"
                ),
                disabled=True,
            )

        ponto_fim = (
            ponto_inicio
        )

    else:

        ponto_fim = campo_local(
            chave="busca_ponto_fim",
            label="🏁 Ponto final",
            placeholder=(
                "Digite pelo menos 3 letras..."
            ),
            focus=ponto_inicio,
        )


# ============================================================
# FOCO
# ============================================================

if ponto_inicio:

    st.caption(
        "📌 As demais buscas estão priorizando "
        f"locais próximos de "
        f"**{ponto_inicio['label']}**."
    )


if st.session_state.erro_autocomplete:

    st.warning(
        st.session_state.erro_autocomplete
    )


# ============================================================
# PARADAS
# ============================================================

st.subheader(
    "Paradas intermediárias"
)


modo_entrada = st.radio(
    "Como deseja adicionar as paradas?",
    [
        "Digitar manualmente",
        "Importar CSV/Excel",
    ],
    horizontal=True,
)


paradas = []


# ============================================================
# PARADAS MANUAIS
# ============================================================

if modo_entrada == "Digitar manualmente":

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
        ):

            st.session_state.num_paradas -= 1
            st.rerun()


    for i in range(
        st.session_state.num_paradas
    ):

        parada = campo_local(
            chave=f"busca_parada_{i}",
            label=f"Parada {i + 1}",
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
# IMPORTAÇÃO CSV / EXCEL
# ============================================================

else:

    st.info(
        "O arquivo deve possuir uma coluna chamada "
        "**endereco**. Os textos serão carregados nos "
        "campos de busca, mas você continuará escolhendo "
        "e confirmando cada local nas sugestões."
    )


    # ========================================================
    # MODELO CSV
    # ========================================================
    #
    # Usamos ponto e vírgula e uma segunda coluna para
    # tornar o arquivo mais amigável ao Excel brasileiro.
    # ========================================================

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
    )


    arquivo = st.file_uploader(
        "Selecione o arquivo",
        type=[
            "csv",
            "xlsx",
        ],
        help=(
            "Formatos aceitos: CSV e Excel XLSX."
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
                str(e)
            )

            st.stop()


        # ====================================================
        # LIMITE PRELIMINAR
        # ====================================================

        limite_paradas = 48


        if (
            len(
                enderecos_importados
            )
            > limite_paradas
        ):

            st.error(
                f"O arquivo possui "
                f"{len(enderecos_importados)} paradas. "
                f"O limite atual de importação é "
                f"{limite_paradas}."
            )

            st.stop()


        st.success(
            f"{len(enderecos_importados)} "
            "endereços importados."
        )


        st.warning(
            "⚠️ Confirme cada endereço selecionando "
            "uma das sugestões. Um texto carregado do "
            "arquivo ainda não conta como local confirmado."
        )


        for i, endereco in enumerate(
            enderecos_importados
        ):

            parada = campo_local(
                chave=(
                    f"import_{hash_arquivo}_{i}"
                ),
                label=(
                    f"Parada {i + 1}"
                ),
                placeholder=(
                    "Selecione o endereço correto..."
                ),
                focus=ponto_inicio,
                default_searchterm=endereco,
            )

            paradas.append(
                parada
            )


    else:

        st.caption(
            "Nenhum arquivo selecionado."
        )


# ============================================================
# CONFIGURAÇÃO DA OTIMIZAÇÃO
# ============================================================

st.divider()


st.subheader(
    "⚙️ Configuração da otimização"
)


col_objetivo, col_modo = (
    st.columns(2)
)


with col_objetivo:

    objetivo = st.radio(
        "Otimizar por",
        [
            "Menor distância",
            "Menor tempo",
        ],
        horizontal=True,
    )


with col_modo:

    modo = st.radio(
        "Modo de otimização",
        [
            "Rápido",
            "Preciso",
        ],
        horizontal=True,
    )


if objetivo == "Menor distância":

    st.info(
        "📏 **Menor distância:** "
        "o algoritmo minimizará os quilômetros percorridos."
    )

else:

    st.info(
        "⏱️ **Menor tempo:** "
        "o algoritmo minimizará o tempo estimado "
        "de deslocamento."
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
)


# ============================================================
# EXECUÇÃO
# ============================================================

if calcular:

    # ========================================================
    # VALIDAÇÕES
    # ========================================================

    if not ors_configurado:

        st.error(
            "OpenRouteService não configurado."
        )

        st.stop()


    if not ponto_inicio:

        st.error(
            "Selecione o ponto inicial "
            "nas sugestões da busca."
        )

        st.stop()


    if (
        not voltar_inicio
        and
        not ponto_fim
    ):

        st.error(
            "Selecione o ponto final "
            "nas sugestões da busca."
        )

        st.stop()


    # ========================================================
    # IMPORTAÇÃO SEM ARQUIVO
    # ========================================================

    if (
        modo_entrada
        == "Importar CSV/Excel"
        and
        len(paradas) == 0
    ):

        st.error(
            "Selecione um arquivo com as paradas."
        )

        st.stop()


    # ========================================================
    # CAMPOS IMPORTADOS NÃO CONFIRMADOS
    # ========================================================

    if (
        modo_entrada
        == "Importar CSV/Excel"
    ):

        nao_confirmadas = [
            indice + 1
            for indice, parada
            in enumerate(
                paradas
            )
            if parada is None
        ]


        if nao_confirmadas:

            numeros = ", ".join(
                str(numero)
                for numero
                in nao_confirmadas
            )


            st.error(
                "Confirme todas as paradas importadas "
                "selecionando uma opção em cada campo. "
                f"Pendentes: {numeros}."
            )

            st.stop()


    # ========================================================
    # MANUAL: IGNORA CAMPOS VAZIOS
    # ========================================================

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
            "Adicione pelo menos uma parada."
        )

        st.stop()


    # ========================================================
    # LOCAIS
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
    # DUPLICADOS
    # ========================================================

    coordenadas_unicas = set()


    for local in locais:

        chave = (
            round(
                local[
                    "latitude"
                ],
                6,
            ),

            round(
                local[
                    "longitude"
                ],
                6,
            ),
        )


        if chave in coordenadas_unicas:

            st.error(
                "O mesmo local foi selecionado "
                "mais de uma vez."
            )

            st.stop()


        coordenadas_unicas.add(
            chave
        )


    # ========================================================
    # LIMITES
    # ========================================================

    if voltar_inicio:

        max_locais = 49

    else:

        max_locais = 50


    if len(locais) > max_locais:

        st.error(
            f"Esta rota possui "
            f"{len(locais)} locais. "
            f"O limite atual é "
            f"{max_locais}."
        )

        st.stop()


    # ========================================================
    # COORDENADAS
    # ========================================================

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


    if voltar_inicio:

        end_idx = 0

    else:

        end_idx = (
            len(locais) - 1
        )


    # ========================================================
    # PROCESSAMENTO
    # ========================================================

    try:

        # ====================================================
        # MATRIX
        # ====================================================

        with st.spinner(
            "Calculando distâncias e tempos "
            "entre os locais..."
        ):

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

            st.error(
                "A Matrix API retornou "
                "um formato incompatível."
            )

            st.stop()


        if (
            "distancias"
            not in matrix_data
            or
            "duracoes"
            not in matrix_data
        ):

            st.error(
                "A Matrix API não retornou "
                "distâncias e tempos."
            )

            st.stop()


        # ====================================================
        # OBJETIVO
        # ====================================================

        if objetivo == "Menor distância":

            matrix_otimizacao = (
                matrix_data[
                    "distancias"
                ]
            )

        else:

            matrix_otimizacao = (
                matrix_data[
                    "duracoes"
                ]
            )


        # ====================================================
        # SOLVER
        # ====================================================

        with st.spinner(
            "Procurando a melhor rota..."
        ):

            resultado = (
                otimizar_rota(
                    matrix_m=(
                        matrix_otimizacao
                    ),
                    start_idx=start_idx,
                    end_idx=end_idx,
                    modo=modo,
                )
            )


        # ====================================================
        # ORDEM
        # ====================================================

        route = (
            resultado[
                "route"
            ]
        )


        locais_ordenados = [
            locais[indice]
            for indice
            in route
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


        # ====================================================
        # DIRECTIONS
        # ====================================================

        with st.spinner(
            "Montando a rota pelas ruas..."
        ):

            rota_real = (
                obter_directions_cache(
                    coordenadas_ordenadas,
                    ORS_KEY,
                )
            )


    except (
        ORSError,
        RouteOptimizationError,
    ) as e:

        st.error(
            str(e)
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


    # ========================================================
    # DISTÂNCIA / TEMPO
    # ========================================================

    distancia_km = (
        rota_real[
            "distancia_m"
        ]
        / 1000
    )


    minutos_total = round(
        rota_real[
            "duracao_s"
        ]
        / 60
    )


    horas = (
        minutos_total
        // 60
    )


    minutos = (
        minutos_total
        % 60
    )


    if horas:

        tempo_texto = (
            f"{horas}h "
            f"{minutos:02d}min"
        )

    else:

        tempo_texto = (
            f"{minutos} min"
        )


    # ========================================================
    # RESULTADOS
    # ========================================================

    st.success(
        "Rota calculada com sucesso."
    )


    col1, col2, col3, col4 = (
        st.columns(4)
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
            len(locais),
        )


    with col4:

        st.metric(
            "Otimizado por",
            (
                "Distância"
                if objetivo
                == "Menor distância"
                else "Tempo"
            ),
        )


    st.caption(
        f"Modo utilizado: **{modo}**"
    )


    # ========================================================
    # STATUS
    # ========================================================

    if (
        resultado[
            "status"
        ]
        == "ÓTIMA COMPROVADA"
    ):

        st.success(
            "🏆 Rota ótima matematicamente comprovada."
        )


    elif (
        resultado[
            "gap_percent"
        ]
        is not None
    ):

        st.info(
            "🎯 Melhor rota encontrada. "
            f"Gap máximo restante: "
            f"{resultado['gap_percent']:.3f}%."
        )


    else:

        st.info(
            "⚡ Rota otimizada em modo rápido."
        )


    # ========================================================
    # ORDEM DA ROTA
    # ========================================================

    st.subheader(
        "📋 Ordem da rota"
    )


    if voltar_inicio:

        lista_exibir = (
            locais_ordenados[:-1]
        )

    else:

        lista_exibir = (
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


    if voltar_inicio:

        st.write(
            f"**{len(lista_exibir) + 1}.** "
            f"{locais_ordenados[-1]['nome']} "
            f"_(retorno)_"
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

        url_google = (
            gerar_url_google_maps(
                trechos_google[0]
            )
        )


        st.link_button(
            "🧭 Abrir rota no Google Maps",
            url_google,
            use_container_width=True,
        )


        with st.expander(
            "Opções avançadas de navegação"
        ):

            url_coordenadas = (
                gerar_url_google_maps(
                    trechos_google[0],
                    usar_coordenadas=True,
                )
            )


            st.link_button(
                "📍 Abrir usando coordenadas",
                url_coordenadas,
                use_container_width=True,
            )


    else:

        st.info(
            f"A rota foi dividida em "
            f"{len(trechos_google)} trechos "
            "para melhorar a compatibilidade "
            "com o Google Maps no celular."
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
                    numero_trecho == 1
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


                url_google = (
                    gerar_url_google_maps(
                        trecho
                    )
                )


                st.link_button(
                    (
                        f"🧭 Abrir trecho "
                        f"{numero_trecho} "
                        f"no Google Maps"
                    ),
                    url_google,
                    use_container_width=True,
                )


                url_coord = (
                    gerar_url_google_maps(
                        trecho,
                        usar_coordenadas=True,
                    )
                )


                st.link_button(
                    "📍 Alternativa por coordenadas",
                    url_coord,
                    use_container_width=True,
                )


        st.caption(
            "O último local de cada trecho também é "
            "o primeiro local do trecho seguinte."
        )


    # ========================================================
    # MAPA
    # ========================================================

    st.subheader(
        "🗺️ Mapa da rota"
    )


    mapa = criar_mapa(
        geometry=(
            rota_real[
                "geometry"
            ]
        ),
        locais_ordenados=(
            locais_ordenados
        ),
        rota_fechada=(
            voltar_inicio
        ),
    )


    st_folium(
        mapa,
        height=600,
        use_container_width=True,
        returned_objects=[],
    )
