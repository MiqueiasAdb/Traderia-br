"""
TraderIA Brasil — Scanner global resiliente

Busca separadamente:

1. Todas as partidas ao vivo;
2. Partidas pré-jogo que começam nos próximos 30 minutos.

Também possui:
- teste da credencial;
- retentativas automáticas;
- cache;
- proteção contra desconexão;
- tratamento do erro interno 404 da API;
- limitação de chamadas.
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

        self._cache: Dict[str, tuple] = {}
        self._ultima_requisicao = 0.0

        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

    # ========================================================
    # SESSÃO HTTP
    # ========================================================

    @staticmethod
    def _criar_sessao() -> requests.Session:
        sessao = requests.Session()

        sessao.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "TraderIA-Brasil/3.2",
                # Evita reutilizar uma conexão que a API
                # já tenha encerrado.
                "Connection": "close",
            }
        )

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
        chave: str,
        validade_segundos: int,
    ) -> Optional[Any]:
        item = self._cache.get(chave)

        if item is None:
            return None

        dados, horario = item
        idade = time.monotonic() - horario

        if idade <= validade_segundos:
            return dados

        return None

    def _buscar_cache_emergencia(
        self,
        chave: str,
        idade_maxima: int = 180,
    ) -> Optional[Any]:
        """
        Se a API ficar temporariamente indisponível, usa a última
        resposta válida durante no máximo alguns minutos.
        """
        item = self._cache.get(chave)

        if item is None:
            return None

        dados, horario = item
        idade = time.monotonic() - horario

        if idade <= idade_maxima:
            logger.warning(
                "🛟 Utilizando cache de emergência com %.0f segundos",
                idade,
            )
            return dados

        return None

    # ========================================================
    # TRATAMENTO DA RESPOSTA
    # ========================================================

    @staticmethod
    def _resposta_segura(dados: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove campos que possam conter credenciais antes de
        colocar a resposta da API nos logs.
        """
        proibidos = {
            "apikey",
            "api_key",
            "key",
            "token",
            "secret",
        }

        return {
            chave: valor
            for chave, valor in dados.items()
            if str(chave).lower() not in proibidos
        }

    def _tratar_erro_api(
        self,
        action: str,
        dados: Dict[str, Any],
    ) -> bool:
        """
        Retorna True quando o JSON contém erro da API.
        """
        if not isinstance(dados, dict):
            return False

        erro = dados.get("error")

        if not erro:
            return False

        codigo = str(erro).strip()

        mensagem = (
            dados.get("message")
            or dados.get("description")
            or dados.get("result")
            or "Mensagem não informada"
        )

        resposta_segura = self._resposta_segura(dados)

        if codigo == "404":
            logger.warning(
                "⚠️ API retornou erro interno 404 | "
                "action=%s | mensagem=%s 
