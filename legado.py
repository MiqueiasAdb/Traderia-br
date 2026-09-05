"""
Cliente legado — apifootball.com (trial até 19/09/2026)

Papel no sistema: assumir ESTATÍSTICAS e H2H para poupar a cota
diária da API-Sports. Quando o trial acabar (ou a chave falhar),
o bot degrada automaticamente para a API-Sports sozinha.

Observação: os IDs desta API são DIFERENTES dos da API-Sports,
então os jogos são casados por nome de time (similaridade).
"""
import difflib
import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

from config import (
    API2_BASE,
    API2_KEY,
    API_INTERVALO_MINIMO,
    API_TIMEOUT_CONEXAO,
    API_TIMEOUT_LEITURA,
)

logger = logging.getLogger("TraderIA")


def _normalizar(texto: str) -> str:
    import unicodedata

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

    return " ".join(sorted(
        palavra
        for palavra in valor.split()
        if palavra and palavra not in excluidas
    ))


class ScannerLegado:
    CACHE_EVENTOS = 60.0
    CACHE_STATS = 60.0
    CACHE_H2H = 1800.0
    SIMILARIDADE_MINIMA = 0.5

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })

        self.ativo = bool(API2_KEY)

        if not self.ativo:
            logger.info(
                "ℹ️ API legada inativa: API2_KEY ausente"
            )
            return

        self._cache: Dict[str, Tuple[float, object]] = {}
        self._last_req = 0.0
        self._invalida = False

        self._aviso_auth = False

    # ========================================================
    # BASE
    # ========================================================

    def _get(
        self, params: dict, cache_seg: float
    ):
        if not self.ativo or self._invalida:
            return None

        chave = str(sorted(params.items()))
        agora = time.time()

        ts, dados = self._cache.get(chave, (0.0, None))
        if dados is not None and agora - ts < cache_seg:
            return dados

        elapsed = time.time() - self._last_req
        if elapsed < API_INTERVALO_MINIMO:
            time.sleep(API_INTERVALO_MINIMO - elapsed)
        self._last_req = time.time()

        completo = {**params, "APIkey": API2_KEY}

        for tentativa in range(1, 3):
            try:
                resposta = self.session.get(
                    API2_BASE,
                    params=completo,
                    timeout=(
                        API_TIMEOUT_CONEXAO,
                        API_TIMEOUT_LEITURA,
                    ),
                )

                if resposta.status_code != 200:
                    logger.warning(
                        "⚠️ API legada HTTP %s (%s)",
                        resposta.status_code,
                        params.get("action"),
                    )
                    return None

                data = resposta.json()

                # Esta API devolve 200 + {"error": ...}
                if isinstance(data, dict) and (
                    "error" in data
                ):
                    mensagem = str(
                        data.get("message", "")
                    )

                    if "authent" in (
                        mensagem.lower()
                    ):
                        self._invalida = True

                        if not self._aviso_auth:
                            self._aviso_auth = True
                            logger.error(
                                "🔑 API legada (apifootball."
                                "com) rejeitou a chave — "
                                "desativada (usando só "
                                "API-Sports)."
                            )
                    else:
                        logger.warning(
                            "⚠️ Aviso da API legada (%s): %s",
                            params.get("action"),
                            mensagem or data.get("error"),
                        )
                    return None

                self._cache[chave] = (agora, data)
                return data

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ):
                time.sleep(1.0)
            except Exception as erro:
                logger.warning(
                    "⚠️ API legada: %s (%s)",
                    type(erro).__name__,
                    params.get("action"),
                )
                return None

        return None

    # ========================================================
    # EVENTOS (para casar IDs)
    # ========================================================

    def eventos_do_dia(self) -> List[dict]:
        from datetime import datetime, timedelta

        agora = datetime.now(
            datetime.now().astimezone().tzinfo
        )
        hoje = agora.strftime("%Y-%m-%d")
        amanha = (
            agora + timedelta(days=1)
        ).strftime("%Y-%m-%d")

        dados = self._get({
            "action": "get_events",
            "from": hoje,
            "to": amanha,
        }, self.CACHE_EVENTOS)

        if isinstance(dados, list):
            return [
                evento
                for evento in dados
                if isinstance(evento, dict)
                and evento.get("match_id")
            ]

        return []

    def achar_match_id(
        self,
        casa: str,
        fora: str,
    ) -> Optional[str]:
        """Localiza o match_id legado pelo nome dos times."""
        alvo_casa = _normalizar(casa)
        alvo_fora = _normalizar(fora)

        if not alvo_casa or not alvo_fora:
            return None

        melhor_id: Optional[str] = None
        melhor_nota = 0.0

        for evento in self.eventos_do_dia():
            nota = (
                difflib.SequenceMatcher(
                    None,
                    alvo_casa,
                    _normalizar(
                        evento.get(
                            "match_hometeam_name",
                            "",
                        )
                    ),
                ).ratio()
                + difflib.SequenceMatcher(
                    None,
                    alvo_fora,
                    _normalizar(
                        evento.get(
                            "match_awayteam_name",
                            "",
                        )
                    ),
                ).ratio()
            ) / 2.0

            if nota > melhor_nota:
                melhor_nota = nota
                melhor_id = str(
                    evento.get("match_id")
                )

        if melhor_nota < self.SIMILARIDADE_MINIMA:
            return None

        return melhor_id

    # ========================================================
    # ESTATÍSTICAS / H2H
    # ========================================================

    def buscar_estatisticas(
        self, match_id_legado: str
    ) -> Dict:
        dados = self._get({
            "action": "get_statistics",
            "match_id": match_id_legado,
        }, self.CACHE_STATS)

        return (
            dados
            if isinstance(dados, dict)
            else {}
        )

    def buscar_h2h(
        self, casa: str, fora: str
    ) -> List[dict]:
        dados = self._get({
            "action": "get_H2H",
            "firstTeam": casa,
            "secondTeam": fora,
        }, self.CACHE_H2H)

        if isinstance(dados, list):
            return [
                partida
                for partida in dados
                if isinstance(partida, dict)
            ]

        return []
