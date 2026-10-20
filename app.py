import streamlit as st

from ors import (
    geocodificar,
    ORSError
)


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide"
)


# ============================================================
# TÍTULO
# ============================================================

st.title(
    "🗺️ Otimizador de Rotas"
)

st.write(
    "Informe o ponto inicial, o ponto final e as paradas. "
    "O sistema encontrará a melhor ordem para percorrer os locais."
)


# ============================================================
# OPENROUTESERVICE
# ============================================================

try:

    ORS_KEY = (
        st.secrets[
            "ORS_KEY"
        ]
    )

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


# ============================================================
# SESSION STATE
# ============================================================

if "num_paradas" not in st.session_state:

    st.session_state.num_paradas = 1


# ============================================================
# INÍCIO E FIM
# ============================================================

col_inicio, col_fim = (
    st.columns(2)
)


with col_inicio:

    ponto_inicio = st.text_input(
        "📍 Ponto inicial",
        placeholder=(
            "Ex.: Prefeitura de Brusque, SC"
        )
    )


with col_fim:

    ponto_fim = st.text_input(
        "🏁 Ponto final",
        placeholder=(
            "Ex.: Limeira, Brusque, SC"
        )
    )


# ============================================================
# PARADAS
# ============================================================

st.subheader(
    "Paradas intermediárias"
)


col_adicionar, col_remover, _ = (
    st.columns(
        [1, 1, 4]
    )
)


with col_adicionar:

    if st.button(
        "➕ Adicionar parada"
    ):

        st.session_state.num_paradas += 1

        st.rerun()


with col_remover:

    if st.button(
        "➖ Remover parada",
        disabled=(
            st.session_state.num_paradas
            <= 1
        )
    ):

        st.session_state.num_paradas -= 1

        st.rerun()


paradas = []


for i in range(
    st.session_state.num_paradas
):

    parada = st.text_input(

        f"Parada {i + 1}",

        key=f"parada_{i}",

        placeholder=(
            "Ex.: Havan, Brusque, SC"
        )
    )

    paradas.append(
        parada
    )


# ============================================================
# OPÇÕES
# ============================================================

st.divider()


col_modo, col_tipo = (
    st.columns(2)
)


with col_modo:

    modo = st.radio(

        "Modo de otimização",

        [
            "Rápido",
            "Preciso"
        ],

        horizontal=True
    )


with col_tipo:

    voltar_inicio = st.checkbox(
        "Retornar ao ponto inicial"
    )


if voltar_inicio:

    st.info(
        "A rota terminará novamente "
        "no ponto inicial."
    )


# ============================================================
# BOTÃO
# ============================================================

st.divider()


if st.button(
    "🚗 Calcular melhor rota",
    type="primary",
    use_container_width=True
):

    # ========================================================
    # VERIFICA API
    # ========================================================

    if not ors_configurado:

        st.error(
            "OpenRouteService não configurado."
        )

        st.stop()


    # ========================================================
    # PARADAS VÁLIDAS
    # ========================================================

    paradas_validas = [

        parada.strip()

        for parada in paradas

        if parada.strip()
    ]


    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    if not ponto_inicio.strip():

        st.error(
            "Informe o ponto inicial."
        )

        st.stop()


    if (
        not voltar_inicio
        and
        not ponto_fim.strip()
    ):

        st.error(
            "Informe o ponto final."
        )

        st.stop()


    # ========================================================
    # MONTA LISTA DE ENDEREÇOS
    # ========================================================

    locais_digitados = []


    locais_digitados.append(
        (
            "Início",
            ponto_inicio.strip()
        )
    )


    for numero, parada in enumerate(
        paradas_validas,
        start=1
    ):

        locais_digitados.append(
            (
                f"Parada {numero}",
                parada
            )
        )


    if voltar_inicio:

        destino_texto = (
            ponto_inicio.strip()
        )

    else:

        destino_texto = (
            ponto_fim.strip()
        )


    # Só adicionamos o fim ao geocoding
    # se ele for diferente do início.

    if not voltar_inicio:

        locais_digitados.append(
            (
                "Fim",
                destino_texto
            )
        )


    # ========================================================
    # GEOCODIFICAÇÃO
    # ========================================================

    resultados = []


    barra = st.progress(
        0,
        text="Localizando endereços..."
    )


    total = len(
        locais_digitados
    )


    try:

        for indice, (
            tipo,
            endereco
        ) in enumerate(
            locais_digitados,
            start=1
        ):

            resultado = geocodificar(
                endereco,
                ORS_KEY
            )


            if resultado is None:

                st.error(
                    f"Não foi possível localizar: "
                    f"{endereco}"
                )

                st.stop()


            resultados.append(
                {
                    "tipo":
                        tipo,

                    "digitado":
                        endereco,

                    "encontrado":
                        resultado[
                            "nome"
                        ],

                    "latitude":
                        resultado[
                            "latitude"
                        ],

                    "longitude":
                        resultado[
                            "longitude"
                        ]
                }
            )


            barra.progress(

                indice / total,

                text=(
                    f"Localizando "
                    f"{indice}/{total}..."
                )
            )


    except ORSError as e:

        barra.empty()

        st.error(
            str(e)
        )

        st.stop()


    barra.empty()


    # ========================================================
    # ROTA FECHADA
    # ========================================================

    if voltar_inicio:

        inicio = resultados[0]

        resultados.append(
            {
                "tipo":
                    "Fim",

                "digitado":
                    ponto_inicio,

                "encontrado":
                    inicio[
                        "encontrado"
                    ],

                "latitude":
                    inicio[
                        "latitude"
                    ],

                "longitude":
                    inicio[
                        "longitude"
                    ]
            }
        )


    # ========================================================
    # SUCESSO
    # ========================================================

    st.success(
        "Todos os locais foram encontrados."
    )


    # ========================================================
    # EXIBE ENDEREÇOS IDENTIFICADOS
    # ========================================================

    st.subheader(
        "📍 Locais identificados"
    )


    for local in resultados:

        with st.container(
            border=True
        ):

            st.write(
                f"**{local['tipo']}**"
            )

            st.write(
                local[
                    "encontrado"
                ]
            )

            st.caption(
                f"Coordenadas: "
                f"{local['latitude']:.6f}, "
                f"{local['longitude']:.6f}"
            )


    # ========================================================
    # COORDENADAS PARA A PRÓXIMA ETAPA
    # ========================================================

    coordenadas = [

        (
            local[
                "latitude"
            ],

            local[
                "longitude"
            ]
        )

        for local in resultados
    ]


    # ========================================================
    # RESUMO
    # ========================================================

    st.subheader(
        "Configuração da rota"
    )


    col1, col2, col3 = (
        st.columns(3)
    )


    with col1:

        st.metric(
            "Locais",
            len(
                resultados
            )
        )


    with col2:

        st.metric(
            "Modo",
            modo
        )


    with col3:

        st.metric(
            "Tipo",
            (
                "Fechada"
                if voltar_inicio
                else "Aberta"
            )
        )


    # ========================================================
    # PRÓXIMA ETAPA
    # ========================================================

    st.info(
        "Geocodificação concluída. "
        "Na próxima etapa estas coordenadas "
        "serão enviadas ao algoritmo de otimização."
    )
