import streamlit as st

from streamlit_searchbox import st_searchbox

from ors import (
    autocomplete_enderecos,
    ORSError,
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

st.title("🗺️ Otimizador de Rotas")

st.write(
    "Informe o ponto inicial, o ponto final e as paradas. "
    "Digite o nome do local ou endereço e selecione uma das "
    "opções encontradas."
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
        "as demais pesquisas darão preferência a "
        "locais próximos dele."
    )


# ============================================================
# SESSION STATE
# ============================================================

if "num_paradas" not in st.session_state:

    st.session_state.num_paradas = 1


if "erro_autocomplete" not in st.session_state:

    st.session_state.erro_autocomplete = None


# ============================================================
# BUSCA COM CACHE
# ============================================================
#
# O usuário costuma digitar:
#
# h
# ha
# hav
# hava
# havan
#
# Sem cache, algumas consultas poderiam se repetir.
#
# O TTL de 5 minutos é suficiente para esse tipo de busca.
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False
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
# FUNÇÃO CHAMADA PELO AUTOCOMPLETE
# ============================================================

def buscar_sugestoes(
    searchterm,
    focus_lat=None,
    focus_lon=None,
):

    texto = searchterm.strip()

    # Só pesquisa após 3 caracteres
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
            "Não foi possível pesquisar o local neste momento."
        )

        return []

    # ========================================================
    # FORMATO DO STREAMLIT-SEARCHBOX
    # ========================================================
    #
    # Cada opção possui:
    #
    # (
    #     "texto mostrado ao usuário",
    #     objeto retornado após selecionar
    # )
    #
    # Portanto, depois da seleção recebemos:
    #
    # {
    #   "label": "...",
    #   "latitude": ...,
    #   "longitude": ...
    # }
    # ========================================================

    return [

        (
            resultado["label"],
            resultado,
        )

        for resultado in resultados
    ]


# ============================================================
# COMPONENTE DE BUSCA
# ============================================================

def campo_local(
    chave,
    label,
    placeholder,
    focus=None,
):

    if focus:

        focus_lat = focus.get(
            "latitude"
        )

        focus_lon = focus.get(
            "longitude"
        )

    else:

        focus_lat = None
        focus_lon = None

    resultado = st_searchbox(

        buscar_sugestoes,

        key=chave,

        label=label,

        placeholder=placeholder,

        # Evita chamar a API a cada tecla imediatamente
        debounce=400,

        # Depois de escolher, mantém o local no campo
        clear_on_submit=False,

        # Permite editar novamente depois de selecionar
        edit_after_submit="option",

        # Estes argumentos são repassados para
        # buscar_sugestoes()
        focus_lat=focus_lat,

        focus_lon=focus_lon,
    )

    return resultado


# ============================================================
# TIPO DA ROTA
# ============================================================
#
# Colocamos esta opção antes dos endereços porque,
# quando marcada, não precisamos pedir ponto final.
# ============================================================

voltar_inicio = st.checkbox(
    "🔄 Retornar ao ponto inicial",
    help=(
        "Quando marcado, a rota começa e termina "
        "no mesmo local."
    ),
)


# ============================================================
# INÍCIO E FIM
# ============================================================

col_inicio, col_fim = st.columns(2)


# ============================================================
# PONTO INICIAL
# ============================================================

with col_inicio:

    ponto_inicio = campo_local(

        chave="busca_ponto_inicio",

        label="📍 Ponto inicial",

        placeholder=(
            "Digite pelo menos 3 letras..."
        ),
    )


# ============================================================
# PONTO FINAL
# ============================================================

with col_fim:

    if voltar_inicio:

        if ponto_inicio:

            st.text_input(
                "🏁 Ponto final",
                value=ponto_inicio["label"],
                disabled=True,
            )

        else:

            st.text_input(
                "🏁 Ponto final",
                value="Selecione primeiro o ponto inicial",
                disabled=True,
            )

        ponto_fim = ponto_inicio

    else:

        ponto_fim = campo_local(

            chave="busca_ponto_fim",

            label="🏁 Ponto final",

            placeholder=(
                "Digite pelo menos 3 letras..."
            ),

            # =================================================
            # IMPORTANTE
            # =================================================
            #
            # Depois que o início é escolhido,
            # damos preferência a resultados próximos dele.
            #
            # Exemplo:
            #
            # início = Brusque
            #
            # busca = Limeira
            #
            # resultados próximos de Brusque passam a
            # ter preferência sobre Limeira/SP.
            # =================================================

            focus=ponto_inicio,
        )


# ============================================================
# AVISO DO FOCO GEOGRÁFICO
# ============================================================

if ponto_inicio:

    st.caption(
        "📌 As demais buscas estão priorizando locais "
        f"próximos de **{ponto_inicio['label']}**."
    )


# ============================================================
# ERRO DE AUTOCOMPLETE
# ============================================================

if st.session_state.erro_autocomplete:

    st.warning(
        st.session_state.erro_autocomplete
    )


# ============================================================
# PARADAS INTERMEDIÁRIAS
# ============================================================

st.subheader(
    "Paradas intermediárias"
)


# ============================================================
# BOTÕES
# ============================================================

col_adicionar, col_remover, _ = st.columns(
    [1, 1, 4]
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
            st.session_state.num_paradas <= 1
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
            "Digite um endereço ou estabelecimento..."
        ),

        # Todas as paradas são priorizadas
        # em relação ao ponto inicial
        focus=ponto_inicio,
    )

    paradas.append(
        parada
    )


# ============================================================
# OPÇÕES DE OTIMIZAÇÃO
# ============================================================

st.divider()


col_modo, col_info = st.columns(
    [1, 1]
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


with col_info:

    if modo == "Rápido":

        st.info(
            "⚡ Prioriza velocidade de cálculo."
        )

    else:

        st.info(
            "🎯 Busca uma solução ainda melhor, "
            "mas pode levar mais tempo."
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
# PROCESSAMENTO
# ============================================================

if calcular:

    # ========================================================
    # VERIFICA API
    # ========================================================

    if not ors_configurado:

        st.error(
            "O OpenRouteService não está configurado."
        )

        st.stop()


    # ========================================================
    # INÍCIO PRECISA TER SIDO SELECIONADO
    # ========================================================

    if not ponto_inicio:

        st.error(
            "Selecione o ponto inicial em uma das "
            "opções apresentadas pela busca."
        )

        st.stop()


    # ========================================================
    # FIM PRECISA TER SIDO SELECIONADO
    # ========================================================

    if (
        not voltar_inicio
        and
        not ponto_fim
    ):

        st.error(
            "Selecione o ponto final em uma das "
            "opções apresentadas pela busca."
        )

        st.stop()


    # ========================================================
    # REMOVE PARADAS NÃO PREENCHIDAS
    # ========================================================

    paradas_validas = [

        parada

        for parada in paradas

        if parada is not None
    ]


    # ========================================================
    # ROTA FECHADA SEM PARADAS
    # ========================================================

    if (
        voltar_inicio
        and
        len(paradas_validas) == 0
    ):

        st.error(
            "Para uma rota que retorna ao ponto inicial, "
            "adicione pelo menos uma parada."
        )

        st.stop()


    # ========================================================
    # MONTA LISTA FINAL
    # ========================================================

    locais = []


    # INÍCIO

    locais.append(
        {
            "tipo": "Início",

            "nome":
                ponto_inicio["label"],

            "latitude":
                ponto_inicio["latitude"],

            "longitude":
                ponto_inicio["longitude"],
        }
    )


    # ========================================================
    # PARADAS
    # ========================================================

    for numero, parada in enumerate(
        paradas_validas,
        start=1,
    ):

        locais.append(
            {
                "tipo":
                    f"Parada {numero}",

                "nome":
                    parada["label"],

                "latitude":
                    parada["latitude"],

                "longitude":
                    parada["longitude"],
            }
        )


    # ========================================================
    # FIM
    # ========================================================

    if voltar_inicio:

        locais.append(
            {
                "tipo":
                    "Fim",

                "nome":
                    ponto_inicio["label"],

                "latitude":
                    ponto_inicio["latitude"],

                "longitude":
                    ponto_inicio["longitude"],
            }
        )

    else:

        locais.append(
            {
                "tipo":
                    "Fim",

                "nome":
                    ponto_fim["label"],

                "latitude":
                    ponto_fim["latitude"],

                "longitude":
                    ponto_fim["longitude"],
            }
        )


    # ========================================================
    # GUARDA PARA O PRÓXIMO PASSO
    # ========================================================
    #
    # Quando adicionarmos route_engine.py,
    # serão estes dados que passaremos ao algoritmo.
    # ========================================================

    st.session_state[
        "locais_confirmados"
    ] = locais


    st.session_state[
        "modo_otimizacao"
    ] = modo


    st.session_state[
        "rota_fechada"
    ] = voltar_inicio


    # ========================================================
    # SUCESSO
    # ========================================================

    st.success(
        "Todos os locais foram selecionados corretamente."
    )


    # ========================================================
    # RESUMO
    # ========================================================

    st.subheader(
        "📍 Locais confirmados"
    )


    # ========================================================
    # MÉTRICAS
    # ========================================================

    col1, col2, col3 = st.columns(
        3
    )


    with col1:

        st.metric(
            "Locais",
            len(
                locais
            )
            - (
                1
                if voltar_inicio
                else 0
            )
        )


    with col2:

        st.metric(
            "Paradas",
            len(
                paradas_validas
            )
        )


    with col3:

        st.metric(
            "Modo",
            modo,
        )


    # ========================================================
    # MOSTRA CADA LOCAL
    # ========================================================

    for local in locais:

        # Na rota fechada não precisamos mostrar
        # o mesmo início novamente como uma caixa final.
        #
        # Mas o dado permanece na lista para o algoritmo.

        if (
            voltar_inicio
            and
            local is locais[-1]
        ):

            continue

        with st.container(
            border=True
        ):

            st.write(
                f"**{local['tipo']}**"
            )

            st.write(
                local[
                    "nome"
                ]
            )

            st.caption(
                f"📐 "
                f"{local['latitude']:.6f}, "
                f"{local['longitude']:.6f}"
            )


    # ========================================================
    # ROTA FECHADA
    # ========================================================

    if voltar_inicio:

        st.write(
            f"🔄 **Retorno:** "
            f"{ponto_inicio['label']}"
        )


    # ========================================================
    # PRÓXIMA ETAPA
    # ========================================================

    st.info(
        "Os locais e suas coordenadas estão prontos. "
        "O próximo passo é enviar esses pontos ao "
        "algoritmo de otimização de rota."
    )
