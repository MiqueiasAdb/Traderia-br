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

# URL oficial da API-Football v3.
API_BASE = "https://apiv3.apifootball.com/"


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
