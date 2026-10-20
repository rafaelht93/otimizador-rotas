import streamlit as st

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ Otimizador de Rotas")

st.write(
    "Aplicação criada com sucesso. "
    "Aqui vamos calcular a melhor rota entre vários pontos."
)

inicio = st.text_input(
    "Ponto inicial"
)

fim = st.text_input(
    "Ponto final"
)

if st.button("Calcular rota"):
    st.success(
        f"Configuração recebida: {inicio} → {fim}"
    )
