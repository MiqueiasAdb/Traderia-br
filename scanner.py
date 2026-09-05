"""
Scanner 24h — API-Sports v3 (api-sports.io)

Migração da apifootball.com para a API-Sports:
- Odds pré-jogo e AO VIVO disponíveis (funcionam até no Free);
- Endpoints v3: /fixtures, /odds, /odds/live, /fixtures/statistics,
  /fixtures/headtohead, /status;
- Respostas são TRADUZIDAS para o formato que o main/brain já
  consomem (match_id, match_hometeam_name, odd_1, ...);
- Orçamento diário de requisições (API_REQ_DIA_LIMITE): Free tem
  100/dia; Pro tem 7.500/dia. Ao esgotar, o scanner sinaliza
  cota_esgotada e o bot entra em monitor até o dia seguinte.
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from config import (
    API2_KEY,
    API_BASE,
    API_INTERVALO_MINIMO,
    API_KEY,
    API_REQ_DIA_LIMITE,
    API_TIMEZONE,
    API_TIMEOUT_CONEXAO,
    API_TIMEOUT_LEITURA,
    MAX_JOGOS_POR_CICLO,
    PRELIVE_WINDOW_MINUTES,
)

logger = logging.getLogger("TraderIA")

from legado import ScannerLegado

try:
    FUSO = ZoneInfo(API_TIMEZONE)
except ZoneInfoNotFoundError:
    FUSO = timezone.utc

_STATUS_LIVE = {
    "1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT",
}
_STATUS_MORTO = {
    "FT", "AET", "PEN", "SUSP", "PST", "CANC",
    "ABD", "AWD", "WO",
}


class Scanner24h:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "x-apisports-key": API_KEY,
            "Accept": "application/json",
        })

        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._last_req = 0.0

        # Orçamento diário de requisições.
        hoje = datetime.now(timezone.utc).date()
        self._req_data = hoje
        self._req_usados = 0
        self.cota_esgotada = False

        # True quando a API devolve erro de plano em /odds.
        self.odds_bloqueadas = False

        # Nome (minúsculo) → team_id, para H2H.
        self._mapa_times: Dict[str, int] = {}

        # IDs de jogos ao vivo da última varredura.
        self._ids_ao_vivo = set()

        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

        self.requisicoes_restantes: Optional[int] = None
        self.plano = "?"

        # API legada (opcional): estatísticas e H2H por lá,
        # poupando a cota diária da API-Sports.
        self.legado = None

        if API2_KEY:
            try:
                self.legado = ScannerLegado()
            except Exception:
                logger.exception(
                    "❌ Falha ao iniciar API legada"
                )

        self.ligas_prioritarias = [
            "championship", "ligue 2",
            "brasileirão", "serie a", "serie b",
            "copa do brasil", "premier league", "la liga",
            "bundesliga", "ligue 1", "champions league",
            "europa league", "libertadores", "sudamericana",
            "eredivisie", "primeira liga", "mls", "j-league",
            "paulista", "carioca",
        ]

        # Consulta o status da conta (1 requisição).
        try:
            status = self._get(
                "status", {}, cache_seg=3600
            )
            if isinstance(status, dict):
                conta = status.get("account", {}) or {}
                sub = (
                    status.get("subscription", {})
                    or {}
                )
                self.plano = str(sub.get("plan", "?"))
                self._req_usados = int(
                    (status.get("requests", {}) or {})
                    .get("current", 0)
                )
                logger.info(
                    "💳 API-Sports — plano %s | "
                    "requisições hoje: %s/%s | conta: %s",
                    self.plano,
                    self._req_usados,
                    API_REQ_DIA_LIMITE,
                    conta.get("firstname", ""),
                )
        except Exception:
            pass

    # ========================================================
    # INFRA: requisição, cache e orçamento
    # ========================================================

    def _novo_dia(self) -> None:
        hoje = datetime.now(timezone.utc).date()

        if hoje != self._req_data:
            self._req_data = hoje
            self._req_usados = 0
            self.cota_esgotada = False

    def _tem_cota(self, custo: int = 1) -> bool:
        self._novo_dia()

        if self._req_usados + custo > API_REQ_DIA_LIMITE:
            if not self.cota_esgotada:
                self.cota_esgotada = True
                logger.warning(
                    "🛑 Cota diária da API-Sports atingida "
                    "(%s/%s). Modo economia até o dia "
                    "seguinte (meia-noite UTC).",
                    self._req_usados,
                    API_REQ_DIA_LIMITE,
                )
            return False

        return True

    def _get(
        self,
        endpoint: str,
        params: dict,
        cache_seg: int = 15,
    ):
        """GET v3 com cache, rate limit, re-tentativas e cota."""
        self._novo_dia()

        chave = (
            endpoint + "?" + str(sorted(params.items()))
        )
        agora = time.time()

        ts, dados = self._cache.get(chave, (0.0, None))
        if dados is not None and agora - ts < cache_seg:
            return dados

        if not self._tem_cota():
            return None

        elapsed = time.time() - self._last_req
        if elapsed < API_INTERVALO_MINIMO:
            time.sleep(API_INTERVALO_MINIMO - elapsed)
        self._last_req = time.time()

        for tentativa in range(1, 4):
            try:
                resposta = self.session.get(
                    f"{API_BASE.rstrip('/')}/{endpoint}",
                    params=params,
                    timeout=(
                        API_TIMEOUT_CONEXAO,
                        API_TIMEOUT_LEITURA,
                    ),
                )

                if resposta.status_code == 429:
                    logger.warning(
                        "⚠️ 429 (limite de ritmo) tentativa "
                        "%s/3 — aguardando",
                        tentativa,
                    )
                    time.sleep(6.0)
                    continue

                if resposta.status_code != 200:
                    logger.warning(
                        "⚠️ HTTP %s em /%s (tentativa %s/3)",
                        resposta.status_code,
                        endpoint,
                        tentativa,
                    )
                    time.sleep(1.0)
                    continue

                try:
                    data = resposta.json()
                except ValueError:
                    logger.error(
                        "❌ Resposta não é JSON (/%s)",
                        endpoint,
                    )
                    return None

                # Cota consumida (controle local).
                self._req_usados += 1
                if (
                    self.requisicoes_restantes
                    is not None
                ):
                    self.requisicoes_restantes = max(
                        0,
                        (
                            self.requisicoes_restantes
                            - 1
                        ),
                    )

                erros = data.get("errors")

                if isinstance(erros, dict) and erros:
                    for chave_erro, detalhe in (
                        erros.items()
                    ):
                        logger.warning(
                            "⚠️ Aviso da API (%s): %s = %s",
                            endpoint,
                            chave_erro,
                            detalhe,
                        )

                        if chave_erro in {
                            "plan", "token",
                        }:
                            if chave_erro == "plan":
                                self.odds_bloqueadas = (
                                    True
                                )
                            logger.error(
                                "🔑 Problema de acesso "
                                "(%s): verifique plano/chave "
                                "em dashboard.api-sports.io",
                                chave_erro,
                            )
                    return None

                if isinstance(erros, list) and erros:
                    logger.warning(
                        "⚠️ Aviso da API (%s): %s",
                        endpoint,
                        erros,
                    )
                    return None

                resposta_dados = data.get("response")
                self._cache[chave] = (
                    agora,
                    resposta_dados,
                )
                return resposta_dados

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as erro:
                logger.warning(
                    "🔄 Oscilação de conexão (%s/3): %s",
                    tentativa,
                    type(erro).__name__,
                )
                time.sleep(1.0)
            except Exception as erro:
                logger.error(
                    "❌ Erro inesperado (/%s): %s",
                    endpoint,
                    erro,
                )
                break

        return None

    # ========================================================
    # TRADUÇÃO v3 → formato interno
    # ========================================================

    @staticmethod
    def _parse_data_v3(texto: str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(
                str(texto).replace("Z", "+00:00")
            )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt
        except (ValueError, TypeError):
            return None

    def _traduzir_fixture(
        self, fixture: dict
    ) -> Optional[dict]:
        try:
            base = fixture.get("fixture", {}) or {}
            times = fixture.get("teams", {}) or {}
            gols = fixture.get("goals", {}) or {}
            liga = fixture.get("league", {}) or {}
            status = (
                base.get("status", {}) or {}
            )

            match_id = str(base.get("id", ""))

            if not match_id:
                return None

            casa = str(
                (times.get("home", {}) or {})
                .get("name", "")
            ).strip()
            fora = str(
                (times.get("away", {}) or {})
                .get("name", "")
            ).strip()

            if not casa or not fora:
                return None

            casa_id = (
                (times.get("home", {}) or {})
                .get("id")
            )
            fora_id = (
                (times.get("away", {}) or {})
                .get("id")
            )

            if casa_id:
                self._mapa_times[
                    casa.lower()
                ] = casa_id

            if fora_id:
                self._mapa_times[
                    fora.lower()
                ] = fora_id

            inicio = self._parse_data_v3(
                str(base.get("date", ""))
            )
            local = (
                inicio.astimezone(FUSO)
                if inicio
                else None
            )

            curto = str(
                status.get("short", "")
            ).upper()

            gols_casa = gols.get("home")
            gols_fora = gols.get("away")

            decorrido = status.get("elapsed")

            if curto in _STATUS_LIVE:
                status_texto = (
                    str(decorrido)
                    if decorrido is not None
                    else curto
                )
            elif curto in _STATUS_MORTO:
                status_texto = "Encerrado"
            else:
                status_texto = (
                    str(decorrido)
                    if decorrido is not None
                    else curto
                )

            return {
                "match_id": match_id,
                "match_hometeam_name": casa,
                "match_awayteam_name": fora,
                "match_hometeam_score": (
                    gols_casa
                    if gols_casa is not None
                    else ""
                ),
                "match_awayteam_score": (
                    gols_fora
                    if gols_fora is not None
                    else ""
                ),
                "match_status": status_texto,
                "match_live": (
                    "1"
                    if curto in _STATUS_LIVE
                    else "0"
                ),
                "match_date": (
                    local.strftime("%Y-%m-%d")
                    if local
                    else ""
                ),
                "match_time": (
                    local.strftime("%H:%M")
                    if local
                    else ""
                ),
                "league_name": str(
                    liga.get("name", "")
                ),
                "country_name": str(
                    liga.get("country", "")
                ),
                "_team_home_id": casa_id,
                "_team_away_id": fora_id,
                "_inicio": inicio,
            }
        except Exception:
            return None

    # ========================================================
    # VARREDURA
    # ========================================================

    def varrer_jogos_ao_vivo(self) -> List[Dict]:
        """Jogos AO VIVO + pré-jogo (1-2 chamadas por ciclo)."""
        agora = datetime.now(FUSO)
        hoje = agora.strftime("%Y-%m-%d")

        jogos_finais: List[Dict] = []
        ids_vistos = set()
        self._ids_ao_vivo = set()

        def adicionar(
            jogo: Optional[dict],
            ao_vivo: bool,
            minutos: Optional[float] = None,
        ):
            if not jogo:
                return

            mid = jogo["match_id"]

            if mid in ids_vistos:
                return

            ids_vistos.add(mid)
            jogo["_modo_analise"] = (
                "AO_VIVO" if ao_vivo else "PRE_JOGO"
            )

            if minutos is not None:
                jogo["_minutos_para_inicio"] = round(
                    minutos, 1
                )

            jogos_finais.append(jogo)

            if ao_vivo:
                self._ids_ao_vivo.add(mid)

            liga = str(
                jogo.get("league_name", "")
            ).strip()

            if liga:
                self.ligas_encontradas.add(liga)

        # 1 chamada: todos os jogos do dia no fuso local
        # (inclui os AO VIVO, com status/placar/minuto).
        dados = self._get(
            "fixtures",
            {
                "date": hoje,
                "timezone": API_TIMEZONE,
            },
            cache_seg=60,
        )

        for fixture in dados or []:
            if not isinstance(fixture, dict):
                continue

            jogo = self._traduzir_fixture(fixture)

            if not jogo:
                continue

            curto = str(
                jogo.get("match_status", "")
            ).upper()

            if curto == "ENCERRADO" or str(
                jogo.get("match_live")
            ) not in {"0", "1"}:
                continue

            if jogo["match_live"] == "1":
                adicionar(jogo, ao_vivo=True)
                continue

            # Pré-jogo: janela antes do apito inicial.
            inicio = jogo.get("_inicio")

            if inicio is None:
                continue

            diff_min = (
                inicio - agora.astimezone(inicio.tzinfo)
            ).total_seconds() / 60.0

            if 0 <= diff_min <= (
                PRELIVE_WINDOW_MINUTES + 5
            ):
                adicionar(
                    jogo,
                    ao_vivo=False,
                    minutos=diff_min,
                )

        # Cobertura de jogos que passam da meia-noite local.
        if agora.hour >= 23:
            amanha = (
                agora + timedelta(days=1)
            ).strftime("%Y-%m-%d")

            dados_amanha = self._get(
                "fixtures",
                {
                    "date": amanha,
                    "timezone": API_TIMEZONE,
                },
                cache_seg=600,
            )

            for fixture in dados_amanha or []:
                if not isinstance(fixture, dict):
                    continue

                jogo = self._traduzir_fixture(
                    fixture
                )

                if not jogo or jogo.get(
                    "match_live"
                ) == "1":
                    continue

                inicio = jogo.get("_inicio")

                if inicio is None:
                    continue

                diff_min = (
                    inicio
                    - agora.astimezone(
                        inicio.tzinfo
                    )
                ).total_seconds() / 60.0

                if 0 <= diff_min <= (
                    PRELIVE_WINDOW_MINUTES + 5
                ):
                    adicionar(
                        jogo,
                        ao_vivo=False,
                        minutos=diff_min,
                    )

        # Priorização e limite por ciclo.
        prioritarios = [
            j
            for j in jogos_finais
            if self._eh_prioritario(j)
        ]
        outros = [
            j
            for j in jogos_finais
            if not self._eh_prioritario(j)
        ]

        selecionados = (
            prioritarios + outros
        )[:MAX_JOGOS_POR_CICLO]

        self.jogos_analisados_hoje += len(
            selecionados
        )

        pre_qtd = sum(
            1
            for j in selecionados
            if j.get("_modo_analise") == "PRE_JOGO"
        )
        live_qtd = len(selecionados) - pre_qtd

        logger.info(
            "⚡ Varredura Concluída: %s jogos selecionados "
            "(%s AO VIVO | %s Pré-Jogo) | req hoje: %s/%s",
            len(selecionados),
            live_qtd,
            pre_qtd,
            self._req_usados,
            API_REQ_DIA_LIMITE,
        )

        return selecionados

    def _eh_prioritario(self, jogo: Dict) -> bool:
        liga = str(
            jogo.get("league_name", "")
        ).lower()

        return any(
            prioridade in liga
            for prioridade in self.ligas_prioritarias
        )

    # ========================================================
    # ODDS (pré + AO VIVO)
    # ========================================================

    @staticmethod
    def _achatar_odds_v3(evento: dict) -> Dict[str, float]:
        """
        Converte bookmakers/bets/values da v3 no formato flat
        que o brain entende (odd_1, odd_x, odd_2, o+2.5,
        u+2.5, bts_yes, bts_no, correct_score_h_a).
        Mantém a MAIOR odd entre os bookmakers.
        """
        flat: Dict[str, float] = {}

        def salvar(chave: str, valor) -> None:
            try:
                odd = float(str(valor).strip())
            except (TypeError, ValueError):
                return

            if odd <= 1.0:
                return

            if odd > flat.get(chave, 0.0):
                flat[chave] = odd

        for bookmaker in (
            evento.get("bookmakers", []) or []
        ):
            for bet in (
                bookmaker.get("bets", []) or []
            ):
                nome_aposta = str(
                    bet.get("name", "")
                ).strip().lower()

                for valor in (
                    bet.get("values", []) or []
                ):
                    rotulo = str(
                        valor.get("value", "")
                    ).strip()
                    odd = valor.get("odd")

                    if nome_aposta in {
                        "match winner",
                    }:
                        chave = {
                            "home": "odd_1",
                            "draw": "odd_x",
                            "away": "odd_2",
                        }.get(rotulo.lower())

                        if chave:
                            salvar(chave, odd)

                    elif nome_aposta == "goals over/under":
                        m = re.match(
                            r"(over|under)\s*(\d+(?:\.\d+)?)",
                            rotulo.lower(),
                        )

                        if m and m.group(2) == "2.5":
                            salvar(
                                (
                                    "o+2.5"
                                    if m.group(1)
                                    == "over"
                                    else "u+2.5"
                                ),
                                odd,
                            )

                    elif nome_aposta in {
                        "both teams score",
                        "both teams to score",
                        "both teams will score",
                    }:
                        chave = {
                            "yes": "bts_yes",
                            "no": "bts_no",
                        }.get(rotulo.lower())

                        if chave:
                            salvar(chave, odd)

                    elif nome_aposta in {
                        "exact score",
                        "correct score",
                    }:
                        m = re.match(
                            r"(\d+)\s*[-x:]\s*(\d+)",
                            rotulo,
                        )

                        if m:
                            salvar(
                                (
                                    f"correct_score_"
                                    f"{m.group(1)}_"
                                    f"{m.group(2)}"
                                ),
                                odd,
                            )

        return flat

    def _odds_ao_vivo_todos(self) -> Dict[str, dict]:
        """1 chamada para odds de TODOS os jogos ao vivo."""
        resposta = self._get(
            "odds/live", {}, cache_seg=30
        )

        mapa: Dict[str, dict] = {}

        for evento in resposta or []:
            if not isinstance(evento, dict):
                continue

            mid = str(
                (evento.get("fixture", {}) or {})
                .get("id", "")
            )

            if mid:
                mapa[mid] = self._achatar_odds_v3(
                    evento
                )

        return mapa

    def buscar_odds(
        self, match_id: str
    ) -> List[Dict]:
        """Retorna [flat] com as odds do jogo."""
        # AO VIVO: 1 chamada cobre todos os jogos.
        if match_id in self._ids_ao_vivo:
            mapa = self._odds_ao_vivo_todos()
            flat = mapa.get(str(match_id), {})

            if flat:
                return [flat]

            # Fallback: alguns jogos ao vivo só têm odds
            # no endpoint por fixture.
            resposta_live = self._get(
                "odds",
                {"fixture": match_id},
                cache_seg=60,
            )

            for evento in resposta_live or []:
                if isinstance(evento, dict):
                    flat = self._achatar_odds_v3(
                        evento
                    )

                    if flat:
                        return [flat]

            return []

        resposta = self._get(
            "odds",
            {"fixture": match_id},
            cache_seg=60,
        )

        for evento in resposta or []:
            if isinstance(evento, dict):
                flat = self._achatar_odds_v3(evento)

                if flat:
                    return [flat]

        return []

    # ========================================================
    # ESTATÍSTICAS / H2H
    # ========================================================

    def buscar_estatisticas(
        self,
        match_id: str,
        casa: str = None,
        fora: str = None,
    ) -> Dict:
        # 1º: API legada (poupa cota v3).
        if self.legado and self.legado.ativo:
            try:
                mid_legado = (
                    self.legado.achar_match_id(
                        casa or "", fora or ""
                    )
                )

                if mid_legado:
                    stats = (
                        self.legado.buscar_estatisticas(
                            mid_legado
                        )
                    )

                    if stats:
                        return stats
            except Exception:
                logger.exception(
                    "⚠️ API legada falhou nas estatísticas"
                )

        resposta = self._get(
            "fixtures/statistics",
            {"fixture": match_id},
            cache_seg=60,
        )

        simples: Dict[str, Dict] = {}

        for indice, lado in enumerate(
            resposta or []
        ):
            if not isinstance(lado, dict):
                continue

            chave = (
                "home" if indice == 0 else "away"
            )

            for stat in (
                lado.get("statistics", []) or []
            ):
                tipo = str(
                    stat.get("type", "")
                ).strip()

                if tipo:
                    simples.setdefault(
                        chave, {}
                    )[tipo] = stat.get(
                        "value"
                    )

        return simples

    def buscar_confronto_direto(
        self,
        time1: str,
        time2: str,
    ) -> List[Dict]:
        # 1º: API legada por nome (sem gastar cota v3).
        if self.legado and self.legado.ativo:
            try:
                h2h_legado = (
                    self.legado.buscar_h2h(
                        time1, time2
                    )
                )

                if h2h_legado:
                    return h2h_legado
            except Exception:
                logger.exception(
                    "⚠️ API legada falhou no H2H"
                )

        id1 = self._mapa_times.get(
            str(time1).lower()
        )
        id2 = self._mapa_times.get(
            str(time2).lower()
        )

        if not id1 or not id2 or id1 == id2:
            return []

        resposta = self._get(
            "fixtures/headtohead",
            {"h2h": f"{id1}-{id2}"},
            cache_seg=1800,
        )

        confrontos: List[Dict] = []

        for partida in (resposta or [])[:8]:
            if not isinstance(partida, dict):
                continue

            gols = (
                partida.get("goals", {}) or {}
            )

            confrontos.append({
                "match_hometeam_score": (
                    gols.get("home", "")
                ),
                "match_awayteam_score": (
                    gols.get("away", "")
                ),
            })

        return confrontos

    def buscar_previsoes(
        self, match_id: str
    ) -> Dict:
        resposta = self._get(
            "predictions",
            {"fixture": match_id},
            cache_seg=300,
        )

        if isinstance(resposta, list) and resposta:
            return resposta[0] or {}

        return {}

    def status(self) -> str:
        return (
            f"🌍 Scanner Ativo (API-Sports {self.plano}) | "
            f"req hoje: {self._req_usados}/"
            f"{API_REQ_DIA_LIMITE} | Jogos processados: "
            f"{self.jogos_analisados_hoje}"
        )
