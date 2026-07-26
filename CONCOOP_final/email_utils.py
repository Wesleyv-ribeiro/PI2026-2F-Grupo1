"""
Módulo de envio de e-mails via Gmail, usado para confirmar o endereço de
e-mail informado no cadastro (verificação de e-mail).

Como funciona
-------------
Quando alguém se cadastra no CONCOOP, criamos a conta já no banco de dados,
mas com `email_verified = 0` e um token aleatório de confirmação
(`email_verify_token`). Enviamos um e-mail (via SMTP do Gmail) com um link
do tipo:

    https://seusite.com/verify-email/<token>

Quando a pessoa clica no link, a rota `/verify-email/<token>` do app.py
marca `email_verified = 1` e o usuário passa a poder fazer login.

Configuração necessária (arquivo .env)
---------------------------------------
GMAIL_USER            -> o endereço Gmail que vai ENVIAR os e-mails
                         (ex: contato.concoop@gmail.com)
GMAIL_APP_PASSWORD    -> uma "Senha de app" gerada na conta Google que envia
                         os e-mails (NÃO é a senha normal da conta).
                         Como gerar: Conta Google > Segurança > Verificação
                         em duas etapas (precisa estar ativada) > Senhas de app.

Comportamento em caso de falha
-------------------------------
Se as credenciais do Gmail não estiverem configuradas, ou se o envio falhar
por qualquer motivo (rede, credenciais erradas, etc.), a função NÃO derruba
o cadastro do usuário: ela retorna False e loga o problema, e o app.py trata
esse caso liberando a conta automaticamente (para não travar o cadastro caso
o administrador ainda não tenha configurado o e-mail) e avisando o usuário
que pode reenviar a confirmação mais tarde, quando o e-mail estiver
configurado.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

GMAIL_USER = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_SENDER_NAME = os.getenv("GMAIL_SENDER_NAME", "CONCOOP").strip()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


def is_configured() -> bool:
    """True se as credenciais do Gmail foram definidas no .env."""
    return bool(GMAIL_USER and GMAIL_APP_PASSWORD)


def send_verification_email(to_email: str, to_name: str, verify_url: str) -> bool:
    """
    Envia o e-mail de confirmação de cadastro com o link de verificação.

    Retorna True se o e-mail foi enviado com sucesso, False caso contrário
    (nunca levanta exceção para não quebrar o fluxo de cadastro/login).
    """
    if not is_configured():
        logger.warning(
            "GMAIL_USER/GMAIL_APP_PASSWORD não configurados. "
            "E-mail de verificação NÃO foi enviado para %s.",
            to_email,
        )
        return False

    subject = "Confirme seu e-mail — CONCOOP"
    text_body = (
        f"Olá, {to_name}!\n\n"
        "Obrigado por se cadastrar no CONCOOP.\n"
        "Para confirmar seu e-mail e ativar sua conta, acesse o link abaixo:\n\n"
        f"{verify_url}\n\n"
        "Se você não se cadastrou no CONCOOP, apenas ignore este e-mail.\n"
    )
    html_body = f"""\
    <html>
      <body style="font-family:Arial,sans-serif;color:#2b2b2b;line-height:1.6;">
        <h2 style="color:#3a6b35;">Confirme seu e-mail</h2>
        <p>Olá, <strong>{to_name}</strong>!</p>
        <p>Obrigado por se cadastrar no <strong>CONCOOP</strong>. Para confirmar
        seu e-mail e ativar sua conta, clique no botão abaixo:</p>
        <p style="margin:24px 0;">
          <a href="{verify_url}"
             style="background:#3a6b35;color:#ffffff;padding:12px 24px;
                    text-decoration:none;border-radius:6px;display:inline-block;">
            Confirmar meu e-mail
          </a>
        </p>
        <p>Ou copie e cole este link no navegador:<br>
        <a href="{verify_url}">{verify_url}</a></p>
        <p style="color:#777;font-size:0.85rem;">
          Se você não se cadastrou no CONCOOP, apenas ignore este e-mail.
        </p>
      </body>
    </html>
    """

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{GMAIL_SENDER_NAME} <{GMAIL_USER}>"
    message["To"] = to_email
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [to_email], message.as_string())
        return True
    except Exception:  # noqa: BLE001 - nunca deixa o envio derrubar o app
        logger.exception("Falha ao enviar e-mail de verificação para %s.", to_email)
        return False