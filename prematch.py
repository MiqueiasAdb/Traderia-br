"""
Pré-Jogo com Odds Externas — Plano B (100% gratuito)

Fontes:
- football-data.org (plano grátis): agenda do Brasileirão Série A
  sem custo — usada para saber quando os jogos começam;
- The Odds API (500 créditos/mês grátis): odds reais de bookmakers.

Economia de créditos:
- A agenda (football-data.org) não gasta créditos;
- As odds só são consultadas quando existe jogo do Brasileirão na
  janela de pré-jogo, no máximo a cada 30 minutos (o plano grátis
  da The Odds API atualiza as odds a cada 30 min de qualquer forma);
- Uma única chamada de odds cobre todos os eventos futuros.
"""
import difflib
import logging
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from config import (
    API_TIMEZONE,
    FD_API_KEY,
    FD_CACHE_SEGUNDOS,
    FD_COMPETICAO,
    ODDS_API_KEY,
    ODDS_CACHE_SEGUNDOS,
    ODDS_EVENTOS_CACHE_SEGUNDOS,
    ODDS_MARKETS,
    ODDS_REGIONS,
    ODDS_SPORT_KEY,
    PRELIVE_WINDOW_MINUTES,
)

logger = logging.getLogger("TraderIA")

try:
    FUSO = ZoneInfo(API_TIMEZONE)
except ZoneInfoNotFoundError:
    FUSO = timezone.utc


class PreJogoOdds:
    ODDS_BASE = "https://api.the-odds-api.com/v4"
    FD_BASE = "https://api.football-data.org/v4"

    # Tolerâncias do casamento agenda ↔ eventos com odds.
    DELTA_MAX_MINUTOS = 10.0
    SIMILARIDADE_MINIMA = 0.5

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "TraderIA-Brasil/1.0",
            "Accept": "application/json",
        })

        self.ativo = bool(ODDS_API_KEY)

        if not self.ativo:
            logger.info(
                "ℹ️ Pré-jogo externo inativo: ODDS_API_KEY ausente"
            )
            return

        if not FD_API_KEY:
            logger.warning(
                "⚠️ FD_API_KEY ausente: a agenda virá da própria "
                "The Odds API (consome créditos). Crie a chave "
                "grátis em football-data.org"
            )

        self._cache_agenda: Tuple[float, List[dict]] = (0.0, [])
        self._cache_odds: Tuple[float, Dict[str, dict]] = (0.0, {})
        self._cache_eventos: Tuple[float, List[dict]] = (0.0, [])

        self._fd_invalida = False
        self._odds_invalida = False

        self.creditos_restantes: Optional[int] = None
        self.creditos_usados: Optional[int] = None

    # ========================================================
    # UTILITÁRIOS
    # ========================================================

    @staticmethod
    def _parse_iso(texto: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(
                str(texto).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalizar_nome(texto: str) -> str:
        valor = unicodedata.normalize(
            "NFKD", str(texto or "")
        )
        valor = "".join(
            caractere
            for caractere in valor
            if not unicodedata.combining(caractere)
        ).lower().replace(".", " ").replace("-", " ")

        excluidas = {
            "fc", "ec", "cf", "sc", "sp", "ac", "ab", "aa",
            "club", "clube", "de", "e", "esporte", "esportes",
            "futebol", "regatas",
        }

        palavras = [
            palavra
            for palavra in valor.split()
            if palavra and palavra not in excluidas
        ]

        return " ".join(sorted(palavras))

    @classmethod
    def _similaridade(cls, a: str, b: str) -> float:
        nome_a = cls._normalizar_nome(a)
        nome_b = cls._normalizar_nome(b)

        if not nome_a or not nome_b:
            return 0.0

        return difflib.SequenceMatcher(
            None, nome_a, nome_b
        ).ratio()

    # ========================================================
    # AGENDA — football-data.org (grátis)
    # ========================================================

    def _agenda_brasileirao(self) -> List[dict]:
        """Jogos de hoje/amanhã (não gasta créditos de odds)."""
        agora = time.time()
        ts, dados = self._cache_agenda

        if dados and agora - ts < FD_CACHE_SEGUNDOS:
            return dados

        if self._fd_invalida or not FD_API_KEY:
            return dados

        hoje = datetime.now(timezone.utc).date()

        try:
            resposta = self.session.get(
                (
                    f"{self.FD_BASE}/competitions/"
                    f"{FD_COMPETICAO}/matches"
                ),
                params={
                    "dateFrom": hoje.isoformat(),
                    "dateTo": (
                        hoje + timedelta(days=1)
                    ).isoformat(),
                },
                headers={"X-Auth-Token": FD_API_KEY},
                timeout=15,
            )
        except requests.RequestException as erro:
            logger.warning(
                "🔄 Falha de conexão com football-data.org: %s",
                type(erro).__name__,
            )
            return dados

        if resposta.status_code in (400, 401, 403):
            self._fd_invalida = True
            logger.error(
                "🔑 FD_API_KEY inválida ou sem acesso (HTTP %s). "
                "Confira em football-data.org",
                resposta.status_code,
            )
            return dados

        if resposta.status_code != 200:
            logger.warning(
                "⚠️ football-data.org HTTP %s",
                resposta.status_code,
            )
            return dados

        try:
            partidas = (
                resposta.json().get("matches", [])
            )
        except ValueError:
            logger.error(
                "❌ football-data.org: resposta não é JSON"
            )
            return dados

        encerrados = {
            "FINISHED", "POSTPONED", "SUSPENDED",
            "CANCELLED", "IN_PLAY", "PAUSED", "UNKNOWN",
        }

        agenda: List[dict] = []

        for partida in partidas:
            if not isinstance(partida, dict):
                continue

            inicio = self._parse_iso(
                partida.get("utcDate", "")
            )

            if inicio is None:
                continue

            status = str(
                partida.get("status", "")
            ).upper()

            if status in encerrados:
                continue

            casa = str(
                partida.get("homeTeam", {})
                .get("name", "")
            ).strip()
            fora = str(
                partida.get("awayTeam", {})
                .get("name", "")
            ).strip()

            if not casa or not fora:
                continue

            agenda.append({
                "match_id": f"fd_{partida.get('id')}",
                "_inicio": inicio,
                "casa": casa,
                "fora": fora,
            })

        self._cache_agenda = (agora, agenda)
        logger.info(
            "📅 Agenda Brasileirão (football-data.org): "
            "%s jogo(s) hoje/amanhã",
            len(agenda),
        )
        return agenda

    # ========================================================
    # ODDS — The Odds API
    # ========================================================

    def _ler_creditos(self, headers) -> None:
        try:
            restantes = headers.get(
                "x-requests-remaining"
            )
            usados = headers.get("x-requests-used")

            if restantes is not None:
                self.creditos_restantes = int(
                    float(restantes)
                )

            if usados is not None:
                self.creditos_usados = int(
                    float(usados)
                )

            logger.info(
                "💳 The Odds API — créditos restantes: %s | "
                "usados: %s",
                self.creditos_restantes,
                self.creditos_usados,
            )

            if (
                self.creditos_restantes is not None
                and self.creditos_restantes <= 20
            ):
                logger.warning(
                    "⚠️ Poucos créditos restantes na "
                    "The Odds API este mês!"
                )
        except (ValueError, TypeError):
            pass

    @staticmethod
    def _achatar_evento(evento: dict) -> Dict[str, float]:
        """
        Converte a resposta da The Odds API (bookmakers/markets)
        no formato flat que o brain já entende:
        odd_1, odd_x, odd_2, o+2.5, u+2.5, bts_yes, bts_no.
        Mantém a MAIOR odd entre os bookmakers.
        """
        flat: Dict[str, float] = {}

        casa = str(evento.get("home_team", ""))
        fora = str(evento.get("away_team", ""))

        for bookmaker in evento.get("bookmakers", []) or []:
            for mercado in (
                bookmaker.get("markets", []) or []
            ):
                chave_mercado = str(
                    mercado.get("key", "")
                )

                for resultado in (
                    mercado.get("outcomes", []) or []
                ):
                    nome = str(
                        resultado.get("name", "")
                    )
                    preco = resultado.get("price")

                    if preco is None:
                        continue

                    try:
                        preco = float(preco)
                    except (TypeError, ValueError):
                        continue

                    if chave_mercado == "h2h":
                        if casa and nome == casa:
                            chave = "odd_1"
                        elif nome.lower() == "draw":
                            chave = "odd_x"
                        elif fora and nome == fora:
                            chave = "odd_2"
                        else:
                            continue
                    elif chave_mercado == "totals":
                        ponto = resultado.get("point")

                        try:
                            if float(ponto) != 2.5:
                                continue
                        except (TypeError, ValueError):
                            continue

                        nome_l = nome.lower()

                        if nome_l == "over":
                            chave = "o+2.5"
                        elif nome_l == "under":
                            chave = "u+2.5"
                        else:
                            continue
                    elif chave_mercado == "btts":
                        nome_l = nome.lower()

                        if nome_l == "yes":
                            chave = "bts_yes"
                        elif nome_l == "no":
                            chave = "bts_no"
                        else:
                            continue
                    else:
                        continue

                    if preco > flat.get(chave, 0.0):
                        flat[chave] = preco

        return flat

    def _buscar_odds(self) -> Dict[str, dict]:
        """Odds de todos os eventos futuros (1 chamada cobre tudo)."""
        agora = time.time()
        ts, dados = self._cache_odds

        if dados and agora - ts < ODDS_CACHE_SEGUNDOS:
            return dados

        if self._odds_invalida:
            return dados

        try:
            resposta = self.session.get(
                (
                    f"{self.ODDS_BASE}/sports/"
                    f"{ODDS_SPORT_KEY}/odds"
                ),
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": ODDS_REGIONS,
                    "markets": ODDS_MARKETS,
                    "oddsFormat": "decimal",
                },
                timeout=20,
            )
        except requests.RequestException as erro:
            logger.warning(
                "🔄 Falha de conexão com The Odds API: %s",
                type(erro).__name__,
            )
            return dados

        self._ler_creditos(resposta.headers)

        if resposta.status_code == 401:
            self._odds_invalida = True
            logger.error(
                "🔑 ODDS_API_KEY inválida (HTTP 401). "
                "Confira em the-odds-api.com"
            )
            return dados

        if resposta.status_code == 429:
            logger.warning(
                "⚠️ The Odds API: limite de requisições (429)"
            )
            return dados

        if resposta.status_code == 404:
            logger.error(
                "❌ The Odds API: sport '%s' inexistente ou "
                "fora de temporada. Veja /v4/sports",
                ODDS_SPORT_KEY,
            )
            return dados

        if resposta.status_code != 200:
            logger.warning(
                "⚠️ The Odds API HTTP %s",
                resposta.status_code,
            )
            return dados

        try:
            eventos = resposta.json()
        except ValueError:
            logger.error(
                "❌ The Odds API: resposta não é JSON"
            )
            return dados

        resultado: Dict[str, dict] = {}

        for evento in eventos:
            if not isinstance(evento, dict):
                continue

            resultado[str(evento.get("id"))] = {
                "_flat": self._achatar_evento(evento),
                "_inicio": self._parse_iso(
                    evento.get("commence_time", "")
                ),
                "casa": str(
                    evento.get("home_team", "")
                ),
                "fora": str(
                    evento.get("away_team", "")
                ),
            }

        self._cache_odds = (agora, resultado)
        logger.info(
            "💰 The Odds API: odds recebidas para "
            "%s evento(s)",
            len(resultado),
        )
        return resultado

    def _eventos_odds(self) -> List[dict]:
        """
        Fallback sem FD_API_KEY: lista de eventos vindos da
        própria The Odds API (consome 1 crédito por chamada,
        então o cache aqui é bem maior).
        """
        agora = time.time()
        ts, dados = self._cache_eventos

        if dados and agora - ts < ODDS_EVENTOS_CACHE_SEGUNDOS:
            return dados

        if self._odds_invalida:
            return dados

        try:
            resposta = self.session.get(
                (
                    f"{self.ODDS_BASE}/sports/"
                    f"{ODDS_SPORT_KEY}/events"
                ),
                params={"apiKey": ODDS_API_KEY},
                timeout=20,
            )
        except requests.RequestException as erro:
            logger.warning(
                "🔄 Falha de conexão com The Odds API: %s",
                type(erro).__name__,
            )
            return dados

        self._ler_creditos(resposta.headers)

        if resposta.status_code == 401:
            self._odds_invalida = True
            logger.error(
                "🔑 ODDS_API_KEY inválida (HTTP 401) em /events. "
                "Confira em the-odds-api.com"
            )
            return dados

        if resposta.status_code != 200:
            logger.warning(
                "⚠️ The Odds API (eventos) HTTP %s",
                resposta.status_code,
            )
            return dados

        try:
            eventos = resposta.json()
        except ValueError:
            return dados

        validos = [
            evento
            for evento in eventos
            if isinstance(evento, dict)
            and evento.get("id")
            and evento.get("commence_time")
        ]

        self._cache_eventos = (agora, validos)
        return validos

    # ========================================================
    # CASAMENTO AGENDA ↔ EVENTOS COM ODDS
    # ========================================================

    def _casar(
        self,
        candidato: dict,
        odds_por_evento: Dict[str, dict],
    ) -> Optional[str]:
        ev_direto = candidato.get("_ev_id")

        if ev_direto is not None:
            chave = str(ev_direto)
            return (
                chave
                if chave in odds_por_evento
                else None
            )

        inicio = candidato["_inicio"]
        melhor_id: Optional[str] = None
        melhor_nota = 0.0

        for ev_id, meta in odds_por_evento.items():
            inicio_odds = meta.get("_inicio")

            if inicio_odds is None:
                continue

            delta = abs(
                (
                    inicio_odds - inicio
                ).total_seconds()
            ) / 60.0

            if delta > self.DELTA_MAX_MINUTOS:
                continue

            nota = (
                self._similaridade(
                    candidato["casa"], meta["casa"]
                )
                + self._similaridade(
                    candidato["fora"], meta["fora"]
                )
            ) / 2.0

            if nota > melhor_nota:
                melhor_id = ev_id
                melhor_nota = nota

        if melhor_nota < self.SIMILARIDADE_MINIMA:
            return None

        return melhor_id

    # ========================================================
    # VARREDURA PÚBLICA
    # ========================================================

    def varrer_pre_jogo(self) -> List[Dict]:
        """Jogos do Brasileirão na janela de pré-jogo com odds reais."""
        if not self.ativo:
            return []

        agora = datetime.now(timezone.utc)
        janela = PRELIVE_WINDOW_MINUTES + 5

        # 1) Agenda: football-data.org (grátis) ou eventos da
        #    própria The Odds API (fallback, gasta créditos).
        agenda = self._agenda_brasileirao()

        candidatos: List[dict] = []

        if agenda:
            for jogo in agenda:
                minutos = (
                    jogo["_inicio"] - agora
                ).total_seconds() / 60.0

                if 0 <= minutos <= janela:
                    jogo["_minutos"] = minutos
                    candidatos.append(jogo)
        else:
            for evento in self._eventos_odds():
                inicio = self._parse_iso(
                    evento.get("commence_time", "")
                )

                if inicio is None:
                    continue

                minutos = (
                    inicio - agora
                ).total_seconds() / 60.0

                if not (0 <= minutos <= janela):
                    continue

                candidatos.append({
                    "match_id": (
                        f"oddsapi_{evento.get('id')}"
                    ),
                    "_inicio": inicio,
                    "_minutos": minutos,
                    "_ev_id": evento.get("id"),
                    "casa": str(
                        evento.get("home_team", "")
                    ),
                    "fora": str(
                        evento.get("away_team", "")
                    ),
                })

        if not candidatos:
            return []

        # 2) Odds (1 chamada cobre todos os eventos futuros).
        odds_por_evento = self._buscar_odds()

        if not odds_por_evento:
            return []

        # 3) Casar agenda ↔ eventos e montar jogos do pipeline.
        jogos: List[Dict] = []

        for candidato in candidatos:
            ev_id = self._casar(
                candidato, odds_por_evento
            )

            if ev_id is None:
                continue

            flat = odds_por_evento[ev_id]["_flat"]

            if not (
                flat.get("odd_1")
                or flat.get("odd_2")
                or flat.get("odd_x")
            ):
                continue

            inicio = candidato["_inicio"]

            jogos.append({
                "match_id": candidato["match_id"],
                "match_hometeam_name": candidato["casa"],
                "match_awayteam_name": candidato["fora"],
                "league_name": "Brasileirão Série A",
                "country_name": "Brazil",
                "match_date": inicio.strftime(
                    "%Y-%m-%d"
                ),
                "match_time": inicio.astimezone(
                    FUSO
                ).strftime("%H:%M"),
                "match_live": "0",
                "_modo_analise": "PRE_JOGO",
                "_minutos_para_inicio": round(
                    candidato["_minutos"], 1
                ),
                "_fonte_odds": "the-odds-api",
                "_odds_flat": flat,
            })

        return jogos
