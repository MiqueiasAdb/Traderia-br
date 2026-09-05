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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bankroll import GestorBanca
from brain import Brain, Sinal
from config import (
    API_KEY,
    API_TIMEZONE,
    MAX_JOGOS_POR_CICLO,
    MODO_MONITOR,
    MONITOR_INTERVALO_MINUTOS,
    SCAN_INTERVAL_SEGUNDOS,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
)
from scanner import Scanner24h
from prematch import PreJogoOdds
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

try:
    FUSO_LOCAL = ZoneInfo(API_TIMEZONE)
except ZoneInfoNotFoundError:
    FUSO_LOCAL = timezone.utc


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

        # Pré-jogo do Brasileirão com odds externas
        # (football-data.org + The Odds API — Plano B).
        self.prematch = None

        try:
            self.prematch = PreJogoOdds()
        except Exception:
            logger.exception(
                "❌ Falha ao iniciar o módulo de "
                "pré-jogo externo"
            )

        self.rodando = False
        self.ciclo = 0

        # Guarda chaves de sinais enviados.
        self.sinais_enviados = set()

        # Modo monitor (resumo de jogos no Telegram, sem depender de odds).
        self.ultimo_resumo_monitor = 0.0
        self.assinatura_jogos = None
        self.monitor_anunciado = False

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

    # ============================================================
    # MODO MONITOR
    # ============================================================

    def _monitor_deve_operar(self) -> bool:
        """
        Decide se o ciclo envia resumo de jogos em vez de sinais.

        - on: sempre monitora;
        - off: nunca monitora;
        - auto (padrão): monitora somente enquanto as odds
          estiverem bloqueadas pelo plano.
        """
        if MODO_MONITOR in {"on", "1", "true", "sim"}:
            return True

        if MODO_MONITOR in {"off", "0", "false", "nao", "não"}:
            return False

        return (
            self.scanner.odds_bloqueadas
            or getattr(
                self.scanner, "cota_esgotada", False
            )
        )

    def _anunciar_monitor(self) -> None:
        if self.monitor_anunciado:
            return

        self.monitor_anunciado = True

        logger.info(
            "👁️ MODO MONITOR ativo (MODO_MONITOR=%s | "
            "odds bloqueadas=%s) — resumos a cada %s min",
            MODO_MONITOR,
            "sim" if self.scanner.odds_bloqueadas else "não",
            MONITOR_INTERVALO_MINUTOS,
        )

    @staticmethod
    def _formatar_jogo(jogo: dict, indice: int) -> str:
        casa = str(jogo.get("match_hometeam_name", "?")).strip()
        fora = str(jogo.get("match_awayteam_name", "?")).strip()
        liga = str(jogo.get("league_name", "?")).strip()
        pais = str(jogo.get("country_name", "")).strip()

        cabecalho = (
            f"{pais} — {liga}"
            if pais and pais.lower() != liga.lower()
            else liga
        )

        if jogo.get("_modo_analise") == "AO_VIVO":
            gols_casa = str(
                jogo.get("match_hometeam_score", "")
            ).strip()
            gols_fora = str(
                jogo.get("match_awayteam_score", "")
            ).strip()

            placar = (
                f"{gols_casa} - {gols_fora}"
                if gols_casa
                else "placar indisponível"
            )
            status = (
                str(jogo.get("match_status", "")).strip()
                or "em andamento"
            )

            if status.lower() in {
                "not started", "ns", "scheduled", "time to be defined"
            }:
                status = "aguardando início"

            return (
                f"{indice}. ⚽ {casa} x {fora}\n"
                f"   🏟️ {cabecalho}\n"
                f"   📊 Placar: {placar} | {status}"
            )

        inicio = str(jogo.get("match_time", "")).strip()
        minutos = jogo.get("_minutos_para_inicio")

        quando = (
            f"início {inicio}"
            if inicio
            else "horário indisponível"
        )

        if isinstance(minutos, (int, float)):
            quando += f" (em {max(0, round(minutos))} min)"

        return (
            f"{indice}. ⚽ {casa} x {fora}\n"
            f"   🏟️ {cabecalho}\n"
            f"   🕒 {quando}"
        )

    def _executar_monitor(self, jogos: list) -> None:
        """Envia resumo dos jogos ao Telegram com controle de frequência."""
        agora = time.time()
        intervalo = max(60, MONITOR_INTERVALO_MINUTOS * 60)

        assinatura = tuple(
            sorted(
                (
                    str(j.get("match_id", "")),
                    str(j.get("_modo_analise", "")),
                )
                for j in jogos
                if isinstance(j, dict)
            )
        )

        if jogos:
            mudou = assinatura != self.assinatura_jogos

            if (
                not mudou
                and agora - self.ultimo_resumo_monitor < intervalo
            ):
                return
        else:
            # Sem jogos: batimento cardíaco em intervalo 3x maior.
            if (
                agora - self.ultimo_resumo_monitor
                < intervalo * 3
            ):
                return

        ao_vivo = [
            j for j in jogos
            if isinstance(j, dict)
            and j.get("_modo_analise") == "AO_VIVO"
        ]
        pre_jogo = [
            j for j in jogos
            if isinstance(j, dict)
            and j.get("_modo_analise") == "PRE_JOGO"
        ]

        horario = datetime.now(FUSO_LOCAL).strftime(
            "%d/%m/%Y %H:%M"
        )

        if jogos:
            linhas = [
                "📡 MONITOR TRADERIA",
                "━━━━━━━━━━━━━━━━━━",
                f"🕐 {horario}",
                "",
            ]

            if ao_vivo:
                linhas.append(f"🔴 AO VIVO ({len(ao_vivo)})")

                for indice, jogo in enumerate(
                    ao_vivo, start=1
                ):
                    linhas.append(
                        self._formatar_jogo(jogo, indice)
                    )

                linhas.append("")

            if pre_jogo:
                linhas.append(f"🕒 PRÉ-JOGO ({len(pre_jogo)})")

                for indice, jogo in enumerate(
                    pre_jogo, start=len(ao_vivo) + 1
                ):
                    linhas.append(
                        self._formatar_jogo(jogo, indice)
                    )

                linhas.append("")

            if self.scanner.odds_bloqueadas:
                linhas.append(
                    "ℹ️ Sinais pausados: plano atual sem acesso "
                    "a odds. Este resumo acompanha os jogos."
                )
        else:
            linhas = [
                "📡 MONITOR TRADERIA",
                "━━━━━━━━━━━━━━━━━━",
                f"🕐 {horario}",
                "",
                "Nenhum jogo elegível neste momento.",
                "Continuo verificando a cada ciclo.",
            ]

        mensagem = "\n".join(linhas)[:4000]

        logger.info(
            "👁️ Enviando resumo do monitor ao Telegram"
        )

        if self.notificador.enviar_mensagem(mensagem):
            self.ultimo_resumo_monitor = agora
            self.assinatura_jogos = (
                assinatura if jogos else None
            )

    def _coletar_pre_jogo_externo(self) -> list:
        """Brasileirão na janela de pré-jogo com odds reais de
        bookmakers (The Odds API + agenda football-data.org)."""
        if self.prematch is None or not self.prematch.ativo:
            return []

        try:
            jogos = self.prematch.varrer_pre_jogo()
        except Exception:
            logger.exception(
                "❌ Pré-jogo externo falhou neste ciclo"
            )
            return []

        if jogos:
            logger.info(
                "🎯 Pré-jogo externo: %s jogo(s) elegível(is)",
                len(jogos),
            )

        return jogos

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

        # Pré-jogo do Brasileirão com odds externas (The Odds API) —
        # funciona mesmo sem odds na API-Football.
        jogos_externos = self._coletar_pre_jogo_externo()

        # Modo monitor: resumo no Telegram em vez de sinais
        # (MODO_MONITOR=on, ou auto com odds bloqueadas).
        if self._monitor_deve_operar():
            self._anunciar_monitor()
            self._executar_monitor(jogos + jogos_externos)

            if not jogos_externos:
                return

            # Sinais de pré-jogo com odds externas continuam
            # ativos no modo monitor (não dependem do plano
            # da API-Football).
            logger.info(
                "🎯 Pré-jogo externo: analisando %s jogo(s) "
                "com odds próprias",
                len(jogos_externos),
            )
            jogos = jogos_externos
        else:
            jogos = jogos + jogos_externos

        if not jogos:
            logger.warning(
                "⚠️ Nenhum jogo elegível neste ciclo"
            )
            return

        # O monitor acima recebeu a lista COMPLETA. Aqui,
        # a análise (que custa ~1 req de odds por jogo)
        # fica limitada aos mais prioritários.
        if len(jogos) > MAX_JOGOS_POR_CICLO:
            logger.info(
                "🧮 Análise limitada aos %s jogos mais "
                "prioritários (de %s) — MAX_JOGOS_POR_CICLO",
                MAX_JOGOS_POR_CICLO,
                len(jogos),
            )
            jogos = sorted(
                jogos,
                key=lambda jogo: not (
                    self.scanner._eh_prioritario(
                        jogo
                    )
                ),
            )[:MAX_JOGOS_POR_CICLO]

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

            if not jogo.get("_fonte_odds") and getattr(
                self.scanner, "odds_bloqueadas", False
            ):
                # Plano sem odds: sem odd real não há sinal,
                # então pular direto (poupa cota e tempo).
                logger.info(
                    "⏭️ [%s/%s] %s x %s: pulado "
                    "(odds bloqueadas no plano)",
                    indice,
                    len(jogos),
                    casa,
                    fora,
                )
                continue

            try:
                if jogo.get("_fonte_odds"):
                    # Pré-jogo externo: as odds já vieram com o
                    # jogo (The Odds API). Evita gastar a cota
                    # da API-Football com IDs que lá não existem.
                    odds = [jogo.get("_odds_flat") or {}]
                    stats = {}
                    h2h = []
                else:
                    odds = self.scanner.buscar_odds(
                        match_id
                    )

                    stats = (
                        self.scanner.buscar_estatisticas(
                            match_id,
                            casa=casa,
                            fora=fora,
                        )
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
