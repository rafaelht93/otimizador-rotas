
        for item
        in paradas

        if item
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

    locais = [
        converter_local(
            ponto_inicio,
            "Início",
        )
    ]

    for i, parada in enumerate(
        paradas_validas,
        start=1,
    ):

        locais.append(
            converter_local(
                parada,
                f"Parada {i}",
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
            "Calculando a melhor rota..."
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

        st.session_state.rota_atual_id = None
        st.session_state.rota_atual_nome = None
        st.session_state.rota_aberta_salva = False
        st.session_state.resultado_historico = False
        st.session_state.resultado_direto = False

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
                "A rota foi calculada, "
                "mas o histórico não pôde "
                f"ser registrado: {erro}"
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
            "Não foi possível calcular: "
            f"{e}"
        )


if st.session_state.resultado_rota:

    st.divider()

    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )
