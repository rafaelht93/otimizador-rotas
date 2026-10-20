import hashlib
import html
import io
import unicodedata
from urllib.parse import urlencode

import folium
import pandas as pd
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
# PÁGINA
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ Otimizador de Rotas")

st.write(
    "Informe os locais manualmente ou importe uma lista por CSV/Excel. "
    "O sistema calculará a melhor sequência da rota."
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


if not ors_configurado:

    st.error(
        "OpenRouteService não configurado. "
        "Cadastre ORS_KEY nos Secrets do Streamlit."
    )


# ============================================================
# SESSION STATE
# ============================================================

if "num_paradas" not in st.session_state:
    st.session_state.num_paradas = 1


if "erro_autocomplete" not in st.session_state:
    st.session_state.erro_autocomplete = None


# ============================================================
# CACHES
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


# ============================================================
# AUTOCOMPLETE MANUAL
# ============================================================

def buscar_sugestoes(
    searchterm,
    focus_lat=None,
    focus_lon=None,
):

    texto = (
        searchterm or ""
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
# CAMPO DE BUSCA
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
# CSV / EXCEL
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

    texto = (
        decodificar_csv(
            bytes_arquivo
        )
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


    cabecalho = (
        normalizar_texto(
            linhas[
                0
            ]
            .strip()
            .strip(
                '"'
            )
            .strip(
                "'"
            )
        )
    )


    # ========================================================
    # CSV DE UMA COLUNA
    # ========================================================

    if cabecalho == "endereco":

        enderecos = [
            limpar_linha_csv_simples(
                linha
            )

            for linha
            in linhas[
                1:
            ]
        ]


        enderecos = [
            endereco

            for endereco
            in enderecos

            if endereco
        ]


        return pd.DataFrame(
            {
                "endereco":
                    enderecos
            }
        )


    # ========================================================
    # CSV COM VÁRIAS COLUNAS
    # ========================================================

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


        valor = str(
            valor
        ).strip()


        if valor:

            enderecos.append(
                valor
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
# LOCAL
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

    # ORS:
    # longitude, latitude
    #
    # Folium:
    # latitude, longitude

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
        / len(
            path
        ),

        sum(
            ponto[
                1
            ]

            for ponto
            in path
        )
        / len(
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


    if rota_fechada:

        locais_marcadores = (
            locais_ordenados[
                :-1
            ]
        )

    else:

        locais_marcadores = (
            locais_ordenados
        )


    # ========================================================
    # MARCADORES
    # ========================================================

    for numero, local in enumerate(
        locais_marcadores,
        start=1,
    ):

        nome = (
            html.escape(
                str(
                    local[
                        "nome"
                    ]
                )
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


    # ========================================================
    # PAINEL DO MAPA
    # ========================================================

    objetivo_texto = (
        "Distância"

        if objetivo
        == "Menor distância"

        else "Tempo"
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
# EXPORTA MAPA HTML
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
            ""
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
# EXPORTAÇÃO CSV / EXCEL
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
            encoding="utf-8-sig",
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

                        else "Tempo"
                    ),

                "Modo":
                    modo,

                "Tipo de rota":
                    (
                        "Fechada"

                        if voltar_inicio

                        else "Aberta"
                    ),

                "Quantidade de pontos na sequência":
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
# ENTRADA DOS LOCAIS
# ============================================================

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
)


ponto_inicio = None
ponto_fim = None
paradas = []
voltar_inicio = False
importacao_pronta = False


# ============================================================
# MODO MANUAL
# ============================================================

if modo_entrada == "Digitar manualmente":

    voltar_inicio = st.checkbox(
        "🔄 Retornar ao ponto inicial",
        key="manual_voltar_inicio",
    )


    col_inicio, col_fim = (
        st.columns(
            2
        )
    )


    with col_inicio:

        ponto_inicio = campo_local(

            "busca_ponto_inicio",

            "📍 Ponto inicial",

            "Digite pelo menos 3 letras...",
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
            )


            ponto_fim = (
                ponto_inicio
            )


        else:

            ponto_fim = campo_local(

                "busca_ponto_fim",

                "🏁 Ponto final",

                "Digite pelo menos 3 letras...",

                focus=ponto_inicio,
            )


    if ponto_inicio:

        st.caption(
            "📌 As demais buscas estão priorizando "
            f"locais próximos de "
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

        paradas.append(

            campo_local(

                f"busca_parada_{i}",

                f"Parada {i + 1}",

                "Digite um endereço "
                "ou estabelecimento...",

                focus=ponto_inicio,
            )
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
        file_name="modelo_paradas.csv",
        mime="text/csv",
    )


    arquivo = st.file_uploader(
        "Selecione o arquivo",
        type=[
            "csv",
            "xlsx",
        ],
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


        # ====================================================
        # BUSCA AUTOMÁTICA
        # ====================================================

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

                + ", ".join(
                    map(
                        str,
                        nao_encontrados,
                    )
                )

                + "."
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

                f"{indice}. "
                f"{endereco_original}",

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


        # ====================================================
        # INÍCIO / FIM
        # ====================================================

        if all(
            local is not None

            for local
            in locais_confirmados
        ):

            importacao_pronta = True


            st.success(
                "Todos os endereços "
                "foram identificados."
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
                    f"{hash_arquivo}"
                ),

                format_func=(
                    lambda indice:

                    f"{indice + 1}. "
                    f"{locais_confirmados[indice]['label']}"
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
                        f"{hash_arquivo}"
                    ),

                    format_func=(
                        lambda indice:

                        f"{indice + 1}. "
                        f"{locais_confirmados[indice]['label']}"
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
                indice_fim
                is not None
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
                f"Paradas intermediárias: "
                f"**{len(paradas)}**"
            )


    else:

        st.caption(
            "Selecione um arquivo "
            "para continuar."
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
        "📏 O algoritmo minimizará "
        "a distância total percorrida."
    )


else:

    st.info(
        "⏱️ O algoritmo minimizará "
        "o tempo estimado de deslocamento."
    )


st.caption(

    (
        "🎯 O modo Preciso executa várias estratégias "
        "e tenta melhorar ou comprovar a solução."
    )

    if modo == "Preciso"

    else

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
# RESULTADO
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

    if len(
        locais
    ) > 50:

        st.error(
            "O limite atual é de 50 locais."
        )

        st.stop()


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

        st.error(
            "A quantidade de locais ultrapassa "
            "o limite da Matrix API."
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


    end_idx = (

        0

        if voltar_inicio

        else

        len(
            locais
        ) - 1
    )


    # ========================================================
    # PROCESSAMENTO
    # ========================================================

    try:

        with st.spinner(
            "Calculando distâncias e tempos..."
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


        with st.spinner(
            "Procurando a melhor rota..."
        ):

            resultado = otimizar_rota(
                matrix_m=matrix_otimizacao,
                start_idx=start_idx,
                end_idx=end_idx,
                modo=modo,
            )


        locais_ordenados = [

            locais[
                indice
            ]

            for indice
            in resultado[
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


    # ========================================================
    # MÉTRICAS
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


    st.success(
        "Rota calculada com sucesso."
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
                locais
            ),
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
        f"Modo utilizado: "
        f"**{modo}**"
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
            "🏆 Rota ótima "
            "matematicamente comprovada."
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
            "⚡ Rota otimizada "
            "em modo rápido."
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


    if voltar_inicio:

        st.write(
            f"**{len(lista_exibir) + 1}.** "
            f"{locais_ordenados[-1]['nome']} "
            f"_(retorno)_"
        )


    # ========================================================
    # CRIA MAPA UMA ÚNICA VEZ
    # ========================================================

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

        distancia_km=(
            distancia_km
        ),

        tempo_texto=(
            tempo_texto
        ),

        objetivo=(
            objetivo
        ),
    )


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

            data=(
                csv_exportacao
            ),

            file_name=(
                "rota_otimizada.csv"
            ),

            mime=(
                "text/csv"
            ),

            use_container_width=True,
        )


    with col_excel:

        st.download_button(

            "⬇️ Baixar relatório em Excel",

            data=(
                excel_exportacao
            ),

            file_name=(
                "rota_otimizada.xlsx"
            ),

            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),

            use_container_width=True,
        )


    with col_mapa:

        st.download_button(

            "🗺️ Baixar mapa interativo",

            data=(
                html_mapa
            ),

            file_name=(
                "mapa_rota_otimizada.html"
            ),

            mime=(
                "text/html"
            ),

            use_container_width=True,
        )


    st.caption(
        "O mapa é exportado em HTML interativo e mantém "
        "a rota, os marcadores numerados, zoom e o resumo "
        "da viagem. Para carregar o mapa-base, o navegador "
        "precisa de internet."
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


    # ========================================================
    # UM TRECHO
    # ========================================================

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


    # ========================================================
    # VÁRIOS TRECHOS
    # ========================================================

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
