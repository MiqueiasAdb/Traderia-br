"""
PASSO 1: A IA VARRE O MUNDO 24/7
"""
import requests
import time
import logging
from datetime import datetime
from typing import List, Dict
from config import API_KEY, API_BASE

logger = logging.getLogger("TraderIA")


class Scanner24h:
    def __init__(self):
        self.session = requests.Session()
        self._cache = {}
        self._last_req = 0
        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

    def _get(self, params: dict, cache_seg: int = 30) -> any:
        elapsed = time.time() - self._last_req
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        self._last_req = time.time()

        params["APIkey"] = API_KEY
        key = str(sorted(params.items()))
        now = time.time()

        if key in self._cache:
            data, ts = self._cache[key]
            if now - ts < cache_seg:
                return data

        try:
            r = self.session.get(API_BASE, params=params, timeout=12)
            data = r.json()
            self._cache[key] = (data, now)
            return data
        except Exception as e:
            logger.error(f"Erro API: {e}")
            return []

    def varrer_jogos_ao_vivo(self) -> List[Dict]:
        hoje = datetime.now().strftime("%Y-%m-%d")

        dados = self._get({
            "action": "get_events",
            "from": hoje,
            "to": hoje,
            "match_live": "1"
        }, cache_seg=15)

        jogos = dados if isinstance(dados, list) else []

        if len(jogos) < 3:
            dados_dia = self._get({
                "action": "get_events",
                "from": hoje,
                "to": hoje,
            }, cache_seg=60)
            if isinstance(dados_dia, list):
                jogos.extend(dados_dia[:30])

        for j in jogos:
            liga = j.get("league_name", "Mundo")
            self.ligas_encontradas.add(liga)

        self.jogos_analisados_hoje += len(jogos)
        return jogos

    def buscar_odds(self, match_id: str) -> List[Dict]:
        dados = self._get({"action": "get_odds", "match_id": match_id}, cache_seg=15)
        return dados if isinstance(dados, list) else []

    def buscar_confronto_direto(self, time1: str, time2: str) -> List[Dict]:
        dados = self._get({"action": "get_H2H", "firstTeam": time1, "secondTeam": time2}, cache_seg=3600)
        return dados if isinstance(dados, list) else []

    def buscar_estatisticas(self, match_id: str) -> Dict:
        dados = self._get({"action": "get_statistics", "match_id": match_id}, cache_seg=20)
        return dados if isinstance(dados, dict) else {}

    def buscar_previsoes(self, match_id: str) -> Dict:
        dados = self._get({"action": "get_predictions", "match_id": match_id}, cache_seg=300)
        return dados if isinstance(dados, dict) else {}

    def status(self) -> str:
        return f"🌍 Varredura Mundial | Jogos: {self.jogos_analisados_hoje} | Ligas: {len(self.ligas_encontradas)}"
