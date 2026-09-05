"""
Scanner 24h — Versão Blindada (Headers Reais + Anti-Bloqueio + Re-tentativas)
"""
import requests
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from config import API_KEY, API_BASE

logger = logging.getLogger("TraderIA")


class Scanner24h:
    def __init__(self):
        self.session = requests.Session()
        # Headers para simular navegador real e evitar bloqueio Cloudflare/404 na Render
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self._cache = {}
        self._last_req = 0
        self.jogos_analisados_hoje = 0
        self.ligas_encontradas = set()

        # Ligas de alta liquidez e volatilidade
        self.ligas_prioritarias = [
            "Brasileirão Série A", "Brasileirão Série B", "Copa do Brasil",
            "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
            "Champions League", "Europa League", "Libertadores", "Sul-Americana",
            "Eredivisie", "Primeira Liga", "MLS", "J-League", "Paulista", "Carioca"
        ]

    def _get(self, params: dict, cache_seg: int = 15) -> any:
        """Requisição segura com tratamento de erros HTTP e Re-tentativas"""
        # Controle de taxa (Rate Limit)
        elapsed = time.time() - self._last_req
        if elapsed < 0.3:
            time.sleep(0.3 - elapsed)
        self._last_req = time.time()

        params["APIkey"] = API_KEY
        key = str(sorted(params.items()))
        now = time.time()

        # Checa Cache
        if key in self._cache:
            data, ts = self._cache[key]
            if now - ts < cache_seg:
                return data

        # Re-tentativas em caso de oscilação na API
        for tentativa in range(1, 4):
            try:
                response = self.session.get(API_BASE, params=params, timeout=12)
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        # Trata caso a API retorne mensagem de erro no JSON
                        if isinstance(data, dict) and "error" in data:
                            logger.warning(f"⚠️ Aviso da API: {data.get('error')}")
                            return []
                        
                        self._cache[key] = (data, now)
                        return data
                    except ValueError:
                        logger.error("❌ Resposta da API não é um JSON válido")
                        return []
                elif response.status_code == 404:
                    # Se der 404 com parâmetro live, tenta sem ele
                    logger.debug(f"ℹ️ API retornou 404 para {params.get('action')}")
                    return []
                else:
                    logger.warning(f"⚠️ Status HTTP {response.status_code} na tentativa {tentativa}/3")

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                logger.warning(f"🔄 Oscilação de conexão ({tentativa}/3). Aguardando 1s...")
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"❌ Erro inesperado na API: {e}")
                break

        return []

    def varrer_jogos_ao_vivo(self) -> List[Dict]:
        """Varre jogos AO VIVO + pré-jogo nos últimos 30 minutos com fuso correto"""
        # Usa data atual UTC e Brasil
        hoje_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        agora_utc = datetime.now(timezone.utc)

        jogos_finais = []

        # 1. BUSCA JOGOS AO VIVO
        dados_live = self._get({
            "action": "get_events",
            "from": hoje_utc,
            "to": hoje_utc,
            "match_live": "1"
        }, cache_seg=10)

        jogos_live = dados_live if isinstance(dados_live, list) else []
        for j in jogos_live:
            j["_status_live"] = True
            jogos_finais.append(j)

        # 2. BUSCA JOGOS DO DIA (Para filtrar Pré-Jogo nos últimos 30min)
        dados_dia = self._get({
            "action": "get_events",
            "from": hoje_utc,
            "to": hoje_utc,
        }, cache_seg=45)

        if isinstance(dados_dia, list):
            for jogo in dados_dia:
                try:
                    # Ignora se já está ao vivo
                    if jogo.get("match_live") == "1":
                        continue

                    # Processa horário pré-jogo
                    horario_str = jogo.get("match_time", "")
                    data_str = jogo.get("match_date", hoje_utc)

                    if horario_str and data_str:
                        # Monta datetime do jogo
                        dt_jogo_str = f"{data_str} {horario_str}"
                        dt_jogo = datetime.strptime(dt_jogo_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                        
                        # Diferença em minutos
                        diff_minutos = (dt_jogo - agora_utc).total_seconds() / 60.0

                        # Se está a 30 min ou menos do início (e ainda não começou)
                        if -5 <= diff_minutos <= 35:
                            jogo["_status_live"] = False
                            jogo["_minutos_para_inicio"] = round(diff_minutos)
                            jogos_finais.append(jogo)
                except Exception:
                    continue

        # 3. FILTRAGEM E ORGANIZAÇÃO
        jogos_processados = []
        for jogo in jogos_finais:
            liga = jogo.get("league_name", "")
            
            # Se tiver ligas prioritárias definidas, prioriza, senão aceita todas
            eh_prioritaria = any(lp.lower() in liga.lower() for lp in self.ligas_prioritarias)
            
            if eh_prioritaria or len(jogos_finais) < 10:
                jogos_processados.append(jogo)

        self.jogos_analisados_hoje += len(jogos_processados)
        logger.info(
            f"⚡ Varredura Concluída: {len(jogos_processados)} jogos selecionados "
            f"({len(jogos_live)} AO VIVO | {len(jogos_processados) - len(jogos_live)} Pré-Jogo)"
        )

        return jogos_processados

    def buscar_odds(self, match_id: str) -> List[Dict]:
        dados = self._get({"action": "get_odds", "match_id": match_id}, cache_seg=10)
        return dados if isinstance(dados, list) else []

    def buscar_confronto_direto(self, time1: str, time2: str) -> List[Dict]:
        dados = self._get({"action": "get_H2H", "firstTeam": time1, "secondTeam": time2}, cache_seg=1800)
        return dados if isinstance(dados, list) else []

    def buscar_estatisticas(self, match_id: str) -> Dict:
        dados = self._get({"action": "get_statistics", "match_id": match_id}, cache_seg=15)
        return dados if isinstance(dados, dict) else {}

    def buscar_previsoes(self, match_id: str) -> Dict:
        dados = self._get({"action": "get_predictions", "match_id": match_id}, cache_seg=300)
        return dados if isinstance(dados, dict) else {}

    def status(self) -> str:
        return f"🌍 Scanner Ativo | Jogos Processados Hoje: {self.jogos_analisados_hoje}"
