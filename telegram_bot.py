"""
NOTIFICADOR TELEGRAM
"""
import requests
import logging
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from brain import Sinal

logger = logging.getLogger("TraderIA")


class NotificadorTelegram:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def _enviar(self, texto: str, parse_mode: str = "Markdown") -> bool:
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": texto,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Erro Telegram: {e}")
            return False

    def enviar_sinal(self, sinal: Sinal) -> bool:
        emoji_dir = "🟢" if sinal.direcao == "BACK" else "🔴"

        msg = (
            f"🚨 *SINAL DE TRADING GLOBAL*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⚽ *{sinal.jogo}*\n"
            f"🏟️ {sinal.liga} | ⏱️ {sinal.minuto} | 📊 {sinal.placar}\n\n"
            f"📈 *Mercado:* {sinal.mercado}\n"
            f"{emoji_dir} *Direção:* {sinal.direcao}\n\n"
            f"💰 *Odd Entrada:* {sinal.odd_entrada:.2f}\n"
            f"🎯 *Odd Alvo:* {sinal.odd_alvo:.2f}\n"
            f"🛑 *Odd Stop:* {sinal.odd_stop:.2f}\n"
            f"💵 *Stake Sugerida:* R$ {sinal.stake:.2f}\n\n"
            f"🧠 *Confiança:* {sinal.confianca}% {sinal.risco}\n"
            f"📊 *Valor Esperado:* +{sinal.valor_esperado:.1f}%\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 *TESE DA IA:*\n"
            f"{sinal.tese}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Você decide SIM ou NÃO.*"
        )
        return self._enviar(msg)

    def enviar_alerta(self, texto: str):
        self._enviar(f"⚠️ *ALERTA TRADERIA*\n\n{texto}")
