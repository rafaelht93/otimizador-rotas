import folium
import streamlit as st

from urllib.parse import urlencode

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


# ============================================================
# TÍTULO
# ============================================================

st.title(
    "🗺️ Otimizador de Rotas"
)

st.write(
    "Informe o ponto inicial, o ponto final e as paradas. "
    "Selecione os locais encontrados e o sistema calculará "
    "a melhor ordem para percorrê-los."
)


# ============================================================
# OPENROUTESERVICE
# ============================================================

try:

    ORS_KEY = st.secrets[
        "ORS_KEY"
    ]

    ors_configurado = True

except Exception:

    ORS_KEY = None

    ors_configurado = False


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "Configuração"
    )

    if ors_configurado:

        st.success(
            "OpenRouteService configurado"
        )

    else:

        st.error(
            "OpenRouteService não configurado"
        )

    st.divider()

    st.caption(
        "Depois que o ponto inicial for escolhido, "
        "as outras pesquisas priorizam locais próximos dele."
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
def obter_matrix_cache(
    coordenadas,
    _api_key,
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
        searchterm.strip()
    )

    if len(texto) < 3:

        return []

    if not ors_configurado:

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
            str(e)
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
# CAMPO DE LOCAL
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
# CRIAÇÃO DO MAPA
# ============================================================

def criar_mapa(
    geometry,
    locais_ordenados,
    rota_fechada,
):

    # ORS:
    # [longitude, latitude]
    #
    # Folium:
    # [latitude, longitude]

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
        ) / len(path),

        sum(
            p[1]
            for p in path
        ) / len(path),
    ]


    mapa = folium.Map(
        location=centro,
        zoom_start=13,
        control_scale=True,
    )


    # ========================================================
    # LINHA DA ROTA
    # ========================================================

    folium.PolyLine(
        path,
        weight=5,
        opacity=0.8,
    ).add_to(
        mapa
    )


    # ========================================================
    # NÃO DUPLICA O INÍCIO EM ROTA FECHADA
    # ========================================================

    if rota_fechada:

        locais_marcadores = (
            locais_ordenados[:-1]
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
                local[
                    "latitude"
                ],
                local[
                    "longitude"
                ],
            ],

            tooltip=(
                f"{numero}. "
                f"{local['nome']}"
            ),

            popup=local[
                "nome"
            ],

            icon=folium.DivIcon(
                html=html
            ),

        ).add_to(
            mapa
        )


    # ========================================================
    # ZOOM
    # ========================================================

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


# ============================================================
# GOOGLE MAPS
# ============================================================

def coordenada_google_maps(
    local,
):
    """
    Converte um local para:

    latitude,longitude
    """

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


# ============================================================
# GERA URL DO GOOGLE MAPS
# ============================================================

def gerar_url_google_maps(
    locais_trecho,
):
    """
    Gera uma URL universal do Google Maps.

    A ordem recebida é preservada:

    primeiro ponto = origem

    último ponto = destino

    demais = waypoints
    """

    if len(
        locais_trecho
    ) < 2:

        return None


    origem = (
        coordenada_google_maps(
            locais_trecho[0]
        )
    )


    destino = (
        coordenada_google_maps(
            locais_trecho[-1]
        )
    )


    params = {
        "api": "1",
        "origin": origem,
        "destination": destino,
        "travelmode": "driving",
        "dir_action": "navigate",
    }


    # ========================================================
    # WAYPOINTS
    # ========================================================

    if len(
        locais_trecho
    ) > 2:

        waypoints = "|".join(

            coordenada_google_maps(
                local
            )

            for local
            in locais_trecho[
                1:-1
            ]
        )


        params[
            "waypoints"
        ] = waypoints


    return (
        "https://www.google.com/maps/dir/?"
        + urlencode(
            params
        )
    )


# ============================================================
# DIVIDE ROTA EM TRECHOS
# ============================================================

def dividir_rota_google_maps(
    locais_ordenados,
    max_pontos_por_trecho=5,
):
    """
    Divide a rota mantendo continuidade.

    Exemplo:

    Trecho 1:
    1 2 3 4 5

    Trecho 2:
    5 6 7 8 9

    O último ponto do trecho anterior
    vira o primeiro do próximo.
    """

    if len(
        locais_ordenados
    ) <= max_pontos_por_trecho:

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


        trecho = (
            locais_ordenados[
                inicio:
                fim + 1
            ]
        )


        trechos.append(
            trecho
        )


        # O último ponto do trecho
        # vira o início do próximo.
        inicio = fim


    return trechos


# ============================================================
# TIPO DA ROTA
# ============================================================

voltar_inicio = st.checkbox(

    "🔄 Retornar ao ponto inicial",

    help=(
        "Quando marcado, a rota começa "
        "e termina no mesmo local."
    ),
)


# ============================================================
# INÍCIO E FIM
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
# FOCO GEOGRÁFICO
# ============================================================

if ponto_inicio:

    st.caption(
        "📌 As demais buscas estão priorizando "
        f"locais próximos de "
        f"**{ponto_inicio['label']}**."
    )


# ============================================================
# ERRO AUTOCOMPLETE
# ============================================================

if (
    st.session_state.erro_autocomplete
):

    st.warning(
        st.session_state.erro_autocomplete
    )


# ============================================================
# PARADAS INTERMEDIÁRIAS
# ============================================================

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


# ============================================================
# CAMPOS DAS PARADAS
# ============================================================

paradas = []


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
# CONFIGURAÇÃO DA OTIMIZAÇÃO
# ============================================================

st.divider()


st.subheader(
    "⚙️ Configuração da otimização"
)


col_objetivo, col_modo = (
    st.columns(2)
)


# ============================================================
# OBJETIVO
# ============================================================

with col_objetivo:

    objetivo = st.radio(

        "Otimizar por",

        [
            "Menor distância",
            "Menor tempo",
        ],

        index=0,

        horizontal=True,

        help=(
            "Escolha se o algoritmo deve minimizar "
            "a distância ou o tempo estimado."
        ),
    )


# ============================================================
# MODO
# ============================================================

with col_modo:

    modo = st.radio(

        "Modo de otimização",

        [
            "Rápido",
            "Preciso",
        ],

        index=0,

        horizontal=True,

        help=(
            "O modo Preciso executa mais estratégias "
            "e pode levar mais tempo."
        ),
    )


# ============================================================
# INFORMAÇÕES
# ============================================================

if objetivo == "Menor distância":

    st.info(
        "📏 **Menor distância:** o algoritmo tentará "
        "reduzir o total de quilômetros percorridos."
    )

else:

    st.info(
        "⏱️ **Menor tempo:** o algoritmo tentará "
        "reduzir o tempo estimado de deslocamento, "
        "mesmo que percorra alguns quilômetros a mais."
    )


if modo == "Preciso":

    st.caption(
        "🎯 O modo Preciso executa várias estratégias "
        "e tenta melhorar ou comprovar a solução."
    )

else:

    st.caption(
        "⚡ O modo Rápido prioriza velocidade de cálculo."
    )


# ============================================================
# BOTÃO CALCULAR
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
        {
            "nome":
                ponto_inicio[
                    "label"
                ],

            "latitude":
                ponto_inicio[
                    "latitude"
                ],

            "longitude":
                ponto_inicio[
                    "longitude"
                ],

            "tipo":
                "Início",
        }
    ]


    # ========================================================
    # PARADAS
    # ========================================================

    for numero, parada in enumerate(
        paradas_validas,
        start=1,
    ):

        locais.append(
            {
                "nome":
                    parada[
                        "label"
                    ],

                "latitude":
                    parada[
                        "latitude"
                    ],

                "longitude":
                    parada[
                        "longitude"
                    ],

                "tipo":
                    f"Parada {numero}",
            }
        )


    # ========================================================
    # PONTO FINAL
    # ========================================================

    if not voltar_inicio:

        locais.append(
            {
                "nome":
                    ponto_fim[
                        "label"
                    ],

                "latitude":
                    ponto_fim[
                        "latitude"
                    ],

                "longitude":
                    ponto_fim[
                        "longitude"
                    ],

                "tipo":
                    "Fim",
            }
        )


    # ========================================================
    # DUPLICADOS
    # ========================================================

    coordenadas_unicas = set()

    duplicados = []


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

            duplicados.append(
                local[
                    "nome"
                ]
            )


        coordenadas_unicas.add(
            chave
        )


    if duplicados:

        st.error(
            "O mesmo local foi selecionado "
            "mais de uma vez."
        )

        st.stop()


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
    # CÁLCULO
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
                obter_matrix_cache(
                    coordenadas,
                    ORS_KEY,
                )
            )


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

            locais[i]

            for i
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
    # DISTÂNCIA
    # ========================================================

    distancia_km = (
        rota_real[
            "distancia_m"
        ]
        / 1000
    )


    # ========================================================
    # TEMPO
    # ========================================================

    duracao_min = (
        rota_real[
            "duracao_s"
        ]
        / 60
    )


    minutos_total = round(
        duracao_min
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
    # RESULTADO
    # ========================================================

    st.success(
        "Rota calculada com sucesso."
    )


    # ========================================================
    # MÉTRICAS
    # ========================================================

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

        if objetivo == "Menor distância":

            objetivo_texto = (
                "Distância"
            )

        else:

            objetivo_texto = (
                "Tempo"
            )


        st.metric(
            "Otimizado por",
            objetivo_texto,
        )


    st.caption(
        f"Modo utilizado: **{modo}**"
    )


    # ========================================================
    # STATUS SOLVER
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
    # ORDEM
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
        "A ordem dos locais foi calculada por este aplicativo. "
        "O Google Maps será usado apenas para navegar "
        "entre os pontos nessa sequência."
    )


    # ========================================================
    # DIVISÃO CONSERVADORA PARA CELULAR
    # ========================================================

    trechos_google = (
        dividir_rota_google_maps(
            locais_ordenados,
            max_pontos_por_trecho=5,
        )
    )


    # ========================================================
    # UMA ÚNICA ROTA
    # ========================================================

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


    # ========================================================
    # VÁRIOS TRECHOS
    # ========================================================

    else:

        st.info(
            f"A rota foi dividida em "
            f"{len(trechos_google)} trechos "
            "para aumentar a compatibilidade "
            "com o Google Maps no celular."
        )


        for numero_trecho, trecho in enumerate(
            trechos_google,
            start=1,
        ):

            origem_trecho = (
                trecho[0][
                    "nome"
                ]
            )

            destino_trecho = (
                trecho[-1][
                    "nome"
                ]
            )


            with st.expander(
                f"Trecho {numero_trecho}: "
                f"{origem_trecho} → "
                f"{destino_trecho}",
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
                    f"🧭 Abrir trecho "
                    f"{numero_trecho} "
                    f"no Google Maps",
                    url_google,
                    use_container_width=True,
                )


        st.caption(
            "O último local de cada trecho é repetido "
            "como ponto inicial do trecho seguinte. "
            "Isso mantém a continuidade da rota."
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
