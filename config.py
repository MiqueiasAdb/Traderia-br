"""
TraderIA Brasil — Configurações

IMPORTANTE:
As credenciais devem ser configuradas somente em:
Render → Environment
"""

import os

from dotenv import load_dotenv

load_dotenv()


# ============================================================
# API-FOOTBALL
# ============================================================

API_KEY = os.getenv("API_KEY", "").strip()

# API-Sports v3 (api-sports.io) — endpoints /fixtures, /odds...
API_BASE = os.getenv(
    "API_BASE",
    "https://v3.football.api-sports.io",
).strip()

# Limite diário de requisições do plano (Free = 100/dia,
# Pro = 7.500/dia). Protege a cota e ativa modo economia.
API_REQ_DIA_LIMITE = int(
    os.getenv("API_REQ_DIA_LIMITE", "100")
)

# ============================================================
# API LEGADA (apifootball.com) — OPCIONAL
# ============================================================
# Chave extra para poupar cota: estatísticas e H2H saem daqui
# enquanto a chave for válida. Sem ela (ou expirada), o bot usa
# só a API-Sports automaticamente.
API2_BASE = os.getenv(
    "API2_BASE",
    "https://apiv3.apifootball.com/",
).strip()
API2_KEY = os.getenv("API2_KEY", "").strip()


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()


# ============================================================
# HORÁRIO E MONITORAMENTO
# ============================================================

API_TIMEZONE = os.getenv(
    "API_TIMEZONE",
    "America/Sao_Paulo",
).strip()

# Pré-jogo somente nos 30 minutos anteriores ao início.
PRELIVE_WINDOW_MINUTES = int(
    os.getenv(
        "PRELIVE_WINDOW_MINUTES",
        "30",
    )
)

# Um ciclo a cada 30 segundos para reduzir bloqueios da API.
SCAN_INTERVAL_SEGUNDOS = int(
    os.getenv(
        "SCAN_INTERVAL_SEGUNDOS",
        "30",
    )
)

# Máximo de partidas analisadas por ciclo.
MAX_JOGOS_POR_CICLO = int(
    os.getenv(
        "MAX_JOGOS_POR_CICLO",
        "10",
    )
)


# ============================================================
# MODO MONITOR
# ============================================================
# Envia ao Telegram um resumo dos jogos encontrados, sem
# depender de odds (útil quando o plano não libera odds).
#
#   on   → sempre monitora (nunca envia sinais);
#   off  → nunca monitora (só sinais, comportamento original);
#   auto → opera com sinais; se a API bloquear as odds,
#          entra em monitor automaticamente.
MODO_MONITOR = os.getenv(
    "MODO_MONITOR",
    "auto",
).strip().lower()

# Intervalo mínimo entre mensagens de monitor no Telegram.
MONITOR_INTERVALO_MINUTOS = int(
    os.getenv(
        "MONITOR_INTERVALO_MINUTOS",
        "30",
    )
)


# ============================================================
# PRÉ-JOGO COM ODDS EXTERNAS (Plano B)
# ============================================================
# Chave da The Odds API (https://the-odds-api.com — 500
# créditos/mês grátis). Sem ela, o módulo fica inativo.
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

# Ligas do pré-jogo externo, na ordem de prioridade de gasto.
# Formato: "Nome|CODIGO_FD|sport_key_oddsapi, ..."
# (CODIGO_FD = código na football-data.org; vazio = usa
# eventos da própria The Odds API).
PREMATCH_LIGAS = os.getenv(
    "PREMATCH_LIGAS",
    "Brasileirão Série A|BSA|soccer_brazil_campeonato, "
    "Premier League|PL|soccer_epl, "
    "La Liga|PD|soccer_spain_la_liga, "
    "Serie A (Itália)|SA|soccer_italy_serie_a, "
    "Bundesliga|BL1|soccer_germany_bundesliga, "
    "Ligue 1|FL1|soccer_france_ligue_one, "
    "Championship|ELC|soccer_efl_champ",
).strip()

# Orçamento diário de créditos na The Odds API (o plano
# grátis tem 500/mês — 12/dia ≈ 360/mês, com folga).
ODDS_CREDITO_DIARIO = int(
    os.getenv("ODDS_CREDITO_DIARIO", "12")
)

# Regiões e mercados consultados (cada mercado+região
# custa 1 crédito por chamada). "btts" pode ser adicionado.
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "eu").strip()
ODDS_MARKETS = os.getenv("ODDS_MARKETS", "h2h,totals").strip()

# Chave da football-data.org (https://www.football-data.org —
# grátis). Fornece a agenda do Brasileirão sem gastar créditos.
FD_API_KEY = os.getenv("FD_API_KEY", "").strip()
FD_COMPETICAO = os.getenv("FD_COMPETICAO", "BSA").strip()

# Cache das odds (o plano grátis atualiza a cada 30 min),
# da agenda e da lista de eventos (fallback sem FD).
ODDS_CACHE_SEGUNDOS = int(
    os.getenv("ODDS_CACHE_SEGUNDOS", "1800")
)
FD_CACHE_SEGUNDOS = int(
    os.getenv("FD_CACHE_SEGUNDOS", "300")
)
ODDS_EVENTOS_CACHE_SEGUNDOS = int(
    os.getenv("ODDS_EVENTOS_CACHE_SEGUNDOS", "7200")
)




# ============================================================
# PROTEÇÃO DA API
# ============================================================

API_MAX_TENTATIVAS = int(
    os.getenv(
        "API_MAX_TENTATIVAS",
        "3",
    )
)

# Intervalo mínimo entre chamadas à API.
API_INTERVALO_MINIMO = float(
    os.getenv(
        "API_INTERVALO_MINIMO",
        "0.75",
    )
)

API_TIMEOUT_CONEXAO = int(
    os.getenv(
        "API_TIMEOUT_CONEXAO",
        "10",
    )
)

API_TIMEOUT_LEITURA = int(
    os.getenv(
        "API_TIMEOUT_LEITURA",
        "35",
    )
)


# ============================================================
# FILTROS DOS SINAIS
# ============================================================

# 0.08 representa EV mínimo de 8%.
EV_MINIMO = float(
    os.getenv(
        "EV_MINIMO",
        "0.08",
    )
)

# Filtro de sanidade: EV acima disso indica modelo
# descalibrado (mercado real raramente deixa >50% de
# valor na mesa). O candidato é descartado.
EV_SANIDADE_MAXIMA = float(
    os.getenv(
        "EV_SANIDADE_MAXIMA",
        "0.50",
    )
)

PROBABILIDADE_MINIMA = float(
    os.getenv(
        "PROBABILIDADE_MINIMA",
        "0.48",
    )
)

PROB_MINIMA_PLACAR_EXATO = float(
    os.getenv(
        "PROB_MINIMA_PLACAR_EXATO",
        "0.35",
    )
)

MINUTO_MINIMO_PLACAR_EXATO = int(
    os.getenv(
        "MINUTO_MINIMO_PLACAR_EXATO",
        "70",
    )
)

ODD_MINIMA = float(
    os.getenv(
        "ODD_MINIMA",
        "1.20",
    )
)

ODD_MAXIMA = float(
    os.getenv(
        "ODD_MAXIMA",
        "10.00",
    )
)


# ============================================================
# GESTÃO DE BANCA
# ============================================================

BANCA_INICIAL = float(
    os.getenv(
        "BANCA_INICIAL",
        "1000",
    )
)

STAKE_POR_OPERACAO = float(
    os.getenv(
        "STAKE_POR_OPERACAO",
        "2",
    )
)

MAX_OPERACOES_DIA = int(
    os.getenv(
        "MAX_OPERACOES_DIA",
        "15",
    )
)
