"""
Conciliação de comprovantes: decide se um comprovante pode dar baixa automática
ou se precisa de revisão manual. (Quem lê o comprovante é a IA; quem guarda o arquivo é o armazenamento;
quem organiza a ordem de chegada é a fila.)
"""
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.supabase import get_supabase
from app.services.ai_parser import ComprovantePixSchema
from app.services.previsao import mes_atual

MIMES_ACEITOS = {"image/jpeg", "image/png", "image/webp", "application/pdf"}

EXTENSAO_POR_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

STATUS_ABERTOS = ("PENDENTE", "ATRASADO")


def _digitos(valor: Optional[str]) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


def _norm(texto: Optional[str]) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.findall(r"[a-z0-9]+", base))


def _achar_por_telefone(supabase, tabela: str, telefone: str) -> List[Dict[str, Any]]:
    """Acha cadastros pelo telefone, ignorando máscara e código do país (compara os 8 últimos números)."""
    dig = _digitos(telefone)
    if len(dig) < 8:
        return []
    linhas = supabase.table(tabela).select("*").ilike("telefone", f"%{dig[-4:]}%").execute().data or []
    return [l for l in linhas if _digitos(l.get("telefone"))[-8:] == dig[-8:]]


def remetente_e_cadastrado(telefone: str) -> bool:
    """True se o telefone pertence a um responsável ou a um aluno ativo cadastrado."""
    supabase = get_supabase()
    if _achar_por_telefone(supabase, "responsaveis", telefone):
        return True
    return any(a.get("ativo") is not False for a in _achar_por_telefone(supabase, "alunos", telefone))


def _alunos_do_responsavel(supabase, responsavel_id: int) -> List[Dict[str, Any]]:
    vinculos = supabase.table("aluno_responsaveis").select("aluno_id").eq("responsavel_id", responsavel_id).execute().data or []
    ids = [v["aluno_id"] for v in vinculos]
    if not ids:
        return []
    return supabase.table("alunos").select("id, nome, ativo").in_("id", ids).eq("ativo", True).execute().data or []


def _identificar(supabase, telefone: Optional[str], pagador_nome: Optional[str]) -> Tuple[Optional[Dict], List[Dict], Optional[str]]:
    """
    Descobre quem pagou. Devolve (responsável, alunos, origem da identificação):
      TELEFONE      - o número que mandou está cadastrado (confiável)
      NOME_EXATO    - o nome do pagador é igual ao de UM cadastro (confiável)
      NOME_PARCIAL  - só o primeiro nome bate (NÃO confiável: vai para revisão)
    """
    responsavel, alunos = None, []

    if telefone:
        achados = _achar_por_telefone(supabase, "responsaveis", telefone)
        if achados:
            responsavel = achados[0]
            alunos = _alunos_do_responsavel(supabase, responsavel["id"])
        if not alunos:
            alunos = [a for a in _achar_por_telefone(supabase, "alunos", telefone) if a.get("ativo") is not False]
        if alunos or responsavel:
            return responsavel, alunos, "TELEFONE"

    if pagador_nome and pagador_nome.strip():
        primeiro = pagador_nome.split()[0]
        alvo = _norm(pagador_nome)

        cand_alunos = supabase.table("alunos").select("id, nome, ativo").ilike("nome", f"%{primeiro}%").eq("ativo", True).execute().data or []
        cand_resp = supabase.table("responsaveis").select("*").ilike("nome", f"%{primeiro}%").execute().data or []

        exatos_alunos = [a for a in cand_alunos if _norm(a["nome"]) == alvo]
        exatos_resp = [r for r in cand_resp if _norm(r["nome"]) == alvo]
        if len(exatos_alunos) + len(exatos_resp) == 1:
            if exatos_alunos:
                return None, exatos_alunos, "NOME_EXATO"
            return exatos_resp[0], _alunos_do_responsavel(supabase, exatos_resp[0]["id"]), "NOME_EXATO"

        if cand_alunos:
            return None, cand_alunos, "NOME_PARCIAL"
        if cand_resp:
            return cand_resp[0], _alunos_do_responsavel(supabase, cand_resp[0]["id"]), "NOME_PARCIAL"

    return None, [], None


def _restante(m: Dict[str, Any]) -> float:
    return round(max(0.0, float(m.get("valor_devido") or 0) - float(m.get("valor_pago") or 0)), 2)


def analisar_conciliacao(supabase, comprovante: Dict[str, Any], dados: ComprovantePixSchema) -> Dict[str, Any]:
    """
    Decide o que fazer com o comprovante, SEM gravar nada.
    status: CONCILIADO (baixa automática) | REVISAO_MANUAL | DUPLICADO
    """
    plano: Dict[str, Any] = {
        "status": "REVISAO_MANUAL", "status_conciliacao": "REVISAO_MANUAL", "motivo": None, "mensagem": "",
        "aluno_id": None, "responsavel_id": None, "nome_identificado": None, "mensalidades": [],
    }

    def revisao(codigo: str, motivo: str):
        plano.update(status="REVISAO_MANUAL", status_conciliacao=codigo, motivo=motivo, mensagem=f"{motivo} Enviado para revisão manual.")
        return plano

    # 1. Mesmo Pix já usado antes?
    e2e = dados.id_transacao_e2e
    if e2e:
        ja_nas_mensalidades = supabase.table("mensalidades").select("id").eq("id_transacao_pix", e2e).limit(1).execute().data
        outros = supabase.table("comprovantes").select("id, status").eq("id_transacao_pix", e2e).in_("status", ["CONCILIADO", "RESOLVIDO"]).execute().data or []
        if ja_nas_mensalidades or any(o["id"] != comprovante.get("id") for o in outros):
            plano.update(status="DUPLICADO", status_conciliacao="COMPROVANTE_DUPLICADO",
                         motivo="Este comprovante (mesmo código Pix) já foi processado antes.",
                         mensagem="Este comprovante (mesmo código Pix) já foi processado antes.")
            return plano

    # 2. Quem pagou?
    responsavel, alunos, origem = _identificar(supabase, comprovante.get("telefone_remetente"), dados.pagador_nome)
    if responsavel:
        plano["responsavel_id"] = responsavel["id"]
        plano["nome_identificado"] = responsavel.get("nome")
    if len(alunos) == 1:
        plano["aluno_id"] = alunos[0]["id"]
    if alunos and not plano["nome_identificado"]:
        plano["nome_identificado"] = alunos[0]["nome"]

    if not alunos:
        return revisao("ALUNO_NAO_ENCONTRADO",
                       f"Não foi possível identificar o aluno ou responsável (pagador: '{dados.pagador_nome or 'não identificado'}').")
    if origem == "NOME_PARCIAL" or (origem == "NOME_EXATO" and len(alunos) != 1 and not responsavel):
        return revisao("IDENTIFICACAO_INCERTA", "Só o primeiro nome do pagador bate com os cadastros; confirme quem é.")
    if (dados.confianca_leitura or "").upper() == "BAIXA":
        return revisao("LEITURA_BAIXA_CONFIANCA", "A leitura do comprovante teve baixa confiança; confira os dados.")

    # 3. Mensalidades em aberto até o mês atual (adiantamentos só pela revisão manual)
    ids_alunos = [a["id"] for a in alunos]
    abertas = supabase.table("mensalidades").select("*").in_("aluno_id", ids_alunos).in_("status", list(STATUS_ABERTOS))\
        .lte("referencia_mes", mes_atual()).order("referencia_mes").execute().data or []
    abertas = [m for m in abertas if _restante(m) > 0]
    if not abertas:
        return revisao("SEM_MENSALIDADES_PENDENTES", "Nenhuma mensalidade em aberto até o mês atual (pode ser adiantamento).")

    # 4. O valor bate?
    valor = round(float(dados.valor_pago), 2)
    total = round(sum(_restante(m) for m in abertas), 2)
    if abs(valor - total) < 0.01:
        escolhidas = abertas
    else:
        escolhidas = next(([m] for m in abertas if abs(_restante(m) - valor) < 0.01), [])

    if not escolhidas:
        return revisao("VALOR_DIVERGENTE",
                       f"O valor do comprovante (R$ {valor:.2f}) é diferente do valor em aberto (R$ {total:.2f}).".replace(".", ","))

    plano.update(status="CONCILIADO", status_conciliacao="BAIXA_AUTOMATICA_SUCESSO", mensalidades=escolhidas,
                 mensagem=f"Pagamento de R$ {valor:.2f} conciliado e baixado com sucesso!".replace(".", ","))
    return plano


def aplicar_baixa_automatica(supabase, plano: Dict[str, Any], url_comprovante: str, dados: ComprovantePixSchema) -> List[int]:
    agora = datetime.now().isoformat()
    ids = []
    for m in plano["mensalidades"]:
        supabase.table("mensalidades").update({
            "status": "PAGO",
            "valor_pago": float(m["valor_devido"]),
            "data_pagamento": agora,
            "forma_pagamento": "PIX",
            "comprovante_url": url_comprovante,
            "id_transacao_pix": dados.id_transacao_e2e,
            "responsavel_pagador_id": plano["responsavel_id"],
        }).eq("id", m["id"]).execute()
        ids.append(m["id"])
    return ids
