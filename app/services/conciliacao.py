import uuid
import mimetypes
from datetime import datetime
from typing import Dict, Any, Optional
from app.core.supabase import get_supabase
from app.core.config import settings
from app.services.ai_parser import extrair_dados_comprovante_bytes, ComprovantePixSchema

MIMES_ACEITOS = {"image/jpeg", "image/png", "image/webp", "application/pdf"}

EXTENSAO_POR_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _ultimos_digitos(telefone: str, qtd: int = 8) -> str:
    return "".join(filter(str.isdigit, telefone or ""))[-qtd:]


def remetente_e_cadastrado(telefone: str) -> bool:
    """True se o telefone pertence a um responsável ou a um aluno cadastrado."""
    final = _ultimos_digitos(telefone)
    if len(final) < 8:
        return False
    supabase = get_supabase()
    resp = supabase.table("responsaveis").select("id").ilike("telefone", f"%{final}%").limit(1).execute()
    if resp.data:
        return True
    alu = supabase.table("alunos").select("id").eq("ativo", True).ilike("telefone", f"%{final}%").limit(1).execute()
    return bool(alu.data)


async def processar_comprovante_e_conciliar(
    arquivo_bytes: bytes,
    nome_arquivo: str,
    telefone_remetente: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fluxo completo:
    1. Salva o arquivo (imagem ou PDF) no Storage do Supabase
    2. Envia para a IA (Gemini) extrair os dados estruturados
    3. Evita processar o mesmo comprovante duas vezes (código E2E do Pix)
    4. Identifica o responsável e os alunos vinculados
    5. Cruza o valor com as mensalidades pendentes
    6. Dá baixa automática ou encaminha para revisão manual
    """
    supabase = get_supabase()

    # Descobre o tipo do arquivo
    if not mime_type:
        mime_type = mimetypes.guess_type(nome_arquivo)[0] or "image/jpeg"
    extensao = EXTENSAO_POR_MIME.get(mime_type, "jpg")

    # 1. Upload do comprovante no Storage do Supabase
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path_storage = f"comprovantes/{timestamp}_{uuid.uuid4().hex[:8]}.{extensao}"

    try:
        supabase.storage.from_(settings.SUPABASE_BUCKET_COMPROVANTES).upload(
            path=path_storage,
            file=arquivo_bytes,
            file_options={"content-type": mime_type}
        )
        url_comprovante = supabase.storage.from_(settings.SUPABASE_BUCKET_COMPROVANTES).get_public_url(path_storage)
    except Exception as e:
        url_comprovante = f"upload_falhou: {str(e)}"

    # 2. Extração via IA
    dados_ia: ComprovantePixSchema = extrair_dados_comprovante_bytes(arquivo_bytes, mime_type)

    resultado = {
        "status_conciliacao": "PENDENTE_REVISAO",
        "mensagem": "",
        "url_comprovante": url_comprovante,
        "dados_extraidos": dados_ia.model_dump(),
        "mensalidades_baixadas": []
    }

    # 3. Anti-duplicidade: o mesmo Pix (código E2E) não pode dar baixa duas vezes
    if dados_ia.id_transacao_e2e:
        ja_usado = supabase.table("mensalidades").select("id").eq("id_transacao_pix", dados_ia.id_transacao_e2e).limit(1).execute()
        if ja_usado.data:
            resultado["status_conciliacao"] = "COMPROVANTE_DUPLICADO"
            resultado["mensagem"] = "Este comprovante (mesmo código Pix) já foi processado antes."
            return resultado

    # 4. Localização do Responsável ou Aluno
    responsavel_id = None
    alunos_ids = []

    if telefone_remetente:
        tel_limpo = "".join(filter(str.isdigit, telefone_remetente))
        resp_query = supabase.table("responsaveis").select("id, nome").ilike("telefone", f"%{tel_limpo[-8:]}%").execute()
        if resp_query.data:
            responsavel_id = resp_query.data[0]["id"]
            vinculos = supabase.table("aluno_responsaveis").select("aluno_id").eq("responsavel_id", responsavel_id).execute()
            alunos_ids = [v["aluno_id"] for v in vinculos.data]

        # Aluno adulto que paga com o próprio telefone
        if not alunos_ids:
            alu_query = supabase.table("alunos").select("id").eq("ativo", True).ilike("telefone", f"%{tel_limpo[-8:]}%").execute()
            alunos_ids = [a["id"] for a in alu_query.data]

    # Se não achou por telefone, tenta pelo nome do pagador identificado pela IA
    if not alunos_ids and dados_ia.pagador_nome:
        aluno_query = supabase.table("alunos").select("id, nome").ilike("nome", f"%{dados_ia.pagador_nome.split()[0]}%").execute()
        if aluno_query.data:
            alunos_ids = [a["id"] for a in aluno_query.data]
        else:
            resp_query = supabase.table("responsaveis").select("id, nome").ilike("nome", f"%{dados_ia.pagador_nome.split()[0]}%").execute()
            if resp_query.data:
                responsavel_id = resp_query.data[0]["id"]
                vinculos = supabase.table("aluno_responsaveis").select("aluno_id").eq("responsavel_id", responsavel_id).execute()
                alunos_ids = [v["aluno_id"] for v in vinculos.data]

    if not alunos_ids:
        resultado["status_conciliacao"] = "ALUNO_NAO_ENCONTRADO"
        resultado["mensagem"] = f"Não foi possível vincular o pagamento ao pagador '{dados_ia.pagador_nome}' ou telefone '{telefone_remetente}'."
        return resultado

    # 5. Mensalidades pendentes
    mensalidades_query = supabase.table("mensalidades")\
        .select("*")\
        .in_("aluno_id", alunos_ids)\
        .eq("status", "PENDENTE")\
        .order("data_vencimento")\
        .execute()

    pendencias = mensalidades_query.data
    if not pendencias:
        resultado["status_conciliacao"] = "SEM_MENSALIDADES_PENDENTES"
        resultado["mensagem"] = "O aluno/responsável foi identificado, mas não possui mensalidades com status PENDENTE."
        return resultado

    # 6. Validação de valores (soma total ou fatura individual idêntica)
    valor_pago = dados_ia.valor_pago
    total_pendente = sum(float(m["valor_devido"]) for m in pendencias)

    mensalidades_para_baixar = []
    if abs(valor_pago - total_pendente) < 0.01:
        mensalidades_para_baixar = pendencias
    else:
        for fatura in pendencias:
            if abs(float(fatura["valor_devido"]) - valor_pago) < 0.01:
                mensalidades_para_baixar = [fatura]
                break

    if mensalidades_para_baixar:
        agora = datetime.now().isoformat()
        ids_baixados = []
        for m in mensalidades_para_baixar:
            supabase.table("mensalidades").update({
                "status": "PAGO",
                "valor_pago": float(m["valor_devido"]),
                "data_pagamento": agora,
                "forma_pagamento": "PIX",
                "comprovante_url": url_comprovante,
                "id_transacao_pix": dados_ia.id_transacao_e2e,
                "responsavel_pagador_id": responsavel_id
            }).eq("id", m["id"]).execute()
            ids_baixados.append(m["id"])

        resultado["status_conciliacao"] = "BAIXA_AUTOMATICA_SUCESSO"
        resultado["mensagem"] = f"Pagamento de R$ {valor_pago:.2f} conciliado e baixado com sucesso!"
        resultado["mensalidades_baixadas"] = ids_baixados
    else:
        resultado["status_conciliacao"] = "VALOR_DIVERGENTE"
        resultado["mensagem"] = f"Valor pago (R$ {valor_pago:.2f}) difere das mensalidades pendentes. Encaminhado para revisão manual."

    return resultado
