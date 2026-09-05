"""
GESTOR DE BANCA
"""
import logging
from config import BANCA_INICIAL, STAKE_POR_OPERACAO, MAX_OPERACOES_DIA
from brain import Sinal

logger = logging.getLogger("TraderIA")


class GestorBanca:
    def __init__(self):
        self.banca = BANCA_INICIAL
        self.pnl_total = 0.0
        self.operacoes_hoje = 0

    def calcular_stake(self, sinal: Sinal) -> float:
        base = self.banca * (STAKE_POR_OPERACAO / 100)
        mult = 1.2 if sinal.confianca >= 80 else 0.8
        stake = round(max(1.0, min(base * mult, self.banca * 0.05)), 2)
        sinal.stake = stake
        return stake

    def registrar_operacao(self, sinal: Sinal):
        self.operacoes_hoje += 1

    def status(self) -> str:
        return f"💰 Banca: R$ {self.banca:,.2f} | 📊 P&L: R$ {self.pnl_total:+,.2f} | Ops hoje: {self.operacoes_hoje}"
