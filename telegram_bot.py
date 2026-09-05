"""
TraderIA Brasil — Notificações Telegram
"""

import logging
from typing import Optional

import requests

from brain import Sinal
from config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

logger = logging.getLogger("TraderIA")


class NotificadorTelegram:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

        self.ativo = bool(
            self.token
            and self.chat_id
        )

        if not self.ativo:
            logger.error(
                "❌ Telegram não configurado no Environment da Render"
            )

    def _enviar(self, texto: str) -> bool:
        if not self.ativo:
            return False

        url = (
            f"https://api.telegram.org/"
            f"bot{self.token}/sendMessage"
        )

        try:
            resposta = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": texto,
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )

            if resposta.status_code != 200:
                logger.error(
                    "❌ Telegram HTTP=%s: %s",
                    resposta.status_code,
                    resposta.text[:300],
                )
                return False

            dados = resposta.json()
            confirmado = bool(dados.get("ok"))

            if confirmado:
                logger.info(
                    "📲 Telegram confirmou o envio"
                )
            else:
                logger.error(
                    "❌ Telegram não confirmou: %s",
                    dados,
                )

            return confirmado

        except requests.RequestException as erro:
            logger.error(
                "❌ Falha de conexão com Telegram: %s",
                erro,
            )
            return False

        except Exception:
            logger.exception(
                "❌ Erro inesperado no Telegram"
            )
            return False

    def enviar_sinal(self, sinal: Sinal) -> bool:
        tipo = (
            "🔴 AO VIVO"
            if sinal.modo_analise == "AO_VIVO"
            else "🕒 PRÉ-JOGO — até 30 min"
        )

        mensagem = (
            "🚨 SINAL TRADERIA\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{tipo}\n\n"
            f"⚽ {sinal.jogo}\n"
            f"🏟️ {sinal.liga}\n"
            f"⏱️ {sinal.minuto} | Placar: {sinal.placar}\n\n"
            f"📈 Mercado: {sinal.mercado}\n"
            f"🟢 Direção: {sinal.direcao}\n"
            f"💰 Odd real encontrada: {sinal.odd_entrada:.2f}\n"
            f"💵 Stake sugerida: R$ {sinal.stake:.2f}\n\n"
            f"🎯 Probabilidade estimada: "
            f"{sinal.probabilidade_modelo:.1f}%\n"
            f"📊 EV estimado: "
            f"{sinal.valor_esperado:+.1f}%\n"
            f"🧠 Confiança do sinal: "
            f"{sinal.confianca}%\n"
            f"🗂️ Qualidade dos dados: "
            f"{sinal.qualidade_dados}/100\n"
            f"⚠️ Risco: {sinal.risco}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📋 TESE\n"
            f"{sinal.tese}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ Você decide se executa ou não.\n\n"
            "Aviso: probabilidade estimada não é garantia. "
            "Confirme a odd na plataforma antes de operar."
        )

        return self._enviar(mensagem)

    def enviar_alerta(self, texto: str) -> bool:
        return self._enviar(
            f"⚠️ ALERTA TRADERIA\n\n{texto}"
        )

    def enviar_status(
        self,
        ciclo: int,
        ao_vivo: int,
        pre_jogo: int,
    ) -> bool:
        return self._enviar(
            "🤖 STATUS TRADERIA\n\n"
            f"Ciclo: {ciclo}\n"
            f"Ao vivo: {ao_vivo}\n"
            f"Pré-jogo: {pre_jogo}\n"
            "Monitoramento ativo."
        )
