import hashlib
import hmac
import json
import requests
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from app.services.conciliacao import (
    processar_comprovante_e_conciliar,
    remetente_e_cadastrado,
    MIMES_ACEITOS,
    EXTENSAO_POR_MIME,
)
from app.core.config import settings
from app.core.security import exigir_login

router = APIRouter(prefix="/webhook", tags=["Webhook & IA WhatsApp"])

GRAPH_URL = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}"

# A Meta pode entregar o mesmo aviso mais de uma vez: guardamos os ids já tratados
_MENSAGENS_VISTAS: set = set()


# ----------------------------------------------------------------------------
# Funções auxiliares (falam com a API da Meta)
# ----------------------------------------------------------------------------
def _headers_meta() -> dict:
    return {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}


def enviar_mensagem_whatsapp(numero_telefone: str, texto: str):
    """Responde ao aluno pelo número oficial do studio (Cloud API)."""
    try:
        url = f"{GRAPH_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": numero_telefone,
            "type": "text",
            "text": {"body": texto},
        }
        res = requests.post(url, json=payload, headers=_headers_meta(), timeout=10)
        if res.status_code >= 300:
            print(f"[WhatsApp] Meta recusou o envio: {res.status_code} {res.text}")
    except Exception as e:
        print(f"[WhatsApp] Falha ao enviar resposta: {e}")


def baixar_midia_meta(media_id: str) -> Optional[bytes]:
    """
    A Meta não manda o arquivo no aviso, só um código (media_id).
    Passo 1: pedir a URL temporária desse código. Passo 2: baixar o arquivo.
    Os dois passos usam o mesmo token de acesso.
    """
    try:
        info = requests.get(f"{GRAPH_URL}/{media_id}", headers=_headers_meta(), timeout=15)
        info.raise_for_status()
        url_arquivo = info.json().get("url")
        if not url_arquivo:
            return None
        arquivo = requests.get(url_arquivo, headers=_headers_meta(), timeout=30)
        arquivo.raise_for_status()
        return arquivo.content
    except Exception as e:
        print(f"[WhatsApp] Falha ao baixar mídia {media_id}: {e}")
        return None


def assinatura_valida(corpo: bytes, cabecalho: Optional[str]) -> bool:
    """Confere se o aviso foi mesmo assinado pela Meta (X-Hub-Signature-256)."""
    if not settings.WHATSAPP_APP_SECRET or not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.split("=", 1)[1])


# ----------------------------------------------------------------------------
# Upload manual (usado pelo front)
# ----------------------------------------------------------------------------
@router.post("/comprovante/upload", dependencies=[Depends(exigir_login)])
async def upload_e_conciliar_comprovante(
    arquivo: UploadFile = File(..., description="Imagem, print ou PDF do comprovante bancário"),
    telefone_remetente: Optional[str] = Form(None, description="Telefone do aluno/responsável para cruzamento direto"),
):
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    mime = arquivo.content_type if arquivo.content_type in MIMES_ACEITOS else None

    return await processar_comprovante_e_conciliar(
        arquivo_bytes=conteudo,
        nome_arquivo=arquivo.filename or "comprovante.jpg",
        telefone_remetente=telefone_remetente,
        mime_type=mime,
    )


# ----------------------------------------------------------------------------
# Webhook da Meta
# ----------------------------------------------------------------------------
@router.get("/whatsapp")
async def verificar_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Verificação feita UMA vez, quando você cadastra o webhook no painel da Meta.
    A Meta manda uma senha; se bater com a do seu .env, devolvemos o desafio dela.
    """
    senha_certa = settings.WHATSAPP_VERIFY_TOKEN
    if hub_mode == "subscribe" and senha_certa and hmac.compare_digest(hub_verify_token or "", senha_certa):
        return PlainTextResponse(hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verificação recusada.")


@router.post("/whatsapp")
async def receber_webhook(request: Request, background: BackgroundTasks):
    """
    Recebe os avisos da Meta. Responde 200 rápido e processa em segundo plano
    (assim a Meta não reenvia o aviso por achar que demoramos).
    """
    corpo = await request.body()
    if not assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Assinatura inválida.")

    try:
        dados = json.loads(corpo)
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    for entrada in dados.get("entry", []):
        for mudanca in entrada.get("changes", []):
            for msg in mudanca.get("value", {}).get("messages", []):
                background.add_task(processar_mensagem, msg)

    return {"status": "received"}


async def processar_mensagem(msg: dict):
    """
    Só segue adiante quando:
      1) a mensagem é imagem ou documento (PDF);
      2) o tipo do arquivo é aceito;
      3) o telefone de quem enviou é de um aluno/responsável cadastrado.
    Todo o resto é ignorado em silêncio.
    """
    msg_id = msg.get("id")
    if msg_id in _MENSAGENS_VISTAS:
        return
    if msg_id:
        _MENSAGENS_VISTAS.add(msg_id)
        if len(_MENSAGENS_VISTAS) > 5000:
            _MENSAGENS_VISTAS.clear()

    tipo = msg.get("type")
    if tipo not in ("image", "document"):
        return

    midia = msg.get(tipo, {})
    mime = (midia.get("mime_type") or "").split(";")[0].strip().lower()
    if mime not in MIMES_ACEITOS or not midia.get("id"):
        return

    telefone = msg.get("from", "")
    if not await run_in_threadpool(remetente_e_cadastrado, telefone):
        return

    arquivo_bytes = await run_in_threadpool(baixar_midia_meta, midia["id"])
    if not arquivo_bytes:
        return

    nome_arquivo = midia.get("filename") or f"comprovante_whatsapp.{EXTENSAO_POR_MIME[mime]}"

    resultado = await processar_comprovante_e_conciliar(
        arquivo_bytes=arquivo_bytes,
        nome_arquivo=nome_arquivo,
        telefone_remetente=telefone,
        mime_type=mime,
    )

    status = resultado.get("status_conciliacao")
    valor = (resultado.get("dados_extraidos") or {}).get("valor_pago", 0)
    cabecalho = "🥋 *Studio Arte em Movimento*\n\n"

    if status == "BAIXA_AUTOMATICA_SUCESSO":
        texto = (cabecalho +
                 f"Olá! Confirmamos o recebimento do seu Pix no valor de *R$ {valor:.2f}*.\n"
                 "Sua mensalidade já foi *quitada com sucesso* no sistema! ✅\n\nÓtimo treino!")
    elif status == "VALOR_DIVERGENTE":
        texto = (cabecalho +
                 f"Identificamos seu comprovante de *R$ {valor:.2f}*, porém o valor diverge da sua fatura em aberto.\n"
                 "Encaminhamos para a recepção verificar manualmente. Em breve retornamos!")
    elif status == "COMPROVANTE_DUPLICADO":
        texto = cabecalho + "Esse comprovante já foi registrado antes. Está tudo certo! ✅"
    elif status == "SEM_MENSALIDADES_PENDENTES":
        texto = cabecalho + "Recebemos seu comprovante, mas você não tem mensalidade em aberto no momento. A recepção vai conferir."
    else:
        texto = cabecalho + "Recebemos seu comprovante e a recepção vai conferir manualmente. Obrigado!"

    await run_in_threadpool(enviar_mensagem_whatsapp, telefone, texto)
