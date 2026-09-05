"""
TraderIA Brasil — Motor estatístico

Regras importantes:
- Não fabrica odds;
- Só envia sinal com odd encontrada na API;
- Exibe probabilidade estimada, não garantia;
- Calcula valor esperado pela fórmula:
  EV = probabilidade_modelo * odd_real - 1.
"""

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import (
    EV_MINIMO,
    MINUTO_MINIMO_PLACAR_EXATO,
    ODD_MAXIMA,
    ODD_MINIMA,
    PROBABILIDADE_MINIMA,
    PROB_MINIMA_PLACAR_EXATO,
)

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

    # Probabilidade estimada pelo modelo.
    probabilidade_modelo: float = 0.0

    # Quantidade/qualidade de dados disponíveis.
    qualidade_dados: int = 0

    # AO_VIVO ou PRE_JOGO.
    modo_analise: str = ""


class Brain:
    def __init__(self):
        self.sinais_hoje = 0

    # ========================================================
    # UTILITÁRIOS
    # ========================================================

    @staticmethod
    def _numero(valor: Any, padrao: float = 0.0) -> float:
        if valor is None:
            return padrao

        texto = str(valor).strip()
        texto = texto.replace("%", "")
        texto = texto.replace(",", ".")

        resultado = re.search(r"-?\d+(?:\.\d+)?", texto)

        if not resultado:
            return padrao

        try:
            return float(resultado.group())
        except ValueError:
            return padrao

    @staticmethod
    def _inteiro(valor: Any, padrao: int = 0) -> int:
        try:
            return int(float(str(valor).strip()))
        except (ValueError, TypeError):
            return padrao

    @staticmethod
    def _normalizar(texto: Any) -> str:
        valor = unicodedata.normalize(
            "NFKD",
            str(texto or ""),
        )

        valor = "".join(
            caractere
            for caractere in valor
            if not unicodedata.combining(caractere)
        )

        return valor.lower().strip()

    @staticmethod
    def _poisson(lamb: float, gols: int) -> float:
        if lamb < 0 or gols < 0:
            return 0.0

        return (
            (lamb ** gols)
            * math.exp(-lamb)
            / math.factorial(gols)
        )

    @staticmethod
    def _minuto_jogo(jogo: Dict[str, Any]) -> int:
        if jogo.get("_modo_analise") != "AO_VIVO":
            return 0

        candidatos = [
            jogo.get("match_status"),
            jogo.get("match_live"),
        ]

        for valor in candidatos:
            texto = str(valor or "")
            numeros = re.findall(r"\d+", texto)

            if numeros:
                numero = int(numeros[0])

                # match_live=1 geralmente significa apenas "ao vivo",
                # não minuto 1.
                if texto.strip() in {"0", "1"}:
                    continue

                return min(numero, 120)

        return 0

    @staticmethod
    def _iterar_dicionarios(dados: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(dados, dict):
            yield dados

            for valor in dados.values():
                yield from Brain._iterar_dicionarios(valor)

        elif isinstance(dados, list):
            for item in dados:
                yield from Brain._iterar_dicionarios(item)

    # ========================================================
    # ESTATÍSTICAS
    # ========================================================

    def _encontrar_estatistica(
        self,
        dados: Any,
        nomes: Tuple[str, ...],
    ) -> Tuple[Optional[float], Optional[float]]:
        nomes_normalizados = tuple(
            self._normalizar(nome)
            for nome in nomes
        )

        for item in self._iterar_dicionarios(dados):
            nome = (
                item.get("type")
                or item.get("name")
                or item.get("statistic_name")
                or item.get("statistics_type")
            )

            nome_normalizado = self._normalizar(nome)

            if not nome_normalizado:
                continue

            if not any(
                alvo in nome_normalizado
                for alvo in nomes_normalizados
            ):
                continue

            casa = (
                item.get("home")
                or item.get("home_value")
                or item.get("hometeam")
            )

            fora = (
                item.get("away")
                or item.get("away_value")
                or item.get("awayteam")
            )

            if casa is not None or fora is not None:
                return (
                    self._numero(casa, 0.0),
                    self._numero(fora, 0.0),
                )

        # Formato simples: {"home": {"Shots on Goal": 5}}
        if isinstance(dados, dict):
            home = dados.get("home", {})
            away = dados.get("away", {})

            if isinstance(home, dict) and isinstance(away, dict):
                for chave in home:
                    normalizada = self._normalizar(chave)

                    if any(
                        alvo in normalizada
                        for alvo in nomes_normalizados
                    ):
                        return (
                            self._numero(home.get(chave), 0.0),
                            self._numero(away.get(chave), 0.0),
                        )

        return None, None

    def _calcular_xg(
        self,
        jogo: Dict[str, Any],
        stats: Any,
        h2h: List[Dict[str, Any]],
    ) -> Tuple[float, float, List[str], int]:
        fatores = []
        qualidade = 25

        # Base conservadora quando não existem dados suficientes.
        xg_casa = 1.30
        xg_fora = 1.05

        chutes_casa, chutes_fora = self._encontrar_estatistica(
            stats,
            (
                "shots on goal",
                "shots on target",
                "chutes no gol",
                "chutes no alvo",
            ),
        )

        posse_casa, posse_fora = self._encontrar_estatistica(
            stats,
            (
                "ball possession",
                "possession",
                "posse de bola",
            ),
        )

        ataques_casa, ataques_fora = self._encontrar_estatistica(
            stats,
            (
                "dangerous attacks",
                "ataques perigosos",
            ),
        )

        if chutes_casa is not None and chutes_fora is not None:
            qualidade += 30

            xg_casa = 0.35 + chutes_casa * 0.18
            xg_fora = 0.30 + chutes_fora * 0.18

            fatores.append(
                f"Chutes no alvo: {chutes_casa:.0f} x "
                f"{chutes_fora:.0f}"
            )

        if posse_casa is not None and posse_fora is not None:
            qualidade += 10

            ajuste_casa = (posse_casa - 50) / 100
            ajuste_fora = (posse_fora - 50) / 100

            xg_casa += ajuste_casa * 0.35
            xg_fora += ajuste_fora * 0.35

            fatores.append(
                f"Posse: {posse_casa:.0f}% x {posse_fora:.0f}%"
            )

        if ataques_casa is not None and ataques_fora is not None:
            qualidade += 15

            xg_casa += min(0.50, ataques_casa / 150)
            xg_fora += min(0.50, ataques_fora / 150)

            fatores.append(
                f"Ataques perigosos: {ataques_casa:.0f} x "
                f"{ataques_fora:.0f}"
            )

        if h2h:
            gols = []
            jogos_validos = 0

            for partida in h2h[:8]:
                if not isinstance(partida, dict):
                    continue

                casa = self._inteiro(
                    partida.get("match_hometeam_score"),
                    -1,
                )
                fora = self._inteiro(
                    partida.get("match_awayteam_score"),
                    -1,
                )

                if casa >= 0 and fora >= 0:
                    gols.append(casa + fora)
                    jogos_validos += 1

            if jogos_validos >= 3:
                media = sum(gols) / jogos_validos
                qualidade += 10

                fator = max(0.80, min(1.20, media / 2.50))
                xg_casa *= fator
                xg_fora *= fator

                fatores.append(
                    f"H2H: média de {media:.2f} gols"
                )

        xg_casa = max(0.15, min(3.50, xg_casa))
        xg_fora = max(0.15, min(3.50, xg_fora))

        qualidade = min(100, qualidade)

        fatores.append(
            f"xG estimado: {xg_casa:.2f} x {xg_fora:.2f}"
        )

        return (
            xg_casa,
            xg_fora,
            fatores,
            qualidade,
        )

    # ========================================================
    # ODDS REAIS
    # ========================================================

    def _extrair_odds(
        self,
        odds: Any,
    ) -> Dict[str, float]:
        """
        Tenta interpretar diferentes formatos retornados pela API.

        Retorna a maior odd válida encontrada para cada mercado.
        """
        encontradas: Dict[str, float] = {}

        def salvar(mercado: str, valor: Any):
            odd = self._numero(valor, 0.0)

            if odd < ODD_MINIMA or odd > ODD_MAXIMA:
                return

            atual = encontradas.get(mercado, 0.0)

            if odd > atual:
                encontradas[mercado] = odd

        for item in self._iterar_dicionarios(odds):
            for chave, valor in item.items():
                chave_n = self._normalizar(chave)
                valor_n = self._normalizar(valor)

                # Formato odd_type / odd_value.
                if chave_n in {
                    "odd_type",
                    "market",
                    "market_name",
                    "bet_name",
                    "name",
                }:
                    tipo = valor_n
                    odd_valor = (
                        item.get("odd_value")
                        or item.get("value")
                        or item.get("odd")
                        or item.get("price")
                    )

                    if tipo in {"1", "home"} or "home win" in tipo:
                        salvar("CASA", odd_valor)

                    elif tipo in {"x", "draw"} or "empate" in tipo:
                        salvar("EMPATE", odd_valor)

                    elif tipo in {"2", "away"} or "away win" in tipo:
                        salvar("FORA", odd_valor)

                    elif "over 2.5" in tipo or "over_2.5" in tipo:
                        salvar("OVER_2_5", odd_valor)

                    elif (
                        "btts yes" in tipo
                        or "both teams to score yes" in tipo
                        or "ambas marcam sim" in tipo
                    ):
                        salvar("BTTS_SIM", odd_valor)

                    resultado_placar = re.search(
                        r"(\d+)\s*[-x:]\s*(\d+)",
                        tipo,
                    )

                    if (
                        resultado_placar
                        and any(
                            palavra in tipo
                            for palavra in (
                                "score",
                                "correct",
                                "exact",
                                "placar",
                            )
                        )
                    ):
                        placar = (
                            f"{resultado_placar.group(1)}x"
                            f"{resultado_placar.group(2)}"
                        )
                        salvar(
                            f"PLACAR_{placar}",
                            odd_valor,
                        )

                # Formatos comuns da API.
                if chave_n in {
                    "odd_1",
                    "home",
                    "home_odd",
                    "match_home",
                }:
                    salvar("CASA", valor)

                elif chave_n in {
                    "odd_x",
                    "draw",
                    "draw_odd",
                    "match_draw",
                }:
                    salvar("EMPATE", valor)

                elif chave_n in {
                    "odd_2",
                    "away",
                    "away_odd",
                    "match_away",
                }:
                    salvar("FORA", valor)

                elif chave_n in {
                    "o+2.5",
                    "over_2.5",
                    "over 2.5",
                    "odd_over_2.5",
                }:
                    salvar("OVER_2_5", valor)

                elif chave_n in {
                    "bts_yes",
                    "btts_yes",
                    "both_teams_score_yes",
                }:
                    salvar("BTTS_SIM", valor)

                # Chaves como correct_score_1_0.
                if any(
                    palavra in chave_n
                    for palavra in (
                        "correct_score",
                        "exact_score",
                        "placar_exato",
                    )
                ):
                    numeros = re.findall(r"\d+", chave_n)

                    if len(numeros) >= 2:
                        placar = f"{numeros[-2]}x{numeros[-1]}"
                        salvar(
                            f"PLACAR_{placar}",
                            valor,
                        )

        return encontradas

    # ========================================================
    # PROBABILIDADES
    # ========================================================

    def _matriz_resultados(
        self,
        gols_casa_atual: int,
        gols_fora_atual: int,
        lambda_casa: float,
        lambda_fora: float,
    ) -> Dict[str, float]:
        matriz = {}

        for novos_casa in range(8):
            for novos_fora in range(8):
                probabilidade = (
                    self._poisson(lambda_casa, novos_casa)
                    * self._poisson(lambda_fora, novos_fora)
                )

                placar = (
                    f"{gols_casa_atual + novos_casa}x"
                    f"{gols_fora_atual + novos_fora}"
                )

                matriz[placar] = (
                    matriz.get(placar, 0.0)
                    + probabilidade
                )

        total = sum(matriz.values())

        if total > 0:
            matriz = {
                placar: probabilidade / total
                for placar, probabilidade in matriz.items()
            }

        return matriz

    @staticmethod
    def _probabilidades_mercados(
        matriz: Dict[str, float],
    ) -> Dict[str, float]:
        probabilidades = {
            "CASA": 0.0,
            "EMPATE": 0.0,
            "FORA": 0.0,
            "OVER_2_5": 0.0,
            "BTTS_SIM": 0.0,
        }

        for placar, probabilidade in matriz.items():
            casa_texto, fora_texto = placar.split("x")
            casa = int(casa_texto)
            fora = int(fora_texto)

            if casa > fora:
                probabilidades["CASA"] += probabilidade
            elif casa == fora:
                probabilidades["EMPATE"] += probabilidade
            else:
                probabilidades["FORA"] += probabilidade

            if casa + fora >= 3:
                probabilidades["OVER_2_5"] += probabilidade

            if casa >= 1 and fora >= 1:
                probabilidades["BTTS_SIM"] += probabilidade

        return probabilidades

    # ========================================================
    # ANÁLISE PRINCIPAL
    # ========================================================

    def analisar(
        self,
        jogo: Dict[str, Any],
        odds: List[Dict[str, Any]],
        h2h: List[Dict[str, Any]],
        stats: Any,
        previsoes: Any,
    ) -> Optional[Sinal]:
        del previsoes  # Reservado para versão futura calibrada.

        match_id = str(jogo.get("match_id", "")).strip()
        casa = str(
            jogo.get("match_hometeam_name", "Casa")
        ).strip()
        fora = str(
            jogo.get("match_awayteam_name", "Fora")
        ).strip()
        liga = str(
            jogo.get("league_name", "Mundo")
        ).strip()

        modo = jogo.get("_modo_analise", "PRE_JOGO")
        minuto = self._minuto_jogo(jogo)

        gols_casa = self._inteiro(
            jogo.get("match_hometeam_score"),
            0,
        )
        gols_fora = self._inteiro(
            jogo.get("match_awayteam_score"),
            0,
        )

        (
            xg_casa,
            xg_fora,
            fatores,
            qualidade,
        ) = self._calcular_xg(
            jogo=jogo,
            stats=stats,
            h2h=h2h,
        )

        if modo == "AO_VIVO":
            # Estima gols ainda esperados no tempo restante.
            tempo_restante = max(
                0.05,
                min(1.0, (95 - minuto) / 95),
            )

            lambda_casa = xg_casa * tempo_restante
            lambda_fora = xg_fora * tempo_restante

            fatores.append(
                f"Minuto analisado: {minuto}'"
            )
        else:
            lambda_casa = xg_casa
            lambda_fora = xg_fora

            minutos_inicio = float(
                jogo.get("_minutos_para_inicio", 0)
            )

            fatores.append(
                f"Começa em aproximadamente "
                f"{minutos_inicio:.0f} minutos"
            )

        matriz = self._matriz_resultados(
            gols_casa,
            gols_fora,
            lambda_casa,
            lambda_fora,
        )

        probabilidades = self._probabilidades_mercados(
            matriz
        )

        odds_reais = self._extrair_odds(odds)

        if not odds_reais:
            logger.info(
                "⏭️ ID=%s sem odds reais interpretáveis",
                match_id,
            )
            return None

        candidatos = []

        nomes = {
            "CASA": "Vitória Casa (1)",
            "EMPATE": "Empate (X)",
            "FORA": "Vitória Fora (2)",
            "OVER_2_5": "Over 2.5 Gols",
            "BTTS_SIM": "Ambas Marcam — Sim",
        }

        for chave, nome in nomes.items():
            probabilidade = probabilidades.get(chave, 0.0)
            odd_real = odds_reais.get(chave)

            if odd_real is None:
                continue

            if probabilidade < PROBABILIDADE_MINIMA:
                continue

            ev = probabilidade * odd_real - 1

            if ev >= EV_MINIMO:
                candidatos.append({
                    "mercado": nome,
                    "chave": chave,
                    "probabilidade": probabilidade,
                    "odd": odd_real,
                    "ev": ev,
                })

        # Placar exato somente ao vivo e nos minutos finais.
        if (
            modo == "AO_VIVO"
            and minuto >= MINUTO_MINIMO_PLACAR_EXATO
        ):
            placares_ordenados = sorted(
                matriz.items(),
                key=lambda item: item[1],
                reverse=True,
            )

            if placares_ordenados:
                placar_topo, prob_topo = placares_ordenados[0]
                chave_odd = f"PLACAR_{placar_topo}"
                odd_placar = odds_reais.get(chave_odd)

                if (
                    odd_placar is not None
                    and prob_topo >= PROB_MINIMA_PLACAR_EXATO
                ):
                    ev_placar = prob_topo * odd_placar - 1

                    if ev_placar >= EV_MINIMO:
                        candidatos.append({
                            "mercado": (
                                f"Placar Exato — {placar_topo}"
                            ),
                            "chave": chave_odd,
                            "probabilidade": prob_topo,
                            "odd": odd_placar,
                            "ev": ev_placar,
                        })

        if not candidatos:
            return None

        # Maior EV entre os mercados aprovados.
        melhor = max(
            candidatos,
            key=lambda item: item["ev"],
        )

        probabilidade = melhor["probabilidade"]
        odd_real = melhor["odd"]
        ev = melhor["ev"]

        # Confiança inclui probabilidade e qualidade dos dados.
        confianca = round(
            (
                probabilidade * 70
                + (qualidade / 100) * 30
            )
        )

        confianca = max(1, min(99, confianca))

        if qualidade >= 75 and probabilidade >= 0.65:
            risco = "🟢 MENOR"
        elif qualidade >= 50 and probabilidade >= 0.52:
            risco = "🟡 MÉDIO"
        else:
            risco = "🔴 ALTO"

        probabilidade_percentual = probabilidade * 100
        ev_percentual = ev * 100

        tese = (
            f"O modelo estima {probabilidade_percentual:.1f}% "
            f"de probabilidade para {melhor['mercado']}. "
            f"A odd real encontrada foi {odd_real:.2f}, "
            f"resultando em EV estimado de "
            f"{ev_percentual:+.1f}%. "
            f"Qualidade dos dados: {qualidade}/100. "
            f"Principais fatores: {'; '.join(fatores[:4])}."
        )

        sinal = Sinal(
            jogo=f"{casa} x {fora}",
            liga=liga,
            minuto=(
                f"{minuto}'"
                if modo == "AO_VIVO"
                else "Pré-jogo"
            ),
            placar=f"{gols_casa}x{gols_fora}",
            mercado=melhor["mercado"],
            direcao="BACK",
            odd_entrada=round(odd_real, 2),

            # Apenas referências informativas.
            # Não representam ordem automática.
            odd_alvo=round(max(1.01, odd_real * 0.85), 2),
            odd_stop=round(odd_real * 1.25, 2),

            stake=0.0,
            confianca=confianca,
            risco=risco,
            tese=tese,
            fatores=fatores,
            match_id=match_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            valor_esperado=round(ev_percentual, 1),
            probabilidade_modelo=round(
                probabilidade_percentual,
                1,
            ),
            qualidade_dados=qualidade,
            modo_analise=modo,
        )

        self.sinais_hoje += 1
        return sinal
