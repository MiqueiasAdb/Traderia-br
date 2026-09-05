"""
Scanner 24h — Versão Corrigida

Correções em relação à versão anterior:
1. A API-Football responde HTTP 200 com {"error": 404, "message": "..."}
   quando algo falha (inclusive CHAVE INVÁLIDA). Agora o log mostra a
   mensagem real em vez de só "404".
2. Falha de autenticação ("Authentification failed!") é detectada e
   reportada de forma clara, com instrução de como resolver.
3. Bug corrigido: o scanner marcava "_status_live" mas o main.py/brain.py
   leem "_modo_analise". Agora os jogos recebem _modo_analise corretamente
   ("AO_VIVO" ou "PRE_JOGO").
4. Fuso horário passado para a API (timezone) e usado no cálculo do
   pré-jogo, evitando jogos com hora errada.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from config import (
    API_BASE,
    API_INTERVALO_MINIMO,
    API_KEY,
    API_TIMEZONE,
    API_TIMEOUT_CONEXAO,
    API_TIMEOUT_LEITURA,
    MAX_JOGOS_POR_CICLO,
    PRELIVE_WINDOW_MINUTES,
)

logger = logging.getLogger("TraderIA")

try:
    FUSO = ZoneInfo(API_TIMEZONE)
except ZoneInfoNotFoundError:
    logger.warning(
        "⚠️ Fuso '%s' indisponível — usando UTC", API_TIMEZONE
    )
    FUSO = timezone.utc


class Scanner24h:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self._cache = {}
        self._last_req = 0.0
        self._alerta_auth_emitido = False

        # Jogos cujas odds vieram vazias/indisponíveis: evita repetir
        # a chamada a cada ciclo e poupa cota da API (30 min por jogo).
        self._odds_indisponivel_ate = {}

        # Vira True quando a API responde que o plano não tem acesso
        # às odds ("please check your plan"). Usado pelo modo monitor.
        self.odds_bloqueadas = False
        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

        # Ligas prioritárias — no plano Free a cobertura é
        # England Championship + France Ligue 2.
        self.ligas_prioritarias = [
            "Championship", "Ligue 2",
            "Brasileirão", "Serie A", "Serie B", "Copa do Brasil",
            "Premier League", "La Liga", "Bundesliga", "Ligue 1",
            "Champions League", "Europa League", "Libertadores",
            "Sul-Americana", "Eredivisie", "Primeira Liga",
        ]

    # ========================================================
    # REQUISIÇÃO BASE
    # ========================================================

    def _get(self, params: dict, cache_seg: int = 15):
        """Requisição segura com cache, rate limit e diagnóstico de erros."""
        # Controle de taxa (Rate Limit)
        elapsed = time.time() - self._last_req
        if elapsed < API_INTERVALO_MINIMO:
            time.sleep(API_INTERVALO_MINIMO - elapsed)
        self._last_req = time.time()

        # Cache (a chave é calculada antes de injetar a APIkey)
        chave_cache = str(sorted(params.items()))
        agora = time.time()

        if chave_cache in self._cache:
            dados, ts = self._cache[chave_cache]
            if agora - ts < cache_seg:
                return dados

        params = {**params, "APIkey": API_KEY}

        for tentativa in range(1, 4):
            try:
                response = self.session.get(
                    API_BASE,
                    params=params,
                    timeout=(API_TIMEOUT_CONEXAO, API_TIMEOUT_LEITURA),
                )

                if response.status_code != 200:
                    logger.warning(
                        "⚠️ Status HTTP %s na tentativa %s/3 (%s)",
                        response.status_code,
                        tentativa,
                        params.get("action"),
                    )
                    time.sleep(1.0)
                    continue

                try:
                    data = response.json()
                except ValueError:
                    logger.error(
                        "❌ Resposta da API não é JSON válido (%s)",
                        params.get("action"),
                    )
                    return None

                # A API-Football devolve HTTP 200 + {"error": ..., "message": ...}
                # quando algo falha. É aqui que o antigo "Aviso da API: 404"
                # era gerado, escondendo a mensagem real.
                if isinstance(data, dict) and "error" in data:
                    codigo = data.get("error")
                    mensagem = str(data.get("message", ""))

                    if "authent" in mensagem.lower():
                        # Falha de chave: avisar de forma clara (uma vez).
                        if not self._alerta_auth_emitido:
                            self._alerta_auth_emitido = True
                            logger.error(
                                "🔑 API_KEY INVÁLIDA OU VAZIA! A API respondeu: "
                                "'%s'. Corrija em: Render → Environment → API_KEY "
                                "(copie a chave exata do painel apifootball.com e "
                                "salve sem espaços/aspas). Depois faça redeploy.",
                                mensagem or "Authentification failed!",
                            )
                        return None

                    logger.warning(
                        "⚠️ Aviso da API (%s): erro %s — %s",
                        params.get("action"),
                        codigo,
                        mensagem or "sem detalhes",
                    )

                    # Plano sem acesso ao endpoint (ex.: odds bloqueadas).
                    if (
                        "plan" in mensagem.lower()
                        and params.get("action") == "get_odds"
                    ):
                        self.odds_bloqueadas = True

                    return None

                self._cache[chave_cache] = (data, agora)
                return data

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                logger.warning(
                    "🔄 Oscilação de conexão (%s/3): %s",
                    tentativa,
                    type(e).__name__,
                )
                time.sleep(1.0)
            except Exception as e:
                logger.error(
                    "❌ Erro inesperado na API: %s: %s",
                    type(e).__name__,
                    e,
                )
                break

        return None

    # ========================================================
    # VARREDURA
    # ========================================================

    def varrer_jogos_ao_vivo(self) -> List[Dict]:
        """Varre jogos AO VIVO + pré-jogo dentro da janela configurada."""
        agora = datetime.now(FUSO)
        hoje = agora.strftime("%Y-%m-%d")
        amanha = (agora + timedelta(days=1)).strftime("%Y-%m-%d")

        jogos_finais: List[Dict] = []
        ids_vistos = set()

        def adicionar(jogo: Dict, ao_vivo: bool, minutos: Optional[float] = None):
            match_id = str(jogo.get("match_id", ""))
            if not match_id or match_id in ids_vistos:
                return
            ids_vistos.add(match_id)
            jogo["_modo_analise"] = "AO_VIVO" if ao_vivo else "PRE_JOGO"
            if minutos is not None:
                jogo["_minutos_para_inicio"] = round(minutos, 1)
            jogos_finais.append(jogo)
            self.ligas_encontridas_add(jogo)

        # 1. BUSCA JOGOS AO VIVO
        dados_live = self._get({
            "action": "get_events",
            "from": hoje,
            "to": amanha,
            "match_live": "1",
            "timezone": API_TIMEZONE,
        }, cache_seg=10)

        jogos_live = dados_live if isinstance(dados_live, list) else []
        for jogo in jogos_live:
            if isinstance(jogo, dict):
                adicionar(jogo, ao_vivo=True)

        # 2. BUSCA JOGOS DO PERÍODO (filtrar Pré-Jogo na janela configurada)
        dados_dia = self._get({
            "action": "get_events",
            "from": hoje,
            "to": amanha,
            "timezone": API_TIMEZONE,
        }, cache_seg=45)

        if isinstance(dados_dia, list):
            for jogo in dados_dia:
                if not isinstance(jogo, dict):
                    continue
                try:
                    if str(jogo.get("match_live")) == "1":
                        continue

                    horario = str(jogo.get("match_time", "")).strip()
                    data_jogo = str(
                        jogo.get("match_date", hoje)
                    ).strip()

                    if not horario or ":" not in horario:
                        continue

                    inicio = datetime.strptime(
                        f"{data_jogo} {horario[:5]}",
                        "%Y-%m-%d %H:%M",
                    ).replace(tzinfo=FUSO)

                    diff_min = (
                        inicio - agora
                    ).total_seconds() / 60.0

                    # Já começou há pouco mas a API ainda não marcou
                    # como live, ou começa dentro da janela de pré-jogo.
                    if -5 <= diff_min <= PRELIVE_WINDOW_MINUTES + 5:
                        adicionar(
                            jogo,
                            ao_vivo=False,
                            minutos=diff_min,
                        )
                except (ValueError, KeyError):
                    continue

        # 3. PRIORIZAÇÃO E LIMITE POR CICLO
        prioritarios = [
            j for j in jogos_finais if self._eh_prioritario(j)
        ]
        outros = [
            j for j in jogos_finais if not self._eh_prioritario(j)
        ]

        selecionados = (prioritarios + outros)[
            :MAX_JOGOS_POR_CICLO
        ]

        self.jogos_analisados_hoje += len(selecionados)

        pre_jogo_qtd = sum(
            1
            for j in selecionados
            if j.get("_modo_analise") == "PRE_JOGO"
        )
        ao_vivo_qtd = len(selecionados) - pre_jogo_qtd

        logger.info(
            "⚡ Varredura Concluída: %s jogos selecionados "
            "(%s AO VIVO | %s Pré-Jogo)",
            len(selecionados),
            ao_vivo_qtd,
            pre_jogo_qtd,
        )

        return selecionados

    def _eh_prioritario(self, jogo: Dict) -> bool:
        liga = str(jogo.get("league_name", "")).lower()
        return any(
            prioridade in liga
            for prioridade in self.ligas_prioritarias
        )

    def ligas_encontridas_add(self, jogo: Dict) -> None:
        liga = str(jogo.get("league_name", "")).strip()
        if liga:
            self.ligas_encontradas.add(liga)

    # ========================================================
    # CONSULTAS POR JOGO
    # ========================================================

    def buscar_odds(self, match_id: str) -> List[Dict]:
        agora = time.time()

        if agora < self._odds_indisponivel_ate.get(match_id, 0):
            return []

        dados = self._get({
            "action": "get_odds",
            "match_id": match_id,
        }, cache_seg=10)

        if isinstance(dados, list) and dados:
            return dados

        # Sem odds (erro ou lista vazia): adia nova tentativa por 30 min.
        self._odds_indisponivel_ate[match_id] = agora + 1800
        return []

    def buscar_confronto_direto(
        self, time1: str, time2: str
    ) -> List[Dict]:
        dados = self._get({
            "action": "get_H2H",
            "firstTeam": time1,
            "secondTeam": time2,
        }, cache_seg=1800)
        return dados if isinstance(dados, list) else []

    def buscar_estatisticas(self, match_id: str) -> Dict:
        dados = self._get({
            "action": "get_statistics",
            "match_id": match_id,
        }, cache_seg=15)
        return dados if isinstance(dados, dict) else {}

    def buscar_previsoes(self, match_id: str) -> Dict:
        dados = self._get({
            "action": "get_predictions",
            "match_id": match_id,
        }, cache_seg=300)
        return dados if isinstance(dados, dict) else {}

    def status(self) -> str:
        return (
            f"🌍 Scanner Ativo | Jogos Processados Hoje: "
            f"{self.jogos_analisados_hoje}"
        )
