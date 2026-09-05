"""
Pré-Jogo com Odds Externas — Plano B Multi-Liga (100% gratuito)

Fontes:
- football-data.org (plano grátis): agenda de até 12 competições,
  incluindo Brasileirão, Premier League, La Liga, Serie A,
  Bundesliga, Ligue 1 e Championship — não gasta créditos;
- The Odds API (500 créditos/mês grátis): odds reais de bookmakers.

Economia de créditos:
- Odds só são consultadas quando existe jogo da liga na janela
  de pré-jogo (cache de 30 min, que é o próprio intervalo de
  atualização do plano grátis);
- Orçamento diário de créditos (ODDS_CREDITO_DIARIO): ao atingir
  o limite, o módulo serve as últimas odds em cache e para de
  gastar até o dia seguinte;
- A ordem das ligas em PREMATCH_LIGAS define a prioridade de gasto.
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
    FD_CACHE_SEGUNDOS,
    FD_API_KEY,
    ODDS_API_KEY,
    ODDS_CACHE_SEGUNDOS,
    ODDS_CREDITO_DIARIO,
    ODDS_EVENTOS_CACHE_SEGUNDOS,
    ODDS_MARKETS,
    ODDS_REGIONS,
    PRELIVE_WINDOW_MINUTES,
    PREMATCH_LIGAS,
)

logger = logging.getLogger("TraderIA")

try:
    FUSO = ZoneInfo(API_TIMEZONE)
except ZoneInfoNotFoundError:
    FUSO = timezone.utc


def _parse_ligas(texto: str) -> List[Tuple[str, str, str]]:
    """
    Converte "Nome|CODIGO|sport_key, ..." em lista de tuplas.
    Campos vazios são aceitos (ex.: liga só com sport_key).
    """
    ligas: List[Tuple[str, str, str]] = []

    for item in str(texto).split(","):
        partes = [
            parte.strip()
            for parte in item.split("|")
        ]

        while len(partes) < 3:
            partes.append("")

        nome, fd_codigo, odds_key = partes[:3]

        if nome and (fd_codigo or odds_key):
            ligas.append((nome, fd_codigo, odds_key))

    return ligas


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

        self.ligas = _parse_ligas(PREMATCH_LIGAS)

        self.ativo = bool(
            ODDS_API_KEY and self.ligas
        )

        if not self.ativo:
            logger.info(
                "ℹ️ Pré-jogo externo inativo: exige "
                "ODDS_API_KEY e ao menos uma liga válida"
            )
            return

        if not FD_API_KEY:
            logger.warning(
                "⚠️ FD_API_KEY ausente: as agendas virão da "
                "própria The Odds API (consome mais créditos)"
            )

        # Caches por liga: {chave: (timestamp, dados)}
        self._cache_agenda: Dict[str, Tuple[float, list]] = {}
        self._cache_odds: Dict[str, Tuple[float, dict]] = {}
        self._cache_eventos: Dict[str, Tuple[float, list]] = {}

        self._fd_invalida = False
        self._odds_invalida = False

        # Orçamento diário de créditos da The Odds API.
        self._gasto_data = datetime.now(FUSO).date()
        self._gasto_hoje = 0

        self.creditos_restantes: Optional[int] = None
        self.creditos_usados: Optional[int] = None

    # ========================================================
    # UTILITÁRIOS
    # ========================================================

    def _novo_dia(self) -> None:
        hoje = datetime.now(FUSO).date()

        if hoje != self._gasto_data:
            self._gasto_data = hoje
            self._gasto_hoje = 0

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

    def _agenda_liga(
        self, nome: str, fd_codigo: str
    ) -> List[dict]:
        """Jogos de hoje/amanhã (não gasta créditos de odds)."""
        if not fd_codigo:
            return []

        agora = time.time()
        ts, dados = self._cache_agenda.get(
            fd_codigo, (0.0, [])
        )

        if dados and agora - ts < FD_CACHE_SEGUNDOS:
            return dados

        if self._fd_invalida or not FD_API_KEY:
            return dados

        hoje = datetime.now(timezone.utc).date()

        try:
            resposta = self.session.get(
                (
                    f"{self.FD_BASE}/competitions/"
                    f"{fd_codigo}/matches"
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
                "🔄 Falha de conexão com football-data.org "
                "(%s): %s",
                fd_codigo,
                type(erro).__name__,
            )
            return dados

        if resposta.status_code in (400, 401, 403):
            if resposta.status_code in (401, 403):
                self._fd_invalida = True
                logger.error(
                    "🔑 FD_API_KEY inválida ou sem acesso "
                    "(HTTP %s). Confira em football-data.org",
                    resposta.status_code,
                )
            else:
                logger.warning(
                    "⚠️ football-data.org: competição '%s' "
                    "indisponível no plano (HTTP %s)",
                    fd_codigo,
                    resposta.status_code,
                )
            return dados

        if resposta.status_code != 200:
            logger.warning(
                "⚠️ football-data.org (%s) HTTP %s",
                fd_codigo,
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

        self._cache_agenda[fd_codigo] = (agora, agenda)
        logger.info(
            "📅 Agenda %s: %s jogo(s) hoje/amanhã",
            nome,
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
            ultima = headers.get("x-requests-last")

            if restantes is not None:
                self.creditos_restantes = int(
                    float(restantes)
                )

            if usados is not None:
                self.creditos_usados = int(
                    float(usados)
                )

            if ultima is not None:
                self._novo_dia()
                self._gasto_hoje += int(float(ultima))

            logger.info(
                "💳 The Odds API — restantes: %s | usados "
                "no mês: %s | gasto hoje: %s/%s",
                self.creditos_restantes,
                self.creditos_usados,
                self._gasto_hoje,
                ODDS_CREDITO_DIARIO,
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

    def _dentro_do_orcamento(self) -> bool:
        self._novo_dia()

        if self._gasto_hoje >= ODDS_CREDITO_DIARIO:
            return False

        if (
            self.creditos_restantes is not None
            and self.creditos_restantes <= 0
        ):
            return False

        return True

    def _buscar_odds(self, sport_key: str) -> Dict[str, dict]:
        """Odds de todos os eventos futuros (1 chamada cobre tudo)."""
        agora = time.time()
        ts, dados = self._cache_odds.get(
            sport_key, (0.0, {})
        )

        if dados and agora - ts < ODDS_CACHE_SEGUNDOS:
            return dados

        if self._odds_invalida:
            return dados

        if not self._dentro_do_orcamento():
            logger.info(
                "🛑 Orçamento diário de créditos atingido "
                "(%s/%s) — usando cache de %s",
                self._gasto_hoje,
                ODDS_CREDITO_DIARIO,
                sport_key,
            )
            return dados

        try:
            resposta = self.session.get(
                f"{self.ODDS_BASE}/sports/{sport_key}/odds",
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
                "🔄 Falha de conexão com The Odds API (%s): %s",
                sport_key,
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
            logger.warning(
                "⚠️ The Odds API: sport '%s' inexistente ou "
                "fora de temporada",
                sport_key,
            )
            return dados

        if resposta.status_code != 200:
            logger.warning(
                "⚠️ The Odds API (%s) HTTP %s",
                sport_key,
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

        self._cache_odds[sport_key] = (agora, resultado)
        logger.info(
            "💰 The Odds API (%s): odds para %s evento(s)",
            sport_key,
            len(resultado),
        )
        return resultado

    def _eventos_odds(self, sport_key: str) -> List[dict]:
        """
        Fallback sem agenda do football-data.org: lista de
        eventos da própria The Odds API (gasta 1 crédito).
        """
        agora = time.time()
        ts, dados = self._cache_eventos.get(
            sport_key, (0.0, [])
        )

        if dados and agora - ts < ODDS_EVENTOS_CACHE_SEGUNDOS:
            return dados

        if self._odds_invalida:
            return dados

        if not self._dentro_do_orcamento():
            return dados

        try:
            resposta = self.session.get(
                (
                    f"{self.ODDS_BASE}/sports/"
                    f"{sport_key}/events"
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
                "⚠️ The Odds API (eventos %s) HTTP %s",
                sport_key,
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

        self._cache_eventos[sport_key] = (agora, validos)
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
        """Jogos na janela de pré-jogo com odds reais, por liga."""
        if not self.ativo:
            return []

        agora = datetime.now(timezone.utc)
        janela = PRELIVE_WINDOW_MINUTES + 5

        jogos: List[Dict] = []

        for nome, fd_codigo, sport_key in self.ligas:
            # 1) Agenda: football-data.org (grátis) ou eventos
            #    da própria The Odds API (fallback, gasta).
            agenda = self._agenda_liga(nome, fd_codigo)

            candidatos: List[dict] = []

            if agenda:
                for jogo in agenda:
                    minutos = (
                        jogo["_inicio"] - agora
                    ).total_seconds() / 60.0

                    if 0 <= minutos <= janela:
                        jogo["_minutos"] = minutos
                        candidatos.append(jogo)
            elif sport_key:
                for evento in self._eventos_odds(
                    sport_key
                ):
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

            if not candidatos or not sport_key:
                continue

            # 2) Odds (1 chamada cobre todos os eventos da liga).
            odds_por_evento = self._buscar_odds(sport_key)

            if not odds_por_evento:
                continue

            # 3) Casar agenda ↔ eventos e montar o jogo.
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
                    "match_hometeam_name": candidato[
                        "casa"
                    ],
                    "match_awayteam_name": candidato[
                        "fora"
                    ],
                    "league_name": nome,
                    "country_name": "",
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

        if jogos:
            logger.info(
                "🎯 Pré-jogo externo total: %s jogo(s) "
                "com odds de bookmakers",
                len(jogos),
            )

        return jogos
