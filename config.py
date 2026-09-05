"""
TraderIA Brasil — Configurações
As credenciais devem existir somente no Environment da Render.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CREDENCIAIS
# ============================================================

API_KEY = os.getenv("API_KEY", "").strip()
API_BASE = "https://apiv3.apifootball.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# MONITORAMENTO
# ============================================================

API_TIMEZONE = os.getenv("API_TIMEZONE", "America/Sao_Paulo")

# Pré-jogos serão analisados somente dentro desta janela.
PRELIVE_WINDOW_MINUTES = int(
    os.getenv("PRELIVE_WINDOW_MINUTES", "30")
)

# Intervalo entre varreduras.
# Evite reduzir para menos de 15 segundos.
SCAN_INTERVAL_SEGUNDOS = int(
    os.getenv("SCAN_INTERVAL_SEGUNDOS", "15")
)

# Limita chamadas à API por ciclo.
MAX_JOGOS_POR_CICLO = int(
    os.getenv("MAX_JOGOS_POR_CICLO", "20")
)


# ============================================================
# FILTROS DE SINAL
# ============================================================

# EV mínimo exigido: 0.08 = 8%.
EV_MINIMO = float(os.getenv("EV_MINIMO", "0.08"))

# Probabilidade mínima geral estimada pelo modelo.
PROBABILIDADE_MINIMA = float(
    os.getenv("PROBABILIDADE_MINIMA", "0.48")
)

# Placar exato é mais arriscado.
PROB_MINIMA_PLACAR_EXATO = float(
    os.getenv("PROB_MINIMA_PLACAR_EXATO", "0.35")
)

# Placar exato será considerado somente depois deste minuto.
MINUTO_MINIMO_PLACAR_EXATO = int(
    os.getenv("MINUTO_MINIMO_PLACAR_EXATO", "70")
)

ODD_MINIMA = float(os.getenv("ODD_MINIMA", "1.20"))
ODD_MAXIMA = float(os.getenv("ODD_MAXIMA", "10.00"))


# ============================================================
# GESTÃO DE BANCA
# ============================================================

BANCA_INICIAL = float(os.getenv("BANCA_INICIAL", "1000"))
STAKE_POR_OPERACAO = float(
    os.getenv("STAKE_POR_OPERACAO", "2")
)

MAX_OPERACOES_DIA = int(
    os.getenv("MAX_OPERACOES_DIA", "15")
)
