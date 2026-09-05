#!/usr/bin/env python3
"""
TraderIA Brasil v2.0 — Servidor Cloud Render + IA 24/7
"""
import os
import time
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from scanner import Scanner24h
from brain import Brain
from telegram_bot import NotificadorTelegram
from bankroll import GestorBanca
from config import SCAN_INTERVAL_SEGUNDOS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TraderIA")


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        msg = f"🟢 TraderIA Brasil 24/7 ONLINE\nAtivo em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        self.wfile.write(msg.encode("utf-8"))

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"🌐 Servidor Web ativo na porta {port}")
    server.serve_forever()


class TraderIA:
    def __init__(self):
        self.scanner = Scanner24h()
        self.brain = Brain()
        self.notificador = NotificadorTelegram()
        self.banca = GestorBanca()
        self.rodando = False
        self.ciclo = 0
        self.sinais_enviados = set()

    def _ciclo_varredura(self):
        self.ciclo += 1
        jogos = self.scanner.varrer_jogos_ao_vivo()

        if not jogos:
            return

        logger.info(f"🔍 Ciclo #{self.ciclo} | {len(jogos)} jogos no mundo | {self.banca.status()}")

        for jogo in jogos:
            match_id = str(jogo.get("match_id", ""))
            if not match_id or match_id in self.sinais_enviados:
                continue

            try:
                odds = self.scanner.buscar_odds(match_id)
                stats = self.scanner.buscar_estatisticas(match_id)
                h2h = []
                casa = jogo.get("match_hometeam_name", "")
                fora = jogo.get("match_awayteam_name", "")
                if casa and fora:
                    h2h = self.scanner.buscar_confronto_direto(casa, fora)

                previsoes = self.scanner.buscar_previsoes(match_id)

                sinal = self.brain.analisar(
                    jogo=jogo,
                    odds=odds,
                    h2h=h2h,
                    stats=stats,
                    previsoes=previsoes,
                )

                if sinal:
                    self.banca.calcular_stake(sinal)
                    self.notificador.enviar_sinal(sinal)
                    self.banca.registrar_operacao(sinal)
                    self.sinais_enviados.add(match_id)

                    logger.info(
                        f"🚨 SINAL GLOBAL ENVIADO: {sinal.jogo} | {sinal.mercado} | "
                        f"Odd {sinal.odd_entrada:.2f}"
                    )

            except Exception as e:
                logger.error(f"Erro no jogo {match_id}: {e}")
                continue

    def loop_ia(self):
        self.rodando = True
        logger.info("🤖 IA Iniciada e monitorando o mundo 24/7")
        while self.rodando:
            try:
                self._ciclo_varredura()
            except Exception as e:
                logger.error(f"Erro ciclo: {e}")
            time.sleep(SCAN_INTERVAL_SEGUNDOS)


def main():
    web_thread = threading.Thread(target=start_health_server, daemon=True)
    web_thread.start()

    trader = TraderIA()
    trader.loop_ia()


if __name__ == "__main__":
    main()
