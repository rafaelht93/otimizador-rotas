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
    ORS_KEY = st.secrets["ORS_KEY"]
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
# CACHE MATRIX V2
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

    texto = searchterm.strip()

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

        st.session_state.erro_autocomplete = None


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
# MAPA
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
# REFERÊNCIA PARA GOOGLE MAPS
# ============================================================

def montar_referencia_google(
    local,
):
    """
    Monta a melhor referência textual possível
    para o Google Maps.

    Em vez de enviar somente latitude/longitude,
    enviamos nome + endereço + cidade + estado.

    Isso permite que o próprio Google identifique
    o estabelecimento/endereço em sua base.
    """

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


    # ========================================================
    # NOME DO LOCAL
    # ========================================================

    if nome:

        componentes.append(
            str(nome).strip()
        )


    # ========================================================
    # ENDEREÇO
    # ========================================================

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


    # ========================================================
    # CIDADE
    # ========================================================

    if locality:

        locality = str(
            locality
        ).strip()

        if locality not in componentes:

            componentes.append(
                locality
            )


    # ========================================================
    # ESTADO
    # ========================================================

    if region:

        region = str(
            region
        ).strip()

        if region not in componentes:

            componentes.append(
                region
            )


    # ========================================================
    # PAÍS
    # ========================================================

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


    # ========================================================
    # FALLBACK
    # ========================================================

    if not referencia:

        referencia = local.get(
            "nome",
            ""
        )


    return referencia


# ============================================================
# REFERÊNCIA POR COORDENADA
# ============================================================

def coordenada_google_maps(
    local,
):

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


# ============================================================
# URL PRINCIPAL DO GOOGLE MAPS
# ============================================================

def gerar_url_google_maps(
    locais_trecho,
    usar_coordenadas=False,
):
    """
    Gera URL do Google Maps.

    Por padrão utiliza referências textuais completas.

    Se usar_coordenadas=True, utiliza lat/lon
    como alternativa de fallback.
    """

    if len(locais_trecho) < 2:

        return None


    # ========================================================
    # ESCOLHE REPRESENTAÇÃO
    # ========================================================

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


    origem = referencias[0]

    destino = referencias[-1]


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

    if len(referencias) > 2:

        params[
            "waypoints"
        ] = "|".join(
            referencias[1:-1]
        )


    return (
        "https://www.google.com/maps/dir/?"
        + urlencode(
            params
        )
    )


# ============================================================
# DIVIDE A ROTA PARA GOOGLE MAPS
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


        trecho = locais_ordenados[
            inicio:
            fim + 1
        ]


        trechos.append(
            trecho
        )


        inicio = fim


    return trechos


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
                value=(
                    ponto_inicio[
                        "label"
                    ]
                ),
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


# ============================================================
# ERRO DE AUTOCOMPLETE
# ============================================================

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
    # FUNÇÃO PARA PRESERVAR DADOS DO PELIAS
    # ========================================================

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

            # Dados preservados para melhorar
            # a referência enviada ao Google Maps.

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


    # ========================================================
    # LOCAIS
    # ========================================================

    locais = [
        converter_local(
            ponto_inicio,
            "Início",
        )
    ]


    # ========================================================
    # PARADAS
    # ========================================================

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


    # ========================================================
    # DESTINO
    # ========================================================

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
    # LIMITE MATRIX
    # ========================================================

    if (
        len(locais)
        * len(locais)
        > 3500
    ):

        st.error(
            "A quantidade de locais ultrapassa "
            "o limite atual da Matrix API."
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


        # ====================================================
        # VALIDA MATRIX
        # ====================================================

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
            for indice in route
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
    # RESULTADOS
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
        "O Google Maps recebe o nome e o endereço "
        "dos locais selecionados, em vez de apenas "
        "suas coordenadas."
    )


    trechos_google = (
        dividir_rota_google_maps(
            locais_ordenados,
            max_pontos_por_trecho=5,
        )
    )


    # ========================================================
    # ROTA ÚNICA
    # ========================================================

    if len(trechos_google) == 1:

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


        # Fallback opcional

        with st.expander(
            "Opções avançadas de navegação"
        ):

            st.write(
                "Se o Google não localizar algum ponto "
                "pelo nome/endereço, tente a versão "
                "por coordenadas."
            )


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


    # ========================================================
    # VÁRIOS TRECHOS
    # ========================================================

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


                # ============================================
                # REFERÊNCIAS ENVIADAS AO GOOGLE
                # ============================================

                st.caption(
                    "Referências enviadas ao Google Maps:"
                )


                for local in trecho:

                    st.caption(
                        "• "
                        + montar_referencia_google(
                            local
                        )
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


                # Fallback
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
