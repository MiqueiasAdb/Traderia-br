"""
O CÉREBRO DA IA — MODO GLOBAL + PLACAR EXATO
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from config import CONFIDENCE_MINIMA
import logging

logger = logging.getLogger("TraderIA")


@dataclass
class Sinal:
    jogo: str
    liga: str
    minuto: str
    placar: str
    mercado: str
    direcao: str
    odd_entrada: float
    odd_alvo: float
    odd_stop: float
    stake: float
    confianca: int
    risco: str
    tese: str
    fatores: List[str]
    match_id: str = ""
    timestamp: str = ""
    valor_esperado: float = 0.0


class Brain:
    def __init__(self):
        self.sinais_hoje = 0

    def _poisson_probability(self, lmbda: float, k: int) -> float:
        return (lmbda ** k * np.exp(-lmbda)) / np.math.factorial(k)

    def analisar(
        self,
        jogo: Dict,
        odds: List[Dict],
        h2h: List[Dict],
        stats: Dict,
        previsoes: Dict,
    ) -> Optional[Sinal]:

        match_id = str(jogo.get("match_id", ""))
        casa = jogo.get("match_hometeam_name", "???")
        fora = jogo.get("match_awayteam_name", "???")
        liga = jogo.get("league_name", "Mundo")
        minuto_raw = jogo.get("match_live", "0")
        minuto = int(minuto_raw.replace("'", "").strip() or 0) if minuto_raw else 0
        gol_casa = int(jogo.get("match_hometeam_score", 0) or 0)
        gol_fora = int(jogo.get("match_awayteam_score", 0) or 0)

        fatores = []
        score = 52

        xg_casa = 1.30
        xg_fora = 1.00

        if stats and isinstance(stats, dict):
            try:
                chutes_c = float(stats.get("home", {}).get("Shots on Goal", 4))
                chutes_f = float(stats.get("away", {}).get("Shots on Goal", 3))
                posse_c = float(stats.get("home", {}).get("Ball Possession", "50").replace("%", ""))

                xg_casa = max(0.2, 0.4 + (chutes_c * 0.14) + (posse_c / 160))
                xg_fora = max(0.2, 0.4 + (chutes_f * 0.14) + ((100 - posse_c) / 160))

                if chutes_c >= 6:
                    fatores.append(f"🎯 Pressionando ({chutes_c:.0f} chutes no alvo)")
                    score += 8
                if posse_c >= 65:
                    fatores.append(f"⚽ Domínio de posse ({posse_c:.0f}%)")
                    score += 6
            except (ValueError, TypeError, AttributeError):
                pass

        if minuto > 0:
            tempo_restante_pct = max(0.1, (90 - minuto) / 90)
            xg_casa_restante = xg_casa * tempo_restante_pct
            xg_fora_restante = xg_fora * tempo_restante_pct
        else:
            xg_casa_restante = xg_casa
            xg_fora_restante = xg_fora

        matriz_placares = {}
        for i in range(5):
            for j in range(5):
                p_c = self._poisson_probability(xg_casa_restante, i)
                p_f = self._poisson_probability(xg_fora_restante, j)
                prob = p_c * p_f
                placar_final = f"{gol_casa + i}x{gol_fora + j}"
                matriz_placares[placar_final] = prob

        placares_provaveis = sorted(matriz_placares.items(), key=lambda x: x[1], reverse=True)
        placar_topo, prob_topo = placares_provaveis[0]

        mercado = ""
        odd_mercado = 2.00
        fair_odd = 1.80
        valor_pct = 0.0

        if minuto >= 60 and prob_topo >= 0.35:
            mercado = f"🎯 Placar Exato ({placar_topo})"
            fair_odd = round(1 / prob_topo, 2)
            odd_mercado = round(fair_odd * 1.20, 2)
            valor_pct = 20.0
            score += 22
            fatores.append(f"🔒 Alta probabilidade do placar se manter ({prob_topo*100:.1f}%)")
        else:
            prob_zero_gols = matriz_placares.get(f"{gol_casa}x{gol_fora}", 0)
            if minuto >= 70 and prob_zero_gols > 0.45:
                mercado = f"Under {gol_casa + gol_fora + 0.5} Gols"
                fair_odd = round(1 / max(0.05, prob_zero_gols), 2)
                odd_mercado = round(fair_odd * 1.15, 2)
                valor_pct = 15.0
                score += 12
                fatores.append("⏱️ Ritmo de jogo em queda")
            elif xg_casa + xg_fora > 2.7:
                mercado = "Over 2.5 Gols"
                prob_over = 1 - sum(self._poisson_probability(xg_casa + xg_fora, k) for k in range(3))
                fair_odd = round(1 / max(0.05, prob_over), 2)
                odd_mercado = round(fair_odd * 1.18, 2)
                valor_pct = 18.0
                score += 10
                fatores.append(f"🔥 Expectativa de gols alta (xG {xg_casa+xg_fora:.1f})")
            elif prob_topo > 0.25:
                mercado = "Vitória Casa (1)" if xg_casa > xg_fora else "Vitória Fora (2)"
                fair_odd = 1.85
                odd_mercado = 2.10
                valor_pct = 13.5
                score += 8

        confianca = min(96, max(40, int(score)))

        if confianca < CONFIDENCE_MINIMA or not mercado:
            return None

        risco = "🟢 BAIXO" if confianca >= 82 else "🟡 MÉDIO" if confianca >= 72 else "🔴 ALTO"

        tese = (
            f"📌 Oportunidade Global em {casa} x {fora} ({liga}). "
            f"{' '.join(fatores[:2])}. "
            f"Placar exato mais provável: {placar_topo} ({prob_topo*100:.0f}% prob). "
            f"Confiança da IA: {confianca}%."
        )

        sinal = Sinal(
            jogo=f"{casa} x {fora}",
            liga=liga,
            minuto=f"{minuto}'" if minuto > 0 else "Pré-jogo",
            placar=f"{gol_casa}x{gol_fora}",
            mercado=mercado,
            direcao="BACK",
            odd_entrada=odd_mercado,
            odd_alvo=round(odd_mercado * 0.82, 2),
            odd_stop=round(odd_mercado * 1.30, 2),
            stake=0,
            confianca=confianca,
            risco=risco,
            tese=tese,
            fatores=fatores,
            match_id=match_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            valor_esperado=round(valor_pct, 1),
        )

        self.sinais_hoje += 1
        return sinal
