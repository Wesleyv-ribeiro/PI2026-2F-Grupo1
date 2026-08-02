"""
Módulo de moderação automática de produtos usando a API do Gemini (Google).

Objetivo
--------
Antes de um produto cadastrado por um produtor ficar visível no Mercado
(marketplace), o título, a descrição e o preço são enviados para o modelo
Gemini, que avalia se o anúncio pode conter algo ilícito, proibido ou não
regulamentado (ex.: armas, drogas, animais silvestres, agrotóxicos sem
registro, medicamentos controlados, produtos de origem duvidosa, etc.).

O resultado da análise é sempre um destes três status:

    - "aprovado"  -> o Gemini tem alta confiança de que o produto é lícito.
                     Fica visível no marketplace imediatamente.
    - "rejeitado" -> o Gemini tem alta confiança de que o produto é
                     ilícito/proibido. NÃO fica visível no marketplace.
    - "pendente"  -> o Gemini não tem certeza suficiente (ou a verificação
                     falhou/não pôde ser realizada). O produto fica oculto
                     do marketplace até um administrador revisar manualmente
                     no painel de admin.

Importante: por segurança, qualquer erro, resposta inesperada ou ausência
de chave de API faz o produto cair em "pendente" (nunca em "aprovado"
automático), garantindo que produtos duvidosos sempre passem por revisão
humana antes de ficarem públicos.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Status possíveis armazenados na coluna products.moderation_status
STATUS_APPROVED = "aprovado"
STATUS_REJECTED = "rejeitado"
STATUS_PENDING = "pendente"

_VALID_STATUSES = {STATUS_APPROVED, STATUS_REJECTED, STATUS_PENDING}

_SYSTEM_INSTRUCTION = """\
Você é um sistema rigoroso de moderação de conteúdo de um marketplace agropecuário brasileiro (CONCOOP).
Sua tarefa é analisar TÍTULO, DESCRIÇÃO e PREÇO de um anúncio de produto e classificá-lo.

Regras de Classificação:

1. REPROVE DE IMEDIATO (decision = "rejected"):
   - Qualquer item ILÍCITO ou PROIBIDO por lei no Brasil.
   - Drogas, entorpecentes, substâncias controladas ou ilícitas.
   - Armas de fogo, munição, explosivos, armas brancas graves.
   - Animais silvestres, partes de animais protegidos ou caça sem documentação.
   - Agrotóxicos ou medicamentos veterinários de venda controlada/sem registro no MAPA/ANVISA.
   - Produtos falsificados, pirateados ou de origem ilegal flagrante.

2. APROVE (decision = "approved"):
   - Produtos agropecuários, hortifrúti, alimentos, bebidas de produção própria/artesanal.
   - Animais de criação regular (bovinos, suínos, aves de postura/corte, peixes de piscicultura).
   - Insumos agrícolas normais, sementes, mudas, ferramentas, máquinas e serviços rurais lícitos.

3. USE APENAS QUANDO HOUVER AMBIGUIDADE REAL (decision = "uncertain"):
   - Somente se as informações forem completamente ilegíveis, desconexas ou faltar o contexto mínimo para entender o que é o produto.

Responda ESTRITAMENTE em JSON sem formatação markdown:
{"decision": "approved" | "rejected" | "uncertain", "reason": "justificativa curta em português em até 2 frases"}
"""


@dataclass
class ModerationResult:
    status: str  # aprovado | rejeitado | pendente
    reason: str
    raw_decision: Optional[str] = None  # decisão crua vinda do Gemini (debug)

    @property
    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.status == STATUS_REJECTED

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING


def _fallback_pending(reason: str) -> ModerationResult:
    """Resultado seguro padrão: envia para revisão humana."""
    return ModerationResult(status=STATUS_PENDING, reason=reason)


def _extract_json(text: str) -> Optional[dict]:
    """Tenta extrair um objeto JSON da resposta do modelo, mesmo que
    venha com blocos de código markdown ou texto ao redor."""
    text = text.strip()
    # Remove cercas de código ```json ... ``` se existirem
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text.strip()).strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Última tentativa: pega o primeiro trecho que pareça um objeto {...}
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None

try:
    from google import genai
    from google.genai import types
except ImportError:  # SDK opcional no servidor atual
    genai = None
    types = None


def check_product_content(
    title: str,
    description: str,
    price: Optional[str] = None,
) -> ModerationResult:
    title = (title or "").strip()
    description = (description or "").strip()
    price = (price or "").strip()

    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY não configurada.")
        return _fallback_pending(
            "Verificação automática indisponível (chave da API não configurada)."
        )

    if genai is None or types is None:
        logger.warning("SDK do Gemini não instalada; deixando produto em revisão manual.")
        return _fallback_pending(
            "Verificação automática indisponível (SDK do Gemini não instalada)."
        )

    prompt = f"""
    Por favor, analise a seguinte oferta de produto:

    - TÍTULO DO PRODUTO: {title}
    - DESCRIÇÃO DO PRODUTO: {description}
    - PREÇO: {price or 'não informado'}
    """

    try:
        # Inicializa o cliente com a nova SDK
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Chama o modelo gemini-2.0-flash
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        raw_text = (response.text or "").strip()
    except Exception as e:
        logger.exception(f"Falha ao consultar a API do Gemini: {e}")
        return _fallback_pending(
            "Não foi possível concluir a verificação automática no momento."
        )

    data = _extract_json(raw_text)
    if not data or "decision" not in data:
        logger.warning("Resposta do Gemini em formato inesperado: %r", raw_text)
        return _fallback_pending(
            "A verificação automática retornou uma resposta inesperada."
        )

    decision = str(data.get("decision", "")).strip().lower()
    reason = str(data.get("reason", "")).strip() or "Sem justificativa fornecida."

    if decision == "approved":
        return ModerationResult(status=STATUS_APPROVED, reason=reason, raw_decision=decision)
    if decision == "rejected":
        return ModerationResult(status=STATUS_REJECTED, reason=reason, raw_decision=decision)

    return ModerationResult(status=STATUS_PENDING, reason=reason, raw_decision=decision)