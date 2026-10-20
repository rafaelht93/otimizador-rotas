import streamlit as st


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

st.title("🗺️ Otimizador de Rotas")

st.write(
    "Informe o ponto inicial, o ponto final e as paradas. "
    "O sistema encontrará a melhor ordem para percorrer os locais."
)


# ============================================================
# VERIFICA SE A CHAVE ORS ESTÁ CONFIGURADA
# ============================================================

try:
    ORS_KEY = st.secrets["ORS_KEY"]
    ors_configurado = True

except Exception:
    ORS_KEY = None
    ors_configurado = False


with st.sidebar:

    st.header("Configuração")

    if ors_configurado:

        st.success(
            "OpenRouteService configurado"
        )

    else:

        st.warning(
            "Chave OpenRouteService ainda não configurada."
        )


# ============================================================
# SESSION STATE
# ============================================================

if "num_paradas" not in st.session_state:

    st.session_state.num_paradas = 1


# ============================================================
# INÍCIO E FIM
# ============================================================

col_inicio, col_fim = st.columns(2)


with col_inicio:

    ponto_inicio = st.text_input(
        "📍 Ponto inicial",
        placeholder="Ex.: Prefeitura de Brusque, SC"
    )


with col_fim:

    ponto_fim = st.text_input(
        "🏁 Ponto final",
        placeholder="Ex.: Limeira, Brusque, SC"
    )


# ============================================================
# PARADAS
# ============================================================

st.subheader("Paradas intermediárias")


col_adicionar, col_remover, _ = st.columns(
    [1, 1, 4]
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
            st.session_state.num_paradas <= 1
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


col_modo, col_tipo = st.columns(2)


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


# Se retornar ao início,
# o ponto final será ignorado futuramente.

if voltar_inicio:

    st.info(
        "A rota terminará novamente no ponto inicial."
    )


# ============================================================
# BOTÃO CALCULAR
# ============================================================

st.divider()


if st.button(
    "🚗 Calcular melhor rota",
    type="primary",
    use_container_width=True
):

    # ========================================================
    # LIMPA PARADAS VAZIAS
    # ========================================================

    paradas_validas = [

        parada.strip()

        for parada in paradas

        if parada.strip()
    ]


    # ========================================================
    # VALIDAÇÕES
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


    if not ors_configurado:

        st.error(
            "A chave do OpenRouteService ainda "
            "não está configurada nos Secrets."
        )

        st.stop()


    # ========================================================
    # RESUMO
    # ========================================================

    if voltar_inicio:

        destino = (
            ponto_inicio.strip()
        )

        tipo_rota = (
            "Rota fechada"
        )

    else:

        destino = (
            ponto_fim.strip()
        )

        tipo_rota = (
            "Rota aberta"
        )


    st.success(
        "Configuração recebida com sucesso."
    )


    st.subheader(
        "Resumo da solicitação"
    )


    st.write(
        f"**Início:** {ponto_inicio}"
    )

    st.write(
        f"**Fim:** {destino}"
    )

    st.write(
        f"**Tipo:** {tipo_rota}"
    )

    st.write(
        f"**Modo:** {modo}"
    )

    st.write(
        f"**Paradas intermediárias:** "
        f"{len(paradas_validas)}"
    )


    if paradas_validas:

        st.write(
            "**Locais informados:**"
        )

        for numero, parada in enumerate(
            paradas_validas,
            start=1
        ):

            st.write(
                f"{numero}. {parada}"
            )


    st.info(
        "Na próxima etapa, estes endereços "
        "serão convertidos em coordenadas e "
        "enviados ao otimizador."
    )
