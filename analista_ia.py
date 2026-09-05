"""
Analista IA — Gemini Flash (Nível 2)

Papel:
1. Escrever a tese dos sinais em linguagem natural, usando
   SOMENTE os números produzidos pelo modelo matemático;
2. Revisar o sinal antes do envio (sanidade: APROVA/DUVIDA/VETA).

Limites de segurança (por design):
- A IA NUNCA define probabilidade, odd ou mercado — isso é do
  brain (modelo matemático);
- Sem GEMINI_API_KEY, ou se o serviço falhar, o bot segue 100%
  com a tese template (fallback total, zero dependência).
"""
import json
import logging
import re
import time
from typing import Dict, Optional

import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_MODELO,
    GEMINI_REVISAO,
    GEMINI_TIMEOUT,
)

logger = logging.getLogger("TraderIA")


class AnalistaIA:
    BASE = "https://generativelanguage.googleapis.com/v1beta"
    CACHE_TTL = 600.0

    def __init__(self):
        self.session = requests.Session()
        self.ativo = bool(GEMINI_API_KEY)

        if not self.ativo:
            logger.info(
                "🤖 Analista IA inativo: GEMINI_API_KEY "
                "ausente (usando tese template)"
            )
            return

        logger.info(
            "🤖 Analista IA ativo (modelo %s | revisão: %s)",
            GEMINI_MODELO,
            "on" if GEMINI_REVISAO in {
                "on", "1", "true", "sim"
            } else "off",
        )

        self._cache: Dict[str, tuple] = {}

    # ========================================================
    # CHAMADA BASE
    # ========================================================

    def _chamar(
        self, prompt: str, max_tokens: int = 350
    ) -> Optional[str]:
        if not self.ativo:
            return None

        url = (
            f"{self.BASE}/models/{GEMINI_MODELO}:"
            f"generateContent"
        )

        corpo = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": max_tokens,
            },
        }

        try:
            resposta = self.session.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=corpo,
                timeout=GEMINI_TIMEOUT,
            )

            if resposta.status_code != 200:
                logger.warning(
                    "🤖 Gemini HTTP %s",
                    resposta.status_code,
                )
                return None

            dados = resposta.json()

            candidatos = dados.get(
                "candidates", []
            ) or []

            texto = (
                (
                    (candidatos[0] or {})
                    .get("content", {})
                    or {}
                )
                .get("parts", [{}])[0]
                .get("text", "")
            ) if candidatos else ""

            texto = str(texto).strip()

            return texto or None

        except requests.RequestException as erro:
            logger.warning(
                "🤖 Gemini falhou: %s",
                type(erro).__name__,
            )
            return None
        except (ValueError, KeyError, IndexError):
            logger.warning(
                "🤖 Gemini: resposta em formato inesperado"
            )
            return None

    # ========================================================
    # CONTEXTO COMPACTO
    # ========================================================

    @staticmethod
    def _contexto_texto(contexto: Dict) -> str:
        linhas = []

        for chave, valor in contexto.items():
            if valor in (None, "", [], {}):
                continue

            if isinstance(valor, (dict, list)):
                valor = json.dumps(
                    valor, ensure_ascii=False
                )

            linhas.append(f"- {chave}: {valor}")

        return "\n".join(linhas)

    def _do_cache(
        self, chave: str
    ) -> Optional[str]:
        item = self._cache.get(chave)

        if not item:
            return None

        ts, valor = item

        if time.time() - ts > self.CACHE_TTL:
            self._cache.pop(chave, None)
            return None

        return valor

    # ========================================================
    # 1. TESE EM LINGUAGEM NATURAL
    # ========================================================

    def escrever_tese(
        self, contexto: Dict
    ) -> Optional[str]:
        if not self.ativo:
            return None

        chave_cache = (
            f"tese:{contexto.get('match_id', '')}:"
            f"{contexto.get('mercado', '')}"
        )

        em_cache = self._do_cache(chave_cache)

        if em_cache:
            return em_cache

        prompt = (
            "Você é um analista esportivo. Escreva a tese "
            "desta aposta em português do Brasil, em 2 a 4 "
            "frases diretas.\n"
            "REGRAS OBRIGATÓRIAS:\n"
            "- Use EXCLUSIVAMENTE os números e fatos da "
            "lista abaixo. É PROIBIDO inventar qualquer "
            "dado, estatística ou fato adicional.\n"
            "- Não prometa lucro nem use linguagem de "
            "certeza; deixe claro que é estimativa.\n"
            "- Não use markdown, títulos ou listas — apenas "
            "texto corrido. Máximo ~500 caracteres.\n\n"
            "DADOS DO JOGO E DO SINAL:\n"
            + self._contexto_texto(contexto)
            + "\n\nTese:"
        )

        tese = self._chamar(prompt, max_tokens=300)

        if tese:
            tese = re.sub(
                r"\*+", "", tese
            ).strip()

            if len(tese) > 700:
                tese = tese[:697] + "..."

            self._cache[chave_cache] = (
                time.time(),
                tese,
            )
            logger.info(
                "🤝 Tese escrita pelo analista IA "
                "(%s caracteres)",
                len(tese),
            )

        return tese

    # ========================================================
    # 2. REVISÃO DE SANIDADE
    # ========================================================

    def revisar(
        self, contexto: Dict, tese: str
    ) -> Dict[str, str]:
        revisao_on = GEMINI_REVISAO in {
            "on", "1", "true", "sim"
        }

        if not revisao_on:
            return {
                "veredito": "APROVA",
                "motivo": "revisão desativada",
            }

        if not self.ativo:
            return {
                "veredito": "APROVA",
                "motivo": "IA indisponível",
            }

        chave_cache = (
            f"revisao:{contexto.get('match_id', '')}:"
            f"{contexto.get('mercado', '')}"
        )

        em_cache = self._do_cache(chave_cache)

        if em_cache:
            return json.loads(em_cache)

        prompt = (
            "Você é um revisor cético de sinais de apostas "
            "esportivas. Avalie se o sinal abaixo é coerente "
            "com os dados apresentados.\n"
            "Vete APENAS por incoerência grave, como: dados "
            "insuficientes para o mercado, odds absurdas para "
            "o cenário descrito, contradição entre tese e "
            "números, ou jogo praticamente encerrado.\n"
            "NÃO vete por discordar da estratégia.\n"
            "Responda EXATAMENTE na primeira linha uma das "
            "palavras: APROVA, DUVIDA ou VETA. Na segunda "
            "linha, um motivo curto.\n\n"
            "DADOS:\n"
            + self._contexto_texto(contexto)
            + "\n\nTESE:\n"
            + (tese or "(tese template)")
        )

        texto = self._chamar(prompt, max_tokens=120)

        if not texto:
            return {
                "veredito": "APROVA",
                "motivo": "IA não respondeu (fail-open)",
            }

        primeira = texto.splitlines()[0].upper()
        motivo = (
            texto.splitlines()[1].strip()
            if len(texto.splitlines()) > 1
            else ""
        )

        if "VETA" in primeira:
            veredito = "VETA"
        elif "DUVIDA" in primeira or (
            "DÚVIDA" in primeira
        ):
            veredito = "DUVIDA"
        elif "APROVA" in primeira:
            veredito = "APROVA"
        else:
            veredito = "APROVA"
            motivo = "resposta não reconhecida"

        resultado = {
            "veredito": veredito,
            "motivo": motivo,
        }

        self._cache[chave_cache] = (
            time.time(),
            json.dumps(resultado),
        )

        return resultado
