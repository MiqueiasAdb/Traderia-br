"""
TraderIA Brasil — Scanner global

Seleciona:
1. Partidas ao vivo;
2. Partidas que começam nos próximos 30 minutos.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from config import (
    API_BASE,
    API_KEY,
    API_TIMEZONE,
    MAX_JOGOS_POR_CICLO,
    PRELIVE_WINDOW_MINUTES,
)

logger = logging.getLogger("TraderIA")


class Scanner24h:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "TraderIA-Brasil/3.0",
        })

        self._cache = {}
        self._last_request = 0.0

        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

    # ========================================================
    # HTTP
    # ========================================================

    def _get(
        self,
        params: Dict[str, Any],
        cache_seg: int = 15,
    ) -> Any:
        if not API_KEY:
            logger.error(
                "❌ API_KEY não configurada no Environment da Render"
            )
            return []

        parametros = dict(params)
        parametros["APIkey"] = API_KEY

        cache_key = str(sorted(parametros.items()))
        agora = time.monotonic()

        if cache_key in self._cache:
            dados_cache, horario_cache = self._cache[cache_key]

            if agora - horario_cache < cache_seg:
                return dados_cache

        # Pequena proteção contra chamadas simultâneas excessivas.
        decorrido = time.monotonic() - self._last_request

        if decorrido < 0.30:
            time.sleep(0.30 - decorrido)

        self._last_request = time.monotonic()

        action = parametros.get("action", "desconhecida")

        try:
            resposta = self.session.get(
                API_BASE,
                params=parametros,
                timeout=25,
            )

            logger.info(
                "⚽ API action=%s HTTP=%s",
                action,
                resposta.status_code,
            )

            resposta.raise_for_status()
            dados = resposta.json()

            if isinstance(dados, dict):
                erro = (
                    dados.get("error")
                    or dados.get("message")
                    if dados.get("error")
                    else None
                )

                if erro:
                    logger.error(
                        "❌ API recusou action=%s: %s",
                        action,
                        erro,
                    )
                    return []

            self._cache[cache_key] = (
                dados,
                time.monotonic(),
            )

            logger.info(
                "✅ action=%s tipo=%s itens=%s",
                action,
                type(dados).__name__,
                len(dados) if isinstance(dados, list) else "N/A",
            )

            return dados

        except requests.RequestException as erro:
            logger.error(
                "❌ Erro HTTP action=%s: %s",
                action,
                erro,
            )
            return []

        except ValueError:
            logger.error(
                "❌ action=%s retornou conteúdo que não é JSON",
                action,
            )
            return []

        except Exception:
            logger.exception(
                "❌ Erro inesperado action=%s",
                action,
            )
            return []

    # ========================================================
    # HORÁRIO E STATUS
    # ========================================================

    @staticmethod
    def _texto(valor: Any) -> str:
        if valor is None:
            return ""

        return str(valor).strip()

    def _partida_ao_vivo(self, jogo: Dict[str, Any]) -> bool:
        match_live = self._texto(
            jogo.get("match_live")
        ).lower()

        status = self._texto(
            jogo.get("match_status")
        ).lower()

        if match_live in {"1", "true", "yes"}:
            return True

        status_ao_vivo = {
            "1st half",
            "2nd half",
            "half time",
            "halftime",
            "extra time",
            "in progress",
            "penalty",
            "penalties",
            "intervalo",
            "ao vivo",
        }

        if status in status_ao_vivo:
            return True

        # Exemplos: 34', 45+2', 90+5'
        if "'" in status and any(
            caractere.isdigit() for caractere in status
        ):
            return True

        return False

    def _partida_encerrada(self, jogo: Dict[str, Any]) -> bool:
        status = self._texto(
            jogo.get("match_status")
        ).lower()

        status_encerrado = {
            "finished",
            "after penalties",
            "after extra time",
            "cancelled",
            "canceled",
            "postponed",
            "abandoned",
            "awarded",
            "finalizado",
            "encerrado",
        }

        return status in status_encerrado

    def _horario_inicio(
        self,
        jogo: Dict[str, Any],
    ) -> Optional[datetime]:
        data = self._texto(jogo.get("match_date"))
        horario = self._texto(jogo.get("match_time"))

        if not data or not horario:
            return None

        fuso = ZoneInfo(API_TIMEZONE)
        texto = f"{data} {horario}"

        formatos = (
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        )

        for formato in formatos:
            try:
                inicio = datetime.strptime(texto, formato)
                return inicio.replace(tzinfo=fuso)
            except ValueError:
                continue

        return None

    # ========================================================
    # VARREDURA PRINCIPAL
    # ========================================================

    def varrer_jogos_ao_vivo(self) -> List[Dict[str, Any]]:
        """
        Retorna:
        - todos os jogos ao vivo encontrados;
        - pré-jogos começando em até 30 minutos.

        Jogos ao vivo são colocados primeiro.
        """
        fuso = ZoneInfo(API_TIMEZONE)
        agora = datetime.now(fuso)

        hoje = agora.strftime("%Y-%m-%d")
        amanha = (
            agora + timedelta(days=1)
        ).strftime("%Y-%m-%d")

        logger.info(
            "🌍 Buscando jogos ao vivo e pré-jogos em até %s minutos",
            PRELIVE_WINDOW_MINUTES,
        )

        dados = self._get(
            {
                "action": "get_events",
                "from": hoje,
                "to": amanha,
                "timezone": API_TIMEZONE,
            },
            cache_seg=10,
        )

        if not isinstance(dados, list):
            logger.warning(
                "⚠️ get_events retornou %s em vez de lista",
                type(dados).__name__,
            )
            return []

        selecionados = {}

        for jogo in dados:
            if not isinstance(jogo, dict):
                continue

            match_id = self._texto(jogo.get("match_id"))

            if not match_id:
                continue

            if self._partida_encerrada(jogo):
                continue

            if self._partida_ao_vivo(jogo):
                jogo["_modo_analise"] = "AO_VIVO"
                jogo["_minutos_para_inicio"] = 0.0
                selecionados[match_id] = jogo
                continue

            inicio = self._horario_inicio(jogo)

            if inicio is None:
                continue

            minutos_para_inicio = (
                inicio - agora
            ).total_seconds() / 60

            if 0 <= minutos_para_inicio <= PRELIVE_WINDOW_MINUTES:
                jogo["_modo_analise"] = "PRE_JOGO"
                jogo["_minutos_para_inicio"] = round(
                    minutos_para_inicio,
                    1,
                )
                jogo["_inicio_datetime"] = inicio.isoformat()
                selecionados[match_id] = jogo

        jogos = list(selecionados.values())

        # Ao vivo primeiro. Depois, pré-jogos mais próximos.
        jogos.sort(
            key=lambda item: (
                0
                if item.get("_modo_analise") == "AO_VIVO"
                else 1,
                float(
                    item.get("_minutos_para_inicio", 999)
                ),
            )
        )

        if len(jogos) > MAX_JOGOS_POR_CICLO:
            logger.warning(
                "⚠️ Limitando análise de %s para %s jogos neste ciclo",
                len(jogos),
                MAX_JOGOS_POR_CICLO,
            )

            jogos = jogos[:MAX_JOGOS_POR_CICLO]

        ao_vivo = sum(
            1
            for jogo in jogos
            if jogo.get("_modo_analise") == "AO_VIVO"
        )

        pre_jogo = sum(
            1
            for jogo in jogos
            if jogo.get("_modo_analise") == "PRE_JOGO"
        )

        logger.info(
            "⚡ Selecionados: %s ao vivo | %s pré-jogo",
            ao_vivo,
            pre_jogo,
        )

        for jogo in jogos:
            liga = jogo.get("league_name", "Mundo")
            self.ligas_encontradas.add(liga)

        self.jogos_analisados_hoje += len(jogos)
        return jogos

    # ========================================================
    # DADOS COMPLEMENTARES
    # ========================================================

    def buscar_odds(self, match_id: str) -> List[Dict[str, Any]]:
        dados = self._get(
            {
                "action": "get_odds",
                "match_id": match_id,
            },
            cache_seg=12,
        )

        return dados if isinstance(dados, list) else []

    def buscar_estatisticas(self, match_id: str) -> Any:
        return self._get(
            {
                "action": "get_statistics",
                "match_id": match_id,
            },
            cache_seg=15,
        )

    def buscar_confronto_direto(
        self,
        time1: str,
        time2: str,
    ) -> List[Dict[str, Any]]:
        dados = self._get(
            {
                "action": "get_H2H",
                "firstTeam": time1,
                "secondTeam": time2,
            },
            cache_seg=3600,
        )

        return dados if isinstance(dados, list) else []

    def buscar_previsoes(self, match_id: str) -> Any:
        return self._get(
            {
                "action": "get_predictions",
                "match_id": match_id,
            },
            cache_seg=900,
        )

    def status(self) -> str:
        return (
            f"Jogos processados: {self.jogos_analisados_hoje} | "
            f"Ligas encontradas: {len(self.ligas_encontradas)}"
        )
