"""
TraderIA v2.0 — Configurações Globais
"""
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("API_KEY", "03d90c5c35c6844c232bea3b465383ac0432")
API_BASE = "https://apiv3.apifootball.com"
WS_URL = "wss://wss.apifootball.com/livescore"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8919203360:AAHrOi5CYOud1r-dYrNOBG0WLymE_elF7FQ")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7328605877")

BANCA_INICIAL = 1000.0
STAKE_POR_OPERACAO = 2.0
MAX_OPERACOES_DIA = 15
CONFIDENCE_MINIMA = 70

MODO_GLOBAL = True
SCAN_INTERVAL_SEGUNDOS = 40
