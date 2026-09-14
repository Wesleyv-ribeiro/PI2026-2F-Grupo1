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
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _clean_env(value: Optional[str]) -> str:
    return (value or "").strip().strip('"').strip("'")


def _parse_env_file(env_path: Path) -> dict[str, str]:
    try:
        raw = env_path.read_bytes()
        text = raw.decode("utf-8-sig")
        if "\x00" in text[:80]:
            text = raw.decode("utf-16")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = _clean_env(val)
        if key and val:
            values[key] = val
    return values


def _load_local_env() -> None:
    """Lê o .env local e preenche só variáveis ainda vazias.

    Não grava chave vazia no ambiente, senão um GEMINI_API_KEY= no arquivo
    impede a chave real de ser carregada depois.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import dotenv_values

        parsed = dotenv_values(env_path) or {}
        values = {
            str(key): _clean_env(val)
            for key, val in parsed.items()
            if key and _clean_env(val)
        }
    except ImportError:
        values = _parse_env_file(env_path)
    for key, val in values.items():
        if not _clean_env(os.environ.get(key)):
            os.environ[key] = val


_load_local_env()


# gemini-2.0-flash foi desligado em 01/06/2026. Chaves válidas ainda
# recebem 404 se o código continuar apontando para esse modelo.
GEMINI_API_KEY = _clean_env(os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = _clean_env(os.getenv("GEMINI_MODEL")) or "gemini-2.5-flash"

_RETIRED_MODELS = {
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
}

_FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
)


def _get_api_key() -> str:
    _load_local_env()
    return (
        _clean_env(os.getenv("GEMINI_API_KEY"))
        or _clean_env(os.getenv("GOOGLE_API_KEY"))
        or GEMINI_API_KEY
    )


def _get_model() -> str:
    return _clean_env(os.getenv("GEMINI_MODEL")) or GEMINI_MODEL


def _candidate_models() -> list[str]:
    preferred = _get_model()
    models: list[str] = []
    if preferred and preferred not in _RETIRED_MODELS:
        models.append(preferred)
    elif preferred in _RETIRED_MODELS:
        logger.warning(
            "GEMINI_MODEL=%s foi descontinuado; usando modelos atuais.",
            preferred,
        )
    for name in _FALLBACK_MODELS:
        if name not in models:
            models.append(name)
    return models


def _is_model_unavailable(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "404",
        "not_found",
        "not found",
        "no longer available",
        "is not found",
        "not supported",
    )
    return any(marker in text for marker in markers)

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

    api_key = _get_api_key()
    models = _candidate_models()

    if not api_key:
        logger.warning("GEMINI_API_KEY/GOOGLE_API_KEY não configurada.")
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

    raw_text = ""
    last_error: Optional[Exception] = None
    try:
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0,
        )
        for model in models:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                raw_text = (response.text or "").strip()
                if raw_text:
                    logger.info("Moderação Gemini concluída com o modelo %s.", model)
                    break
                logger.warning("Modelo %s devolveu resposta vazia.", model)
            except Exception as e:
                last_error = e
                if _is_model_unavailable(e):
                    logger.warning(
                        "Modelo Gemini %s indisponível (%s); tentando outro.",
                        model,
                        e,
                    )
                    continue
                logger.exception("Falha ao consultar a API do Gemini: %s", e)
                return _fallback_pending(
                    "Não foi possível concluir a verificação automática no momento."
                )
        else:
            logger.error(
                "Nenhum modelo Gemini respondeu. Último erro: %s", last_error
            )
            return _fallback_pending(
                "Não foi possível concluir a verificação automática no momento."
            )
    except Exception as e:
        logger.exception("Falha ao consultar a API do Gemini: %s", e)
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