#!/usr/bin/env python3
"""
TraderIA Brasil — Render Cloud

- Health check HTTP;
- Monitoramento ao vivo;
- Pré-jogo somente até 30 minutos;
- Logs completos;
- Controle de sinais duplicados.
"""

import logging
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple

from bankroll import GestorBanca
from brain import Brain, Sinal
from config import (
    API_KEY,
    SCAN_INTERVAL_SEGUNDOS,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
)
from scanner import Scanner24h
from telegram_bot import NotificadorTelegram


# ============================================================
# LOGS
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)

logger = logging.getLogger("TraderIA")


# ============================================================
# HEALTH CHECK
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.end_headers()

        mensagem = (
            "TraderIA Brasil ONLINE\n"
            f"Servidor: "
            f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        )

        self.wfile.write(
            mensagem.encode("utf-8")
        )

    def log_message(self, formato, *args):
        return


def start_health_server():
    porta = int(os.getenv("PORT", "10000"))

    servidor = HTTPServer(
        ("0.0.0.0", porta),
        HealthCheckHandler,
    )

    logger.info(
        "🌐 Health check ativo na porta %s",
        porta,
    )

    servidor.serve_forever()


# ============================================================
# TRADERIA
# ============================================================

class TraderIA:
    def __init__(self):
        logger.info(
            "⚙️ Inicializando componentes"
        )

        self.scanner = Scanner24h()
        self.brain = Brain()
        self.notificador = NotificadorTelegram()
        self.banca = GestorBanca()

        self.rodando = False
        self.ciclo = 0

        # Guarda chaves de sinais enviados.
        self.sinais_enviados = set()

        logger.info(
            "✅ Componentes inicializados"
        )

    def _validar_configuracao(self) -> bool:
        ausentes = []

        if not API_KEY:
            ausentes.append("API_KEY")

        if not TELEGRAM_TOKEN:
            ausentes.append("TELEGRAM_TOKEN")

        if not TELEGRAM_CHAT_ID:
            ausentes.append("TELEGRAM_CHAT_ID")

        if ausentes:
            logger.error(
                "❌ Variáveis ausentes: %s",
                ", ".join(ausentes),
            )
            return False

        logger.info(
            "🔐 Variáveis de ambiente encontradas"
        )
        return True

    @staticmethod
    def _minuto_sinal(sinal: Sinal) -> int:
        numeros = "".join(
            caractere
            for caractere in sinal.minuto
            if caractere.isdigit()
        )

        return int(numeros) if numeros else 0

    def _chave_sinal(self, sinal: Sinal) -> Tuple:
        """
        Permite outro sinal no mesmo jogo quando:
        - o mercado for diferente; ou
        - o jogo entrar em outra faixa de 10 minutos.
        """
        minuto = self._minuto_sinal(sinal)

        faixa_minuto = (
            (minuto // 10) * 10
            if sinal.modo_analise == "AO_VIVO"
            else "PRE_JOGO"
        )

        return (
            sinal.match_id,
            sinal.mercado,
            faixa_minuto,
        )

    def _ciclo_varredura(self):
        self.ciclo += 1
        inicio = time.monotonic()

        logger.info(
            "🔄 Iniciando ciclo #%s",
            self.ciclo,
        )

        try:
            jogos = self.scanner.varrer_jogos_ao_vivo()
        except Exception:
            logger.exception(
                "❌ Scanner falhou no ciclo #%s",
                self.ciclo,
            )
            return

        if not isinstance(jogos, list):
            logger.error(
                "❌ Scanner retornou tipo inválido: %s",
                type(jogos).__name__,
            )
            return

        quantidade_ao_vivo = sum(
            1
            for jogo in jogos
            if jogo.get("_modo_analise") == "AO_VIVO"
        )

        quantidade_pre_jogo = sum(
            1
            for jogo in jogos
            if jogo.get("_modo_analise") == "PRE_JOGO"
        )

        logger.info(
            "📡 Ciclo #%s: %s jogos | "
            "ao vivo=%s | pré-jogo=%s",
            self.ciclo,
            len(jogos),
            quantidade_ao_vivo,
            quantidade_pre_jogo,
        )

        if not jogos:
            logger.warning(
                "⚠️ Nenhum jogo elegível neste ciclo"
            )
            return

        analisados = 0
        oportunidades = 0
        enviados = 0
        erros = 0

        for indice, jogo in enumerate(jogos, start=1):
            if not isinstance(jogo, dict):
                continue

            match_id = str(
                jogo.get("match_id", "")
            ).strip()

            casa = str(
                jogo.get("match_hometeam_name", "?")
            ).strip()

            fora = str(
                jogo.get("match_awayteam_name", "?")
            ).strip()

            modo = jogo.get(
                "_modo_analise",
                "DESCONHECIDO",
            )

            if not match_id:
                logger.warning(
                    "⚠️ Jogo sem match_id: %s x %s",
                    casa,
                    fora,
                )
                continue

            if modo == "PRE_JOGO":
                minutos_inicio = float(
                    jogo.get(
                        "_minutos_para_inicio",
                        0,
                    )
                )

                logger.info(
                    "🕒 [%s/%s] PRÉ-JOGO: %s x %s | "
                    "começa em %.1f min",
                    indice,
                    len(jogos),
                    casa,
                    fora,
                    minutos_inicio,
                )
            else:
                logger.info(
                    "🔴 [%s/%s] AO VIVO: %s x %s",
                    indice,
                    len(jogos),
                    casa,
                    fora,
                )

            try:
                odds = self.scanner.buscar_odds(
                    match_id
                )

                stats = self.scanner.buscar_estatisticas(
                    match_id
                )

                h2h = []

                if casa != "?" and fora != "?":
                    h2h = (
                        self.scanner
                        .buscar_confronto_direto(
                            casa,
                            fora,
                        )
                    )

                # A previsão pré-jogo da API não é consultada em
                # todos os ciclos para reduzir chamadas.
                previsoes = {}

                logger.info(
                    "📊 ID=%s | odds=%s | h2h=%s | stats=%s",
                    match_id,
                    len(odds)
                    if isinstance(odds, list)
                    else 0,
                    len(h2h)
                    if isinstance(h2h, list)
                    else 0,
                    "sim" if stats else "não",
                )

                sinal = self.brain.analisar(
                    jogo=jogo,
                    odds=odds,
                    h2h=h2h,
                    stats=stats,
                    previsoes=previsoes,
                )

                analisados += 1

                if not sinal:
                    logger.info(
                        "⏭️ Sem oportunidade: %s x %s",
                        casa,
                        fora,
                    )
                    continue

                oportunidades += 1
                chave = self._chave_sinal(sinal)

                if chave in self.sinais_enviados:
                    logger.info(
                        "↩️ Sinal duplicado bloqueado: %s",
                        chave,
                    )
                    continue

                self.banca.calcular_stake(sinal)

                logger.warning(
                    "🎯 OPORTUNIDADE: %s | %s | "
                    "odd=%.2f | prob=%.1f%% | EV=%+.1f%%",
                    sinal.jogo,
                    sinal.mercado,
                    sinal.odd_entrada,
                    sinal.probabilidade_modelo,
                    sinal.valor_esperado,
                )

                confirmado = (
                    self.notificador
                    .enviar_sinal(sinal)
                )

                if confirmado:
                    self.banca.registrar_operacao(
                        sinal
                    )

                    self.sinais_enviados.add(chave)
                    enviados += 1

                    logger.warning(
                        "📲 SINAL ENVIADO: %s",
                        sinal.jogo,
                    )
                else:
                    logger.error(
                        "❌ Sinal não confirmado pelo Telegram: %s",
                        sinal.jogo,
                    )

            except Exception:
                erros += 1

                logger.exception(
                    "❌ Erro analisando ID=%s — %s x %s",
                    match_id,
                    casa,
                    fora,
                )

        duracao = time.monotonic() - inicio

        logger.info(
            "✅ Ciclo #%s concluído em %.2fs | "
            "analisados=%s | oportunidades=%s | "
            "enviados=%s | erros=%s",
            self.ciclo,
            duracao,
            analisados,
            oportunidades,
            enviados,
            erros,
        )

    def loop_ia(self):
        self.rodando = True

        logger.info(
            "🤖 IA iniciada"
        )

        logger.info(
            "⏱️ Novo ciclo a cada %s segundos",
            SCAN_INTERVAL_SEGUNDOS,
        )

        self._validar_configuracao()

        while self.rodando:
            try:
                self._ciclo_varredura()
            except Exception:
                logger.exception(
                    "❌ Erro não tratado no ciclo principal"
                )

            logger.info(
                "💤 Próximo ciclo em %s segundos",
                SCAN_INTERVAL_SEGUNDOS,
            )

            time.sleep(
                SCAN_INTERVAL_SEGUNDOS
            )


# ============================================================
# INICIALIZAÇÃO
# ============================================================

def main():
    logger.info(
        "🚀 Inicializando TraderIA Brasil"
    )

    thread_web = threading.Thread(
        target=start_health_server,
        name="health-check",
        daemon=True,
    )

    thread_web.start()

    trader = TraderIA()
    trader.loop_ia()


if __name__ == "__main__":
    main()
