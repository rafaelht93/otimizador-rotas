import hashlib
import html
import io
import json
import logging
import math
import re
import threading
import secrets
import uuid
import time
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
    AUTOCOMPLETE_MIN_CHARS,
    ORS_BASE_URL,
    MAX_SUPPORTED_LOCATIONS,
    ORSError,
    autocomplete_enderecos,
    buscar_enderecos,
    obter_matriz_distancias,
    obter_rota_completa,
)

from route_engine import (
    RouteOptimizationError,
    otimizar_rota,
)

from rotaki_features import (
    geoapify_buscar,
    validar_coordenadas,
    otimizar_janelas_locais,
    previsoes_ordem,
    inverter_locais,
    nova_chave_compartilhamento,
    validar_ordem_ids,
    minutos_hora,
)

try:
    from streamlit_sortables import sort_items
except ImportError:
    sort_items = None

from app_logic import (
    dividir_rota_google_maps as dividir_rota_google_maps_logic,
    locais_para_execucao as locais_para_execucao_logic,
    resolver_perfil_efetivo,
    validar_regras_plano,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

st.set_page_config(
    page_title="Otimizador de Rotas",
    page_icon="🗺️",
    layout="wide",
)

APP_URL = (
    "https://otimizador-rotas-hpu3l5hwuf5c6id4jtwsnz."
    "streamlit.app"
)



class LimiteUsoError(Exception):
    pass


class PagamentoError(Exception):
    pass


class OperacaoUsuarioError(Exception):
    pass


logger = logging.getLogger("otimizador_rotas")


def mostrar_erro_usuario(
    contexto,
    erro,
    mensagem_padrao="Não foi possível concluir a operação. Tente novamente.",
):
    logger.error(
        "%s: %s",
        contexto,
        erro,
        exc_info=(type(erro), erro, erro.__traceback__),
    )

    if isinstance(
        erro,
        (
            LimiteUsoError,
            PagamentoError,
            ORSError,
            RouteOptimizationError,
            ValueError,
            PermissionError,
            OperacaoUsuarioError,
        ),
    ):
        st.error(str(erro))
    else:
        st.error(mensagem_padrao)


# ============================================================
# VISUALIZAÇÃO PÚBLICA DE ROTA COMPARTILHADA (somente leitura)
# ============================================================

def exibir_rota_compartilhada_publica():
    token = str(st.query_params.get("rota_compartilhada") or "")
    if not token or len(token) > 128:
        return False
    st.title("🗺️ Rota compartilhada · Rotaki")
    try:
        endereco = str(st.secrets["SUPABASE_URL"]).rstrip("/")
        chave = str(st.secrets["SUPABASE_SECRET_KEY"])
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        resposta = requests.get(
            f"{endereco}/rest/v1/compartilhamentos_rotas",
            headers={"apikey": chave},
            params={"select": "dados_publicos,expira_em,revogado",
                    "token_hash": f"eq.{digest}", "revogado": "eq.false",
                    "expira_em": f"gt.{datetime.now(ZoneInfo('UTC')).isoformat()}",
                    "limit": "1"}, timeout=12,
        )
        resposta.raise_for_status()
        registros = resposta.json()
        if not registros:
            st.warning("Este compartilhamento expirou ou foi revogado.")
            return True
        dados = registros[0].get("dados_publicos") or {}
        locais = dados.get("locais") or []
        st.subheader(dados.get("nome") or "Rota")
        st.caption("Consulta pública apenas da sequência compartilhada. Não permite editar nem executar entregas.")
        for i, local in enumerate(locais, start=1):
            st.write(f"**{i}.** {local.get('nome') or local.get('endereco') or 'Local'}")
            destino = f"{float(local['latitude']):.7f},{float(local['longitude']):.7f}"
            st.link_button(f"🧭 Navegar até o ponto {i}",
                'https://www.google.com/maps/dir/?'+urlencode({
                    'api':'1','destination':destino,'travelmode':'driving','dir_action':'navigate',
                }), use_container_width=True)
        if locais:
            mapa = folium.Map(location=[float(locais[0]['latitude']), float(locais[0]['longitude'])], zoom_start=12)
            for i, loc in enumerate(locais, start=1):
                folium.Marker([float(loc['latitude']), float(loc['longitude'])],
                              tooltip=f"{i}. {str(loc.get('nome') or '')[:100]}").add_to(mapa)
            st_folium(mapa, height=450, use_container_width=True, returned_objects=[])
    except Exception:
        logger.exception("Falha ao abrir rota pública")
        st.warning("Não foi possível abrir o compartilhamento. Verifique se ele continua válido.")
    return True

if st.query_params.get("rota_compartilhada"):
    exibir_rota_compartilhada_publica()
    st.stop()

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

usuario_id = dados_usuario.get("sub")

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

    ORS_KEY = st.secrets["ORS_KEY"]
    ors_configurado = True

except Exception:

    ORS_KEY = None
    ors_configurado = False


try:

    SUPABASE_URL = str(
        st.secrets["SUPABASE_URL"]
    ).rstrip("/")

    SUPABASE_SECRET_KEY = str(
        st.secrets["SUPABASE_SECRET_KEY"]
    )

    supabase_configurado = True

except Exception:

    SUPABASE_URL = None
    SUPABASE_SECRET_KEY = None
    supabase_configurado = False


# ============================================================
# SESSION STATE
# ============================================================

def init_state(nome, valor):

    if nome not in st.session_state:
        st.session_state[nome] = valor


init_state("pagina_app", "nova")
init_state("num_paradas", 1)
init_state("form_version", 0)
init_state("manual_campos_version", 0)

init_state("resultado_rota", None)
init_state("rota_atual_id", None)
init_state("rota_atual_nome", None)
init_state("rota_aberta_salva", False)

init_state("resultado_historico", False)
init_state("resultado_direto", False)
init_state("resultado_direto_titulo", None)

init_state("rota_nome_sugerido", "")
init_state("ultima_execucao_id", None)
init_state("mensagem_sistema", None)
init_state("erro_autocomplete", None)

init_state("preset_local_salvo_ids", [])

# Edição de uma rota salva a partir da página Nova rota.
init_state("rota_base_edicao_id", None)
init_state("rota_base_edicao_nome", None)
init_state("rota_base_edicao_pontos", [])
init_state("rota_base_edicao_voltar", False)
init_state("rota_base_edicao_versao", 0)
init_state("rota_base_edicao_sequencia", 0)

init_state("local_form_version", 0)
init_state("local_cadastro_aberto", False)
init_state("local_endereco_editando_id", None)
init_state("local_endereco_edit_version", 0)

init_state("recorrente_cadastro_aberto", False)
init_state("recorrente_form_version", 0)
init_state("recorrente_gerar_id", None)
init_state("recorrente_gerar_version", 0)

init_state("edicao_rota", None)
init_state("edicao_version", 0)

init_state("admin_refresh", 0)

init_state("mp_checkout_url", None)
init_state("mp_checkout_plano", None)

init_state("execucao_entrega_id", None)
init_state("execucao_parada_em_edicao", None)

# Estado específico da importação CSV/XLSX.
# Os índices removidos são mantidos enquanto o mesmo arquivo
# permanecer carregado, evitando que um rerun restaure a linha.
init_state("import_file_hash", None)
init_state("import_removed_indices", [])
init_state("import_manual_overrides", {})
init_state("import_editing_index", None)
init_state("import_edit_version", 0)

# Página renderizada no rerun anterior. Quando a página muda, o app
# força a rolagem para o topo para evitar abrir a nova tela na mesma
# posição vertical da tela anterior.
init_state("pagina_scroll_anterior", None)
init_state("scroll_execucao_pendente", 0)
init_state("scroll_execucao_aplicado", 0)
init_state("motorista_modo", False)
init_state("nova_parada_execucao", None)
init_state("compartilhamento_link", None)
init_state("ordem_manualmente_alterada", False)


# ============================================================
# UTILIDADES
# ============================================================

def normalizar_texto(texto):

    texto = unicodedata.normalize(
        "NFKD",
        str(texto).strip().lower(),
    )

    return "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )


def preparar_json(dados):

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


def formatar_data(valor):

    if not valor:
        return ""

    try:

        data = datetime.fromisoformat(
            str(valor).replace(
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

        return str(valor)


def formatar_dinheiro(valor):

    try:

        numero = float(valor)

    except Exception:

        numero = 0.0

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def mostrar_mensagem_sistema():

    mensagem = st.session_state.get(
        "mensagem_sistema"
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
        st.success(texto)

    elif tipo == "warning":
        st.warning(texto)

    elif tipo == "error":
        st.error(texto)

    else:
        st.info(texto)

    st.session_state.mensagem_sistema = None


def limpar_rota_atual():

    st.session_state.resultado_rota = None
    st.session_state.rota_atual_id = None
    st.session_state.rota_atual_nome = None
    st.session_state.rota_aberta_salva = False
    st.session_state.resultado_historico = False
    st.session_state.resultado_direto = False
    st.session_state.resultado_direto_titulo = None
    st.session_state.rota_nome_sugerido = ""
    st.session_state.ultima_execucao_id = None


def iniciar_nova_rota(
    preset_ids=None,
):

    limpar_rota_atual()

    st.session_state.num_paradas = 1
    st.session_state.form_version += 1

    st.session_state.preset_local_salvo_ids = list(
        preset_ids
        or
        []
    )

    st.session_state.recorrente_gerar_id = None
    st.session_state.rota_base_edicao_id = None
    st.session_state.rota_base_edicao_nome = None
    st.session_state.rota_base_edicao_pontos = []
    st.session_state.rota_base_edicao_voltar = False
    st.session_state.rota_base_edicao_versao += 1
    st.session_state["manual_retorno_inicio"] = False
    st.session_state["locais_retorno_inicio"] = False
    st.session_state.pagina_app = "nova"


def rolar_para_topo_ao_trocar_pagina():
    """Rola para o topo ao navegar ou ao abrir outra execução na mesma página."""
    pagina_atual = st.session_state.get("pagina_app", "nova")
    pagina_anterior = st.session_state.get("pagina_scroll_anterior")
    solicitado = int(st.session_state.get("scroll_execucao_pendente", 0))
    aplicado = int(st.session_state.get("scroll_execucao_aplicado", 0))

    if pagina_anterior == pagina_atual and solicitado == aplicado:
        return

    st.session_state.pagina_scroll_anterior = pagina_atual
    st.session_state.scroll_execucao_aplicado = solicitado

    # Executa no documento principal (Streamlit >= 1.52). Usa um token
    # de mudança para que o mesmo componente execute após a seleção.
    st.html(
        f"""
        <script>
        (() => {{
            function voltarAoTopo() {{
                window.scrollTo({{top: 0, left: 0, behavior: 'instant'}});
                document.documentElement.scrollTop = 0;
                document.body.scrollTop = 0;
                document.querySelectorAll(
                    '[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main'
                ).forEach(el => el.scrollTo({{top: 0, left: 0, behavior: 'instant'}}));
            }}
            voltarAoTopo();
            requestAnimationFrame(voltarAoTopo);
            requestAnimationFrame(() => requestAnimationFrame(voltarAoTopo));
            setTimeout(voltarAoTopo, 120);
            setTimeout(voltarAoTopo, 400);
        }})();
        </script>
        <!-- navegacao:{html.escape(str(pagina_atual))};selecao:{solicitado} -->
        """,
        unsafe_allow_javascript=True,
    )


# ============================================================
# BUSCA DE ENDEREÇOS E COORDENADAS
# ============================================================

def converter_texto_coordenada(valor):
    """Aceita vírgula decimal brasileira ou ponto decimal."""
    return float(str(valor).strip().replace(",", "."))


def criar_local_por_coordenadas(latitude, longitude, nome=""):
    try:
        lat = converter_texto_coordenada(latitude)
        lon = converter_texto_coordenada(longitude)
    except (TypeError, ValueError) as erro:
        raise ValueError("Latitude e longitude devem ser números válidos.") from erro

    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ValueError("As coordenadas devem ser números finitos.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Latitude deve estar entre -90 e 90; longitude entre -180 e 180.")

    titulo = str(nome or "").strip() or f"Coordenadas: {lat:.6f}, {lon:.6f}"
    return {
        "label": titulo, "nome": titulo,
        "latitude": lat, "longitude": lon,
        "layer": "coordinate", "name": titulo,
        "street": None, "housenumber": None,
        "locality": None, "region": None, "country": "Brasil",
    }


def interpretar_texto_coordenadas(texto):
    """Aceita lat,lon (com ponto) ou lat;lon (com vírgula decimal)."""
    texto = str(texto or "").strip().lstrip("@")
    if not texto:
        return None
    if ";" in texto:
        partes = texto.split(";")
    elif re.fullmatch(r"\s*[+-]?\d+(?:\.\d+)?\s*,\s*[+-]?\d+(?:\.\d+)?\s*", texto):
        partes = texto.split(",")
    else:
        partes = texto.split()
    if len(partes) != 2:
        return None
    try:
        return criar_local_por_coordenadas(partes[0], partes[1])
    except ValueError:
        return None


def limpar_enderecos_manuais_apos_importacao():
    """Evita que campos antigos reapareçam ao voltar à entrada manual."""
    prefixos = ("inicio_", "fim_", "parada_")
    for chave in list(st.session_state.keys()):
        if chave.startswith(prefixos):
            del st.session_state[chave]
    st.session_state.num_paradas = 1
    st.session_state.manual_campos_version = (
        st.session_state.get("manual_campos_version", 0) + 1
    )


def ordenar_resultados_enderecos(resultados, texto, cidade_referencia=""):
    """Prefere rua+número corretos e a cidade declarada; não remove alternativas."""
    cidade = normalizar_texto(cidade_referencia).split(",")[0].strip()
    consulta = normalizar_texto(texto)
    numeros = re.findall(r"(?<!\w)\d{1,6}(?!\w)", consulta)
    numero = next((n for n in reversed(numeros) if len(n) <= 5), "")
    vistos = set()
    unicos = []
    for indice, item in enumerate(resultados):
        try:
            coord = (round(float(item["latitude"]), 7), round(float(item["longitude"]), 7))
        except (TypeError, ValueError, KeyError):
            continue
        identidade = (coord, str(item.get("housenumber") or ""), normalizar_texto(item.get("label", "")))
        if identidade in vistos:
            continue
        vistos.add(identidade)
        layer = str(item.get("layer") or "")
        nota = 0
        localidade = normalizar_texto(item.get("locality") or "")
        endereco = normalizar_texto(item.get("label") or "")
        rua = normalizar_texto(item.get("street") or "")
        if cidade and (cidade in localidade or cidade in endereco):
            nota += 9
        if layer == "address":
            nota += 3
        elif layer in {"venue", "street"}:
            nota += 1
        if numero:
            num_encontrado = re.sub(r"\D", "", str(item.get("housenumber") or ""))
            if num_encontrado == numero:
                nota += 7
            elif num_encontrado:
                nota -= 6
            elif layer == "address":
                nota -= 2
        if rua and len(rua) > 3 and rua in consulta:
            nota += 3
        unicos.append((nota, indice, item))
    unicos.sort(key=lambda trio: (-trio[0], trio[1]))
    return [item for _, _, item in unicos]


# ============================================================
# SUPABASE
# ============================================================

def supabase_request(
    tabela,
    method,
    params=None,
    json_body=None,
    prefer=None,
):
    """Requisição REST de baixo nível usando a chave secreta do backend."""
    if not supabase_configurado:
        raise RuntimeError("Supabase não configurado.")

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    try:
        response = requests.request(
            method=method,
            url=f"{SUPABASE_URL}/rest/v1/{tabela}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=20,
        )
    except requests.Timeout as e:
        logger.exception("Timeout ao acessar Supabase")
        raise RuntimeError("O Supabase demorou demais para responder.") from e
    except requests.RequestException as e:
        logger.exception("Falha de conexão com Supabase")
        raise RuntimeError("Não foi possível conectar ao Supabase.") from e

    if response.status_code >= 400:
        detalhe = ""
        try:
            payload = response.json()
            detalhe = str(payload.get("message") or payload.get("error") or "")
        except Exception:
            detalhe = response.text[:300]

        logger.error(
            "Erro Supabase REST tabela=%s method=%s status=%s detalhe=%s",
            tabela,
            method,
            response.status_code,
            detalhe,
        )
        raise RuntimeError(f"supabase_http_{response.status_code}:{detalhe}")

    if response.status_code == 204 or not response.text.strip():
        return []

    try:
        return response.json()
    except ValueError:
        return []


def supabase_rpc(nome, payload=None):
    """Executa uma RPC no Supabase usando apenas o backend."""
    if not supabase_configurado:
        raise RuntimeError("Supabase não configurado.")

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/{nome}",
            headers={
                "apikey": SUPABASE_SECRET_KEY,
                "Content-Type": "application/json",
            },
            json=payload or {},
            timeout=30,
        )
    except requests.Timeout as e:
        logger.exception("Timeout na RPC %s", nome)
        raise RuntimeError("O Supabase demorou demais para responder.") from e
    except requests.RequestException as e:
        logger.exception("Falha de conexão na RPC %s", nome)
        raise RuntimeError("Não foi possível conectar ao Supabase.") from e

    if response.status_code >= 400:
        detalhe = ""
        try:
            dados = response.json()
            detalhe = str(dados.get("message") or dados.get("error") or "")
        except Exception:
            detalhe = response.text[:300]

        logger.error(
            "Erro Supabase RPC=%s status=%s detalhe=%s",
            nome,
            response.status_code,
            detalhe,
        )
        raise RuntimeError(f"supabase_rpc_{nome}:{detalhe}")

    if not response.text.strip():
        return []

    try:
        return response.json()
    except ValueError:
        return []


def supabase_user_request(
    tabela,
    method,
    params=None,
    json_body=None,
    prefer=None,
):
    """Acesso a dados do usuário com escopo injetado automaticamente."""
    metodo = str(method).upper()
    parametros = dict(params or {})
    alvo = str(usuario_id)

    if metodo in ("GET", "PATCH", "DELETE"):
        filtro_existente = parametros.get("user_id")
        filtro_correto = f"eq.{alvo}"
        if filtro_existente not in (None, filtro_correto):
            raise PermissionError("Tentativa de acesso fora do escopo do usuário.")
        parametros["user_id"] = filtro_correto

    corpo = json_body

    if metodo == "POST" and json_body is not None:
        if isinstance(json_body, list):
            corpo = []
            for item in json_body:
                registro = dict(item)
                existente = registro.get("user_id")
                if existente not in (None, alvo):
                    raise PermissionError("Tentativa de gravação fora do escopo do usuário.")
                registro["user_id"] = alvo
                corpo.append(registro)
        elif isinstance(json_body, dict):
            corpo = dict(json_body)
            existente = corpo.get("user_id")
            if existente not in (None, alvo):
                raise PermissionError("Tentativa de gravação fora do escopo do usuário.")
            corpo["user_id"] = alvo

    return supabase_request(
        tabela,
        metodo,
        params=parametros,
        json_body=corpo,
        prefer=prefer,
    )


def exigir_admin_atual():
    """Revalida o privilégio administrativo no momento da operação."""
    registros = supabase_user_request(
        "usuarios_app",
        "GET",
        params={
            "select": "user_id,ativo,is_admin",
            "limit": "1",
        },
    )

    perfil = registros[0] if registros else None

    if not perfil or not perfil.get("ativo", True) or not perfil.get("is_admin", False):
        raise PermissionError("Acesso administrativo não autorizado.")

    return perfil


def supabase_admin_request(
    tabela,
    method,
    params=None,
    json_body=None,
    prefer=None,
):
    """Acesso sem filtro de usuário permitido apenas após revalidar admin."""
    exigir_admin_atual()
    return supabase_request(
        tabela,
        method,
        params=params,
        json_body=json_body,
        prefer=prefer,
    )



# ============================================================
# MERCADO PAGO / EDGE FUNCTION
# ============================================================

def mercado_pago_backend(
    action,
    plan_name=None,
):

    if not supabase_configurado:

        raise PagamentoError(
            "O sistema de pagamentos está indisponível."
        )

    payload = {
        "action":
            action,

        "user_id":
            usuario_id,
    }

    if plan_name:

        payload["plan_name"] = plan_name

    try:

        response = requests.post(
            (
                f"{SUPABASE_URL}"
                "/functions/v1/"
                "mercado-pago-subscription"
            ),
            headers={
                "apikey":
                    SUPABASE_SECRET_KEY,

                "Content-Type":
                    "application/json",
            },
            json=payload,
            timeout=30,
        )

    except requests.Timeout as e:

        raise PagamentoError(
            "O sistema de pagamentos demorou "
            "demais para responder."
        ) from e

    except requests.RequestException as e:

        raise PagamentoError(
            "Não foi possível acessar "
            "o sistema de pagamentos."
        ) from e

    try:

        dados = response.json()

    except ValueError:

        dados = {}

    if response.status_code >= 400:

        codigo = dados.get(
            "error"
        )

        mensagem_tecnica = dados.get(
            "message",
            "",
        )

        mensagens = {
            "plan_price_not_configured":
                (
                    "O preço deste plano ainda "
                    "não foi configurado."
                ),

            "plan_not_found":
                (
                    "O plano solicitado não foi encontrado."
                ),

            "user_without_email":
                (
                    "Sua conta não possui um e-mail "
                    "disponível para criar a assinatura."
                ),

            "user_not_available":
                (
                    "Sua conta não está disponível "
                    "para contratação."
                ),

            "subscription_not_found":
                (
                    "Nenhuma assinatura foi encontrada."
                ),

            "invalid_paid_plan":
                (
                    "Este plano não requer assinatura."
                ),
        }

        if codigo in mensagens:

            raise PagamentoError(
                mensagens[codigo]
            )

        if (
            "credentials not configured"
            in mensagem_tecnica.lower()
        ):

            raise PagamentoError(
                "As credenciais do Mercado Pago "
                "ainda não foram configuradas "
                "no Supabase."
            )

        raise PagamentoError(
            "Não foi possível processar "
            "a solicitação no Mercado Pago."
        )

    return dados


def listar_assinaturas_usuario(
    user_id_alvo=None,
    limite=20,
):

    if user_id_alvo not in (None, usuario_id):
        raise PermissionError("Consulta de assinatura fora do escopo do usuário.")

    return supabase_user_request(
        "assinaturas",
        "GET",
        params={
            "select":
                (
                    "id,user_id,plano,gateway,"
                    "gateway_subscription_id,"
                    "external_reference,status,"
                    "payer_email,amount,currency,"
                    "init_point,next_payment_date,"
                    "last_payment_id,"
                    "last_payment_status,"
                    "last_payment_at,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "created_at.desc",

            "limit":
                str(limite),
        },
    )


def obter_assinatura_atual(
    user_id_alvo=None,
):

    assinaturas = listar_assinaturas_usuario(
        user_id_alvo=user_id_alvo,
        limite=1,
    )

    if assinaturas:
        return assinaturas[0]

    return None


def nome_status_assinatura(
    status,
):

    mapa = {
        "pending":
            "Aguardando pagamento",

        "authorized":
            "Ativa",

        "paused":
            "Pausada",

        "canceled":
            "Cancelada",

        "cancelled":
            "Cancelada",
    }

    return mapa.get(
        str(status or "").lower(),
        status or "Sem status",
    )


# ============================================================
# EXECUÇÕES DE ENTREGA
# ============================================================

def nome_status_execucao(status):
    mapa = {
        "em_andamento": "Em andamento",
        "finalizada": "Finalizada",
        "cancelada": "Cancelada",
    }

    return mapa.get(
        str(status or "").lower(),
        status or "—",
    )


def nome_status_parada(status):
    mapa = {
        "pendente": "⏳ Pendente",
        "entregue": "✅ Entregue",
        "nao_entregue": "❌ Não entregue",
    }

    return mapa.get(
        str(status or "").lower(),
        status or "—",
    )


def criar_mapa_trecho_execucao(origem, destino, geometria):
    """Mostra somente o trajeto viário entre a parada anterior e a atual."""
    caminho = [
        [float(ponto[1]), float(ponto[0])]
        for ponto in (geometria or [])
        if isinstance(ponto, (list, tuple)) and len(ponto) >= 2
    ]
    if len(caminho) < 2:
        raise ValueError("O serviço de rotas não retornou geometria para este trecho.")

    mapa = folium.Map(location=caminho[len(caminho) // 2], zoom_start=14, control_scale=True)
    folium.PolyLine(caminho, weight=5, color="#2563eb", opacity=0.85).add_to(mapa)
    for local, cor, rotulo in (
        (origem, "green", "Saída deste trecho"),
        (destino, "red", "Chegada deste trecho"),
    ):
        folium.Marker(
            [float(local["latitude"]), float(local["longitude"])],
            tooltip=html.escape(f"{rotulo}: {local.get('nome') or local.get('endereco') or 'Local'}"),
            icon=folium.Icon(color=cor, icon="map-marker"),
        ).add_to(mapa)
    mapa.fit_bounds(caminho)
    return mapa


def mostrar_mapa_trecho_execucao(origem, destino, chave):
    """A consulta ORS só acontece quando este mapa é realmente mostrado."""
    if not origem or not destino:
        st.warning("Não foi possível identificar o ponto anterior deste trecho.")
        return
    if not ors_configurado:
        st.warning("O serviço ORS não está configurado para exibir o mapa.")
        return
    try:
        coordenadas = tuple(
            (float(local["latitude"]), float(local["longitude"]))
            for local in (origem, destino)
        )
        if coordenadas[0] == coordenadas[1]:
            st.info("O ponto anterior e esta parada possuem as mesmas coordenadas.")
            return
        dados = obter_directions_cache_v3(coordenadas, ORS_KEY)
        mapa = criar_mapa_trecho_execucao(origem, destino, dados.get("geometry"))
        distancia = float(dados.get("distancia_m") or 0) / 1000
        minutos = float(dados.get("duracao_s") or 0) / 60
        st.caption(
            f"Trecho: {origem.get('nome') or origem.get('endereco') or 'Origem'} "
            f"→ {destino.get('nome') or destino.get('endereco') or 'Destino'} "
            f"• {distancia:.2f} km • {minutos:.0f} min (estimado)"
        )
        st_folium(
            mapa,
            height=320,
            use_container_width=True,
            returned_objects=[],
            key=f"mapa_trecho_exec_{chave}",
        )
    except (ORSError, ValueError, TypeError, KeyError) as erro:
        st.warning(f"Não foi possível carregar o mapa deste trecho: {erro}")



def criar_link_rota_compartilhavel(rota_id, nome_rota, dados, dias=7):
    """Só proprietário logado; token randômico não é armazenado em claro."""
    if not rota_id:
        raise ValueError("Salve a rota antes de gerar um link de compartilhamento.")
    rota_autorizada = supabase_user_request(
        "rotas", "GET", params={"select":"id", "id":f"eq.{rota_id}", "limit":"1"})
    if not rota_autorizada:
        raise PermissionError("A rota não pertence ao usuário atual.")
    locais = dados.get("locais_ordenados") or []
    if not locais or len(locais) > 200:
        raise ValueError("A rota não possui paradas compartilháveis.")
    publicos = []
    for local in locais:
        lat, lon = validar_coordenadas(local["latitude"], local["longitude"])
        publicos.append({"nome": str(local.get("nome") or local.get("label") or "Local")[:200],
                        "latitude":lat,"longitude":lon})
    token, digest = nova_chave_compartilhamento()
    expira = (datetime.now(ZoneInfo("UTC")) + timedelta(days=dias)).isoformat()
    supabase_user_request("compartilhamentos_rotas", "POST", json_body={
        "rota_id":rota_id, "token_hash":digest, "dados_publicos":{
            "nome":str(nome_rota or "Rota")[:200],"locais":publicos},
        "expira_em":expira,
    })
    return f"{APP_URL}/?rota_compartilhada={token}"


def interface_compartilhamento_rota(dados):
    rota_id = st.session_state.rota_atual_id
    if not rota_id:
        return
    if st.session_state.get('compartilhamento_rota_id') != rota_id:
        st.session_state.compartilhamento_link = None
        st.session_state.compartilhamento_rota_id = rota_id
    with st.expander("🔗 Compartilhar rota (link válido por 7 dias)"):
        st.warning("Qualquer pessoa com o link poderá ver a sequência e a posição das paradas. Não compartilhe rotas com endereços sensíveis.")
        if st.button("Gerar link privado por token", key="gerar_link_compartilhar"):
            try:
                st.session_state.compartilhamento_link = criar_link_rota_compartilhavel(
                    rota_id, st.session_state.rota_atual_nome, dados)
            except Exception as erro:
                mostrar_erro_usuario("Falha ao criar compartilhamento", erro,
                    "Verifique se a migração SQL dos compartilhamentos foi executada.")
        if st.session_state.compartilhamento_link:
            st.code(st.session_state.compartilhamento_link, language=None)
            st.caption("Guarde o link. O token não é recuperável após sair da sessão.")
        try:
            links = supabase_user_request('compartilhamentos_rotas','GET',params={
                'select':'id,expira_em,revogado,criado_em', 'rota_id':f'eq.{rota_id}',
                'order':'criado_em.desc','limit':'20'})
            for item in links:
                if item.get('revogado'):continue
                c1,c2=st.columns([4,1])
                c1.caption('Expira em: '+formatar_data(item['expira_em']))
                if c2.button('Revogar',key=f"revogar_{item['id']}"):
                    supabase_user_request('compartilhamentos_rotas','PATCH',
                        params={'id':f"eq.{item['id']}"}, json_body={'revogado':True})
                    st.session_state.compartilhamento_link=None
                    st.rerun()
        except Exception:
            st.caption('Para ativar os links compartilhados, execute a migração SQL.')


def salvar_foto_comprovante(execucao_id, parada_id, arquivo):
    """Upload autenticado no backend em bucket privado e metadata vinculada ao usuário."""
    paradas_autorizadas = listar_paradas_execucao(execucao_id)
    parada = next((x for x in paradas_autorizadas if str(x['id']) == str(parada_id)), None)
    if not parada or parada.get('status') not in ('entregue','nao_entregue'):
        raise PermissionError('Conclua a parada antes de anexar um comprovante.')
    bytes_foto = arquivo.getvalue()
    if len(bytes_foto) > 5 * 1024 * 1024:
        raise ValueError('A imagem deve ter no máximo 5 MB.')
    if bytes_foto.startswith(b'\xff\xd8\xff'):
        mime, extensao = 'image/jpeg','jpg'
    elif bytes_foto.startswith(b'\x89PNG\r\n\x1a\n'):
        mime, extensao = 'image/png','png'
    else:
        raise ValueError('O arquivo deve ser uma imagem JPEG ou PNG válida.')
    identificador = uuid.uuid4().hex
    caminho = f'{usuario_id}/{execucao_id}/{parada_id}/{identificador}.{extensao}'
    resposta = requests.post(
        f'{SUPABASE_URL}/storage/v1/object/rotaki-comprovantes/{caminho}',
        headers={'apikey':SUPABASE_SECRET_KEY,'Content-Type':mime,'x-upsert':'false'},
        data=bytes_foto,timeout=30)
    if not resposta.ok:
        raise OperacaoUsuarioError('Não foi possível gravar a imagem no armazenamento privado.')
    try:
        supabase_user_request('comprovantes_entrega','POST',json_body={
            'execucao_id':execucao_id,'parada_id':parada_id,
            'storage_path':caminho,'tipo_mime':mime})
    except Exception:
        try:
            requests.delete(f'{SUPABASE_URL}/storage/v1/object/rotaki-comprovantes/{caminho}',
                            headers={'apikey':SUPABASE_SECRET_KEY},timeout=10)
        except requests.RequestException: pass
        raise


def renderizar_comprovantes(execucao_id, parada):
    if parada.get('status') not in ('entregue','nao_entregue'):
        return
    with st.container(border=True):
        st.markdown('**📷 Comprovante fotográfico desta parada**')
        try:
            encontrados = supabase_user_request('comprovantes_entrega','GET',params={
                'select':'id,storage_path,criado_em','execucao_id':f'eq.{execucao_id}',
                'parada_id':f"eq.{parada['id']}",'order':'criado_em.desc','limit':'10'})
            for item in encontrados:
                caminho = item.get('storage_path')
                if not caminho or not caminho.startswith(f'{usuario_id}/{execucao_id}/{parada["id"]}/'):
                    continue
                signed = requests.post(f'{SUPABASE_URL}/storage/v1/object/sign/rotaki-comprovantes/{caminho}',
                    headers={'apikey':SUPABASE_SECRET_KEY,'Content-Type':'application/json'},
                    json={'expiresIn':120},timeout=10)
                if signed.ok:
                    url = signed.json().get('signedURL') or signed.json().get('signedUrl')
                    if url:
                        url = url if str(url).startswith('https://') else SUPABASE_URL+'/storage/v1'+('/'+url.lstrip('/'))
                        st.image(url,caption='Comprovante '+formatar_data(item['criado_em']),width=260)
        except Exception:
            st.caption('Execute a migração SQL para habilitar armazenamento de comprovantes.')
        if str(parada.get('status')) in ('entregue','nao_entregue'):
            fonte = st.radio('Origem da foto',['Escolher imagem','Câmera'], horizontal=True,
                             key=f"fonte_pod_{parada['id']}")
            imagem = (st.camera_input('Fotografar comprovante',key=f"camera_pod_{parada['id']}")
                      if fonte == 'Câmera' else st.file_uploader('Anexar imagem',type=['png','jpg','jpeg'],
                        key=f"foto_pod_{parada['id']}"))
            if imagem is not None and st.button('💾 Salvar comprovante',key=f"salvar_pod_{parada['id']}"):
                try:
                    salvar_foto_comprovante(execucao_id,parada['id'],imagem)
                    st.success('Comprovante registrado em armazenamento privado.')
                    st.rerun()
                except Exception as erro:
                    mostrar_erro_usuario('Falha ao salvar comprovante',erro)


def renderizar_reotimizacao_execucao(execucao, paradas, dados_base):
    pendentes = [p for p in paradas if p.get('status') == 'pendente']
    if not pendentes:
        return
    with st.expander('🔄 Reotimizar paradas restantes e incluir nova parada'):
        st.caption('Paradas já concluídas serão preservadas. Uma nova otimização consome um cálculo do plano.')
        concluidas = [p for p in paradas if p.get('status') in ('entregue','nao_entregue')]
        origem_padrao = concluidas[-1] if concluidas else (dados_base.get('locais_ordenados') or [{}])[0]
        st.caption('Local de partida: use a última parada concluída ou informe sua posição atual em coordenadas.')
        origem_manual = st.text_input('Posição atual (latitude, longitude, opcional)',
                                      key=f"local_atual_exec_{execucao['id']}")
        if origem_manual.strip():
            origem = interpretar_texto_coordenadas(origem_manual)
            if not origem:
                st.error('Coordenadas inválidas. Utilize -27.123456, -48.123456.')
                return
        else:
            origem=origem_padrao
        nova = campo_local(f"nova_exec_{execucao['id']}", 'Adicionar outra entrega (opcional)',
                          'Endereço ou latitude, longitude...', focus=origem)
        if st.button('🚗 Calcular nova ordem das pendentes',key=f"reopt_exec_{execucao['id']}",
                     type='primary'):
            try:
                existentes=[]
                for p in pendentes:
                    locais_existentes = dict(p)
                    locais_existentes['label']=p.get('endereco') or p.get('nome') or 'Parada'
                    existentes.append(locais_existentes)
                if nova:
                    la,lo = validar_coordenadas(nova['latitude'],nova['longitude'])
                    locais_existentes = {'id':str(uuid.uuid4()),
                        'nome':str(nova.get('label') or 'Nova parada'),
                        'label':str(nova.get('label') or 'Nova parada'),
                        'endereco':str(nova.get('label') or 'Nova parada'),
                        'latitude':la,'longitude':lo,'tipo':'Parada',
                        '_novo':True}
                    existentes.append(locais_existentes)
                origem_local = dict(origem)
                origem_local['label'] = str(origem.get('label') or origem.get('nome') or 'Partida atual')
                locais_calculo = [converter_local(origem_local,'Início')]
                for i,p in enumerate(existentes,start=1):
                    locais_calculo.append(converter_local(p,f'Parada {i}'))
                # Se houver destino final ainda pendente, ele permanece fixo.
                fim_original = (dados_base.get('locais_ordenados') or [])[-1]
                fechado = bool(dados_base.get('voltar_inicio'))
                if fechado and fim_original:
                    local_fim = dict(fim_original)
                    local_fim['label'] = str(local_fim.get('label') or local_fim.get('nome') or 'Base')
                    coord_fim = (round(float(local_fim['latitude']),6),round(float(local_fim['longitude']),6))
                    coord_orig = (round(float(origem_local['latitude']),6),round(float(origem_local['longitude']),6))
                    if coord_fim != coord_orig:
                        locais_calculo.append(converter_local(local_fim,'Fim'))
                elif not fechado:
                    # Ponto final original permanece como último, caso ainda esteja pendente.
                    final = dados_base.get('locais_base') or []
                    if final:
                        end_base = final[-1]
                        for j,p in enumerate(existentes):
                            if (round(float(p['latitude']),6),round(float(p['longitude']),6)) == (
                               round(float(end_base['latitude']),6),round(float(end_base['longitude']),6)):
                                locais_calculo.append(locais_calculo.pop(j+1))
                                break
                result = calcular_rota_a_partir_de_locais(locais_calculo, False,
                          dados_base.get('objetivo','Menor distância'),
                          dados_base.get('modo','Rápido'))
                indice = {(round(float(p['latitude']),6),round(float(p['longitude']),6)):p for p in existentes}
                ordenadas=[]
                for x in result['locais_ordenados'][1:]:
                    chave=(round(float(x['latitude']),6),round(float(x['longitude']),6))
                    p=indice.get(chave)
                    if p and p not in ordenadas:
                        ordenadas.append(p)
                ids = [str(p['id']) for p in existentes]
                validar_ordem_ids(ids,[str(p['id']) for p in ordenadas])
                payload=[{'id':str(p['id']),'nome':str(p.get('nome') or p.get('label') or 'Parada'),
                         'endereco':str(p.get('endereco') or p.get('label') or ''),
                         'latitude':float(p['latitude']),'longitude':float(p['longitude']),
                         'tipo':str(p.get('tipo') or 'Parada')} for p in ordenadas]
                supabase_rpc('rotaki_reotimizar_execucao_tx', {
                    'p_user_id':usuario_id,'p_execucao_id':execucao['id'],
                    'p_paradas':payload,
                    'p_snapshot':{'origem_atual':origem_local,'resultado':result,
                       'primeira_parada_id':str(ordenadas[0]['id']),
                       'reotimizada_em':agora_iso()},
                })
                st.success('Paradas pendentes reordenadas sem alterar as concluídas.')
                st.rerun()
            except Exception as erro:
                mostrar_erro_usuario('Falha ao reotimizar execução',erro)



def renderizar_execucao_entrega_detalhe(execucao_id):
    execucao = obter_execucao_entrega(
        execucao_id
    )

    if not execucao:
        st.warning(
            "A execução selecionada não foi encontrada."
        )
        st.session_state.execucao_entrega_id = None
        return

    paradas = listar_paradas_execucao(
        execucao_id
    )

    dados_rota_execucao = execucao.get("dados_rota") or {}
    if not isinstance(dados_rota_execucao, dict):
        dados_rota_execucao = {}
    locais_originais = dados_rota_execucao.get("locais_ordenados") or []
    origem_primeiro_trecho = locais_originais[0] if locais_originais else None
    origem_por_parada = {
        parada["id"]: (
            origem_primeiro_trecho if indice == 0 else paradas[indice - 1]
        )
        for indice, parada in enumerate(paradas)
    }
    snapshot_novo = dados_rota_execucao.get('ultima_reotimizacao') or {}
    origem_nova = snapshot_novo.get('origem_atual') or {}
    primeira_pendente = next((p for p in paradas if p.get('status') == 'pendente'), None)
    if (primeira_pendente and origem_nova.get('latitude') is not None
            and str(primeira_pendente['id']) == str(snapshot_novo.get('primeira_parada_id'))):
        origem_por_parada[primeira_pendente['id']] = origem_nova

    total = len(paradas)
    concluidas = sum(
        1
        for parada in paradas
        if parada.get("status")
        in ("entregue", "nao_entregue")
    )

    entregues = sum(
        1
        for parada in paradas
        if parada.get("status") == "entregue"
    )

    nao_entregues = sum(
        1
        for parada in paradas
        if parada.get("status") == "nao_entregue"
    )

    st.subheader(
        "🚚 Execução da rota"
    )
    st.session_state.motorista_modo = st.toggle(
        "📱 Modo motorista (botões ampliados)",
        value=bool(st.session_state.motorista_modo),key=f"modo_motorista_{execucao_id}")
    if st.session_state.motorista_modo:
        st.markdown('''<style>
        div[data-testid="stButton"] button, div[data-testid="stLinkButton"] a {
            min-height: 54px; font-size: 1.04rem; font-weight: 600;
        }
        div[data-testid="stTextArea"] textarea {min-height: 100px;}
        </style>''', unsafe_allow_html=True)
        st.caption('Tela de navegação simplificada, para uso em celular estacionado.')

    st.caption(
        execucao.get("nome_rota")
        or "Rota"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Status",
        nome_status_execucao(
            execucao.get("status")
        ),
    )

    c2.metric(
        "Concluídas",
        f"{concluidas} / {total}",
    )

    c3.metric(
        "Entregues",
        entregues,
    )

    c4.metric(
        "Não entregues",
        nao_entregues,
    )

    if total > 0:
        st.progress(
            min(
                max(concluidas / total, 0.0),
                1.0,
            )
        )

    st.caption(
        "Iniciada em "
        f"{formatar_data(execucao.get('iniciada_em'))}"
    )

    if execucao.get("finalizada_em"):
        st.caption(
            "Encerrada em "
            f"{formatar_data(execucao.get('finalizada_em'))}"
        )

    em_andamento = (
        execucao.get("status") == "em_andamento"
    )

    proxima = next(
        (
            parada
            for parada in paradas
            if parada.get("status") == "pendente"
        ),
        None,
    )

    if em_andamento and proxima:
        st.markdown(
            "### 📍 Próxima parada"
        )

        with st.container(border=True):
            st.markdown(
                f"## {proxima['ordem']}. {proxima['nome']}"
            )

            if proxima.get("endereco"):
                st.write(
                    f"📍 {proxima['endereco']}"
                )
            agenda = dados_rota_execucao.get('previsoes_chegada') or []
            if not dados_rota_execucao.get('ultima_reotimizacao') and len(agenda) > int(proxima.get('ordem') or 0):
                st.caption(f"🕒 Chegada planejada: {agenda[int(proxima['ordem'])].get('chegada','—')} (estimativa)")

            st.link_button(
                "🧭 Navegar até esta parada",
                gerar_url_navegacao_destino(
                    proxima
                ),
                use_container_width=True,
            )

            st.markdown("#### 🗺️ Mapa até a próxima parada")
            mostrar_mapa_trecho_execucao(
                origem_por_parada.get(proxima["id"]),
                proxima,
                f"principal_{execucao_id}_{proxima['id']}",
            )

            observacao = st.text_area(
                "Observação da parada",
                value=(
                    proxima.get("observacao")
                    or ""
                ),
                key=(
                    "exec_obs_proxima_"
                    f"{proxima['id']}"
                ),
            )

            c1, c2 = st.columns(2)

            with c1:
                if st.button(
                    "✅ Entregue",
                    type="primary",
                    use_container_width=True,
                    key=(
                        "exec_entregue_"
                        f"{proxima['id']}"
                    ),
                ):
                    try:
                        atualizar_parada_execucao(
                            parada_id=proxima["id"],
                            execucao_id=execucao_id,
                            status="entregue",
                            observacao=observacao,
                        )
                        st.rerun()
                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha ao atualizar parada da execução",
                            e,
                        )

            with c2:
                if st.button(
                    "❌ Não entregue",
                    use_container_width=True,
                    key=(
                        "exec_nao_entregue_"
                        f"{proxima['id']}"
                    ),
                ):
                    try:
                        atualizar_parada_execucao(
                            parada_id=proxima["id"],
                            execucao_id=execucao_id,
                            status="nao_entregue",
                            observacao=observacao,
                        )
                        st.rerun()
                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha ao atualizar parada da execução",
                            e,
                        )

    st.markdown(
        "### 📋 Todas as paradas"
    )

    for parada in paradas:
        status = parada.get("status")

        titulo = (
            f"{parada['ordem']}. {parada['nome']} — "
            f"{nome_status_parada(status)}"
        )

        with st.expander(
            titulo,
            expanded=(
                proxima is not None
                and parada["id"] == proxima["id"]
            ),
        ):
            if parada.get("endereco"):
                st.write(
                    f"📍 {parada['endereco']}"
                )

            if parada.get("concluida_em"):
                st.caption(
                    "Atualizada em "
                    f"{formatar_data(parada['concluida_em'])}"
                )

            if parada.get("observacao"):
                st.write(
                    f"📝 {parada['observacao']}"
                )

            st.link_button(
                "🧭 Navegar",
                gerar_url_navegacao_destino(
                    parada
                ),
                use_container_width=True,
            )

            # O mapa de cada trecho faz parte obrigatória dos detalhes
            # da parada: não há toggle, checkbox ou botão para mostrá-lo.
            st.markdown("#### 🗺️ Mapa do trecho anterior até esta parada")
            mostrar_mapa_trecho_execucao(
                origem_por_parada.get(parada["id"]),
                parada,
                f"lista_{execucao_id}_{parada['id']}",
            )

            if em_andamento:
                obs_edicao = st.text_area(
                    "Observação",
                    value=(
                        parada.get("observacao")
                        or ""
                    ),
                    key=(
                        "exec_obs_"
                        f"{parada['id']}"
                    ),
                )

                c1, c2, c3 = st.columns(3)

                with c1:
                    if st.button(
                        "✅ Entregue",
                        key=(
                            "exec_lista_ok_"
                            f"{parada['id']}"
                        ),
                        use_container_width=True,
                    ):
                        atualizar_parada_execucao(
                            parada["id"],
                            execucao_id,
                            "entregue",
                            obs_edicao,
                        )
                        st.rerun()

                with c2:
                    if st.button(
                        "❌ Não entregue",
                        key=(
                            "exec_lista_fail_"
                            f"{parada['id']}"
                        ),
                        use_container_width=True,
                    ):
                        atualizar_parada_execucao(
                            parada["id"],
                            execucao_id,
                            "nao_entregue",
                            obs_edicao,
                        )
                        st.rerun()

                with c3:
                    if st.button(
                        "↩️ Pendente",
                        key=(
                            "exec_lista_pending_"
                            f"{parada['id']}"
                        ),
                        use_container_width=True,
                    ):
                        atualizar_parada_execucao(
                            parada["id"],
                            execucao_id,
                            "pendente",
                            obs_edicao,
                        )
                        st.rerun()

            # Foto opcional vinculada à parada já concluída.
            renderizar_comprovantes(execucao_id, parada)

    if em_andamento:
        renderizar_reotimizacao_execucao(execucao, paradas, dados_rota_execucao)
        st.divider()

        if concluidas == total and total > 0:
            if st.button(
                "🏁 Finalizar rota",
                type="primary",
                use_container_width=True,
                key=(
                    "finalizar_execucao_"
                    f"{execucao_id}"
                ),
            ):
                try:
                    finalizar_execucao_entrega(
                        execucao_id
                    )
                    st.success(
                        "Execução finalizada."
                    )
                    st.rerun()
                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha na operação",
                        e,
                    )
        else:
            pendentes = max(
                total - concluidas,
                0,
            )

            st.info(
                f"Faltam {pendentes} parada(s) para "
                "encerrar a execução."
            )

        with st.expander(
            "⚠️ Cancelar execução"
        ):
            confirmar = st.checkbox(
                "Confirmo o cancelamento desta execução",
                key=(
                    "confirmar_cancel_exec_"
                    f"{execucao_id}"
                ),
            )

            if st.button(
                "Cancelar execução",
                disabled=not confirmar,
                use_container_width=True,
                key=(
                    "cancel_exec_"
                    f"{execucao_id}"
                ),
            ):
                try:
                    cancelar_execucao_entrega(
                        execucao_id
                    )
                    st.rerun()
                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha na operação",
                        e,
                    )


def renderizar_execucoes_entrega():
    st.subheader(
        "🚚 Execuções"
    )

    st.write(
        "Acompanhe a rota em andamento e registre "
        "o resultado de cada parada."
    )

    try:
        execucoes = listar_execucoes_entrega_usuario(
            limite=100
        )
    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar dados",
            e,
        )
        return

    ativa = next(
        (
            item
            for item in execucoes
            if item.get("status") == "em_andamento"
        ),
        None,
    )

    selecionada = (
        st.session_state.execucao_entrega_id
        or (
            ativa["id"]
            if ativa
            else None
        )
    )

    if selecionada:
        renderizar_execucao_entrega_detalhe(
            selecionada
        )

        st.divider()

    st.markdown(
        "### Histórico de execuções"
    )

    if not execucoes:
        st.info(
            "Nenhuma execução de rota registrada ainda."
        )
        return

    for item in execucoes:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 2, 2])

            with c1:
                st.markdown(
                    "**"
                    + (
                        item.get("nome_rota")
                        or "Rota"
                    )
                    + "**"
                )

                st.caption(
                    formatar_data(
                        item.get("iniciada_em")
                    )
                )

            with c2:
                st.write(
                    nome_status_execucao(
                        item.get("status")
                    )
                )

            with c3:
                st.write(
                    f"{item.get('concluidas', 0)} / "
                    f"{item.get('total_paradas', 0)}"
                )

            if st.button(
                "Abrir execução",
                key=(
                    "abrir_execucao_"
                    f"{item['id']}"
                ),
                use_container_width=True,
            ):
                st.session_state.execucao_entrega_id = (
                    item["id"]
                )
                # Muda o token mesmo sem trocar a página interna.
                st.session_state.scroll_execucao_pendente += 1
                st.rerun()


# ============================================================
# PLANOS
# ============================================================

def listar_planos(
    apenas_ativos=False,
):

    params = {
        "select":
            (
                "nome,ordem,descricao,"
                "limite_rotas_mes,"
                "limite_locais_rota,"
                "permite_preciso,"
                "permite_recorrentes,"
                "historico_dias,"
                "permite_exportacoes,"
                "preco_mensal,moeda,"
                "ativo,"
                "created_at,updated_at"
            ),

        "order":
            "ordem.asc",
    }

    if apenas_ativos:

        params["ativo"] = (
            "eq.true"
        )

    return supabase_request(
        "planos",
        "GET",
        params=params,
    )


def obter_plano(
    nome,
):

    registros = supabase_request(
        "planos",
        "GET",
        params={
            "select":
                (
                    "nome,ordem,descricao,"
                    "limite_rotas_mes,"
                    "limite_locais_rota,"
                    "permite_preciso,"
                    "permite_recorrentes,"
                    "historico_dias,"
                    "permite_exportacoes,"
                    "preco_mensal,moeda,"
                    "ativo"
                ),

            "nome":
                f"eq.{nome}",

            "limit":
                "1",
        },
    )

    if registros:

        return registros[0]

    return None


# ============================================================
# USUÁRIO
# ============================================================

def obter_usuario_app(
    user_id_alvo=None,
):
    if user_id_alvo not in (None, usuario_id):
        raise PermissionError("Consulta de perfil fora do escopo do usuário.")

    registros = supabase_user_request(
        "usuarios_app",
        "GET",
        params={
            "select": (
                "user_id,nome,email,plano,ativo,"
                "limite_rotas_mes,limite_locais_rota,permite_preciso,"
                "usar_limites_personalizados,is_admin,created_at,updated_at"
            ),
            "limit": "1",
        },
    )

    return registros[0] if registros else None



def garantir_usuario_app():

    perfil = obter_usuario_app()

    if perfil is None:

        registros = supabase_user_request(
            "usuarios_app",
            "POST",
            json_body={
                "user_id":
                    usuario_id,

                "nome":
                    usuario_nome,

                "email":
                    usuario_email
                    or
                    None,

                "plano":
                    "Gratuito",

                "ativo":
                    True,

                "usar_limites_personalizados":
                    False,

                "is_admin":
                    False,
            },
            prefer="return=representation",
        )

        if not registros:

            raise RuntimeError(
                "Não foi possível criar "
                "o perfil do usuário."
            )

        return registros[0]

    alteracoes = {}

    if (
        perfil.get("nome")
        or
        ""
    ) != usuario_nome:

        alteracoes["nome"] = (
            usuario_nome
        )

    if (
        perfil.get("email")
        or
        ""
    ) != usuario_email:

        alteracoes["email"] = (
            usuario_email
            or
            None
        )

    if alteracoes:

        alteracoes["updated_at"] = (
            agora_iso()
        )

        registros = supabase_user_request(
            "usuarios_app",
            "PATCH",
            params={
                "user_id":
                    f"eq.{usuario_id}",
            },
            json_body=alteracoes,
            prefer="return=representation",
        )

        if registros:

            perfil = registros[0]

    return perfil


def perfil_efetivo(
    perfil,
):
    if not perfil:
        return None

    nome_plano = perfil.get("plano") or "Gratuito"
    plano = obter_plano(nome_plano)

    if not plano:
        raise RuntimeError(f"O plano '{nome_plano}' não foi encontrado.")

    try:
        return resolver_perfil_efetivo(perfil, plano)
    except ValueError as e:
        raise RuntimeError(str(e)) from e



def intervalo_mes_atual():

    agora = datetime.now(
        ZoneInfo(
            "America/Sao_Paulo"
        )
    )

    inicio = agora.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    if inicio.month == 12:

        fim = inicio.replace(
            year=(
                inicio.year
                + 1
            ),
            month=1,
        )

    else:

        fim = inicio.replace(
            month=(
                inicio.month
                + 1
            ),
        )

    return inicio, fim


def proximo_ciclo_texto():

    _, fim = intervalo_mes_atual()

    return fim.strftime(
        "%d/%m/%Y"
    )


def contar_execucoes_mes_usuario(
    user_id_alvo,
):
    if user_id_alvo != usuario_id:
        exigir_admin_atual()

    resultado = supabase_rpc(
        "contar_uso_mes",
        {"p_user_id": user_id_alvo},
    )

    if isinstance(resultado, int):
        return int(resultado)

    if isinstance(resultado, list):
        if not resultado:
            return 0
        primeiro = resultado[0]
        if isinstance(primeiro, dict):
            for chave in ("contar_uso_mes", "count", "uso"):
                if chave in primeiro:
                    return int(primeiro[chave] or 0)
        return int(primeiro or 0)

    if isinstance(resultado, dict):
        for chave in ("contar_uso_mes", "count", "uso"):
            if chave in resultado:
                return int(resultado[chave] or 0)

    return int(resultado or 0)


def resumo_uso_mes_admin():
    exigir_admin_atual()
    registros = supabase_rpc("resumo_uso_mes", {})
    return {
        item["user_id"]: int(item.get("uso") or 0)
        for item in (registros or [])
    }


def listar_assinaturas_admin():
    exigir_admin_atual()
    resultado = supabase_rpc(
        "resumo_assinaturas_admin",
        {},
    )
    return resultado or []




def contar_execucoes_mes_atual():

    return contar_execucoes_mes_usuario(
        usuario_id
    )


perfil_usuario_app = None
perfil_usuario_efetivo = None
uso_rotas_mes = None
erro_perfil_usuario = None


if supabase_configurado:

    try:

        perfil_usuario_app = (
            garantir_usuario_app()
        )

        perfil_usuario_efetivo = (
            perfil_efetivo(
                perfil_usuario_app
            )
        )

        uso_rotas_mes = (
            contar_execucoes_mes_atual()
        )

    except Exception as e:

        erro_perfil_usuario = str(e)


usuario_e_admin = bool(
    perfil_usuario_efetivo
    and
    perfil_usuario_efetivo.get(
        "is_admin",
        False,
    )
)


# ============================================================
# CONTA INATIVA
# ============================================================

if (
    perfil_usuario_efetivo
    and
    not perfil_usuario_efetivo.get(
        "ativo",
        True,
    )
):

    st.title(
        "🗺️ Otimizador de Rotas"
    )

    st.error(
        "⛔ Sua conta está inativa."
    )

    st.write(
        "O acesso ao Otimizador de Rotas "
        "está temporariamente desabilitado."
    )

    if usuario_email:

        st.caption(
            f"Conta conectada: {usuario_email}"
        )

    st.button(
        "🚪 Sair",
        on_click=st.logout,
    )

    st.stop()


# ============================================================
# PERMISSÕES
# ============================================================

def limite_locais_usuario():

    if not perfil_usuario_efetivo:
        return 0

    return int(
        perfil_usuario_efetivo.get(
            "limite_locais_rota"
        )
        or
        0
    )


def permite_preciso_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_preciso",
            False,
        )
    )


def permite_recorrentes_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_recorrentes",
            False,
        )
    )


def permite_exportacoes_usuario():

    return bool(
        perfil_usuario_efetivo
        and
        perfil_usuario_efetivo.get(
            "permite_exportacoes",
            False,
        )
    )


def historico_dias_usuario():

    if not perfil_usuario_efetivo:
        return 7

    return perfil_usuario_efetivo.get(
        "historico_dias"
    )


def opcoes_modo_usuario():

    if permite_preciso_usuario():

        return [
            "Rápido",
            "Preciso",
        ]

    return [
        "Rápido",
    ]


def validar_quantidade_plano(
    quantidade_locais,
):

    if not perfil_usuario_efetivo:

        raise LimiteUsoError(
            "Não foi possível validar "
            "as regras da sua conta."
        )

    limite = int(
        perfil_usuario_efetivo.get(
            "limite_locais_rota"
        )
        or
        0
    )

    if (
        limite > 0
        and
        quantidade_locais > limite
    ):

        raise LimiteUsoError(
            f"Esta rota possui {quantidade_locais} locais, "
            f"mas seu plano permite até {limite}."
        )


def validar_permissao_calculo(
    quantidade_locais,
    modo,
):
    if not supabase_configurado:
        raise LimiteUsoError(
            "O controle de uso está indisponível. Nenhum cálculo foi iniciado."
        )

    try:
        perfil_atual = obter_usuario_app()
        efetivo = perfil_efetivo(perfil_atual)
        validar_regras_plano(
            efetivo,
            quantidade_locais,
            modo,
            uso_atual=None,
        )
    except ValueError as e:
        mapa = {
            "perfil_nao_encontrado": "Seu perfil não foi encontrado.",
            "conta_inativa": "Sua conta está inativa.",
            "limite_locais": (
                f"Seu plano permite até {limite_locais_usuario()} locais por rota."
            ),
            "preciso_indisponivel": "O modo Preciso não está disponível no seu plano.",
        }
        raise LimiteUsoError(mapa.get(str(e), "Não foi possível validar seu plano.")) from e
    except Exception as e:
        logger.error(
            "Falha ao validar plano antes da reserva: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        raise LimiteUsoError(
            "Não foi possível validar seu plano. Nenhum cálculo foi iniciado."
        ) from e


def reservar_calculo_atomico(
    quantidade_locais,
    objetivo,
    modo,
    voltar_inicio,
):
    try:
        resultado = supabase_rpc(
            "reservar_calculo",
            {
                "p_user_id": usuario_id,
                "p_quantidade_locais": int(quantidade_locais),
                "p_objetivo": objetivo,
                "p_modo": modo,
                "p_voltar_inicio": bool(voltar_inicio),
            },
        )
    except Exception as e:
        texto = str(e)
        mensagens = {
            "quota_exceeded": (
                "Você atingiu o limite mensal de cálculos do seu plano. "
                f"Seu próximo ciclo começa em {proximo_ciclo_texto()}."
            ),
            "location_limit_exceeded": (
                f"Seu plano permite até {limite_locais_usuario()} locais por rota."
            ),
            "precise_not_allowed": "O modo Preciso não está disponível no seu plano.",
            "user_inactive": "Sua conta está inativa.",
            "user_not_found": "Seu perfil não foi encontrado.",
            "plan_not_available": "Seu plano está indisponível no momento.",
        }
        for codigo, mensagem in mensagens.items():
            if codigo in texto:
                raise LimiteUsoError(mensagem) from e

        logger.error(
            "Falha ao reservar cálculo: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        raise LimiteUsoError(
            "Não foi possível reservar seu cálculo. Tente novamente."
        ) from e

    if not resultado:
        raise LimiteUsoError("Não foi possível reservar seu cálculo. Tente novamente.")

    registro = resultado[0] if isinstance(resultado, list) else resultado
    return registro


def cancelar_reserva_calculo(execucao_id):
    if not execucao_id:
        return

    try:
        supabase_rpc(
            "cancelar_reserva_calculo",
            {
                "p_execucao_id": execucao_id,
                "p_user_id": usuario_id,
            },
        )
    except Exception:
        logger.exception("Falha ao liberar reserva de cálculo %s", execucao_id)



# ============================================================
# MENU
# ============================================================

def botao_menu(
    label,
    pagina,
    ativo=False,
):

    if st.sidebar.button(
        label,
        use_container_width=True,
        type=(
            "primary"
            if ativo
            else
            "secondary"
        ),
        key=(
            f"menu_{pagina}"
        ),
    ):

        if pagina == "nova":

            iniciar_nova_rota()

        else:

            st.session_state.pagina_app = (
                pagina
            )

        st.rerun()


with st.sidebar:

    st.markdown(
        "## 🗺️ Menu"
    )

    st.write(
        f"👤 **{usuario_nome}**"
    )

    if usuario_email:

        st.caption(
            usuario_email
        )

    st.divider()


botao_menu(
    "➕ Nova rota",
    "nova",
    (
        st.session_state.pagina_app
        == "nova"
    ),
)

botao_menu(
    "📂 Minhas rotas",
    "minhas",
    (
        st.session_state.pagina_app
        in (
            "minhas",
            "editar",
        )
    ),
)

botao_menu(
    "📍 Locais",
    "locais",
    (
        st.session_state.pagina_app
        == "locais"
    ),
)

botao_menu(
    "🔁 Recorrentes",
    "recorrentes",
    (
        st.session_state.pagina_app
        == "recorrentes"
    ),
)

botao_menu(
    "📊 Histórico",
    "historico",
    (
        st.session_state.pagina_app
        == "historico"
    ),
)


botao_menu(
    "🚚 Execuções",
    "execucoes",
    (
        st.session_state.pagina_app
        == "execucoes"
    ),
)

botao_menu(
    "💎 Planos",
    "planos",
    (
        st.session_state.pagina_app
        == "planos"
    ),
)


if usuario_e_admin:

    with st.sidebar:

        st.divider()

        st.caption(
            "Administração"
        )

    botao_menu(
        "🛠️ Administração",
        "administracao",
        (
            st.session_state.pagina_app
            == "administracao"
        ),
    )


with st.sidebar:

    st.divider()

    st.markdown(
        "### 📦 Plano e uso"
    )

    if perfil_usuario_efetivo:

        plano = (
            perfil_usuario_efetivo[
                "plano"
            ]
        )

        limite_mes = int(
            perfil_usuario_efetivo.get(
                "limite_rotas_mes"
            )
            or
            0
        )

        limite_locais = int(
            perfil_usuario_efetivo.get(
                "limite_locais_rota"
            )
            or
            0
        )

        uso = int(
            uso_rotas_mes
            or
            0
        )

        st.write(
            f"**Plano:** {plano}"
        )

        if usuario_e_admin:

            st.caption(
                "🛡️ Administrador"
            )

        if perfil_usuario_efetivo.get(
            "usar_limites_personalizados"
        ):

            st.caption(
                "⚙️ Regras individuais ativas"
            )

        if limite_mes > 0:

            percentual = min(
                max(
                    uso / limite_mes,
                    0.0,
                ),
                1.0,
            )

            st.caption(
                f"Rotas este mês: "
                f"**{uso} / {limite_mes}**"
            )

            st.progress(
                percentual
            )

            if uso >= limite_mes:

                st.warning(
                    "⚠️ Limite mensal atingido."
                )

                st.caption(
                    "Novo ciclo em "
                    f"**{proximo_ciclo_texto()}**."
                )

            elif percentual >= 0.8:

                restantes = max(
                    limite_mes
                    -
                    uso,
                    0,
                )

                st.warning(
                    f"Restam {restantes} cálculo(s)."
                )

        else:

            st.caption(
                f"Rotas este mês: "
                f"**{uso} / ilimitadas**"
            )

        st.caption(
            "Limite: "
            f"**{limite_locais} locais/rota**"
        )

        st.caption(
            "Modo Preciso: "
            +
            (
                "**habilitado**"
                if permite_preciso_usuario()
                else
                "**não disponível**"
            )
        )

    elif erro_perfil_usuario:

        st.error(
            "Não foi possível validar "
            "os dados do plano."
        )

    st.divider()

    st.button(
        "🚪 Sair",
        on_click=st.logout,
        use_container_width=True,
    )


# ============================================================
# CABEÇALHO
# ============================================================

st.title(
    "🗺️ Otimizador de Rotas"
)

mostrar_mensagem_sistema()


# ============================================================
# ROTAS SALVAS
# ============================================================

def listar_rotas_usuario():

    return supabase_user_request(
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

    registros = supabase_user_request(
        "rotas",
        "GET",
        params={
            "select":
                "id",

            "user_id":
                f"eq.{usuario_id}",
        },
    )

    return len(registros)


def salvar_rota_banco(
    nome,
    dados,
):

    nome = (
        nome
        or
        ""
    ).strip()

    if not nome:

        raise ValueError(
            "Informe um nome para a rota."
        )

    resultado = supabase_user_request(
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

    return resultado[0]


def atualizar_rota_banco(
    rota_id,
    dados,
):

    resultado = supabase_user_request(
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
            "A rota não foi encontrada."
        )

    return resultado[0]


def atualizar_rota_completa_banco(
    rota_id,
    nome,
    dados,
):

    resultado = supabase_user_request(
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
                nome.strip(),

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

    return resultado[0]


def renomear_rota_banco(
    rota_id,
    novo_nome,
):

    novo_nome = (
        novo_nome
        or
        ""
    ).strip()

    if not novo_nome:

        raise ValueError(
            "Informe o novo nome."
        )

    resultado = supabase_user_request(
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

    return resultado[0]


def excluir_rota_banco(
    rota_id,
):

    resultado = supabase_user_request(
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


# ============================================================
# EXECUÇÃO DE ENTREGAS
# ============================================================

def _mesma_coordenada(local_a, local_b):
    from app_logic import mesma_coordenada
    return mesma_coordenada(local_a, local_b)



def locais_para_execucao(dados):
    return locais_para_execucao_logic(dados)



def obter_execucao_entrega(execucao_id):
    registros = supabase_user_request(
        "execucoes_entrega",
        "GET",
        params={
            "select":
                (
                    "id,user_id,rota_id,nome_rota,status,"
                    "total_paradas,concluidas,iniciada_em,"
                    "finalizada_em,dados_rota,created_at,updated_at"
                ),
            "id":
                f"eq.{execucao_id}",
            "user_id":
                f"eq.{usuario_id}",
            "limit":
                "1",
        },
    )

    if registros:
        return registros[0]

    return None


def listar_execucoes_entrega_usuario(limite=100):
    return supabase_user_request(
        "execucoes_entrega",
        "GET",
        params={
            "select":
                (
                    "id,user_id,rota_id,nome_rota,status,"
                    "total_paradas,concluidas,iniciada_em,"
                    "finalizada_em,created_at,updated_at"
                ),
            "user_id":
                f"eq.{usuario_id}",
            "order":
                "iniciada_em.desc",
            "limit":
                str(limite),
        },
    )


def obter_execucao_em_andamento_usuario():
    registros = supabase_user_request(
        "execucoes_entrega",
        "GET",
        params={
            "select":
                (
                    "id,user_id,rota_id,nome_rota,status,"
                    "total_paradas,concluidas,iniciada_em,"
                    "finalizada_em,created_at,updated_at"
                ),
            "user_id":
                f"eq.{usuario_id}",
            "status":
                "eq.em_andamento",
            "order":
                "iniciada_em.desc",
            "limit":
                "1",
        },
    )

    if registros:
        return registros[0]

    return None


def listar_paradas_execucao(execucao_id):
    return supabase_user_request(
        "execucoes_paradas",
        "GET",
        params={
            "select":
                (
                    "id,execucao_id,user_id,ordem,tipo,nome,endereco,"
                    "latitude,longitude,status,concluida_em,observacao,"
                    "created_at,updated_at"
                ),
            "execucao_id":
                f"eq.{execucao_id}",
            "user_id":
                f"eq.{usuario_id}",
            "order":
                "ordem.asc",
        },
    )


def iniciar_execucao_entrega(
    dados,
    rota_id=None,
    nome_rota=None,
):
    paradas = locais_para_execucao(dados)

    if not paradas:
        raise ValueError("A rota não possui paradas para executar.")

    payload_paradas = []
    for ordem, local in enumerate(paradas, start=1):
        payload_paradas.append(
            {
                "tipo": local.get("tipo"),
                "nome": str(
                    local.get("nome")
                    or local.get("label")
                    or f"Parada {ordem}"
                ),
                "endereco": montar_referencia_google(local),
                "latitude": float(local["latitude"]),
                "longitude": float(local["longitude"]),
                "observacao": local.get("observacao"),
            }
        )

    try:
        resultado = supabase_rpc(
            "iniciar_execucao_entrega_tx",
            {
                "p_user_id": usuario_id,
                "p_rota_id": rota_id,
                "p_nome_rota": nome_rota,
                "p_dados_rota": preparar_json(dados),
                "p_paradas": preparar_json(payload_paradas),
            },
        )
    except Exception as e:
        if "delivery_already_active" in str(e):
            raise OperacaoUsuarioError(
                "Você já possui uma rota em andamento. "
                "Finalize ou cancele essa execução antes de iniciar outra."
            ) from e
        logger.error(
            "Falha transacional ao iniciar execução: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        raise OperacaoUsuarioError("Não foi possível iniciar a execução da rota.") from e

    if not resultado:
        raise OperacaoUsuarioError("Não foi possível iniciar a execução da rota.")

    return resultado[0] if isinstance(resultado, list) else resultado





def atualizar_parada_execucao(
    parada_id,
    execucao_id,
    status,
    observacao="",
):
    status = str(status or "").strip().lower()

    if status not in ("pendente", "entregue", "nao_entregue"):
        raise ValueError("Status da parada inválido.")

    try:
        resultado = supabase_rpc(
            "atualizar_parada_execucao_tx",
            {
                "p_user_id": usuario_id,
                "p_execucao_id": execucao_id,
                "p_parada_id": parada_id,
                "p_status": status,
                "p_observacao": (observacao or "").strip() or None,
            },
        )
    except Exception as e:
        texto = str(e)
        if "delivery_not_active" in texto:
            raise OperacaoUsuarioError("A execução não está mais em andamento.") from e
        if "delivery_not_found" in texto:
            raise OperacaoUsuarioError("A execução não foi encontrada.") from e
        if "stop_not_found" in texto:
            raise OperacaoUsuarioError("A parada não foi encontrada.") from e
        logger.error(
            "Falha transacional ao atualizar parada: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        raise OperacaoUsuarioError("Não foi possível atualizar a parada.") from e

    if not resultado:
        raise OperacaoUsuarioError("A parada não foi encontrada.")

    return resultado[0] if isinstance(resultado, list) else resultado



def finalizar_execucao_entrega(execucao_id):
    paradas = listar_paradas_execucao(
        execucao_id
    )

    pendentes = [
        parada
        for parada in paradas
        if parada.get("status") == "pendente"
    ]

    if pendentes:
        raise OperacaoUsuarioError(
            "Ainda existem paradas pendentes. "
            "Marque todas como Entregue ou Não entregue antes de finalizar."
        )

    resultado = supabase_user_request(
        "execucoes_entrega",
        "PATCH",
        params={
            "id":
                f"eq.{execucao_id}",
            "user_id":
                f"eq.{usuario_id}",
            "status":
                "eq.em_andamento",
        },
        json_body={
            "status":
                "finalizada",
            "concluidas":
                len(paradas),
            "finalizada_em":
                agora_iso(),
            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:
        raise OperacaoUsuarioError(
            "A execução não está mais em andamento."
        )

    return resultado[0]


def cancelar_execucao_entrega(execucao_id):
    resultado = supabase_user_request(
        "execucoes_entrega",
        "PATCH",
        params={
            "id":
                f"eq.{execucao_id}",
            "user_id":
                f"eq.{usuario_id}",
            "status":
                "eq.em_andamento",
        },
        json_body={
            "status":
                "cancelada",
            "finalizada_em":
                agora_iso(),
            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:
        raise OperacaoUsuarioError(
            "A execução não está mais em andamento."
        )

    return resultado[0]


# ============================================================
# HISTÓRICO
# ============================================================

def registrar_execucao(
    dados,
    rota_id=None,
    nome_rota=None,
):
    reserva_id = dados.get("_reserva_calculo_id")

    if not reserva_id:
        raise RuntimeError("Cálculo sem reserva de uso associada.")

    dados_salvar = dict(dados)
    dados_salvar.pop("_reserva_calculo_id", None)

    locais_base = dados_salvar.get("locais_base") or []

    resultado = supabase_rpc(
        "concluir_calculo",
        {
            "p_execucao_id": reserva_id,
            "p_user_id": usuario_id,
            "p_rota_id": rota_id,
            "p_nome_rota": nome_rota,
            "p_quantidade_locais": len(locais_base),
            "p_distancia_m": float(dados_salvar.get("distancia_m", 0)),
            "p_duracao_s": float(dados_salvar.get("duracao_s", 0)),
            "p_objetivo": dados_salvar.get("objetivo", "Menor distância"),
            "p_modo": dados_salvar.get("modo", "Rápido"),
            "p_voltar_inicio": bool(dados_salvar.get("voltar_inicio", False)),
            "p_dados": preparar_json(dados_salvar),
        },
    )

    if not resultado:
        raise RuntimeError("Não foi possível concluir o registro da execução.")

    return resultado[0] if isinstance(resultado, list) else resultado



def registrar_execucao_seguro(
    dados,
    rota_id=None,
    nome_rota=None,
):
    try:
        return registrar_execucao(dados, rota_id, nome_rota), None
    except Exception as e:
        logger.error(
            "Falha ao concluir registro de uso: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        return None, (
            "Não foi possível concluir o registro do cálculo. "
            "A reserva continua protegendo sua cota temporariamente."
        )



def vincular_execucao_rota(
    execucao_id,
    rota_id,
    nome_rota,
):

    if not execucao_id:
        return

    supabase_user_request(
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

        "status":
            "eq.concluido",

        "order":
            "created_at.desc",

        "limit":
            str(limite),
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
                days=int(dias)
            )
        )

        params["created_at"] = (
            f"gte.{data_limite.isoformat()}"
        )

    return supabase_user_request(
        "execucoes_rotas",
        "GET",
        params=params,
    )


# ============================================================
# LOCAIS SALVOS
# ============================================================

def listar_locais_salvos_usuario():

    return supabase_user_request(
        "locais_salvos",
        "GET",
        params={
            "select":
                (
                    "id,nome,endereco,"
                    "latitude,longitude,"
                    "observacao,dados,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "nome.asc",
        },
    )


def contar_locais_salvos_usuario():

    registros = supabase_user_request(
        "locais_salvos",
        "GET",
        params={
            "select":
                "id",

            "user_id":
                f"eq.{usuario_id}",
        },
    )

    return len(registros)


def nome_padrao_local(
    origem,
):

    return (
        origem.get("name")
        or
        origem.get("label")
        or
        origem.get("nome")
        or
        "Local"
    )


def salvar_local_banco(
    nome,
    origem,
    observacao="",
):

    nome = (
        nome
        or
        ""
    ).strip()

    if not nome:

        raise ValueError(
            "Informe um nome para o local."
        )

    if not origem:

        raise ValueError(
            "Selecione um endereço."
        )

    endereco = (
        origem.get("label")
        or
        origem.get("endereco")
        or
        origem.get("nome")
        or
        nome
    )

    resultado = supabase_user_request(
        "locais_salvos",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome,

            "endereco":
                str(
                    endereco
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

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),

            "dados":
                preparar_json(
                    origem
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Não foi possível salvar o local."
        )

    return resultado[0]


def atualizar_local_banco(
    local_id,
    nome,
    observacao,
    nova_origem=None,
):

    payload = {
        "nome":
            (
                nome
                or
                ""
            ).strip(),

        "observacao":
            (
                (
                    observacao
                    or
                    ""
                ).strip()
                or
                None
            ),

        "updated_at":
            agora_iso(),
    }

    if nova_origem:

        payload.update(
            {
                "endereco":
                    str(
                        nova_origem.get(
                            "label"
                        )
                        or
                        nova_origem.get(
                            "endereco"
                        )
                        or
                        nome
                    ),

                "latitude":
                    float(
                        nova_origem[
                            "latitude"
                        ]
                    ),

                "longitude":
                    float(
                        nova_origem[
                            "longitude"
                        ]
                    ),

                "dados":
                    preparar_json(
                        nova_origem
                    ),
            }
        )

    resultado = supabase_user_request(
        "locais_salvos",
        "PATCH",
        params={
            "id":
                f"eq.{local_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body=payload,
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "O local não foi encontrado."
        )

    return resultado[0]


def excluir_local_banco(
    local_id,
):

    resultado = supabase_user_request(
        "locais_salvos",
        "DELETE",
        params={
            "id":
                f"eq.{local_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "O local não foi encontrado."
        )


def salvar_locais_em_lote(
    origens,
):

    existentes = (
        listar_locais_salvos_usuario()
    )

    coordenadas_existentes = {
        (
            round(
                float(
                    item[
                        "latitude"
                    ]
                ),
                6,
            ),

            round(
                float(
                    item[
                        "longitude"
                    ]
                ),
                6,
            ),
        )

        for item
        in existentes
    }

    novos = []
    ignorados = 0

    for origem in origens:

        chave = (
            round(
                float(
                    origem[
                        "latitude"
                    ]
                ),
                6,
            ),

            round(
                float(
                    origem[
                        "longitude"
                    ]
                ),
                6,
            ),
        )

        if chave in coordenadas_existentes:

            ignorados += 1
            continue

        nome = nome_padrao_local(
            origem
        )

        novos.append(
            {
                "user_id":
                    usuario_id,

                "nome":
                    str(
                        nome
                    ),

                "endereco":
                    str(
                        origem.get(
                            "label"
                        )
                        or
                        nome
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

                "observacao":
                    None,

                "dados":
                    preparar_json(
                        origem
                    ),
            }
        )

        coordenadas_existentes.add(
            chave
        )

    if not novos:

        return 0, ignorados

    resultado = supabase_user_request(
        "locais_salvos",
        "POST",
        json_body=novos,
        prefer="return=representation",
    )

    return (
        len(
            resultado
        ),
        ignorados,
    )


def local_salvo_para_origem(
    registro,
):

    dados = (
        registro.get(
            "dados"
        )
        or
        {}
    )

    return {
        "label":
            (
                registro.get(
                    "nome"
                )
                or
                registro.get(
                    "endereco"
                )
                or
                "Local"
            ),

        "latitude":
            float(
                registro[
                    "latitude"
                ]
            ),

        "longitude":
            float(
                registro[
                    "longitude"
                ]
            ),

        "name":
            dados.get("name"),

        "street":
            dados.get("street"),

        "housenumber":
            dados.get(
                "housenumber"
            ),

        "locality":
            dados.get(
                "locality"
            ),

        "region":
            dados.get(
                "region"
            ),

        "country":
            dados.get(
                "country"
            ),

        "layer":
            dados.get(
                "layer"
            ),

        "endereco_salvo":
            registro.get(
                "endereco"
            ),

        "local_salvo_id":
            registro.get(
                "id"
            ),

        "observacao":
            registro.get(
                "observacao"
            ),
    }


# ============================================================
# RECORRENTES
# ============================================================

def listar_recorrentes_usuario():

    return supabase_user_request(
        "rotas_recorrentes",
        "GET",
        params={
            "select":
                (
                    "id,nome,local_ids,"
                    "inicio_local_id,"
                    "fim_local_id,"
                    "voltar_inicio,"
                    "objetivo,modo,"
                    "observacao,"
                    "created_at,updated_at"
                ),

            "user_id":
                f"eq.{usuario_id}",

            "order":
                "updated_at.desc",
        },
    )


def validar_acesso_recorrentes():

    if not permite_recorrentes_usuario():

        raise LimiteUsoError(
            "Rotas recorrentes são um "
            "recurso do plano Pro."
        )


def salvar_recorrente_banco(
    nome,
    local_ids,
    inicio_local_id,
    fim_local_id,
    voltar_inicio,
    objetivo,
    modo,
    observacao="",
):

    validar_acesso_recorrentes()

    validar_quantidade_plano(
        len(
            local_ids
        )
    )

    if len(
        local_ids
    ) < 2:

        raise ValueError(
            "Selecione pelo menos dois locais."
        )

    if inicio_local_id not in local_ids:

        raise ValueError(
            "Ponto inicial inválido."
        )

    if (
        not voltar_inicio
        and
        (
            not fim_local_id
            or
            fim_local_id not in local_ids
            or
            fim_local_id == inicio_local_id
        )
    ):

        raise ValueError(
            "Selecione um ponto final válido."
        )

    if (
        modo == "Preciso"
        and
        not permite_preciso_usuario()
    ):

        raise LimiteUsoError(
            "O modo Preciso não está "
            "disponível no seu plano."
        )

    resultado = supabase_user_request(
        "rotas_recorrentes",
        "POST",
        json_body={
            "user_id":
                usuario_id,

            "nome":
                nome.strip(),

            "local_ids":
                preparar_json(
                    local_ids
                ),

            "inicio_local_id":
                inicio_local_id,

            "fim_local_id":
                (
                    None
                    if voltar_inicio
                    else
                    fim_local_id
                ),

            "voltar_inicio":
                bool(
                    voltar_inicio
                ),

            "objetivo":
                objetivo,

            "modo":
                modo,

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "Não foi possível salvar "
            "a rota recorrente."
        )

    return resultado[0]


def atualizar_recorrente_banco(
    recorrente_id,
    nome,
    local_ids,
    inicio_local_id,
    fim_local_id,
    voltar_inicio,
    objetivo,
    modo,
    observacao="",
):

    validar_acesso_recorrentes()

    validar_quantidade_plano(
        len(
            local_ids
        )
    )

    resultado = supabase_user_request(
        "rotas_recorrentes",
        "PATCH",
        params={
            "id":
                f"eq.{recorrente_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        json_body={
            "nome":
                nome.strip(),

            "local_ids":
                preparar_json(
                    local_ids
                ),

            "inicio_local_id":
                inicio_local_id,

            "fim_local_id":
                (
                    None
                    if voltar_inicio
                    else
                    fim_local_id
                ),

            "voltar_inicio":
                bool(
                    voltar_inicio
                ),

            "objetivo":
                objetivo,

            "modo":
                modo,

            "observacao":
                (
                    (
                        observacao
                        or
                        ""
                    ).strip()
                    or
                    None
                ),

            "updated_at":
                agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota recorrente "
            "não foi encontrada."
        )

    return resultado[0]


def duplicar_recorrente_banco(
    recorrente,
):

    return salvar_recorrente_banco(
        nome=(
            f"{recorrente['nome']} - cópia"
        ),

        local_ids=(
            recorrente.get(
                "local_ids"
            )
            or
            []
        ),

        inicio_local_id=(
            recorrente.get(
                "inicio_local_id"
            )
        ),

        fim_local_id=(
            recorrente.get(
                "fim_local_id"
            )
        ),

        voltar_inicio=bool(
            recorrente.get(
                "voltar_inicio",
                True,
            )
        ),

        objetivo=(
            recorrente.get(
                "objetivo",
                "Menor tempo",
            )
        ),

        modo=(
            recorrente.get(
                "modo",
                "Rápido",
            )
        ),

        observacao=(
            recorrente.get(
                "observacao"
            )
            or
            ""
        ),
    )


def excluir_recorrente_banco(
    recorrente_id,
):

    validar_acesso_recorrentes()

    resultado = supabase_user_request(
        "rotas_recorrentes",
        "DELETE",
        params={
            "id":
                f"eq.{recorrente_id}",

            "user_id":
                f"eq.{usuario_id}",
        },
        prefer="return=representation",
    )

    if not resultado:

        raise RuntimeError(
            "A rota recorrente "
            "não foi encontrada."
        )


# ============================================================
# ORS / AUTOCOMPLETE
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def buscar_sugestoes_cache(texto, focus_lat, focus_lon, cidade_referencia, _api_key):
    """Responde com uma consulta por digitação, mantendo busca precisa.

    - Texto parcial: /autocomplete (otimizado para respostas rápidas).
    - Rua com número: /search (mais adequado a endereço completo).
    - Só recorre ao segundo endpoint se não houver resultados ou houver
      indisponibilidade do primeiro. Evita consultas sequenciais desnecessárias.
    """
    coordenada = interpretar_texto_coordenadas(texto)
    if coordenada:
        return [coordenada]

    try:
        principal = str(st.secrets.get('GEOCODER_PRINCIPAL','pelias')).lower()
        altkey = str(st.secrets.get('GEOAPIFY_KEY','')).strip()
        if altkey and principal == 'geoapify':
            alternativos = geoapify_buscar(texto, altkey,
                autocomplete=not bool(re.search(r'(?<!\w)\d{1,6}(?!\w)', texto)),
                limite=12, lat=focus_lat, lon=focus_lon)
            if alternativos:
                return ordenar_resultados_enderecos(alternativos,texto,cidade_referencia)[:12]
    except Exception as erro_geocoder:
        logger.warning('Geocodificador principal Geoapify falhou: %s', erro_geocoder)
    cidade = str(cidade_referencia or "").strip()
    consulta = texto
    if cidade and normalizar_texto(cidade.split(",")[0]) not in normalizar_texto(texto):
        consulta = f"{texto}, {cidade}"

    endereco_com_numero = (
        len(texto) >= 10
        and bool(re.search(r"(?<!\w)\d{1,6}(?!\w)", texto))
    )
    comum = dict(
        api_key=_api_key,
        limite=12,
        focus_lat=focus_lat,
        focus_lon=focus_lon,
    )
    inicio = time.perf_counter()
    endpoint = "search" if endereco_com_numero else "autocomplete"

    try:
        if endereco_com_numero:
            sugestoes = buscar_enderecos(texto=consulta, **comum)
        else:
            sugestoes = autocomplete_enderecos(texto=texto, **comum)
    except ORSError:
        # Fallback único apenas em indisponibilidade, sem repetir a mesma API.
        logger.warning("Busca %s falhou; tentando endpoint alternativo", endpoint)
        endpoint = "autocomplete" if endereco_com_numero else "search"
        try:
            if endereco_com_numero:
                sugestoes = autocomplete_enderecos(texto=texto, **comum)
            else:
                sugestoes = buscar_enderecos(texto=consulta, **comum)
        except ORSError:
            # A busca alternativa Geoapify ainda poderá responder.
            sugestoes = []
        fallback_ja_executado = True
    else:
        fallback_ja_executado = False

    if not sugestoes and len(texto) >= 8 and not fallback_ja_executado:
        # Endereço difícil: segunda estratégia só quando a primeira não achou nada.
        try:
            if endereco_com_numero:
                sugestoes = autocomplete_enderecos(texto=texto, **comum)
            else:
                sugestoes = buscar_enderecos(texto=consulta, **comum)
            endpoint += "+fallback"
        except ORSError:
            logger.info("Fallback de geocodificação indisponível")

    try:
        chave_alt = str(st.secrets.get("GEOAPIFY_KEY", "")).strip()
        preferencia = str(st.secrets.get("GEOCODER_PRINCIPAL", "pelias")).lower()
        if chave_alt and (not sugestoes or preferencia == "geoapify"):
            alternativos = geoapify_buscar(texto, chave_alt,
                autocomplete=not endereco_com_numero, limite=12,
                lat=focus_lat, lon=focus_lon)
            if alternativos:
                sugestoes = (alternativos + sugestoes) if preferencia == "geoapify" else alternativos
    except Exception as falha_alt:
        logger.warning("Geoapify indisponível; usando resultado Pelias: %s", falha_alt)
    ordenados = ordenar_resultados_enderecos(sugestoes, texto, cidade_referencia)[:15]
    logger.debug(
        "Pelias endpoint=%s chars=%d resultados=%d duracao_ms=%.0f",
        endpoint, len(texto), len(ordenados),
        (time.perf_counter() - inicio) * 1000,
    )
    return ordenados


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_endereco_importado_cache_v2(
    texto, _api_key, cidade_referencia="", cache_version="import-search-v3",
):
    """CSV/XLSX: prioriza busca de endereço completo, com contexto opcional."""
    coordenada = interpretar_texto_coordenadas(texto)
    if coordenada:
        return [coordenada]
    cidade = str(cidade_referencia or "").strip()
    consulta = texto
    if cidade and normalizar_texto(cidade.split(",")[0]) not in normalizar_texto(texto):
        consulta = f"{texto}, {cidade}"
    try:
        if st.secrets.get('GEOAPIFY_KEY') and str(st.secrets.get('GEOCODER_PRINCIPAL','pelias')).lower() == 'geoapify':
            candidatos = geoapify_buscar(consulta, str(st.secrets['GEOAPIFY_KEY']),autocomplete=False,limite=12)
        else:
            candidatos = buscar_enderecos(texto=consulta, api_key=_api_key, limite=12)
    except Exception:
        candidatos = []
    if not candidatos and st.secrets.get('GEOAPIFY_KEY'):
        try:
            candidatos = geoapify_buscar(consulta, str(st.secrets['GEOAPIFY_KEY']),autocomplete=False,limite=12)
        except Exception:
            pass
    if consulta != texto and len(candidatos) < 3:
        candidatos += buscar_enderecos(texto=texto, api_key=_api_key, limite=8)
    return ordenar_resultados_enderecos(candidatos, texto, cidade)[:12]


@st.cache_data(
    ttl=3600,
    show_spinner=False,
)
def obter_matrix_cache_v4(
    coordenadas,
    _api_key,
    cache_version="matrix-v4",
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
def obter_directions_cache_v3(
    coordenadas,
    _api_key,
    cache_version="directions-v3",
):

    return obter_rota_completa(
        coordenadas=list(
            coordenadas
        ),
        api_key=_api_key,
    )


def buscar_sugestoes(searchterm, focus_lat=None, focus_lon=None):
    texto = str(searchterm or "").strip()
    ponto_direto = interpretar_texto_coordenadas(texto)
    if ponto_direto:
        return [(ponto_direto["label"], ponto_direto)]
    if len(texto) < AUTOCOMPLETE_MIN_CHARS or not ors_configurado:
        return []
    try:
        resultados = buscar_sugestoes_cache(
            texto, focus_lat, focus_lon,
            "", ORS_KEY,
        )
        st.session_state.erro_autocomplete = None
    except ORSError as erro:
        st.session_state.erro_autocomplete = str(erro)
        logger.warning("Falha no autocomplete: %s", erro)
        return []
    except Exception as erro:
        st.session_state.erro_autocomplete = "Não foi possível buscar sugestões agora."
        logger.exception("Falha inesperada no autocomplete: %s", erro)
        return []
    return [(resultado["label"], resultado) for resultado in resultados]


def campo_local(chave, label, placeholder, focus=None, default_local=None):
    """Recebe endereço pesquisado ou par latitude/longitude no mesmo campo.

    `default_use_searchterm` preserva o texto digitado no searchbox;
    coordenadas completas são reconhecidas sem escolher uma sugestão.
    Outros textos só viram local após selecionar um endereço geocodificado.
    """
    focus_lat = focus.get("latitude") if focus else None
    focus_lon = focus.get("longitude") if focus else None
    selecionado = st_searchbox(
        buscar_sugestoes,
        key=chave,
        label=label,
        placeholder=placeholder,
        default=default_local,
        default_searchterm=(
            str((default_local or {}).get("label", ""))
            if isinstance(default_local, dict) else ""
        ),
        debounce=250,
        clear_on_submit=False,
        edit_after_submit="option",
        default_use_searchterm=True,
        focus_lat=focus_lat,
        focus_lon=focus_lon,
    )
    if isinstance(selecionado, str):
        return interpretar_texto_coordenadas(selecionado)
    return selecionado


# ============================================================
# IMPORTAÇÃO
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
        linhas[0]
        .strip()
        .strip('"')
        .strip("'")
    )

    if cabecalho == "endereco":

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

        if localizar_coluna_endereco(
            dataframe
        ) is not None:

            return dataframe

    raise ValueError(
        "Não foi possível identificar "
        "a coluna 'endereco'."
    )


def chave_endereco_duplicado(endereco):
    """Ignora diferenças de maiúsculas, acentos, pontuação e espaços."""
    texto = normalizar_texto(endereco)
    return " ".join(re.findall(r"[^\W_]+", texto, flags=re.UNICODE))


def localizar_duplicados_importacao(enderecos_ativos):
    grupos = {}
    for indice, endereco in enderecos_ativos:
        chave = chave_endereco_duplicado(endereco)
        if chave:
            grupos.setdefault(chave, []).append(indice)
    return {
        indice
        for indices in grupos.values()
        if len(indices) > 1
        for indice in indices
    }


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
            "Utilize CSV ou XLSX."
        )

    coluna = localizar_coluna_endereco(
        dataframe
    )

    if coluna is None:

        raise ValueError(
            "O arquivo precisa possuir "
            "a coluna 'endereco'."
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
            "Nenhum endereço válido."
        )

    hash_arquivo = (
        hashlib.sha256(
            arquivo.name.encode("utf-8", errors="replace") + b"\0" + bytes_arquivo
        )
        .hexdigest()[:12]
    )

    return (
        enderecos,
        hash_arquivo,
    )


# ============================================================
# MOTOR DA ROTA
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

        "endereco_salvo":
            origem.get(
                "endereco_salvo"
            ),

        "local_salvo_id":
            origem.get(
                "local_salvo_id"
            ),

        "observacao":
            origem.get(
                "observacao"
            ),
    }


def validar_locais_base(
    locais,
    voltar_inicio,
):

    if len(
        locais
    ) < 2:

        raise ValueError(
            "São necessários pelo menos "
            "dois locais."
        )

    limite_tecnico = (
        MAX_SUPPORTED_LOCATIONS
    )

    if len(
        locais
    ) > limite_tecnico:

        raise ValueError(
            "O limite técnico desta rota "
            f"é de {limite_tecnico} locais."
        )

    coordenadas = set()

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

        if chave in coordenadas:

            raise ValueError(
                "O mesmo local foi selecionado "
                "mais de uma vez."
            )

        coordenadas.add(
            chave
        )


@st.cache_data(ttl=3600, show_spinner=False)
def identificar_locais_sem_via_cache(coordenadas, _api_key, raio_m=300):
    """Verifica a rede viária via Snap, sem modificar a geometria das paradas.

    Retorna índices comprovadamente sem ponto roteável dentro do raio, ou
    None quando a consulta diagnóstica falha (não produz falsos culpados).
    """
    resposta = requests.post(
        f"{ORS_BASE_URL}/snap/driving-car/json",
        headers={
            "Authorization": _api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "locations": [[float(lon), float(lat)] for lat, lon in coordenadas],
            "radius": int(raio_m),
        },
        timeout=(5, 15),
    )
    resposta.raise_for_status()
    dados = resposta.json()
    pontos = dados.get("locations") if isinstance(dados, dict) else None
    if not isinstance(pontos, list) or len(pontos) != len(coordenadas):
        return None
    return [indice for indice, ponto in enumerate(pontos) if ponto is None]


def enriquecer_erro_roteabilidade(erro, locais, api_key):
    """Relaciona erros do ORS aos nomes reais, sem indicar culpados incertos."""
    mensagem = str(erro)
    padrao_par = re.search(r"entre os pontos\s+(\d+)\s+e\s+(\d+)", mensagem, re.I)
    termos_roteabilidade = (
        "via roteável", "vias acessíveis", "não foi possível encontrar uma rota",
        "não foi possível encontrar rota", "não encontrou resultado para a consulta",
    )
    if not padrao_par and not any(
        termo in mensagem.lower() for termo in termos_roteabilidade
    ):
        return erro

    # O Snap aponta locais afastados da malha viária. Uma simples falha
    # entre um par de pontos não comprova qual dos dois está incorreto.
    try:
        coords = tuple(
            (float(local["latitude"]), float(local["longitude"]))
            for local in locais
        )
        sem_via = identificar_locais_sem_via_cache(coords, api_key)
    except (requests.RequestException, ValueError, TypeError, KeyError) as falha:
        logger.info("Não foi possível executar diagnóstico Snap: %s", type(falha).__name__)
        sem_via = None

    if sem_via:
        nomes = [
            f"{i + 1}. {locais[i].get('nome') or f'Local {i + 1}'}"
            for i in sem_via
        ]
        return ORSError(
            "O ORS não encontrou uma via roteável para carros em um raio "
            "de 300 metros dos seguintes locais: "
            + "; ".join(nomes)
            + ". Confira as coordenadas ou selecione um acesso por rua."
        )

    if padrao_par:
        a, b = int(padrao_par.group(1)) - 1, int(padrao_par.group(2)) - 1
        if 0 <= a < len(locais) and 0 <= b < len(locais):
            return ORSError(
                "Não foi possível encontrar um trajeto viário entre: "
                f"'{locais[a].get('nome') or f'Local {a + 1}'}' e "
                f"'{locais[b].get('nome') or f'Local {b + 1}'}'. "
                "Confira ambos os endereços e se há acesso de carro entre eles."
            )

    return erro


@st.cache_resource(show_spinner=False)
def obter_semaforo_otimizacoes_pesadas():
    """Semáforo compartilhado entre sessões no processo do Streamlit."""
    return threading.BoundedSemaphore(value=2)


def calcular_rota_a_partir_de_locais(
    locais,
    voltar_inicio,
    objetivo,
    modo,
    preservar_ordem=False,
):
    if not ors_configurado:
        raise ORSError("OpenRouteService não configurado.")

    validar_locais_base(locais, voltar_inicio)
    validar_permissao_calculo(len(locais), modo)

    reserva = reservar_calculo_atomico(
        quantidade_locais=len(locais),
        objetivo=objetivo,
        modo=modo,
        voltar_inicio=voltar_inicio,
    )

    reserva_id = reserva.get("id") if isinstance(reserva, dict) else None

    if not reserva_id:
        raise LimiteUsoError("Não foi possível reservar seu cálculo. Tente novamente.")

    try:
        coordenadas = tuple(
            (local["latitude"], local["longitude"])
            for local in locais
        )

        start_idx = 0
        end_idx = 0 if voltar_inicio else len(locais) - 1

        matrix_data = obter_matrix_cache_v4(
            coordenadas,
            ORS_KEY,
        )

        matrix_otimizacao = (
            matrix_data["distancias"]
            if objetivo == "Menor distância"
            else matrix_data["duracoes"]
        )

        parametros_solver = {
            "matrix_m": matrix_otimizacao,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "modo": modo,
        }

        tem_janelas = any(local.get("janela_inicio") or local.get("janela_fim") for local in locais)
        if preservar_ordem:
            ordem_selecionada = list(range(len(locais)))
            if voltar_inicio:
                ordem_selecionada.append(0)
            resultado_solver = {
                "route": ordem_selecionada,
                "status": "ORDEM_MANUAL",
                "metodo": "Sequência informada pelo usuário",
                "gap_percent": None,
            }
        elif tem_janelas:
            resultado_solver = otimizar_janelas_locais(
                locais, matrix_otimizacao, matrix_data["duracoes"],
                voltar_inicio=voltar_inicio,
                partida=str(locais[0].get("horario_partida") or "08:00"),
                limite_s=20,
            )
        elif modo == "Preciso":
            semaforo = obter_semaforo_otimizacoes_pesadas()
            if not semaforo.acquire(blocking=False):
                raise LimiteUsoError(
                    "Já existem duas otimizações pesadas em andamento. "
                    "Tente novamente após uma delas terminar."
                )
            try:
                resultado_solver = otimizar_rota(**parametros_solver)
            finally:
                semaforo.release()
        else:
            resultado_solver = otimizar_rota(**parametros_solver)

        locais_ordenados = [
            locais[indice]
            for indice in resultado_solver["route"]
        ]

        coordenadas_ordenadas = tuple(
            (local["latitude"], local["longitude"])
            for local in locais_ordenados
        )

        rota_real = obter_directions_cache_v3(
            coordenadas_ordenadas,
            ORS_KEY,
        )

        gap = resultado_solver.get("gap_percent")
        if gap is not None:
            gap = float(gap)

        previsoes = previsoes_ordem(
            locais, resultado_solver["route"], matrix_data["duracoes"],
            partida=str(locais[0].get("horario_partida") or "08:00"),
            chegadas=resultado_solver.get("chegadas_min"),
        )
        if preservar_ordem:
            from rotaki_features import janela_local
            for p, registro in zip(resultado_solver["route"], previsoes):
                _, limite, _ = janela_local(locais[p])
                if registro["minuto_absoluto"] > limite:
                    raise ValueError(
                        "A ordem manual ultrapassa a janela de atendimento de "
                        + str(locais[p].get("nome") or "um ponto")
                    )
        return {
            "versao": 15,
            "preservar_ordem": bool(preservar_ordem),
            "previsoes_chegada": preparar_json(previsoes),
            "horario_partida": str(locais[0].get("horario_partida") or "08:00"),
            "voltar_inicio": bool(voltar_inicio),
            "objetivo": objetivo,
            "modo": modo,
            "locais_base": preparar_json(locais),
            "locais_ordenados": preparar_json(locais_ordenados),
            "distancia_m": float(rota_real["distancia_m"]),
            "duracao_s": float(rota_real["duracao_s"]),
            "geometry": preparar_json(rota_real["geometry"]),
            "solver": {
                "status": str(resultado_solver.get("status", "")),
                "metodo": str(resultado_solver.get("metodo", "")),
                "gap_percent": gap,
            },
            "_reserva_calculo_id": reserva_id,
        }
    except ORSError as erro:
        cancelar_reserva_calculo(reserva_id)
        raise enriquecer_erro_roteabilidade(erro, locais, ORS_KEY) from erro
    except Exception:
        cancelar_reserva_calculo(reserva_id)
        raise



# ============================================================
# MAPA / GOOGLE MAPS / EXPORTAÇÃO
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

    tempo = (
        f"{horas}h {minutos:02d}min"
        if horas
        else
        f"{minutos} min"
    )

    return distancia_km, tempo


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
        )
        /
        len(
            path
        ),

        sum(
            p[1]
            for p in path
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
    ).add_to(
        mapa
    )

    marcadores = (
        locais_ordenados[:-1]
        if rota_fechada
        else
        locais_ordenados
    )

    for numero, local in enumerate(
        marcadores,
        start=1,
    ):

        nome = html.escape(
            str(
                local[
                    "nome"
                ]
            )
        )

        icone = f"""
        <div style="
            background:#1e3a8a;
            color:#fff;
            width:30px;
            height:30px;
            border-radius:50%;
            display:flex;
            align-items:center;
            justify-content:center;
            font-weight:700;
            border:2px solid white;
        ">{numero}</div>
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
                f"{numero}. {nome}"
            ),
            icon=folium.DivIcon(
                html=icone
            ),
        ).add_to(
            mapa
        )

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


def montar_referencia_google(
    local,
):

    if local.get(
        "endereco_salvo"
    ):

        return str(
            local[
                "endereco_salvo"
            ]
        )

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

    rua = local.get(
        "street"
    )

    numero = local.get(
        "housenumber"
    )

    if nome:

        componentes.append(
            str(
                nome
            )
        )

    if rua:

        endereco = str(
            rua
        )

        if numero:

            endereco += (
                f", {numero}"
            )

        componentes.append(
            endereco
        )

    for valor in (
        local.get(
            "locality"
        ),
        local.get(
            "region"
        ),
        local.get(
            "country"
        )
        or
        "Brazil",
    ):

        if valor:

            valor = str(
                valor
            )

            if valor not in componentes:

                componentes.append(
                    valor
                )

    return ", ".join(
        componentes
    )


def coordenada_google_maps(
    local,
):

    return (
        f"{local['latitude']:.7f},"
        f"{local['longitude']:.7f}"
    )


def gerar_url_google_maps(
    locais,
    usar_coordenadas=False,
):

    if len(
        locais
    ) < 2:

        return None

    if usar_coordenadas:

        referencias = [
            coordenada_google_maps(
                local
            )

            for local
            in locais
        ]

    else:

        referencias = [
            montar_referencia_google(
                local
            )

            for local
            in locais
        ]

    params = {
        "api":
            "1",

        "origin":
            referencias[0],

        "destination":
            referencias[-1],

        "travelmode":
            "driving",

        "dir_action":
            "navigate",
    }

    if len(
        referencias
    ) > 2:

        params["waypoints"] = "|".join(
            referencias[1:-1]
        )

    return (
        "https://www.google.com/maps/dir/?"
        +
        urlencode(
            params
        )
    )


def gerar_url_navegacao_destino(local):
    referencia = coordenada_google_maps(local)

    params = {
        "api": "1",
        "destination": referencia,
        "travelmode": "driving",
        "dir_action": "navigate",
    }

    return "https://www.google.com/maps/dir/?" + urlencode(params)



def dividir_rota_google_maps(
    locais,
    max_pontos_por_trecho=5,
):
    return dividir_rota_google_maps_logic(
        locais,
        max_pontos_por_trecho,
    )



def dataframe_rota_otimizada(
    locais,
    rota_fechada,
):

    linhas = []

    for ordem, local in enumerate(
        locais,
        start=1,
    ):

        if ordem == 1:

            tipo = "Início"

        elif (
            rota_fechada
            and
            ordem == len(
                locais
            )
        ):

            tipo = "Retorno"

        elif ordem == len(
            locais
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

                "endereco":
                    montar_referencia_google(
                        local
                    ),

                "observacao":
                    local.get(
                        "observacao"
                    )
                    or
                    "",

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


def gerar_excel_rota(dataframe, dados):
    """Excel com a rota otimizada visível ao abrir e o resumo em outra aba.

    A aba Rota é sempre a primeira e contém todas as paradas na ordem real
    calculada, inclusive o retorno ao início quando a rota é circular.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if not isinstance(dataframe, pd.DataFrame):
        dataframe = pd.DataFrame(dataframe)

    # Compatibilidade com resultados antigos salvos no histórico.
    if dataframe.empty:
        ordenados = dados.get("locais_ordenados") or []
        if ordenados:
            dataframe = dataframe_rota_otimizada(
                ordenados, bool(dados.get("voltar_inicio", False))
            )
    if dataframe.empty:
        raise ValueError(
            "Não há paradas na rota calculada para exportar ao Excel."
        )

    colunas = [
        "ordem", "tipo", "local", "endereco",
        "observacao", "latitude", "longitude",
    ]
    if 'chegada_prevista' in dataframe.columns:
        colunas.append('chegada_prevista')
    rotulos = {
        "ordem": "Nº",
        "tipo": "Tipo",
        "local": "Local",
        "endereco": "Endereço",
        "observacao": "Observação",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "chegada_prevista": "Chegada prevista",
    }
    planilha_rota = dataframe.reindex(columns=colunas).rename(columns=rotulos)

    # Evita interpretação de texto fornecido por usuários como fórmula Excel.
    # Coordenadas numéricas permanecem números, com precisão preservada.
    def texto_seguro(valor):
        if isinstance(valor, str) and valor.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + valor
        return valor

    for coluna in ("Tipo", "Local", "Endereço", "Observação", "Chegada prevista"):
        if coluna in planilha_rota.columns:
            planilha_rota[coluna] = planilha_rota[coluna].map(texto_seguro)

    distancia_km, tempo = dados_metricas(dados)
    objetivo = dados.get("objetivo") or "—"
    modo = dados.get("modo") or "—"

    resumo = pd.DataFrame([{
        "Distância total (km)": round(distancia_km, 2),
        "Tempo estimado": tempo,
        "Objetivo": objetivo,
        "Modo": modo,
        "Quantidade de pontos na sequência": len(planilha_rota),
    }])
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # IMPORTANTÍSSIMO: a primeira aba é a Rota, e não o Resumo.
        # A tabela começa na linha 6, abaixo dos indicadores principais.
        planilha_rota.to_excel(
            writer, sheet_name="Rota", index=False, startrow=5,
        )
        resumo.to_excel(writer, sheet_name="Resumo", index=False)

        workbook = writer.book
        workbook.active = 0
        aba_rota = writer.sheets["Rota"]
        aba_rota.sheet_view.showGridLines = False
        aba_rota.sheet_view.tabSelected = True
        writer.sheets["Resumo"].sheet_view.tabSelected = False

        ultima_coluna_excel = get_column_letter(len(planilha_rota.columns))
        aba_rota.merge_cells(f"A1:{ultima_coluna_excel}1")
        titulo = aba_rota["A1"]
        titulo.value = "ROTA OTIMIZADA — ORDEM DE VISITA"
        titulo.font = Font(name="Aptos", size=16, bold=True, color="FFFFFF")
        titulo.fill = PatternFill("solid", fgColor="16324F")
        titulo.alignment = Alignment(vertical="center")
        aba_rota.row_dimensions[1].height = 32

        indicadores = (
            ("A3", "Pontos", "B3", len(planilha_rota)),
            ("C3", "Distância (km)", "D3", round(distancia_km, 2)),
            ("E3", "Tempo estimado", "F3", tempo),
            ("A4", "Objetivo", "B4", objetivo),
            ("C4", "Modo", "D4", modo),
        )
        for pos_label, label, pos_valor, valor in indicadores:
            cel_label = aba_rota[pos_label]
            cel_valor = aba_rota[pos_valor]
            cel_label.value = label
            cel_label.font = Font(name="Aptos", size=10, bold=True, color="34516B")
            cel_valor.value = texto_seguro(valor)
            cel_valor.font = Font(name="Aptos", size=10, bold=True, color="192D40")

        linha_cabecalho = 6
        ultima_linha = linha_cabecalho + len(planilha_rota)
        for cel in aba_rota[linha_cabecalho]:
            cel.fill = PatternFill("solid", fgColor="255B82")
            cel.font = Font(name="Aptos", bold=True, color="FFFFFF")
            cel.alignment = Alignment(vertical="center")
        aba_rota.row_dimensions[linha_cabecalho].height = 23

        larguras = (9, 14, 35, 55, 37, 18, 18, 20)
        for i, largura in enumerate(larguras, start=1):
            aba_rota.column_dimensions[get_column_letter(i)].width = largura

        for linha in range(linha_cabecalho + 1, ultima_linha + 1):
            for coluna in range(1, len(planilha_rota.columns) + 1):
                cel = aba_rota.cell(row=linha, column=coluna)
                cel.alignment = Alignment(vertical="top", wrap_text=coluna in (3, 4, 5))
                if linha % 2 == 0:
                    cel.fill = PatternFill("solid", fgColor="F2F6FA")
            for coluna in (6, 7):
                aba_rota.cell(row=linha, column=coluna).number_format = "0.0000000"
            aba_rota.row_dimensions[linha].height = 28

        aba_rota.auto_filter.ref = f"A{linha_cabecalho}:{ultima_coluna_excel}{ultima_linha}"
        aba_rota.freeze_panes = "A7"
        aba_rota.print_title_rows = "1:6"
        aba_rota.sheet_properties.pageSetUpPr.fitToPage = True
        aba_rota.page_setup.fitToWidth = 1
        aba_rota.sheet_properties.tabColor = "255B82"

        aba_resumo = writer.sheets["Resumo"]
        aba_resumo.sheet_view.showGridLines = False
        aba_resumo.freeze_panes = "A2"
        for cel in aba_resumo[1]:
            cel.fill = PatternFill("solid", fgColor="255B82")
            cel.font = Font(name="Aptos", bold=True, color="FFFFFF")
        for i, largura in enumerate((25, 24, 25, 16, 38), start=1):
            aba_resumo.column_dimensions[get_column_letter(i)].width = largura
        aba_resumo.sheet_properties.tabColor = "8999AA"

    return buffer.getvalue()


# ============================================================
# RESULTADO
# ============================================================

def renderizar_resultado_rota(
    dados,
):

    if not dados:
        return

    locais = (
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

    voltar_inicio = bool(
        dados.get(
            "voltar_inicio",
            False,
        )
    )

    distancia_km, tempo = (
        dados_metricas(
            dados
        )
    )

    if st.session_state.resultado_historico:

        st.info(
            "📊 Snapshot do histórico."
        )

    if st.session_state.resultado_direto_titulo:

        st.info(
            st.session_state
            .resultado_direto_titulo
        )

    if (
        st.session_state.rota_atual_id
        and
        st.session_state.rota_atual_nome
    ):

        st.info(
            "📂 Rota salva: "
            f"**{st.session_state.rota_atual_nome}**"
        )

        col1, col2, col3 = (
            st.columns(
                3
            )
        )

        with col1:

            if st.button(
                "🔄 Recalcular",
                use_container_width=True,
            ):

                try:

                    novo = (
                        calcular_rota_a_partir_de_locais(
                            locais_base,
                            voltar_inicio,
                            dados.get(
                                "objetivo",
                                "Menor distância",
                            ),
                            dados.get(
                                "modo",
                                "Rápido",
                            ),
                            preservar_ordem=bool(dados.get("preservar_ordem", False)),
                        )
                    )

                    registrar_execucao_seguro(
                        novo,
                        st.session_state
                        .rota_atual_id,
                        st.session_state
                        .rota_atual_nome,
                    )

                    st.session_state.resultado_rota = (
                        novo
                    )

                    st.rerun()

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

        with col2:

            if st.button(
                "✏️ Editar locais",
                use_container_width=True,
            ):

                st.session_state.edicao_rota = {
                    "id":
                        st.session_state
                        .rota_atual_id,

                    "nome":
                        st.session_state
                        .rota_atual_nome,

                    "dados":
                        dados,
                }

                st.session_state.edicao_version += 1
                st.session_state.pagina_app = "editar"

                st.rerun()

        with col3:

            if st.button(
                "💾 Atualizar rota salva",
                use_container_width=True,
            ):

                try:

                    atualizar_rota_banco(
                        st.session_state
                        .rota_atual_id,
                        dados,
                    )

                    st.success(
                        "Rota atualizada."
                    )

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

    st.subheader(
        "✅ Resultado da rota"
    )

    c1, c2, c3, c4 = (
        st.columns(
            4
        )
    )

    c1.metric(
        "Distância",
        f"{distancia_km:.2f} km",
    )

    c2.metric(
        "Tempo",
        tempo,
    )

    c3.metric(
        "Locais",
        len(
            locais_base
        ),
    )

    c4.metric(
        "Modo",
        dados.get(
            "modo",
            "—",
        ),
    )

    st.subheader(
        "📋 Ordem da rota"
    )

    exibir = (
        locais[:-1]
        if voltar_inicio
        else
        locais
    )

    for numero, local in enumerate(
        exibir,
        start=1,
    ):

        st.write(
            f"**{numero}.** "
            f"{local['nome']}"
        )

        if local.get(
            "observacao"
        ):

            st.caption(
                f"📝 {local['observacao']}"
            )

    if (
        voltar_inicio
        and
        locais
    ):

        st.write(
            f"**{len(exibir) + 1}.** "
            f"{locais[-1]['nome']} "
            "_(retorno)_"
        )

    if dados.get("previsoes_chegada"):
        with st.expander("🕒 Horários estimados de chegada", expanded=False):
            st.caption("Estimativas calculadas com a matriz de trânsito do ORS; não são horários GPS em tempo real.")
            st.dataframe(pd.DataFrame([
                {"Parada": item["ordem"], "Local": item["nome"],
                 "Previsão": item["chegada"] + (f" (+{item['dia_adicional']} dia)" if item.get('dia_adicional') else "")}
                for item in dados["previsoes_chegada"]
            ]), hide_index=True, use_container_width=True)

    st.subheader(
        "🚚 Executar rota"
    )

    ativa = None

    try:
        ativa = obter_execucao_em_andamento_usuario()
    except Exception:
        ativa = None

    if ativa:
        st.info(
            "Você já possui uma rota em andamento."
        )

        if st.button(
            "🚚 Continuar execução em andamento",
            type="primary",
            use_container_width=True,
            key="resultado_continuar_execucao",
        ):
            st.session_state.execucao_entrega_id = (
                ativa["id"]
            )
            st.session_state.pagina_app = "execucoes"
            st.rerun()

    else:
        if st.button(
            "🚚 Iniciar execução desta rota",
            type="primary",
            use_container_width=True,
            key="resultado_iniciar_execucao",
        ):
            try:
                execucao = iniciar_execucao_entrega(
                    dados=dados,
                    rota_id=(
                        st.session_state.rota_atual_id
                    ),
                    nome_rota=(
                        st.session_state.rota_atual_nome
                        or st.session_state.rota_nome_sugerido
                        or "Rota"
                    ),
                )

                st.session_state.execucao_entrega_id = (
                    execucao["id"]
                )
                st.session_state.pagina_app = "execucoes"
                st.rerun()

            except Exception as e:
                mostrar_erro_usuario(
                    "Falha ao iniciar execução",
                    e,
                )

    st.subheader(
        "☁️ Salvar rota"
    )

    if not st.session_state.rota_atual_id:

        with st.form(
            "salvar_rota_form"
        ):

            nome = st.text_input(
                "Nome da rota",
                value=(
                    st.session_state
                    .rota_nome_sugerido
                ),
            )

            salvar = (
                st.form_submit_button(
                    "💾 Salvar em Minhas rotas",
                    use_container_width=True,
                )
            )

            if salvar:

                try:

                    registro = (
                        salvar_rota_banco(
                            nome,
                            dados,
                        )
                    )

                    if (
                        st.session_state
                        .ultima_execucao_id
                    ):

                        vincular_execucao_rota(
                            st.session_state
                            .ultima_execucao_id,
                            registro[
                                "id"
                            ],
                            registro[
                                "nome"
                            ],
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

                    st.session_state.rota_aberta_salva = True

                    st.rerun()

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

    else:

        st.success(
            "Esta rota já está salva."
        )

    if st.session_state.rota_atual_id:
        interface_compartilhamento_rota(dados)

    try:

        mapa = criar_mapa(
            dados.get(
                "geometry"
            )
            or
            [],
            locais,
            voltar_inicio,
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao montar mapa",
            e,
            "Não foi possível montar o mapa.",
        )
        return

    st.subheader(
        "💾 Exportar rota"
    )

    if permite_exportacoes_usuario():

        dataframe = (
            dataframe_rota_otimizada(
                locais,
                voltar_inicio,
            )
        )
        previsoes_exportacao = dados.get("previsoes_chegada") or []
        if len(previsoes_exportacao) == len(dataframe):
            dataframe["chegada_prevista"] = [
                item["chegada"] for item in previsoes_exportacao
            ]

        csv_bytes = (
            dataframe
            .to_csv(
                index=False,
                sep=";",
            )
            .encode(
                "utf-8-sig"
            )
        )

        excel_bytes = (
            gerar_excel_rota(
                dataframe,
                dados,
            )
        )

        html_bytes = (
            mapa.get_root()
            .render()
            .encode(
                "utf-8"
            )
        )

        c1, c2, c3 = (
            st.columns(
                3
            )
        )

        with c1:

            st.download_button(
                "⬇️ CSV",
                data=csv_bytes,
                file_name="rota.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with c2:

            st.download_button(
                "⬇️ Excel",
                data=excel_bytes,
                file_name="rota_otimizada.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                help="Inclui todas as paradas na ordem otimizada e uma aba de resumo.",
                use_container_width=True,
            )

        with c3:

            st.download_button(
                "🗺️ Mapa HTML",
                data=html_bytes,
                file_name="mapa.html",
                mime="text/html",
                use_container_width=True,
            )

    else:

        st.info(
            "🔒 Exportação em CSV, Excel e "
            "HTML está disponível no plano Pro."
        )

        if st.button(
            "💎 Ver plano Pro",
            key="resultado_ver_pro",
        ):

            st.session_state.pagina_app = (
                "planos"
            )

            st.rerun()

    st.subheader(
        "🧭 Navegação no Google Maps"
    )

    trechos = (
        dividir_rota_google_maps(
            locais,
            5,
        )
    )

    if len(
        trechos
    ) == 1:

        st.link_button(
            "🧭 Abrir no Google Maps",
            gerar_url_google_maps(
                trechos[0]
            ),
            use_container_width=True,
        )

    else:

        for numero, trecho in enumerate(
            trechos,
            start=1,
        ):

            with st.expander(
                f"Trecho {numero}: "
                f"{trecho[0]['nome']} → "
                f"{trecho[-1]['nome']}"
            ):

                st.link_button(
                    f"🧭 Abrir trecho {numero}",
                    gerar_url_google_maps(
                        trecho
                    ),
                    use_container_width=True,
                )

    st.subheader(
        "🗺️ Mapa"
    )

    st_folium(
        mapa,
        height=600,
        use_container_width=True,
        returned_objects=[],
    )


# ============================================================
# PÁGINA PLANOS / MERCADO PAGO
# ============================================================

def renderizar_assinatura_atual():

    try:

        assinatura = (
            obter_assinatura_atual()
        )

    except Exception as e:

        st.warning(
            "Não foi possível consultar "
            f"a assinatura: {e}"
        )

        return

    if not assinatura:

        if (
            perfil_usuario_efetivo
            and
            perfil_usuario_efetivo.get(
                "plano"
            ) == "Pro"
        ):

            if usuario_e_admin:

                st.info(
                    "🛡️ Sua conta possui acesso Pro "
                    "administrativo. Não há uma "
                    "assinatura Mercado Pago vinculada."
                )

            else:

                st.info(
                    "Seu acesso Pro foi concedido "
                    "administrativamente."
                )

        return

    status = assinatura.get(
        "status"
    )

    st.subheader(
        "💳 Sua assinatura"
    )

    with st.container(
        border=True
    ):

        c1, c2, c3 = (
            st.columns(
                3
            )
        )

        with c1:

            st.metric(
                "Plano",
                assinatura.get(
                    "plano",
                    "—",
                ),
            )

        with c2:

            st.metric(
                "Status",
                nome_status_assinatura(
                    status
                ),
            )

        with c3:

            st.metric(
                "Valor",
                (
                    formatar_dinheiro(
                        assinatura.get(
                            "amount"
                        )
                    )
                    + "/mês"
                ),
            )

        if assinatura.get(
            "next_payment_date"
        ):

            st.caption(
                "Próxima cobrança: "
                f"**{formatar_data(assinatura['next_payment_date'])}**"
            )

        if assinatura.get(
            "last_payment_status"
        ):

            st.caption(
                "Último pagamento: "
                f"**{assinatura['last_payment_status']}**"
            )

        c1, c2 = (
            st.columns(
                2
            )
        )

        with c1:

            if st.button(
                "🔄 Atualizar status",
                use_container_width=True,
                key="mp_sync_assinatura",
            ):

                try:

                    with st.spinner(
                        "Consultando Mercado Pago..."
                    ):

                        mercado_pago_backend(
                            "sync"
                        )

                    st.session_state.mensagem_sistema = {
                        "tipo":
                            "success",

                        "texto":
                            (
                                "Status da assinatura "
                                "atualizado."
                            ),
                    }

                    st.rerun()

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

        with c2:

            if str(
                status
            ).lower() in (
                "authorized",
                "pending",
                "paused",
            ):

                confirmar = st.checkbox(
                    "Confirmo o cancelamento",
                    key="mp_confirmar_cancelamento",
                )

                if st.button(
                    "Cancelar assinatura",
                    use_container_width=True,
                    disabled=(
                        not confirmar
                    ),
                    key="mp_cancelar_assinatura",
                ):

                    try:

                        with st.spinner(
                            "Cancelando assinatura..."
                        ):

                            mercado_pago_backend(
                                "cancel"
                            )

                        st.session_state.mensagem_sistema = {
                            "tipo":
                                "success",

                            "texto":
                                (
                                    "Assinatura cancelada."
                                ),
                        }

                        st.rerun()

                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha na operação",
                            e,
                        )


def renderizar_planos():

    st.subheader(
        "💎 Planos"
    )

    st.write(
        "Escolha o nível de recursos adequado "
        "ao seu uso do Otimizador de Rotas."
    )

    try:

        planos = listar_planos(
            apenas_ativos=True
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar página",
            e,
        )
        return

    plano_atual = (
        perfil_usuario_efetivo.get(
            "plano"
        )
        if perfil_usuario_efetivo
        else
        "Gratuito"
    )

    st.info(
        f"Seu plano atual: **{plano_atual}**"
    )

    colunas = st.columns(
        len(
            planos
        )
    )

    for coluna, plano in zip(
        colunas,
        planos,
    ):

        with coluna:

            with st.container(
                border=True
            ):

                st.markdown(
                    f"## {plano['nome']}"
                )

                preco = float(
                    plano.get(
                        "preco_mensal"
                    )
                    or
                    0
                )

                if plano[
                    "nome"
                ] == "Gratuito":

                    st.markdown(
                        "### Grátis"
                    )

                elif preco > 0:

                    st.markdown(
                        "### "
                        f"{formatar_dinheiro(preco)}"
                        "/mês"
                    )

                else:

                    st.markdown(
                        "### Preço a definir"
                    )

                if plano.get(
                    "descricao"
                ):

                    st.write(
                        plano[
                            "descricao"
                        ]
                    )

                limite_mes = int(
                    plano[
                        "limite_rotas_mes"
                    ]
                )

                if limite_mes > 0:

                    st.write(
                        f"✓ **{limite_mes}** "
                        "cálculos/mês"
                    )

                else:

                    st.write(
                        "✓ Cálculos ilimitados"
                    )

                st.write(
                    "✓ Até "
                    f"**{plano['limite_locais_rota']}** "
                    "locais por rota"
                )

                st.write(
                    "✓ Modo Rápido"
                )

                if plano[
                    "permite_preciso"
                ]:

                    st.write(
                        "✓ Modo Preciso"
                    )

                else:

                    st.write(
                        "— Modo Preciso"
                    )

                if plano[
                    "permite_recorrentes"
                ]:

                    st.write(
                        "✓ Rotas recorrentes"
                    )

                else:

                    st.write(
                        "— Rotas recorrentes"
                    )

                if plano[
                    "historico_dias"
                ] is None:

                    st.write(
                        "✓ Histórico completo"
                    )

                else:

                    st.write(
                        "✓ Histórico dos últimos "
                        f"**{plano['historico_dias']} dias**"
                    )

                if plano[
                    "permite_exportacoes"
                ]:

                    st.write(
                        "✓ Exportação CSV, Excel e HTML"
                    )

                else:

                    st.write(
                        "— Exportações avançadas"
                    )

                if plano[
                    "nome"
                ] == plano_atual:

                    st.success(
                        "✓ Seu plano atual"
                    )

                elif plano[
                    "nome"
                ] == "Pro":

                    if preco <= 0:

                        st.info(
                            "Preço ainda não definido "
                            "pelo administrador."
                        )

                    elif st.button(
                        "💳 Assinar Pro",
                        type="primary",
                        use_container_width=True,
                        key="assinar_plano_pro",
                    ):

                        try:

                            with st.spinner(
                                "Preparando checkout "
                                "no Mercado Pago..."
                            ):

                                resposta = (
                                    mercado_pago_backend(
                                        "create",
                                        "Pro",
                                    )
                                )

                            if resposta.get(
                                "already_active"
                            ):

                                st.success(
                                    "Sua assinatura Pro "
                                    "já está ativa."
                                )

                            else:

                                checkout_url = (
                                    resposta.get(
                                        "checkout_url"
                                    )
                                )

                                if checkout_url:

                                    st.session_state.mp_checkout_url = (
                                        checkout_url
                                    )

                                    st.session_state.mp_checkout_plano = (
                                        "Pro"
                                    )

                                else:

                                    st.warning(
                                        "O Mercado Pago não "
                                        "retornou um link "
                                        "de pagamento."
                                    )

                        except Exception as e:
                            mostrar_erro_usuario(
                                "Falha no fluxo de pagamento",
                                e,
                            )

    if st.session_state.mp_checkout_url:

        st.divider()

        st.success(
            "Checkout criado. "
            "Continue o pagamento no Mercado Pago."
        )

        st.link_button(
            "💳 Continuar no Mercado Pago",
            st.session_state.mp_checkout_url,
            type="primary",
            use_container_width=True,
        )

        st.caption(
            "Depois de concluir o pagamento, "
            "retorne ao aplicativo. O plano será "
            "liberado automaticamente pelo webhook; "
            "você também pode usar "
            "'Atualizar status'."
        )

    st.divider()

    renderizar_assinatura_atual()


# ============================================================
# HISTÓRICO
# ============================================================

def renderizar_historico():

    st.subheader(
        "📊 Histórico"
    )

    limite_historico = (
        historico_dias_usuario()
    )

    if limite_historico is None:

        periodo = st.selectbox(
            "Período",
            [
                "Últimos 7 dias",
                "Últimos 30 dias",
                "Últimos 90 dias",
                "Todo o histórico",
            ],
        )

        mapa = {
            "Últimos 7 dias":
                7,

            "Últimos 30 dias":
                30,

            "Últimos 90 dias":
                90,

            "Todo o histórico":
                None,
        }

        dias = mapa[
            periodo
        ]

    else:

        dias = int(
            limite_historico
        )

        st.info(
            "Seu plano permite consultar "
            f"os últimos **{dias} dias** "
            "de histórico."
        )

    try:

        execucoes = (
            listar_execucoes_usuario(
                dias=dias
            )
        )

        total_rotas = (
            contar_rotas_usuario()
        )

        total_locais = (
            contar_locais_salvos_usuario()
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar página",
            e,
        )
        return

    total_km = (
        sum(
            float(
                item.get(
                    "distancia_m",
                    0,
                )
            )

            for item
            in execucoes
        )
        /
        1000
    )

    total_pontos = sum(
        int(
            item.get(
                "quantidade_locais",
                0,
            )
        )

        for item
        in execucoes
    )

    c1, c2, c3, c4, c5 = (
        st.columns(
            5
        )
    )

    c1.metric(
        "Rotas salvas",
        total_rotas,
    )

    c2.metric(
        "Locais salvos",
        total_locais,
    )

    c3.metric(
        "Cálculos",
        len(
            execucoes
        ),
    )

    c4.metric(
        "Km",
        f"{total_km:.1f}",
    )

    c5.metric(
        "Locais roteados",
        total_pontos,
    )

    st.divider()

    for execucao in execucoes:

        with st.container(
            border=True
        ):

            st.markdown(
                "### "
                +
                (
                    execucao.get(
                        "nome_rota"
                    )
                    or
                    "Rota não salva"
                )
            )

            st.caption(
                formatar_data(
                    execucao.get(
                        "created_at"
                    )
                )
            )

            if st.button(
                "📂 Abrir resultado",
                key=(
                    "abrir_hist_"
                    f"{execucao['id']}"
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

                st.session_state.resultado_historico = True
                st.session_state.rota_aberta_salva = False
                st.session_state.resultado_direto = False
                st.session_state.pagina_app = "nova"

                st.rerun()


# ============================================================
# LOCAIS
# ============================================================

def renderizar_locais_salvos():

    st.subheader(
        "📍 Locais salvos"
    )

    try:

        locais = (
            listar_locais_salvos_usuario()
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar página",
            e,
        )
        return

    editando_endereco = None

    if (
        st.session_state
        .local_endereco_editando_id
    ):

        editando_endereco = next(
            (
                item

                for item
                in locais

                if item[
                    "id"
                ]
                ==
                st.session_state
                .local_endereco_editando_id
            ),
            None,
        )

    if editando_endereco:

        with st.container(
            border=True
        ):

            st.markdown(
                "### 📍 Alterar endereço"
            )

            st.write(
                editando_endereco[
                    "endereco"
                ]
            )

            novo = campo_local(
                chave=(
                    "editar_endereco_"
                    f"{st.session_state.local_endereco_edit_version}"
                ),
                label="Novo endereço",
                placeholder=(
                    "Digite pelo menos 4 letras..."
                ),
            )

            c1, c2 = st.columns(
                2
            )

            with c1:

                if st.button(
                    "💾 Salvar endereço",
                    type="primary",
                    use_container_width=True,
                ):

                    if not novo:

                        st.error(
                            "Selecione o endereço."
                        )

                    else:

                        atualizar_local_banco(
                            editando_endereco[
                                "id"
                            ],
                            editando_endereco[
                                "nome"
                            ],
                            editando_endereco.get(
                                "observacao",
                                "",
                            ),
                            novo,
                        )

                        st.session_state.local_endereco_editando_id = None
                        st.session_state.local_endereco_edit_version += 1

                        st.rerun()

            with c2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                ):

                    st.session_state.local_endereco_editando_id = None
                    st.rerun()

    elif (
        st.session_state
        .local_cadastro_aberto
    ):

        versao = (
            st.session_state
            .local_form_version
        )

        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Cadastrar novo local"
            )

            nome = st.text_input(
                "Nome do cliente/local",
                key=(
                    f"novo_nome_{versao}"
                ),
            )

            endereco = campo_local(
                chave=(
                    f"novo_endereco_{versao}"
                ),
                label="Endereço",
                placeholder=(
                    "Digite pelo menos 4 letras..."
                ),
            )

            observacao = st.text_area(
                "Observação",
                key=(
                    f"nova_obs_{versao}"
                ),
            )

            c1, c2 = st.columns(
                2
            )

            with c1:

                if st.button(
                    "💾 Salvar local",
                    type="primary",
                    use_container_width=True,
                ):

                    if not endereco:

                        st.error(
                            "Selecione um endereço."
                        )

                    else:

                        salvar_local_banco(
                            (
                                nome.strip()
                                or
                                nome_padrao_local(
                                    endereco
                                )
                            ),
                            endereco,
                            observacao,
                        )

                        st.session_state.local_cadastro_aberto = False
                        st.session_state.local_form_version += 1

                        st.rerun()

            with c2:

                if st.button(
                    "Cancelar",
                    use_container_width=True,
                ):

                    st.session_state.local_cadastro_aberto = False
                    st.rerun()

    else:

        if st.button(
            "➕ Cadastrar novo local",
            type="primary",
            use_container_width=True,
        ):

            st.session_state.local_cadastro_aberto = True
            st.session_state.local_form_version += 1

            st.rerun()

    st.divider()

    filtro = normalizar_texto(
        st.text_input(
            "🔎 Buscar local"
        )
    )

    numero_local_visivel = 0
    for local in locais:

        texto = normalizar_texto(
            " ".join(
                [
                    str(
                        local.get(
                            "nome"
                        )
                        or
                        ""
                    ),

                    str(
                        local.get(
                            "endereco"
                        )
                        or
                        ""
                    ),

                    str(
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                ]
            )
        )

        if (
            filtro
            and
            filtro not in texto
        ):

            continue

        numero_local_visivel += 1
        with st.container(
            border=True
        ):

            st.markdown(
                f"### {numero_local_visivel}. {local['nome']}"
            )

            st.write(
                f"📍 {local['endereco']}"
            )

            if local.get(
                "observacao"
            ):

                st.caption(
                    f"📝 {local['observacao']}"
                )

            c1, c2 = (
                st.columns(
                    2
                )
            )

            with c1:

                if st.button(
                    "🚗 Usar na rota",
                    key=(
                        f"usar_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    iniciar_nova_rota(
                        [
                            local[
                                "id"
                            ]
                        ]
                    )

                    st.rerun()

            with c2:

                if st.button(
                    "📍 Alterar endereço",
                    key=(
                        f"end_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.local_endereco_editando_id = (
                        local[
                            "id"
                        ]
                    )

                    st.session_state.local_endereco_edit_version += 1

                    st.rerun()

            with st.expander(
                "⚙️ Editar / excluir"
            ):

                nome = st.text_input(
                    "Nome",
                    value=local[
                        "nome"
                    ],
                    key=(
                        f"nome_{local['id']}"
                    ),
                )

                observacao = st.text_area(
                    "Observação",
                    value=(
                        local.get(
                            "observacao"
                        )
                        or
                        ""
                    ),
                    key=(
                        f"obs_{local['id']}"
                    ),
                )

                if st.button(
                    "💾 Salvar",
                    key=(
                        f"salvar_{local['id']}"
                    ),
                    use_container_width=True,
                ):

                    atualizar_local_banco(
                        local[
                            "id"
                        ],
                        nome,
                        observacao,
                    )

                    st.rerun()

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"conf_{local['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"excluir_{local['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    excluir_local_banco(
                        local[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# MINHAS ROTAS
# ============================================================

def renderizar_minhas_rotas():

    st.subheader(
        "📂 Minhas rotas"
    )

    try:

        rotas = (
            listar_rotas_usuario()
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar página",
            e,
        )
        return

    if not rotas:

        st.info(
            "Você ainda não possui rotas salvas."
        )

        return

    for rota in rotas:

        dados = (
            rota.get(
                "dados"
            )
            or
            {}
        )

        with st.container(
            border=True
        ):

            st.markdown(
                f"### {rota['nome']}"
            )

            st.caption(
                "Última alteração: "
                f"{formatar_data(rota['updated_at'])}"
            )

            st.write(
                f"{len(dados.get('locais_base') or [])} "
                "local(is)"
            )

            c1, c2, c3 = (
                st.columns(
                    3
                )
            )

            with c1:

                if st.button(
                    "📂 Abrir",
                    key=(
                        f"abrir_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.resultado_rota = (
                        dados
                    )

                    st.session_state.rota_atual_id = (
                        rota[
                            "id"
                        ]
                    )

                    st.session_state.rota_atual_nome = (
                        rota[
                            "nome"
                        ]
                    )

                    st.session_state.rota_aberta_salva = True
                    st.session_state.resultado_historico = False
                    st.session_state.resultado_direto = False
                    st.session_state.pagina_app = "nova"

                    st.rerun()

            with c2:

                if st.button(
                    "✏️ Editar",
                    key=(
                        f"editar_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.edicao_rota = (
                        rota
                    )

                    st.session_state.edicao_version += 1
                    st.session_state.pagina_app = "editar"

                    st.rerun()

            with c3:

                if st.button(
                    "🔄 Recalcular",
                    key=(
                        f"recalc_{rota['id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        resultado = (
                            calcular_rota_a_partir_de_locais(
                                (
                                    dados.get(
                                        "locais_base"
                                    )
                                    or
                                    []
                                ),
                                bool(
                                    dados.get(
                                        "voltar_inicio",
                                        False,
                                    )
                                ),
                                dados.get(
                                    "objetivo",
                                    "Menor distância",
                                ),
                                dados.get(
                                    "modo",
                                    "Rápido",
                                ),
                                preservar_ordem=bool(dados.get("preservar_ordem", False)),
                            )
                        )

                        registrar_execucao_seguro(
                            resultado,
                            rota[
                                "id"
                            ],
                            rota[
                                "nome"
                            ],
                        )

                        st.session_state.resultado_rota = (
                            resultado
                        )

                        st.session_state.rota_atual_id = (
                            rota[
                                "id"
                            ]
                        )

                        st.session_state.rota_atual_nome = (
                            rota[
                                "nome"
                            ]
                        )

                        st.session_state.rota_aberta_salva = True
                        st.session_state.pagina_app = "nova"

                        st.rerun()

                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha na operação",
                            e,
                        )

            with st.expander(
                "⚙️ Gerenciar"
            ):

                if st.button("📑 Duplicar esta rota", key=f"duplicar_{rota['id']}"):
                    try:
                        salvar_rota_banco(f"{rota['nome']} (cópia)", dados)
                        st.success("Cópia criada. A rota original foi preservada.")
                        st.rerun()
                    except Exception as erro:
                        mostrar_erro_usuario("Falha ao duplicar rota", erro)

                novo_nome = (
                    st.text_input(
                        "Nome",
                        value=rota[
                            "nome"
                        ],
                        key=(
                            f"rota_nome_{rota['id']}"
                        ),
                    )
                )

                if st.button(
                    "💾 Renomear",
                    key=(
                        f"renomear_{rota['id']}"
                    ),
                ):

                    renomear_rota_banco(
                        rota[
                            "id"
                        ],
                        novo_nome,
                    )

                    st.rerun()

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"rota_conf_{rota['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir",
                    key=(
                        f"rota_excluir_{rota['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                ):

                    excluir_rota_banco(
                        rota[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# RECORRENTES
# ============================================================

def renderizar_recorrentes():

    st.subheader(
        "🔁 Rotas recorrentes"
    )

    if not permite_recorrentes_usuario():

        st.warning(
            "🔒 Rotas recorrentes estão "
            "disponíveis no plano Pro."
        )

        st.write(
            "No Pro você pode manter modelos "
            "de rotas e selecionar somente os "
            "clientes que serão atendidos no dia."
        )

        if st.button(
            "💎 Conhecer o plano Pro",
            type="primary",
        ):

            st.session_state.pagina_app = "planos"
            st.rerun()

        return

    locais = (
        listar_locais_salvos_usuario()
    )

    recorrentes = (
        listar_recorrentes_usuario()
    )

    mapa_locais = {
        local["id"]:
            local

        for local
        in locais
    }

    ids_locais = list(
        mapa_locais.keys()
    )

    recorrente_id = (
        st.session_state
        .recorrente_gerar_id
    )

    recorrente = next(
        (
            item

            for item
            in recorrentes

            if item[
                "id"
            ] == recorrente_id
        ),
        None,
    )

    if recorrente:

        ids_validos = [
            x

            for x
            in (
                recorrente.get(
                    "local_ids"
                )
                or
                []
            )

            if x in mapa_locais
        ]

        with st.container(
            border=True
        ):

            st.markdown(
                "### 🚗 Rota de hoje"
            )

            selecionados = (
                st.multiselect(
                    "Locais atendidos hoje",
                    options=ids_validos,
                    default=ids_validos,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )
            )

            if len(
                selecionados
            ) >= 2:

                inicio = st.selectbox(
                    "Ponto inicial",
                    selecionados,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )

                voltar = st.checkbox(
                    "Retornar ao início",
                    value=bool(
                        recorrente.get(
                            "voltar_inicio",
                            True,
                        )
                    ),
                )

                fim = None

                if not voltar:

                    fim = st.selectbox(
                        "Ponto final",
                        [
                            x

                            for x
                            in selecionados

                            if x != inicio
                        ],
                        format_func=(
                            lambda x:
                            mapa_locais[
                                x
                            ][
                                "nome"
                            ]
                        ),
                    )

                objetivo = st.radio(
                    "Otimizar por",
                    [
                        "Menor distância",
                        "Menor tempo",
                    ],
                    index=(
                        0
                        if recorrente.get(
                            "objetivo"
                        )
                        ==
                        "Menor distância"
                        else
                        1
                    ),
                    horizontal=True,
                )

                modos = (
                    opcoes_modo_usuario()
                )

                modo_original = (
                    recorrente.get(
                        "modo",
                        "Rápido",
                    )
                )

                modo_default = (
                    modo_original
                    if modo_original
                    in modos
                    else
                    "Rápido"
                )

                modo = st.radio(
                    "Modo",
                    modos,
                    index=modos.index(
                        modo_default
                    ),
                    horizontal=True,
                )

                if st.button(
                    "🚗 Calcular rota de hoje",
                    type="primary",
                    use_container_width=True,
                ):

                    intermediarios = [
                        x

                        for x
                        in selecionados

                        if x not in (
                            inicio,
                            fim,
                        )
                    ]

                    locais_solver = [
                        converter_local(
                            local_salvo_para_origem(
                                mapa_locais[
                                    inicio
                                ]
                            ),
                            "Início",
                        )
                    ]

                    for i, item_id in enumerate(
                        intermediarios,
                        start=1,
                    ):

                        locais_solver.append(
                            converter_local(
                                local_salvo_para_origem(
                                    mapa_locais[
                                        item_id
                                    ]
                                ),
                                f"Parada {i}",
                            )
                        )

                    if not voltar:

                        locais_solver.append(
                            converter_local(
                                local_salvo_para_origem(
                                    mapa_locais[
                                        fim
                                    ]
                                ),
                                "Fim",
                            )
                        )

                    try:

                        resultado = (
                            calcular_rota_a_partir_de_locais(
                                locais_solver,
                                voltar,
                                objetivo,
                                modo,
                            )
                        )

                        execucao, _ = (
                            registrar_execucao_seguro(
                                resultado,
                                nome_rota=(
                                    recorrente[
                                        "nome"
                                    ]
                                ),
                            )
                        )

                        st.session_state.resultado_rota = (
                            resultado
                        )

                        st.session_state.resultado_direto = True

                        st.session_state.resultado_direto_titulo = (
                            "🔁 Gerada a partir de "
                            f"**{recorrente['nome']}**"
                        )

                        st.session_state.ultima_execucao_id = (
                            execucao[
                                "id"
                            ]
                            if execucao
                            else
                            None
                        )

                        st.session_state.rota_nome_sugerido = (
                            recorrente[
                                "nome"
                            ]
                            +
                            " - "
                            +
                            datetime.now(
                                ZoneInfo(
                                    "America/Sao_Paulo"
                                )
                            ).strftime(
                                "%d-%m-%Y"
                            )
                        )

                        st.session_state.recorrente_gerar_id = None
                        st.session_state.pagina_app = "nova"

                        st.rerun()

                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha na operação",
                            e,
                        )

    if st.button(
        "➕ Criar rota recorrente",
        type="primary",
        use_container_width=True,
    ):

        st.session_state.recorrente_cadastro_aberto = True
        st.session_state.recorrente_form_version += 1

        st.rerun()

    if (
        st.session_state
        .recorrente_cadastro_aberto
    ):

        with st.container(
            border=True
        ):

            st.markdown(
                "### ➕ Nova recorrente"
            )

            nome = st.text_input(
                "Nome da recorrente"
            )

            selecionados = st.multiselect(
                "Locais",
                ids_locais,
                format_func=(
                    lambda x:
                    mapa_locais[
                        x
                    ][
                        "nome"
                    ]
                ),
            )

            inicio = None
            fim = None
            voltar = True

            if len(
                selecionados
            ) >= 2:

                inicio = st.selectbox(
                    "Ponto inicial",
                    selecionados,
                    format_func=(
                        lambda x:
                        mapa_locais[
                            x
                        ][
                            "nome"
                        ]
                    ),
                )

                voltar = st.checkbox(
                    "Retornar ao início",
                    value=True,
                )

                if not voltar:

                    fim = st.selectbox(
                        "Ponto final",
                        [
                            x

                            for x
                            in selecionados

                            if x != inicio
                        ],
                        format_func=(
                            lambda x:
                            mapa_locais[
                                x
                            ][
                                "nome"
                            ]
                        ),
                    )

            objetivo = st.radio(
                "Otimizar por",
                [
                    "Menor distância",
                    "Menor tempo",
                ],
                index=1,
                horizontal=True,
            )

            modo = st.radio(
                "Modo",
                opcoes_modo_usuario(),
                horizontal=True,
            )

            observacao = st.text_area(
                "Observação"
            )

            if st.button(
                "💾 Salvar recorrente",
                type="primary",
                use_container_width=True,
            ):

                try:

                    salvar_recorrente_banco(
                        nome,
                        selecionados,
                        inicio,
                        fim,
                        voltar,
                        objetivo,
                        modo,
                        observacao,
                    )

                    st.session_state.recorrente_cadastro_aberto = False

                    st.rerun()

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

    st.divider()

    for item in recorrentes:

        ids_validos = [
            x

            for x
            in (
                item.get(
                    "local_ids"
                )
                or
                []
            )

            if x in mapa_locais
        ]

        with st.container(
            border=True
        ):

            st.markdown(
                f"### 🔁 {item['nome']}"
            )

            st.caption(
                f"{len(ids_validos)} locais · "
                f"{item['objetivo']} · "
                f"{item['modo']}"
            )

            c1, c2 = (
                st.columns(
                    2
                )
            )

            with c1:

                if st.button(
                    "🚗 Gerar rota de hoje",
                    key=(
                        f"gerar_rec_{item['id']}"
                    ),
                    use_container_width=True,
                ):

                    st.session_state.recorrente_gerar_id = (
                        item[
                            "id"
                        ]
                    )

                    st.session_state.recorrente_gerar_version += 1

                    st.rerun()

            with c2:

                if st.button(
                    "📋 Duplicar",
                    key=(
                        f"dup_rec_{item['id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        duplicar_recorrente_banco(
                            item
                        )

                        st.rerun()

                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha na operação",
                            e,
                        )

            with st.expander(
                "🗑️ Excluir"
            ):

                confirmar = st.checkbox(
                    "Confirmo a exclusão",
                    key=(
                        f"conf_rec_{item['id']}"
                    ),
                )

                if st.button(
                    "🗑️ Excluir recorrente",
                    key=(
                        f"del_rec_{item['id']}"
                    ),
                    disabled=(
                        not confirmar
                    ),
                    use_container_width=True,
                ):

                    excluir_recorrente_banco(
                        item[
                            "id"
                        ]
                    )

                    st.rerun()


# ============================================================
# EDIÇÃO DE ROTA
# ============================================================

def renderizar_edicao_rota():

    rota = (
        st.session_state
        .edicao_rota
    )

    if not rota:

        st.warning(
            "Nenhuma rota selecionada."
        )

        return

    dados = (
        rota.get(
            "dados"
        )
        or
        {}
    )

    locais = (
        dados.get(
            "locais_base"
        )
        or
        []
    )

    st.subheader(
        f"✏️ Editar rota — {rota['nome']}"
    )

    ativos = []

    for i, local in enumerate(
        locais
    ):

        if st.checkbox(
            local[
                "nome"
            ],
            value=True,
            key=(
                f"edit_"
                f"{st.session_state.edicao_version}_"
                f"{i}"
            ),
        ):

            ativos.append(
                local
            )

    if len(
        ativos
    ) < 2:

        st.warning(
            "Mantenha pelo menos dois locais."
        )

        return

    validar_limite_visual = (
        limite_locais_usuario()
    )

    if (
        validar_limite_visual > 0
        and
        len(
            ativos
        ) > validar_limite_visual
    ):

        st.warning(
            "Seu plano permite "
            f"{validar_limite_visual} locais."
        )

    modo = dados.get(
        "modo",
        "Rápido",
    )

    if (
        modo == "Preciso"
        and
        not permite_preciso_usuario()
    ):

        st.warning(
            "A rota foi salva em modo Preciso, "
            "mas seu plano atual não possui "
            "essa modalidade. O recálculo "
            "será feito no modo Rápido."
        )

        modo = "Rápido"

    if st.button(
        "🚗 Recalcular rota editada",
        type="primary",
        use_container_width=True,
    ):

        try:

            resultado = (
                calcular_rota_a_partir_de_locais(
                    ativos,
                    bool(
                        dados.get(
                            "voltar_inicio",
                            False,
                        )
                    ),
                    dados.get(
                        "objetivo",
                        "Menor distância",
                    ),
                    modo,
                )
            )

            atualizar_rota_completa_banco(
                rota[
                    "id"
                ],
                rota[
                    "nome"
                ],
                resultado,
            )

            registrar_execucao_seguro(
                resultado,
                rota[
                    "id"
                ],
                rota[
                    "nome"
                ],
            )

            st.session_state.resultado_rota = resultado
            st.session_state.rota_atual_id = rota["id"]
            st.session_state.rota_atual_nome = rota["nome"]
            st.session_state.rota_aberta_salva = True
            st.session_state.pagina_app = "nova"
            st.session_state.edicao_rota = None

            st.rerun()

        except Exception as e:
            mostrar_erro_usuario(
                "Falha na operação",
                e,
            )


# ============================================================
# ADMINISTRAÇÃO
# ============================================================

def listar_usuarios_admin():
    return supabase_admin_request(
        "usuarios_app",
        "GET",
        params={
            "select": (
                "user_id,nome,email,plano,ativo,limite_rotas_mes,"
                "limite_locais_rota,permite_preciso,usar_limites_personalizados,"
                "is_admin,created_at,updated_at"
            ),
            "order": "created_at.desc",
        },
    )



def atualizar_usuario_admin(
    user_id_alvo,
    plano,
    ativo,
    personalizado,
    limite_rotas,
    limite_locais,
    permite_preciso,
):
    resultado = supabase_admin_request(
        "usuarios_app",
        "PATCH",
        params={"user_id": f"eq.{user_id_alvo}"},
        json_body={
            "plano": plano,
            "ativo": bool(ativo),
            "usar_limites_personalizados": bool(personalizado),
            "limite_rotas_mes": int(limite_rotas),
            "limite_locais_rota": int(limite_locais),
            "permite_preciso": bool(permite_preciso),
            "updated_at": agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:
        raise RuntimeError("Usuário não encontrado.")

    return resultado[0]



def atualizar_plano_admin(
    nome,
    limite_rotas,
    limite_locais,
    permite_preciso,
    permite_recorrentes,
    historico_completo,
    historico_dias,
    permite_exportacoes,
    preco_mensal,
):
    if nome == "Gratuito":
        preco_mensal = 0

    resultado = supabase_admin_request(
        "planos",
        "PATCH",
        params={"nome": f"eq.{nome}"},
        json_body={
            "limite_rotas_mes": int(limite_rotas),
            "limite_locais_rota": int(limite_locais),
            "permite_preciso": bool(permite_preciso),
            "permite_recorrentes": bool(permite_recorrentes),
            "historico_dias": None if historico_completo else int(historico_dias),
            "permite_exportacoes": bool(permite_exportacoes),
            "preco_mensal": float(preco_mensal),
            "moeda": "BRL",
            "updated_at": agora_iso(),
        },
        prefer="return=representation",
    )

    if not resultado:
        raise RuntimeError("Plano não encontrado.")

    return resultado[0]



def renderizar_administracao():

    try:
        exigir_admin_atual()
    except Exception as e:
        mostrar_erro_usuario(
            "Tentativa de acesso administrativo",
            e,
            "Acesso não autorizado.",
        )
        return

    st.subheader(
        "🛠️ Administração"
    )

    try:

        usuarios = (
            listar_usuarios_admin()
        )

        planos = (
            listar_planos()
        )

    except Exception as e:
        mostrar_erro_usuario(
            "Falha ao carregar página",
            e,
        )
        return

    try:
        uso_por_usuario = resumo_uso_mes_admin()
        assinaturas_admin = listar_assinaturas_admin()
    except Exception as e:
        logger.error(
            "Falha ao carregar resumo administrativo: %s",
            e,
            exc_info=(type(e), e, e.__traceback__),
        )
        uso_por_usuario = {}
        assinaturas_admin = []

    assinatura_por_usuario = {}
    for assinatura_item in assinaturas_admin:
        uid = assinatura_item.get("user_id")
        if uid and uid not in assinatura_por_usuario:
            assinatura_por_usuario[uid] = assinatura_item

    total_uso = sum(uso_por_usuario.values())

    c1, c2, c3 = (
        st.columns(
            3
        )
    )

    c1.metric(
        "Usuários",
        len(
            usuarios
        ),
    )

    c2.metric(
        "Ativos",
        sum(
            1

            for u
            in usuarios

            if u.get(
                "ativo",
                True,
            )
        ),
    )

    c3.metric(
        "Cálculos este mês",
        total_uso,
    )

    st.divider()

    st.subheader(
        "💎 Configuração dos planos"
    )

    st.caption(
        "Alterações aqui afetam automaticamente "
        "os usuários que não possuem "
        "regras individuais."
    )

    for plano in planos:

        with st.expander(
            f"💎 {plano['nome']}"
        ):

            if plano[
                "nome"
            ] == "Gratuito":

                st.text_input(
                    "Preço mensal",
                    value="Grátis",
                    disabled=True,
                    key=(
                        f"preco_gratis_{plano['nome']}"
                    ),
                )

                preco_mensal = 0.0

            else:

                preco_mensal = (
                    st.number_input(
                        "Preço mensal (R$)",
                        min_value=0.0,
                        max_value=99999.0,
                        value=float(
                            plano.get(
                                "preco_mensal"
                            )
                            or
                            0
                        ),
                        step=1.0,
                        format="%.2f",
                        key=(
                            f"plano_preco_{plano['nome']}"
                        ),
                    )
                )

                if preco_mensal <= 0:

                    st.warning(
                        "Enquanto o preço estiver em "
                        "R$ 0,00, o checkout deste "
                        "plano ficará desabilitado."
                    )

            limite_rotas = st.number_input(
                "Cálculos por mês",
                min_value=0,
                max_value=100000,
                value=int(
                    plano[
                        "limite_rotas_mes"
                    ]
                ),
                step=10,
                key=(
                    f"plano_rotas_{plano['nome']}"
                ),
            )

            limite_locais = st.number_input(
                "Locais por rota",
                min_value=2,
                max_value=MAX_SUPPORTED_LOCATIONS,
                value=int(
                    plano[
                        "limite_locais_rota"
                    ]
                ),
                key=(
                    f"plano_locais_{plano['nome']}"
                ),
            )

            permite_preciso = (
                st.checkbox(
                    "Permitir modo Preciso",
                    value=bool(
                        plano[
                            "permite_preciso"
                        ]
                    ),
                    key=(
                        f"plano_preciso_{plano['nome']}"
                    ),
                )
            )

            permite_recorrentes = (
                st.checkbox(
                    "Permitir rotas recorrentes",
                    value=bool(
                        plano[
                            "permite_recorrentes"
                        ]
                    ),
                    key=(
                        f"plano_rec_{plano['nome']}"
                    ),
                )
            )

            permite_exportacoes = (
                st.checkbox(
                    "Permitir exportações",
                    value=bool(
                        plano[
                            "permite_exportacoes"
                        ]
                    ),
                    key=(
                        f"plano_exp_{plano['nome']}"
                    ),
                )
            )

            historico_completo = (
                st.checkbox(
                    "Histórico completo",
                    value=(
                        plano[
                            "historico_dias"
                        ]
                        is None
                    ),
                    key=(
                        f"plano_hist_full_{plano['nome']}"
                    ),
                )
            )

            historico_dias = (
                st.number_input(
                    "Dias de histórico",
                    min_value=1,
                    max_value=3650,
                    value=int(
                        plano[
                            "historico_dias"
                        ]
                        or
                        30
                    ),
                    disabled=historico_completo,
                    key=(
                        f"plano_hist_{plano['nome']}"
                    ),
                )
            )

            if st.button(
                "💾 Salvar configuração do plano",
                key=(
                    f"salvar_plano_{plano['nome']}"
                ),
                use_container_width=True,
            ):

                try:

                    atualizar_plano_admin(
                        plano[
                            "nome"
                        ],
                        limite_rotas,
                        limite_locais,
                        permite_preciso,
                        permite_recorrentes,
                        historico_completo,
                        historico_dias,
                        permite_exportacoes,
                        preco_mensal,
                    )

                    st.success(
                        "Plano atualizado."
                    )

                    st.rerun()

                except Exception as e:
                    mostrar_erro_usuario(
                        "Falha em operação administrativa",
                        e,
                    )

    st.divider()

    st.subheader(
        "👥 Usuários"
    )

    filtro = normalizar_texto(
        st.text_input(
            "🔎 Buscar usuário"
        )
    )

    nomes_planos = [
        p[
            "nome"
        ]

        for p
        in planos

        if p.get(
            "ativo",
            True,
        )
    ]

    for usuario in usuarios:

        texto = normalizar_texto(
            (
                str(
                    usuario.get(
                        "nome"
                    )
                    or
                    ""
                )
                +
                " "
                +
                str(
                    usuario.get(
                        "email"
                    )
                    or
                    ""
                )
            )
        )

        if (
            filtro
            and
            filtro not in texto
        ):

            continue

        plano_usuario = next(
            (p for p in planos if p.get("nome") == (usuario.get("plano") or "Gratuito")),
            None,
        )

        efetivo = resolver_perfil_efetivo(
            usuario,
            plano_usuario,
        )

        uso = uso_por_usuario.get(
            usuario["user_id"],
            0,
        )

        assinatura = assinatura_por_usuario.get(
            usuario["user_id"]
        )

        titulo = (
            usuario.get(
                "nome"
            )
            or
            "Usuário"
        )

        if usuario.get(
            "is_admin"
        ):

            titulo += " 🛡️"

        with st.container(
            border=True
        ):

            st.markdown(
                f"### {titulo}"
            )

            st.caption(
                usuario.get(
                    "email"
                )
                or
                ""
            )

            c1, c2, c3, c4 = (
                st.columns(
                    4
                )
            )

            c1.metric(
                "Plano",
                efetivo[
                    "plano"
                ],
            )

            limite_mes = (
                efetivo[
                    "limite_rotas_mes"
                ]
            )

            c2.metric(
                "Uso mensal",
                (
                    f"{uso} / {limite_mes}"
                    if limite_mes > 0
                    else
                    f"{uso} / ∞"
                ),
            )

            c3.metric(
                "Locais/rota",
                efetivo[
                    "limite_locais_rota"
                ],
            )

            c4.metric(
                "Status",
                (
                    "Ativo"
                    if efetivo[
                        "ativo"
                    ]
                    else
                    "Inativo"
                ),
            )

            if assinatura:

                st.caption(
                    "💳 Mercado Pago: "
                    f"**{nome_status_assinatura(assinatura['status'])}**"
                    " · "
                    f"{formatar_dinheiro(assinatura['amount'])}/mês"
                )

            with st.expander(
                "⚙️ Gerenciar usuário"
            ):

                plano_atual = (
                    usuario.get(
                        "plano",
                        "Gratuito",
                    )
                )

                if plano_atual not in nomes_planos:

                    opcoes_planos = (
                        nomes_planos
                        +
                        [
                            plano_atual
                        ]
                    )

                else:

                    opcoes_planos = (
                        nomes_planos
                    )

                plano = st.selectbox(
                    "Plano",
                    opcoes_planos,
                    index=opcoes_planos.index(
                        plano_atual
                    ),
                    key=(
                        f"admin_plano_{usuario['user_id']}"
                    ),
                )

                ativo = st.checkbox(
                    "Conta ativa",
                    value=bool(
                        usuario.get(
                            "ativo",
                            True,
                        )
                    ),
                    key=(
                        f"admin_ativo_{usuario['user_id']}"
                    ),
                )

                personalizado = st.checkbox(
                    "Usar limites personalizados",
                    value=bool(
                        usuario.get(
                            "usar_limites_personalizados",
                            False,
                        )
                    ),
                    key=(
                        f"admin_personalizado_{usuario['user_id']}"
                    ),
                )

                plano_selecionado = next(
                    (p for p in planos if p.get("nome") == plano),
                    None,
                )

                if not plano_selecionado:
                    st.error("Plano selecionado não encontrado.")
                    continue

                if personalizado:

                    limite_rotas = (
                        st.number_input(
                            "Limite mensal individual",
                            min_value=0,
                            max_value=100000,
                            value=int(
                                usuario.get(
                                    "limite_rotas_mes"
                                )
                                or
                                0
                            ),
                            key=(
                                f"admin_rotas_{usuario['user_id']}"
                            ),
                        )
                    )

                    limite_locais = (
                        st.number_input(
                            "Locais/rota individual",
                            min_value=2,
                            max_value=MAX_SUPPORTED_LOCATIONS,
                            value=int(
                                usuario.get(
                                    "limite_locais_rota"
                                )
                                or
                                2
                            ),
                            key=(
                                f"admin_locais_{usuario['user_id']}"
                            ),
                        )
                    )

                    preciso = (
                        st.checkbox(
                            "Permitir modo Preciso individualmente",
                            value=bool(
                                usuario.get(
                                    "permite_preciso",
                                    False,
                                )
                            ),
                            key=(
                                f"admin_preciso_{usuario['user_id']}"
                            ),
                        )
                    )

                else:

                    limite_rotas = int(
                        plano_selecionado[
                            "limite_rotas_mes"
                        ]
                    )

                    limite_locais = int(
                        plano_selecionado[
                            "limite_locais_rota"
                        ]
                    )

                    preciso = bool(
                        plano_selecionado[
                            "permite_preciso"
                        ]
                    )

                    st.info(
                        "Este usuário seguirá "
                        "automaticamente as regras "
                        f"do plano **{plano}**."
                    )

                if st.button(
                    "💾 Salvar alterações",
                    key=(
                        f"admin_salvar_{usuario['user_id']}"
                    ),
                    use_container_width=True,
                ):

                    try:

                        atualizar_usuario_admin(
                            usuario[
                                "user_id"
                            ],
                            plano,
                            ativo,
                            personalizado,
                            limite_rotas,
                            limite_locais,
                            preciso,
                        )

                        st.session_state.admin_refresh += 1

                        st.success(
                            "Usuário atualizado."
                        )

                        st.rerun()

                    except Exception as e:
                        mostrar_erro_usuario(
                            "Falha na operação",
                            e,
                        )


# ============================================================
# NOVA ROTA A PARTIR DE UMA ROTA SALVA
# ============================================================

def preparar_local_rota_salva_para_campo(local):
    """Usa as coordenadas salvas; não geocodifica novamente ao carregar."""
    if not isinstance(local, dict):
        raise ValueError("A rota salva contém um local inválido.")
    latitude = float(local["latitude"])
    longitude = float(local["longitude"])
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("A rota salva contém coordenadas fora do intervalo válido.")
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise ValueError("A rota salva contém coordenadas inválidas.")
    rotulo = str(local.get("label") or local.get("nome") or local.get("name") or "").strip()
    if not rotulo:
        rotulo = f"{latitude:.6f}, {longitude:.6f}"
    dados = dict(local)
    dados.update({"label": rotulo, "latitude": latitude, "longitude": longitude})
    return dados


def carregar_rota_base_no_editor(rota):
    """Preenche o editor com os locais salvos em ordem original."""
    dados = rota.get("dados") or {}
    originais = dados.get("locais_base") or []
    if len(originais) < 2:
        raise ValueError("Esta rota salva não possui ao menos dois locais válidos.")
    pontos = []
    for item in originais:
        st.session_state.rota_base_edicao_sequencia += 1
        pontos.append({
            "uid": st.session_state.rota_base_edicao_sequencia,
            "local": preparar_local_rota_salva_para_campo(item),
        })
    st.session_state.rota_base_edicao_id = rota["id"]
    st.session_state.rota_base_edicao_nome = rota["nome"]
    st.session_state.rota_base_edicao_pontos = pontos
    st.session_state.rota_base_edicao_voltar = bool(dados.get("voltar_inicio", False))
    st.session_state.rota_base_edicao_versao += 1
    st.session_state.ordem_manualmente_alterada = False


def remover_ponto_editor_rota_salva(uid):
    """Exclusão local no editor; a rota no banco só muda após salvar."""
    pontos = st.session_state.rota_base_edicao_pontos
    if len(pontos) <= 2:
        return
    st.session_state.rota_base_edicao_pontos = [
        p for p in pontos if p["uid"] != uid
    ]


def inserir_parada_editor_rota_salva():
    pontos = st.session_state.rota_base_edicao_pontos
    st.session_state.rota_base_edicao_sequencia += 1
    novo = {"uid": st.session_state.rota_base_edicao_sequencia, "local": None}
    if st.session_state.rota_base_edicao_voltar:
        pontos.append(novo)
    else:
        pontos.insert(max(1, len(pontos) - 1), novo)


def campo_editavel_rota_salva(ponto, numero, tipo, foco):
    """Mostra o endereço anterior e permite trocá-lo com autocomplete."""
    a, b = st.columns([8, 2], vertical_alignment="bottom")
    with a:
        encontrado = campo_local(
            chave=f"editor_rota_{st.session_state.rota_base_edicao_versao}_{ponto['uid']}",
            label=f"{numero}. {tipo}",
            placeholder="Endereço ou latitude, longitude...",
            focus=foco,
            default_local=ponto.get("local"),
        )
        if isinstance(encontrado, dict) and "latitude" in encontrado and "longitude" in encontrado:
            ponto["local"] = encontrado
        elif not encontrado:
            # Quando texto está sendo editado, não persistir endereço antigo sem confirmação.
            pass
    with b:
        if st.button(
            "🗑️ Excluir", key=f"excluir_editor_{st.session_state.rota_base_edicao_versao}_{ponto['uid']}",
            disabled=len(st.session_state.rota_base_edicao_pontos) <= 2,
            use_container_width=True,
        ):
            remover_ponto_editor_rota_salva(ponto["uid"])
            st.rerun()
    return encontrado if isinstance(encontrado, dict) else None


def renderizar_ferramentas_editor_rota(lista, retorno):
    """Edição avançada de rota salva: drag/drop, inversão, tabela e mapa."""
    if not lista or len(lista) < 2:
        return
    versao = st.session_state.rota_base_edicao_versao
    with st.expander("🛠️ Reorganizar e corrigir os pontos", expanded=False):
        if not retorno and st.button("🔁 Inverter origem e destino", key=f"inverter_base_{versao}"):
            st.session_state.rota_base_edicao_pontos = list(reversed(lista))
            st.session_state.rota_base_edicao_versao += 1
            st.session_state.ordem_manualmente_alterada = True
            st.session_state[f"preservar_ordem_{st.session_state.form_version}_Usar rota salva"] = True
            st.rerun()
        elif retorno:
            st.caption("Desative 'Retornar ao ponto inicial' antes de inverter a rota.")

        indices_intermediarios = list(range(1, len(lista) if retorno else len(lista)-1))
        if len(indices_intermediarios) > 1:
            st.markdown("**Reorganizar paradas manualmente**")
            nomes = []
            por_nome = {}
            for idx in indices_intermediarios:
                p = lista[idx]
                nome = f"{p['uid']} · {str((p.get('local') or {}).get('label') or 'Nova parada')[:70]}"
                nomes.append(nome)
                por_nome[nome] = p
            if sort_items:
                ordenados = sort_items(nomes, key=f"sort_rotaki_{st.session_state.rota_base_edicao_id}_{versao}")
                if ordenados and ordenados != nomes:
                    if st.button("✅ Aplicar ordem arrastada", key=f"aplicar_sort_{versao}"):
                        itens = [por_nome[n] for n in ordenados]
                        st.session_state.rota_base_edicao_pontos = [lista[0]] + itens + ([] if retorno else [lista[-1]])
                        st.session_state.rota_base_edicao_versao += 1
                        st.session_state.ordem_manualmente_alterada = True
                        st.session_state[f"preservar_ordem_{st.session_state.form_version}_Usar rota salva"] = True
                        st.rerun()
            else:
                st.caption("Para arrastar e soltar, adicione `streamlit-sortables` ao requirements.txt.")
                for idx in indices_intermediarios:
                    p = lista[idx]
                    a,b,c = st.columns([6,1,1])
                    a.write(str((p.get('local') or {}).get('label') or 'Nova parada'))
                    if b.button("⬆",key=f"up_{versao}_{p['uid']}",disabled=idx == indices_intermediarios[0]):
                        lista[idx-1], lista[idx] = lista[idx],lista[idx-1]
                        st.session_state.ordem_manualmente_alterada = True
                        st.session_state[f"preservar_ordem_{st.session_state.form_version}_Usar rota salva"] = True
                        st.session_state.rota_base_edicao_versao += 1
                        st.rerun()
                    if c.button("⬇",key=f"down_{versao}_{p['uid']}",disabled=idx == indices_intermediarios[-1]):
                        lista[idx+1], lista[idx] = lista[idx],lista[idx+1]
                        st.session_state.ordem_manualmente_alterada = True
                        st.session_state[f"preservar_ordem_{st.session_state.form_version}_Usar rota salva"] = True
                        st.session_state.rota_base_edicao_versao += 1
                        st.rerun()

        st.markdown("**Edição em tabela**")
        st.caption("Edite nomes e coordenadas em lote. Alterar apenas o texto NÃO altera a posição geográfica; para isso, informe novas coordenadas ou corrija no mapa.")
        tabela = pd.DataFrame([{
            'ID':str(p['uid']),
            'Endereço ou nome':str((p.get('local') or {}).get('label') or ''),
            'Latitude':(p.get('local') or {}).get('latitude'),
            'Longitude':(p.get('local') or {}).get('longitude'),
        } for p in lista])
        edited = st.data_editor(tabela, hide_index=True, disabled=['ID'],
                                use_container_width=True, key=f"tabela_lote_{versao}")
        if st.button("💾 Aplicar edições em lote",key=f"aplicar_lote_{versao}"):
            novos = []
            try:
                for i, linha in edited.reset_index(drop=True).iterrows():
                    nome = str(linha['Endereço ou nome'] or '').strip()
                    if not nome:
                        raise ValueError(f"Ponto {i+1}: informe um nome ou endereço.")
                    lat, lon = validar_coordenadas(linha['Latitude'], linha['Longitude'])
                    anterior = lista[i]
                    local = dict(anterior.get('local') or {})
                    local.update({'label': nome,'nome': nome,'latitude': lat, 'longitude': lon})
                    novos.append({'uid': anterior['uid'], 'local': local})
                st.session_state.rota_base_edicao_pontos = novos
                st.session_state.rota_base_edicao_versao += 1
                st.rerun()
            except (ValueError, TypeError) as erro:
                st.error(str(erro))

        st.markdown("**Corrigir coordenadas clicando no mapa**")
        opcoes = list(range(len(lista)))
        indice = st.selectbox(
            "Escolha o ponto que deseja corrigir",
            opcoes,
            format_func=lambda i: (
                f"{i+1}. {(lista[i].get('local') or {}).get('label') or 'Novo ponto'}"
            ),
            key=f"map_editor_selec_{versao}",
        )
        ponto_selecionado = lista[indice]
        local = ponto_selecionado.get("local") or {}
        uid_ponto = str(ponto_selecionado["uid"])
        # O candidato é individual por ponto. Selecionar outro local não
        # reutiliza acidentalmente o clique anterior.
        chave_clique = f"clique_mapa_{versao}_{uid_ponto}"
        clique = st.session_state.get(chave_clique)
        if clique:
            try:
                lat_candidato, lon_candidato = validar_coordenadas(
                    clique["lat"], clique["lng"]
                )
            except (TypeError, ValueError, KeyError):
                st.session_state.pop(chave_clique, None)
                clique = None

        coordenadas_existentes = [
            p.get("local") or {}
            for p in lista
            if (p.get("local") or {}).get("latitude") is not None
            and (p.get("local") or {}).get("longitude") is not None
        ]
        # Começar o mapa próximo do ponto escolhido, e não obrigatoriamente
        # perto da primeira parada da rota.
        referencia = (
            local
            if local.get("latitude") is not None
            and local.get("longitude") is not None
            else coordenadas_existentes[0]
            if coordenadas_existentes
            else {"latitude": -27.1, "longitude": -48.9}
        )
        m = folium.Map(
            location=[float(referencia["latitude"]), float(referencia["longitude"])],
            zoom_start=15,
        )
        for i, p in enumerate(lista):
            loc = p.get("local") or {}
            if loc.get("latitude") is not None and loc.get("longitude") is not None:
                folium.Marker(
                    [float(loc["latitude"]), float(loc["longitude"])],
                    tooltip=f"{i+1}. {str(loc.get('label') or '')[:80]}",
                    icon=folium.Icon(color="red" if indice == i else "blue"),
                ).add_to(m)

        # Exibir o PIN VERDE no mesmo mapa, antes da confirmação, para que
        # o usuário confira visualmente a localização clicada.
        if clique:
            folium.Marker(
                [lat_candidato, lon_candidato],
                tooltip="📍 Nova posição selecionada — ainda não salva",
                popup="Nova posição selecionada (aguardando confirmação)",
                icon=folium.Icon(color="green", icon="ok-sign"),
                z_index_offset=1000,
            ).add_to(m)

        st.caption(
            "🔴 Ponto atual • 🔵 Outros locais • 🟢 Nova posição clicada. "
            "Clique no mapa para posicionar ou mover o pin verde."
        )
        saida = st_folium(
            m,
            height=360,
            use_container_width=True,
            returned_objects=["last_clicked"],
            key=f"mapa_editor_{versao}_{uid_ponto}",
        )
        novo_clique = (saida or {}).get("last_clicked")
        if novo_clique:
            try:
                nova_lat, nova_lon = validar_coordenadas(
                    novo_clique["lat"], novo_clique["lng"]
                )
            except (KeyError, TypeError, ValueError):
                st.warning("O mapa retornou coordenadas inválidas. Clique novamente.")
            else:
                # O componente pode devolver o último clique em reruns;
                # atualizar somente quando a posição realmente mudar evita
                # um ciclo infinito de rerun e mantém o pin visível.
                if (
                    not clique
                    or not math.isclose(nova_lat, lat_candidato, abs_tol=1e-8)
                    or not math.isclose(nova_lon, lon_candidato, abs_tol=1e-8)
                ):
                    st.session_state[chave_clique] = {
                        "lat": nova_lat,
                        "lng": nova_lon,
                    }
                    st.rerun()

        if clique:
            st.success(
                f"📍 Pin verde em: {lat_candidato:.6f}, {lon_candidato:.6f}. "
                "Confira no mapa e confirme abaixo para salvar."
            )
        if st.button(
            "✅ Confirmar posição do pin verde",
            disabled=not clique,
            key=f"confirmar_mapa_{versao}_{uid_ponto}",
        ):
            try:
                lat, lon = validar_coordenadas(clique["lat"], clique["lng"])
                novo = dict(local)
                novo.update(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "label": str(local.get("label") or f"{lat:.6f}, {lon:.6f}"),
                    }
                )
                ponto_selecionado["local"] = novo
                st.session_state.pop(chave_clique, None)
                st.session_state.rota_base_edicao_versao += 1
                st.rerun()
            except (KeyError, TypeError, ValueError) as erro:
                st.error(str(erro))



# ============================================================
# PÁGINAS
# ============================================================

# O Streamlit preserva a posição vertical entre reruns. Ao navegar
# para outra página interna, reposicionamos a interface no topo.
rolar_para_topo_ao_trocar_pagina()

if (
    st.session_state.pagina_app
    == "planos"
):

    renderizar_planos()
    st.stop()


if (
    st.session_state.pagina_app
    == "administracao"
):

    renderizar_administracao()
    st.stop()


if (
    st.session_state.pagina_app
    == "minhas"
):

    renderizar_minhas_rotas()
    st.stop()


if (
    st.session_state.pagina_app
    == "locais"
):

    renderizar_locais_salvos()
    st.stop()


if (
    st.session_state.pagina_app
    == "recorrentes"
):

    renderizar_recorrentes()
    st.stop()


if (
    st.session_state.pagina_app
    == "execucoes"
):

    renderizar_execucoes_entrega()
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


if (
    st.session_state.resultado_rota
    and
    (
        st.session_state.resultado_direto
        or
        st.session_state.rota_aberta_salva
        or
        st.session_state.resultado_historico
    )
):

    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )

    st.stop()


# ============================================================
# NOVA ROTA
# ============================================================

st.subheader(
    "➕ Nova rota"
)

limite_locais = (
    limite_locais_usuario()
)

if limite_locais:

    st.caption(
        "Seu plano permite até "
        f"**{limite_locais} locais por rota**."
    )

modo_entrada = st.radio(
    "Como deseja informar os locais?",
    [
        "Digitar manualmente",
        "Importar CSV/Excel",
        "Selecionar locais salvos",
        "Usar rota salva",
    ],
    index=(
        2
        if
        st.session_state
        .preset_local_salvo_ids
        else
        0
    ),
    horizontal=True,
    key=(
        f"modo_entrada_"
        f"{st.session_state.form_version}"
    ),
)

st.caption(
    "Digite o endereço **ou as coordenadas diretamente no mesmo campo**, "
    "no formato `-27.0985, -48.9123` (latitude, longitude). "
    'No CSV, coloque coordenadas entre aspas: `"-27.0985, -48.9123"`.'
)

ponto_inicio = None
ponto_fim = None
paradas = []
voltar_inicio = False


# ============================================================
# MANUAL
# ============================================================

if modo_entrada == "Digitar manualmente":
    # Retorno é renderizado após o último ponto do formulário.
    voltar_inicio = bool(st.session_state.get("manual_retorno_inicio", False))
    ponto_inicio = campo_local(
        chave=f"inicio_{st.session_state.form_version}_{st.session_state.manual_campos_version}",
        label="1. Ponto inicial",
        placeholder="Endereço completo ou latitude, longitude...",
    )

    for i in range(st.session_state.num_paradas):
        paradas.append(campo_local(
            chave=f"parada_{st.session_state.form_version}_{st.session_state.manual_campos_version}_{i}",
            label=f"{i + 2}. Parada {i + 1}",
            placeholder="Endereço completo ou latitude, longitude...",
            focus=ponto_inicio,
        ))

    col_adicionar, col_remover = st.columns(2)
    with col_adicionar:
        if st.button("➕ Adicionar parada", use_container_width=True):
            st.session_state.num_paradas += 1
            st.rerun()
    with col_remover:
        if st.button(
            "➖ Remover parada",
            disabled=st.session_state.num_paradas <= 1,
            use_container_width=True,
        ):
            st.session_state.num_paradas -= 1
            st.rerun()

    numero_fim = st.session_state.num_paradas + 2
    if not voltar_inicio:
        ponto_fim = campo_local(
            chave=f"fim_{st.session_state.form_version}_{st.session_state.manual_campos_version}",
            label=f"{numero_fim}. Ponto final",
            placeholder="Endereço completo ou latitude, longitude...",
            focus=ponto_inicio,
        )

    voltar_inicio = st.checkbox(
        "🔄 Retornar ao ponto inicial", key="manual_retorno_inicio"
    )
    if voltar_inicio:
        ponto_fim = ponto_inicio


# ============================================================
# ROTA SALVA - ENDEREÇOS EDITÁVEIS
# ============================================================

elif modo_entrada == "Usar rota salva":
    rota_salva_selecionada = None
    enderecos_invalidos_rota_salva = False
    try:
        rotas_disponiveis = listar_rotas_usuario()
    except Exception as erro:
        mostrar_erro_usuario("Falha ao carregar rotas salvas", erro)
        rotas_disponiveis = []

    if not rotas_disponiveis:
        st.info("Nenhuma rota salva disponível. Salve uma rota primeiro em Minhas rotas.")
    else:
        por_id = {str(r["id"]): r for r in rotas_disponiveis}
        escolha = st.selectbox(
            "Selecione uma rota salva para usar como base",
            options=list(por_id),
            index=None,
            placeholder="Escolha a rota...",
            format_func=lambda rid: por_id[rid].get("nome", "Rota sem nome"),
            key=f"rota_base_escolhida_{st.session_state.form_version}",
        )
        if escolha is not None:
            rota_salva_selecionada = por_id[escolha]
            if str(st.session_state.rota_base_edicao_id) != escolha:
                try:
                    carregar_rota_base_no_editor(rota_salva_selecionada)
                except (ValueError, KeyError, TypeError) as erro:
                    st.error(f"Não foi possível carregar a rota: {erro}")
                    st.stop()

            st.caption(
                "Altere os endereços diretamente, exclua pontos ou adicione novas paradas. "
                "As mudanças só substituem a rota original ao clicar em "
                "**Recalcular e salvar alterações**."
            )
            lista = st.session_state.rota_base_edicao_pontos
            retorno_atual = bool(st.session_state.rota_base_edicao_voltar)
            renderizar_ferramentas_editor_rota(lista, retorno_atual)
            foco = None
            valores = {}

            # Ponto inicial
            primeiro = lista[0]
            valores[primeiro["uid"]] = campo_editavel_rota_salva(
                primeiro, 1, "Ponto inicial", None
            )
            ponto_inicio = valores[primeiro["uid"]]
            foco = ponto_inicio or primeiro.get("local")

            # Paradas (último é destino somente em rota sem retorno).
            intermediarios = lista[1:] if retorno_atual else lista[1:-1]
            for numero_parada, ponto in enumerate(intermediarios, start=1):
                valores[ponto["uid"]] = campo_editavel_rota_salva(
                    ponto, numero_parada + 1, f"Parada {numero_parada}", foco
                )
                paradas.append(valores[ponto["uid"]])

            st.button(
                "➕ Adicionar parada",
                key=f"adicionar_editor_{st.session_state.rota_base_edicao_versao}",
                on_click=inserir_parada_editor_rota_salva,
                use_container_width=True,
                disabled=(limite_locais > 0 and len(lista) >= limite_locais),
            )

            if not retorno_atual:
                ultimo = lista[-1]
                valores[ultimo["uid"]] = campo_editavel_rota_salva(
                    ultimo, len(lista), "Ponto final", foco
                )
                ponto_fim = valores[ultimo["uid"]]

            # Sempre após o último ponto da rota.
            voltar_inicio = st.checkbox(
                "🔄 Retornar ao ponto inicial",
                value=retorno_atual,
                key=f"editor_retorno_{st.session_state.rota_base_edicao_versao}",
            )
            st.session_state.rota_base_edicao_voltar = voltar_inicio
            if voltar_inicio != retorno_atual:
                # Reclassifica imediatamente a última parada como destino
                # (ou o inverso), sem esperar outra interação do usuário.
                st.rerun()
            if voltar_inicio:
                ponto_fim = ponto_inicio

            enderecos_invalidos_rota_salva = any(
                valores.get(p["uid"]) is None for p in lista
            )
            if enderecos_invalidos_rota_salva:
                st.warning(
                    "Existem endereços ainda não confirmados. "
                    "Escolha uma sugestão para cada ponto ou informe coordenadas válidas."
                )
            if limite_locais > 0 and len(lista) > limite_locais:
                st.warning(f"Seu plano permite até {limite_locais} locais por rota.")


# ============================================================
# LOCAIS SALVOS
# ============================================================

elif (
    modo_entrada
    ==
    "Selecionar locais salvos"
):

    locais_salvos = (
        listar_locais_salvos_usuario()
    )

    mapa = {
        item[
            "id"
        ]:
            item

        for item
        in locais_salvos
    }

    selecionados = st.multiselect(
        "Locais",
        list(
            mapa.keys()
        ),
        default=[
            x

            for x
            in st.session_state
            .preset_local_salvo_ids

            if x in mapa
        ],
        format_func=(
            lambda x: f"{mapa[x]['nome']} — {mapa[x].get('endereco', '')}"
        ),
    )

    if selecionados:
        st.markdown("**Endereços selecionados:**")
        for numero, identificador in enumerate(selecionados, start=1):
            registro = mapa[identificador]
            st.write(f"{numero}. {registro['nome']} — {registro.get('endereco', '')}")

    if (
        limite_locais > 0
        and
        len(
            selecionados
        ) > limite_locais
    ):

        st.warning(
            "Seu plano permite "
            f"{limite_locais} locais."
        )

    if len(
        selecionados
    ) >= 2:

        inicio_id = st.selectbox(
            "Ponto inicial",
            selecionados,
            format_func=(
                lambda x: f"{selecionados.index(x) + 1}. {mapa[x]['nome']}"
            ),
        )

        ponto_inicio = (
            local_salvo_para_origem(
                mapa[
                    inicio_id
                ]
            )
        )

        voltar_inicio = bool(st.session_state.get("locais_retorno_inicio", False))
        fim_id = None

        if not voltar_inicio:
            fim_id = st.selectbox(
                "Ponto final",
                [
                    x

                    for x
                    in selecionados

                    if x != inicio_id
                ],
                format_func=(
                    lambda x: f"{selecionados.index(x) + 1}. {mapa[x]['nome']}"
                ),
            )

            ponto_fim = (
                local_salvo_para_origem(
                    mapa[
                        fim_id
                    ]
                )
            )

        voltar_inicio = st.checkbox(
            "🔄 Retornar ao ponto inicial", key="locais_retorno_inicio"
        )
        if voltar_inicio:
            ponto_fim = ponto_inicio

        paradas = [
            local_salvo_para_origem(
                mapa[
                    x
                ]
            )

            for x
            in selecionados

            if x not in (
                inicio_id,
                fim_id,
            )
        ]


# ============================================================
# IMPORTAÇÃO
# ============================================================

else:

    modelo = (
        'endereco\n'
        '"Havan, Brusque, SC"\n'
        '"Prefeitura Municipal de Brusque, Brusque, SC"\n'
        '"-27.0985, -48.9123"\n'
    )

    st.download_button(
        "⬇️ Baixar modelo CSV",
        data=modelo.encode(
            "utf-8-sig"
        ),
        file_name="modelo.csv",
    )

    arquivo = st.file_uploader(
        "CSV ou XLSX",
        type=[
            "csv",
            "xlsx",
        ],
    )

    if arquivo is None:
        st.session_state.import_file_hash = None

    if arquivo:

        try:

            enderecos, hash_arquivo = (
                ler_arquivo_paradas(
                    arquivo
                )
            )

            # Um novo arquivo começa sem exclusões herdadas do anterior.
            if (
                st.session_state.import_file_hash
                != hash_arquivo
            ):
                limpar_enderecos_manuais_apos_importacao()
                st.session_state.import_file_hash = hash_arquivo
                st.session_state.import_removed_indices = []
                st.session_state.import_manual_overrides = {}
                st.session_state.import_editing_index = None
                st.session_state.import_edit_version += 1

            removidos = set(
                st.session_state.import_removed_indices
            )

            enderecos_ativos = [
                (indice_original, endereco)
                for indice_original, endereco
                in enumerate(enderecos)
                if indice_original not in removidos
            ]

            numeracao_csv = {
                indice: pos
                for pos, (indice, _) in enumerate(enderecos_ativos, start=1)
            }

            st.caption(
                f"{len(enderecos_ativos)} endereço(s) ativo(s) "
                f"de {len(enderecos)} importado(s)."
            )

            indices_duplicados = localizar_duplicados_importacao(
                enderecos_ativos
            )
            if indices_duplicados:
                st.error(
                    f"🔴 {len(indices_duplicados)} linha(s) com endereços "
                    "repetidos no arquivo. Os itens aparecem destacados em "
                    "vermelho abaixo. Confira, edite ou exclua as duplicidades."
                )

            if removidos:
                if st.button(
                    "↩️ Restaurar endereços removidos",
                    key=f"restaurar_import_{hash_arquivo}",
                    use_container_width=True,
                ):
                    st.session_state.import_removed_indices = []
                    st.rerun()

            if (
                limite_locais > 0
                and len(enderecos_ativos) > limite_locais
            ):
                st.warning(
                    "O arquivo possui "
                    f"{len(enderecos_ativos)} locais ativos, "
                    "mas seu plano permite "
                    f"{limite_locais}. "
                    "Remova alguns pontos antes de calcular."
                )

            confirmados_por_id = {}

            # Substituições manuais feitas pelo usuário para linhas cujo
            # resultado automático do CSV não ficou correto. A chave é o
            # índice original da linha no arquivo; o valor é um local
            # normalizado, no mesmo formato do autocomplete manual.
            substituicoes = dict(
                st.session_state.import_manual_overrides
                or {}
            )

            if enderecos_ativos:
                with st.spinner(
                    "Localizando endereços..."
                ):
                    resultados = {
                        indice_original:
                            buscar_endereco_importado_cache_v2(
                                endereco,
                                ORS_KEY,
                            )
                        for indice_original, endereco
                        in enderecos_ativos
                        if indice_original not in substituicoes
                    }

                for indice_original, endereco in enderecos_ativos:
                    substituicao = substituicoes.get(
                        indice_original
                    )
                    candidatos = resultados.get(
                        indice_original,
                        [],
                    )

                    coluna_endereco, coluna_editar, coluna_remover = st.columns(
                        [10, 1, 1]
                    )

                    with coluna_endereco:
                        if indice_original in indices_duplicados:
                            st.markdown(
                                '<div style="border-left:4px solid #dc2626;'
                                'background:rgba(220,38,38,.10);'
                                'padding:8px 12px;margin-bottom:6px;'
                                'border-radius:4px;color:#dc2626">'
                                f'🔴 <strong>Endereço repetido (item {numeracao_csv[indice_original]}): '
                                f'{html.escape(endereco)}</strong></div>',
                                unsafe_allow_html=True,
                            )
                        if substituicao:
                            confirmados_por_id[
                                indice_original
                            ] = substituicao

                            st.markdown(
                                f"**{numeracao_csv[indice_original]}. {endereco}**"
                            )
                            st.caption(
                                "✏️ Local corrigido manualmente: "
                                f"{substituicao.get('label', endereco)}"
                            )

                        elif not candidatos:
                            st.error(
                                f"{numeracao_csv[indice_original]}. Não encontrado: "
                                f"{endereco}"
                            )

                        else:
                            pos = st.selectbox(
                                f"{numeracao_csv[indice_original]}. {endereco}",
                                list(
                                    range(
                                        len(candidatos)
                                    )
                                ),
                                key=(
                                    f"imp_{hash_arquivo}_"
                                    f"{indice_original}"
                                ),
                                format_func=(
                                    lambda x,
                                    c=candidatos:
                                    c[x]["label"]
                                ),
                            )

                            confirmados_por_id[
                                indice_original
                            ] = candidatos[pos]

                    with coluna_editar:
                        st.write("")
                        if st.button(
                            "✏️",
                            key=(
                                f"editar_import_"
                                f"{hash_arquivo}_"
                                f"{indice_original}"
                            ),
                            help=(
                                "Pesquisar outro local e substituir "
                                "o resultado desta linha"
                            ),
                            use_container_width=True,
                        ):
                            st.session_state.import_editing_index = (
                                indice_original
                            )
                            st.session_state.import_edit_version += 1
                            st.rerun()

                    with coluna_remover:
                        st.write("")
                        if st.button(
                            "✖",
                            key=(
                                f"remover_import_"
                                f"{hash_arquivo}_"
                                f"{indice_original}"
                            ),
                            help="Remover este endereço da rota",
                            use_container_width=True,
                        ):
                            novos_removidos = set(
                                st.session_state.import_removed_indices
                            )
                            novos_removidos.add(
                                indice_original
                            )
                            st.session_state.import_removed_indices = sorted(
                                novos_removidos
                            )

                            if (
                                st.session_state.import_editing_index
                                == indice_original
                            ):
                                st.session_state.import_editing_index = None

                            st.rerun()

                    if (
                        st.session_state.import_editing_index
                        == indice_original
                    ):
                        st.info(
                            "Digite pelo menos "
                            f"{AUTOCOMPLETE_MIN_CHARS} caracteres para "
                            "pesquisar o local correto. Ao selecionar um "
                            "resultado, ele substituirá esta linha do CSV."
                        )

                        chave_edicao = (
                            f"editar_local_csv_{hash_arquivo}_"
                            f"{indice_original}_"
                            f"{st.session_state.import_edit_version}"
                        )

                        novo_local = campo_local(
                            chave_edicao,
                            "Pesquisar local correto",
                            (
                                "Digite pelo menos "
                                f"{AUTOCOMPLETE_MIN_CHARS} caracteres..."
                            ),
                        )

                        col_aplicar_info, col_cancelar = st.columns(
                            [5, 1]
                        )

                        with col_aplicar_info:
                            if novo_local:
                                novas_substituicoes = dict(
                                    st.session_state.import_manual_overrides
                                    or {}
                                )
                                novas_substituicoes[
                                    indice_original
                                ] = novo_local
                                st.session_state.import_manual_overrides = (
                                    novas_substituicoes
                                )
                                st.session_state.import_editing_index = None
                                st.rerun()

                        with col_cancelar:
                            if st.button(
                                "Cancelar",
                                key=(
                                    f"cancelar_edicao_import_"
                                    f"{hash_arquivo}_"
                                    f"{indice_original}_"
                                    f"{st.session_state.import_edit_version}"
                                ),
                                use_container_width=True,
                            ):
                                st.session_state.import_editing_index = None
                                st.rerun()

            ids_confirmados = [
                indice_original
                for indice_original, _
                in enderecos_ativos
                if indice_original in confirmados_por_id
            ]

            # Diferentes textos podem ter sido geocodificados no mesmo ponto.
            # Isso também inviabiliza o cálculo, então identificamos os itens.
            coordenadas_importadas = {}
            duplicados_coordenadas = set()
            for indice in ids_confirmados:
                local = confirmados_por_id[indice]
                coord = (
                    round(float(local["latitude"]), 6),
                    round(float(local["longitude"]), 6),
                )
                if coord in coordenadas_importadas:
                    duplicados_coordenadas.update((coordenadas_importadas[coord], indice))
                else:
                    coordenadas_importadas[coord] = indice

            if duplicados_coordenadas:
                descricoes = [
                    f"item {numeracao_csv[indice]}: {enderecos[indice]}"
                    for indice in sorted(duplicados_coordenadas)
                ]
                st.error(
                    "🔴 Atenção: endereços diferentes foram associados às "
                    "mesmas coordenadas pelo serviço de busca: "
                    + "; ".join(descricoes)
                    + ". Corrija a seleção ou remova o endereço repetido."
                )

            if len(ids_confirmados) >= 2:

                st.divider()
                st.markdown(
                    "#### Defina o início e o fim da rota"
                )

                chave_inicio = (
                    f"csv_inicio_{hash_arquivo}"
                )

                if (
                    chave_inicio in st.session_state
                    and st.session_state[chave_inicio]
                    not in ids_confirmados
                ):
                    del st.session_state[chave_inicio]

                indice_inicio = st.selectbox(
                    "Ponto inicial",
                    ids_confirmados,
                    index=None,
                    placeholder="Selecione o ponto inicial...",
                    key=chave_inicio,
                    format_func=(
                        lambda x:
                        f"{numeracao_csv[x]}. {confirmados_por_id[x]['label']}"
                    ),
                )

                voltar_inicio = bool(
                    st.session_state.get(f"csv_voltar_{hash_arquivo}", False)
                )

                indice_fim = None
                ponto_inicio = None
                ponto_fim = None

                if indice_inicio is not None:
                    ponto_inicio = confirmados_por_id[
                        indice_inicio
                    ]

                if voltar_inicio:
                    ponto_fim = ponto_inicio

                    st.text_input(
                        "Ponto final",
                        value=(
                            ponto_inicio["label"]
                            if ponto_inicio
                            else ""
                        ),
                        disabled=True,
                        key=(
                            f"csv_fim_retorno_{hash_arquivo}_"
                            f"{indice_inicio}"
                        ),
                    )

                else:
                    opcoes_fim = [
                        indice
                        for indice in ids_confirmados
                        if indice != indice_inicio
                    ]

                    chave_fim = (
                        f"csv_fim_{hash_arquivo}"
                    )

                    if (
                        chave_fim in st.session_state
                        and st.session_state[chave_fim]
                        not in opcoes_fim
                    ):
                        del st.session_state[chave_fim]

                    indice_fim = st.selectbox(
                        "Ponto final",
                        opcoes_fim,
                        index=None,
                        placeholder="Selecione o ponto final...",
                        key=chave_fim,
                        format_func=(
                            lambda x:
                            f"{numeracao_csv[x]}. {confirmados_por_id[x]['label']}"
                        ),
                    )

                    if indice_fim is not None:
                        ponto_fim = confirmados_por_id[
                            indice_fim
                        ]

                voltar_inicio = st.checkbox(
                    "🔄 Retornar ao ponto inicial",
                    key=f"csv_voltar_{hash_arquivo}",
                )
                if voltar_inicio:
                    ponto_fim = ponto_inicio

                paradas = [
                    confirmados_por_id[indice]
                    for indice in ids_confirmados
                    if indice not in (
                        indice_inicio,
                        indice_fim,
                    )
                ]

                if st.button(
                    "📍 Salvar endereços em Locais",
                    use_container_width=True,
                ):
                    inseridos, ignorados = (
                        salvar_locais_em_lote(
                            [
                                confirmados_por_id[indice]
                                for indice in ids_confirmados
                            ]
                        )
                    )

                    st.success(
                        f"{inseridos} salvo(s). "
                        f"{ignorados} ignorado(s)."
                    )

            elif enderecos_ativos:
                st.warning(
                    "Mantenha pelo menos dois endereços válidos "
                    "para montar uma rota."
                )
            else:
                st.warning(
                    "Todos os endereços foram removidos. "
                    "Restaure algum endereço ou carregue outro arquivo."
                )

        except Exception as e:
            mostrar_erro_usuario(
                "Falha na operação",
                e,
            )


# ============================================================
# HORÁRIOS POR PARADA
# ============================================================

locais_para_horarios = [ponto_inicio] + [p for p in paradas if p]
if not voltar_inicio and ponto_fim:
    locais_para_horarios.append(ponto_fim)

horarios_editados = None
horario_saida = "08:00"
if locais_para_horarios and all(locais_para_horarios):
    with st.expander("🕒 Horários e janelas de atendimento (opcional)"):
        st.caption("Informe HH:MM. O algoritmo só aceitará uma rota que respeite as janelas preenchidas.")
        horario_saida = st.text_input("Saída prevista (HH:MM)", value="08:00",
                                      key=f"saida_rota_{st.session_state.form_version}")
        linhas_horarios = []
        for i, loc in enumerate(locais_para_horarios, start=1):
            linhas_horarios.append({
                "Ponto": i, "Local": str(loc.get("label") or loc.get("nome") or f"Ponto {i}"),
                "Início": str(loc.get("janela_inicio") or ""),
                "Fim": str(loc.get("janela_fim") or ""),
                "Atendimento (min)": int(loc.get("servico_min") or 0),
            })
        horarios_editados = st.data_editor(pd.DataFrame(linhas_horarios),
            disabled=["Ponto", "Local"], hide_index=True, use_container_width=True,
            key=f"horarios_tabela_{st.session_state.form_version}_{modo_entrada}")

# ============================================================
# CONFIGURAÇÃO DA OTIMIZAÇÃO
# ============================================================

st.divider()

st.subheader(
    "⚙️ Configuração da otimização"
)

c1, c2 = (
    st.columns(
        2
    )
)

with c1:

    objetivo = st.radio(
        "Otimizar por",
        [
            "Menor distância",
            "Menor tempo",
        ],
        horizontal=True,
    )

with c2:

    modos = (
        opcoes_modo_usuario()
    )

    modo = st.radio(
        "Modo",
        modos,
        horizontal=True,
    )

    if not permite_preciso_usuario():

        st.caption(
            "🔒 Modo Preciso disponível "
            "no plano Pro."
        )


# ============================================================
# CONTROLE DA ORDEM
# ============================================================

preservar_ordem = st.checkbox(
    "📌 Manter exatamente a ordem informada (sem reordenar pelo algoritmo)",
    value=bool(st.session_state.ordem_manualmente_alterada) if modo_entrada == 'Usar rota salva' else False,
    key=f"preservar_ordem_{st.session_state.form_version}_{modo_entrada}",
    help="Útil após arrastar as paradas. O ORS calcula o percurso e os horários mantendo sua sequência.",
)

# ============================================================
# CALCULAR
# ============================================================

st.divider()

salvar_sobre_rota_base = False
if modo_entrada == "Usar rota salva" and rota_salva_selecionada is not None:
    coluna_previa, coluna_salvar = st.columns(2)
    with coluna_previa:
        calcular_agora = st.button(
            "🚗 Calcular sem salvar",
            type="secondary",
            use_container_width=True,
        )
    with coluna_salvar:
        salvar_sobre_rota_base = st.button(
            "💾 Recalcular e salvar alterações",
            type="primary",
            use_container_width=True,
        )
else:
    calcular_agora = st.button(
        "🚗 Calcular melhor rota",
        type="primary",
        use_container_width=True,
    )

if calcular_agora or salvar_sobre_rota_base:
    if modo_entrada == "Usar rota salva":
        if rota_salva_selecionada is None:
            st.error("Escolha primeiro uma rota salva.")
            st.stop()
        if enderecos_invalidos_rota_salva:
            st.error("Confirme todos os endereços antes de calcular ou salvar.")
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
        item

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
        minutos_hora(horario_saida)
        if horarios_editados is not None and len(horarios_editados) == len(locais):
            for i, linha in horarios_editados.reset_index(drop=True).iterrows():
                ini = linha.get("Início")
                fim = linha.get("Fim")
                serv = linha.get("Atendimento (min)")
                locais[i]["janela_inicio"] = "" if pd.isna(ini) else str(ini).strip()
                locais[i]["janela_fim"] = "" if pd.isna(fim) else str(fim).strip()
                locais[i]["servico_min"] = 0 if pd.isna(serv) else int(serv)
        locais[0]["horario_partida"] = horario_saida

        with st.spinner(
            "Calculando a melhor rota..."
        ):

            resultado = (
                calcular_rota_a_partir_de_locais(
                    locais,
                    voltar_inicio,
                    objetivo,
                    modo,
                    preservar_ordem=preservar_ordem,
                )
            )

        # Pré-visualizar não altera a rota salva; salvar substitui os
        # dados apenas depois do recálculo bem-sucedido.
        id_atualizado = None
        nome_atualizado = None
        if salvar_sobre_rota_base:
            id_atualizado = st.session_state.rota_base_edicao_id
            nome_atualizado = st.session_state.rota_base_edicao_nome
            atualizar_rota_completa_banco(
                id_atualizado, nome_atualizado, resultado
            )

        st.session_state.resultado_rota = resultado
        st.session_state.rota_atual_id = id_atualizado
        st.session_state.rota_atual_nome = nome_atualizado
        st.session_state.rota_aberta_salva = bool(id_atualizado)
        st.session_state.resultado_historico = False
        st.session_state.resultado_direto = False

        execucao, erro = registrar_execucao_seguro(
            resultado, id_atualizado, nome_atualizado
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

        if salvar_sobre_rota_base:
            st.session_state.mensagem_sistema = {
                "tipo": "success",
                "texto": (
                    f"Rota '{nome_atualizado}' atualizada com sucesso. "
                    "Os novos endereços e a ordem otimizada foram salvos."
                ),
            }
            st.rerun()

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
        mostrar_erro_usuario(
            "Falha inesperada no cálculo",
            e,
            "Não foi possível calcular a rota. Tente novamente.",
        )


if st.session_state.resultado_rota:

    st.divider()

    renderizar_resultado_rota(
        st.session_state.resultado_rota
    )
