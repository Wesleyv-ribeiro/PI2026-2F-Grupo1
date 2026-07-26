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
Você é um sistema de moderação de conteúdo de um marketplace agropecuário \
brasileiro chamado CONCOOP, onde produtores rurais anunciam produtos \
(alimentos, artesanato, insumos agrícolas, animais de produção, etc.).

Sua única tarefa é analisar o TÍTULO, a DESCRIÇÃO e o PREÇO de um anúncio \
de produto e decidir se ele pode ser publicado.

Reprove (decision = "rejected") somente quando houver indícios CLAROS de que o \
produto é:
- Ilícito (drogas/entorpecentes, armas de fogo, munição, explosivos);
- Vida selvagem/animais protegidos por lei sem documentação (tráfico de \
  animais silvestres, partes de animais protegidos);
- Medicamentos controlados, agrotóxicos ou produtos veterinários sem registro \
  vendidos de forma irregular;
- Produtos falsificados/pirateados;
- Qualquer outro item cuja comercialização seja proibida por lei no Brasil.

Aprove (decision = "approved") quando o produto for claramente um produto \
agropecuário/artesanal lícito comum (ex: mel, queijo, hortaliças, carnes de \
criação regular, artesanato, mudas, sementes, serviços agrícolas, etc.) e não \
houver nenhum sinal de irregularidade.

Se houver qualquer dúvida, ambiguidade, linguagem vaga, gírias suspeitas, ou \
informação insuficiente para ter certeza, responda decision = "uncertain" \
para que um administrador humano revise manualmente. Na dúvida, SEMPRE \
prefira "uncertain" em vez de "approved".

Responda ESTRITAMENTE em JSON, sem markdown, sem texto adicional, no formato:
{"decision": "approved" | "rejected" | "uncertain", "reason": "justificativa \
curta em português, no máximo 2 frases"}
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


def check_product_content(
    title: str,
    description: str,
    price: Optional[str] = None,
) -> ModerationResult:
    """
    Envia o título/descrição/preço do produto para o Gemini e retorna
    um ModerationResult com o status ("aprovado", "rejeitado" ou "pendente")
    e a justificativa.

    Nunca levanta exceção: qualquer falha de configuração, rede ou parsing
    resulta em status "pendente", para que o item seja revisado manualmente
    pelo administrador em vez de ser publicado sem verificação.
    """
    title = (title or "").strip()
    description = (description or "").strip()
    price = (price or "").strip()

    if not GEMINI_API_KEY:
        logger.warning(
            "GEMINI_API_KEY não configurada. Produto enviado para revisão manual."
        )
        return _fallback_pending(
            "Verificação automática indisponível (chave da API do Gemini não "
            "configurada). Aguardando revisão de um administrador."
        )

    try:
        import google.generativeai as genai
    except ImportError:
        logger.exception(
            "Pacote 'google-generativeai' não instalado. Rode: "
            "pip install google-generativeai"
        )
        return _fallback_pending(
            "Verificação automática indisponível (dependência ausente). "
            "Aguardando revisão de um administrador."
        )

    prompt = (
        f"TÍTULO: {title}\n"
        f"DESCRIÇÃO: {description}\n"
        f"PREÇO: {price or 'não informado'}\n"
    )

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_SYSTEM_INSTRUCTION,
        )
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0,
            },
        )
        raw_text = (response.text or "").strip()
    except Exception:  # noqa: BLE001 - qualquer erro da API cai em pendente
        logger.exception("Falha ao consultar a API do Gemini para moderação.")
        return _fallback_pending(
            "Não foi possível concluir a verificação automática no momento. "
            "Aguardando revisão de um administrador."
        )

    data = _extract_json(raw_text)
    if not data or "decision" not in data:
        logger.warning("Resposta do Gemini em formato inesperado: %r", raw_text)
        return _fallback_pending(
            "A verificação automática retornou uma resposta inesperada. "
            "Aguardando revisão de um administrador."
        )

    decision = str(data.get("decision", "")).strip().lower()
    reason = str(data.get("reason", "")).strip() or "Sem justificativa fornecida."

    if decision == "approved":
        return ModerationResult(status=STATUS_APPROVED, reason=reason, raw_decision=decision)
    if decision == "rejected":
        return ModerationResult(status=STATUS_REJECTED, reason=reason, raw_decision=decision)
    # "uncertain" ou qualquer valor não reconhecido -> pendente (revisão humana)
    return ModerationResult(status=STATUS_PENDING, reason=reason, raw_decision=decision)