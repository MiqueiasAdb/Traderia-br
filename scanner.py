"""
TraderIA Brasil — Scanner global resiliente

Seleciona:
1. Jogos ao vivo;
2. Pré-jogos começando nos próximos 30 minutos.

Inclui:
- retentativas automáticas;
- backoff progressivo;
- cache;
- proteção contra desconexão da API;
- limitação de requisições.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from config import (
    API_BASE,
    API_INTERVALO_MINIMO,
    API_KEY,
    API_MAX_TENTATIVAS,
    API_TIMEOUT_CONEXAO,
    API_TIMEOUT_LEITURA,
    API_TIMEZONE,
    MAX_JOGOS_POR_CICLO,
    PRELIVE_WINDOW_MINUTES,
)

logger = logging.getLogger("TraderIA")


class Scanner24h:
    def __init__(self):
        self.session = self._criar_sessao()
        self._cache = {}
        self._last_request = 0.0

        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

    # ========================================================
    # SESSÃO HTTP
    # ========================================================

    @staticmethod
    def _criar_sessao() -> requests.Session:
        sessao = requests.Session()

        sessao.headers.update({
            "Accept": "application/json",
            "User-Agent": "TraderIA-Brasil/3.1",
            # Evita reutilizar uma conexão que o servidor
            # remoto já tenha encerrado.
            "Connection": "close",
        })

        return sessao

    def _reiniciar_sessao(self):
        try:
            self.session.close()
        except Exception:
            pass

        self.session = self._criar_sessao()

    # ========================================================
    # CACHE
    # ========================================================

    def _buscar_cache(
        self,
        cache_key: str,
        validade: int,
    ) -> Optional[Any]:
        item = self._cache.get(cache_key)

        if not item:
            return None

        dados, horario = item
        idade = time.monotonic() - horario

        if idade <= validade:
            return dados

        return None

    def _buscar_cache_emergencia(
        self,
        cache_key: str,
        idade_maxima: int = 180,
    ) -> Optional[Any]:
        """
        Se a API falhar, permite utilizar por alguns minutos
        a última resposta recebida.
        """
        item = self._cache.get(cache_key)

        if not item:
            return None

        dados, horario = item
        idade = time.monotonic() - horario

        if idade <= idade_maxima:
            logger.warning(
                "🛟 Utilizando cache de emergência com %.0fs",
                idade,
            )
            return dados

        return None

    # ========================================================
    # REQUISIÇÕES COM RETENTATIVA
    # ========================================================

    def _get(
        self,
        params: Dict[str, Any],
        cache_seg: int = 20,
    ) -> Any:
        if not API_KEY:
            logger.error(
                "❌ API_KEY ausente no Environment da Render"
            )
            return []

        parametros = dict(params)
        parametros["APIkey"] = API_KEY

        action = str(
            parametros.get("action", "desconhecida")
        )

        cache_key = str(sorted(parametros.items()))

        dados_cache = self._buscar_cache(
            cache_key,
            cache_seg,
        )

        if dados_cache is not None:
            logger.info(
                "♻️ Cache utilizado para action=%s",
                action,
            )
            return dados_cache

        for tentativa in range(1, API_MAX_TENTATIVAS + 1):
            # Proteção básica contra excesso de requisições.
            decorrido = (
                time.monotonic() - self._last_request
            )

            if decorrido < API_INTERVALO_MINIMO:
                time.sleep(
                    API_INTERVALO_MINIMO - decorrido
                )

            self._last_request = time.monotonic()

            try:
                logger.info(
                    "📡 API action=%s | tentativa=%s/%s",
                    action,
                    tentativa,
                    API_MAX_TENTATIVAS,
                )

                resposta = self.session.get(
                    API_BASE,
                    params=parametros,
                    timeout=(
                        API_TIMEOUT_CONEXAO,
                        API_TIMEOUT_LEITURA,
                    ),
                )

                logger.info(
                    "⚽ API action=%s HTTP=%s",
                    action,
                    resposta.status_code,
                )

                # Rate limit.
                if resposta.status_code == 429:
                    espera = tentativa * 5

                    logger.warning(
                        "⚠️ API limitou as requisições. "
                        "Nova tentativa em %ss",
                        espera,
                    )

                    time.sleep(espera)
                    continue

                # Erros temporários do servidor.
                if resposta.status_code in {
                    500,
                    502,
                    503,
                    504,
                }:
                    espera = tentativa * 3

                    logger.warning(
                        "⚠️ API indisponível HTTP=%s. "
                        "Nova tentativa em %ss",
                        resposta.status_code,
                        espera,
                    )

                    time.sleep(espera)
                    continue

                resposta.raise_for_status()
                dados = resposta.json()

                if isinstance(dados, dict) and dados.get("error"):
                    logger.error(
                        "❌ API recusou action=%s: %s",
                        action,
                        dados.get("error"),
                    )
                    return []

                self._cache[cache_key] = (
                    dados,
                    time.monotonic(),
                )

                logger.info(
                    "✅ action=%s | tipo=%s | itens=%s",
                    action,
                    type(dados).__name__,
                    (
                        len(dados)
                        if isinstance(dados, list)
                        else "N/A"
                    ),
                )

                return dados

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout,
            ) as erro:
                espera = tentativa * 3

                logger.warning(
                    "⚠️ Conexão interrompida action=%s | "
                    "tentativa=%s/%s | erro=%s",
                    action,
                    tentativa,
                    API_MAX_TENTATIVAS,
                    erro,
                )

                self._reiniciar_sessao()

                if tentativa < API_MAX_TENTATIVAS:
                    logger.info(
                        "🔁 Nova tentativa em %ss",
                        espera,
                    )
                    time.sleep(espera)

            except requests.RequestException as erro:
                logger.error(
                    "❌ Erro HTTP action=%s: %s",
                    action,
                    erro,
                )
                break

            except ValueError:
                logger.error(
                    "❌ action=%s retornou conteúdo não JSON",
                    action,
                )
                break

            except Exception:
                logger.exception(
                    "❌ Erro inesperado action=%s",
                    action,
                )
                break

        logger.error(
            "❌ Todas as tentativas falharam para action=%s",
            action,
        )

        cache_emergencia = self._buscar_cache_emergencia(
            cache_key,
            idade_maxima=180,
        )

        if cache_emergencia is not None:
            return cache_emergencia

        return []

    # ========================================================
    # UTILITÁRIOS
    # ========================================================

    @staticmethod
    def _texto(valor: Any) -> str:
        if valor is None:
            return ""

        return str(valor).strip()

    def _partida_ao_vivo(
        self,
        jogo: Dict[str, Any],
    ) -> bool:
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

        if "'" in status and any(
            caractere.isdigit()
            for caractere in status
        ):
            return True

        return False

    def _partida_encerrada(
        self,
        jogo: Dict[str, Any],
    ) -> bool:
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
        data = self._texto(
            jogo.get("match_date")
        )

        horario = self._texto(
            jogo.get("match_time")
        )

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
                inicio = datetime.strptime(
                    texto,
                    formato,
                )

                return inicio.replace(tzinfo=fuso)

            except ValueError:
                continue

        return None

    # ========================================================
    # BUSCA DE EVENTOS
    # ========================================================

    def _buscar_eventos_data(
        self,
        data: str,
    ) -> List[Dict[str, Any]]:
        dados = self._get(
            {
                "action": "get_events",
                "from": data,
                "to": data,
                "timezone": API_TIMEZONE,
            },
            cache_seg=25,
        )

        if not isinstance(dados, list):
            logger.warning(
                "⚠️ get_events retornou %s",
                type(dados).__name__,
            )
            return []

        return dados

    def varrer_jogos_ao_vivo(
        self,
    ) -> List[Dict[str, Any]]:
        """
        Busca os eventos do dia atual.

        Busca o dia seguinte apenas perto da meia-noite,
        evitando uma resposta mundial de dois dias completos.
        """
        fuso = ZoneInfo(API_TIMEZONE)
        agora = datetime.now(fuso)

        hoje = agora.strftime("%Y-%m-%d")

        logger.info(
            "🌍 Buscando jogos ao vivo e pré-jogos "
            "em até %s minutos",
            PRELIVE_WINDOW_MINUTES,
        )

        dados = self._buscar_eventos_data(hoje)

        # Se a janela de 30 minutos atravessar a meia-noite,
        # consulta também o dia seguinte.
        limite = agora + timedelta(
            minutes=PRELIVE_WINDOW_MINUTES
        )

        if limite.date() != agora.date():
            amanha = limite.strftime("%Y-%m-%d")
            dados_amanha = self._buscar_eventos_data(
                amanha
            )
            dados.extend(dados_amanha)

        selecionados = {}

        for jogo_original in dados:
            if not isinstance(jogo_original, dict):
                continue

            # Não altera diretamente os dados guardados no cache.
            jogo = dict(jogo_original)

            match_id = self._texto(
                jogo.get("match_id")
            )

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

            if (
                0
                <= minutos_para_inicio
                <= PRELIVE_WINDOW_MINUTES
            ):
                jogo["_modo_analise"] = "PRE_JOGO"

                jogo["_minutos_para_inicio"] = round(
                    minutos_para_inicio,
                    1,
                )

                jogo["_inicio_datetime"] = (
                    inicio.isoformat()
                )

                selecionados[match_id] = jogo

        jogos = list(selecionados.values())

        # Ao vivo primeiro; pré-jogos mais próximos depois.
        jogos.sort(
            key=lambda item: (
                (
                    0
                    if item.get("_modo_analise")
                    == "AO_VIVO"
                    else 1
                ),
                float(
                    item.get(
                        "_minutos_para_inicio",
                        999,
                    )
                ),
            )
        )

        if len(jogos) > MAX_JOGOS_POR_CICLO:
            logger.warning(
                "⚠️ %s jogos elegíveis. "
                "Analisando os %s prioritários",
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

    def buscar_odds(
        self,
        match_id: str,
    ) -> List[Dict[str, Any]]:
        dados = self._get(
            {
                "action": "get_odds",
                "match_id": match_id,
            },
            cache_seg=30,
        )

        return dados if isinstance(dados, list) else []

    def buscar_estatisticas(
        self,
        match_id: str,
    ) -> Any:
        return self._get(
            {
                "action": "get_statistics",
                "match_id": match_id,
            },
            cache_seg=30,
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
            cache_seg=21600,  # 6 horas
        )

        return dados if isinstance(dados, list) else []

    def buscar_previsoes(
        self,
        match_id: str,
    ) -> Any:
        return self._get(
            {
                "action": "get_predictions",
                "match_id": match_id,
            },
            cache_seg=1800,
        )

    def status(self) -> str:
        return (
            f"Jogos processados: "
            f"{self.jogos_analisados_hoje} | "
            f"Ligas: {len(self.ligas_encontradas)}"
        )
