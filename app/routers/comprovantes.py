from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.supabase import get_supabase
from app.services import armazenamento
from app.services.fila import STATUS_EM_REVISAO, acordar

router = APIRouter(prefix="/comprovantes", tags=["Comprovantes & Revisão Manual"])

FORMAS_PAGAMENTO = {"PIX", "DINHEIRO", "CARTAO", "BOLETO"}


def _brl(valor: float) -> str:
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _restante(m: Dict[str, Any]) -> float:
    return round(max(0.0, float(m.get("valor_devido") or 0) - float(m.get("valor_pago") or 0)), 2)


def _obter_comprovante(supabase, comprovante_id: int) -> Dict[str, Any]:
    res = supabase.table("comprovantes").select("*").eq("id", comprovante_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Comprovante não encontrado.")
    return res.data[0]


def _exigir_em_revisao(comp: Dict[str, Any]) -> None:
    if comp["status"] not in STATUS_EM_REVISAO:
        raise HTTPException(status_code=409, detail="Este comprovante já foi resolvido.")


class AlocacaoEntrada(BaseModel):
    mensalidade_id: int
    valor: float = Field(..., gt=0)
    modalidade_id: Optional[int] = None   # só informativo: a qual modalidade o pagamento se refere


class BaixaRevisao(BaseModel):
    aluno_id: int
    valor_comprovante: Optional[float] = Field(None, gt=0)
    forma_pagamento: str = "PIX"
    descricao: Optional[str] = None
    alocacoes: List[AlocacaoEntrada] = Field(..., min_length=1)


class DescarteEntrada(BaseModel):
    descricao: Optional[str] = None


@router.get("/resumo")
def resumo():
    """Números para o botão 'Revisar transações': quantas esperam revisão e quantas estão na fila de leitura."""
    supabase = get_supabase()
    linhas = supabase.table("comprovantes").select("id, status")\
        .in_("status", list(STATUS_EM_REVISAO) + ["NA_FILA", "PROCESSANDO"]).execute().data or []
    return {
        "revisao": sum(1 for l in linhas if l["status"] in STATUS_EM_REVISAO),
        "na_fila": sum(1 for l in linhas if l["status"] in ("NA_FILA", "PROCESSANDO")),
    }


@router.get("/")
def listar(status: str = "revisao"):
    """Lista os comprovantes que aguardam revisão manual (status=revisao) ou leitura (status=fila)."""
    grupos = {"revisao": list(STATUS_EM_REVISAO), "fila": ["NA_FILA", "PROCESSANDO"]}
    if status not in grupos:
        raise HTTPException(status_code=400, detail="Use status=revisao ou status=fila.")
    supabase = get_supabase()
    return supabase.table("comprovantes").select("*, aluno:alunos(nome)")\
        .in_("status", grupos[status]).order("criado_em").execute().data or []


@router.get("/aluno/{aluno_id}/abertas")
def mensalidades_abertas(aluno_id: int):
    """Mensalidades que ainda podem receber pagamento (mês atual, atrasadas e futuras) + modalidades do aluno."""
    supabase = get_supabase()
    aluno = supabase.table("alunos").select(
        "id, nome, matriculas(ativo, valor_acordado, modalidade:modalidades(id, nome, valor_mensalidade, ativo))"
    ).eq("id", aluno_id).execute().data
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")

    from app.services.previsao import situacao_mensalidade
    linhas = supabase.table("mensalidades").select("*").eq("aluno_id", aluno_id)\
        .in_("status", ["PENDENTE", "ATRASADO"]).order("referencia_mes").execute().data or []
    mensalidades = [
        {"id": m["id"], "referencia_mes": m["referencia_mes"], "valor_devido": float(m["valor_devido"] or 0),
         "valor_pago": float(m.get("valor_pago") or 0), "restante": _restante(m), "situacao": situacao_mensalidade(m)}
        for m in linhas if _restante(m) > 0
    ]

    modalidades = []
    for mat in aluno[0].get("matriculas") or []:
        mod = mat.get("modalidade") or {}
        if mat.get("ativo") is False or mod.get("ativo") is False or not mod:
            continue
        valor = mat.get("valor_acordado")
        modalidades.append({"id": mod["id"], "nome": mod["nome"],
                            "valor": float(valor if valor is not None else mod.get("valor_mensalidade") or 0)})
    return {"aluno": {"id": aluno[0]["id"], "nome": aluno[0]["nome"]}, "mensalidades": mensalidades, "modalidades": modalidades}


@router.post("/{comprovante_id}/baixa")
def baixa_por_revisao(comprovante_id: int, dados: BaixaRevisao):
    """
    Resolve um comprovante em revisão: distribui o valor pelas mensalidades escolhidas (inclusive meses futuros,
    no caso de adiantamento). A mensalidade só vira PAGO quando o que falta é totalmente coberto;
    se for menos, ela continua em aberto com o pagamento parcial registrado.
    """
    supabase = get_supabase()
    comp = _obter_comprovante(supabase, comprovante_id)
    _exigir_em_revisao(comp)

    forma = (dados.forma_pagamento or "").strip().upper()
    if forma not in FORMAS_PAGAMENTO:
        raise HTTPException(status_code=400, detail=f"Forma de pagamento inválida. Use: {', '.join(sorted(FORMAS_PAGAMENTO))}.")

    valor_comp = dados.valor_comprovante if dados.valor_comprovante is not None else comp.get("valor_pago")
    if valor_comp is None or float(valor_comp) <= 0:
        raise HTTPException(status_code=400, detail="Informe o valor do comprovante.")
    valor_comp = round(float(valor_comp), 2)

    aluno = supabase.table("alunos").select("id, nome").eq("id", dados.aluno_id).execute().data
    if not aluno:
        raise HTTPException(status_code=400, detail="Aluno não encontrado.")

    ids = [a.mensalidade_id for a in dados.alocacoes]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="A mesma mensalidade foi escolhida mais de uma vez.")
    mensalidades = {m["id"]: m for m in (supabase.table("mensalidades").select("*").in_("id", ids).execute().data or [])}

    soma = 0.0
    for a in dados.alocacoes:
        m = mensalidades.get(a.mensalidade_id)
        if not m or m["aluno_id"] != dados.aluno_id:
            raise HTTPException(status_code=400, detail="Uma das mensalidades escolhidas não pertence a este aluno.")
        if m["status"] not in ("PENDENTE", "ATRASADO"):
            raise HTTPException(status_code=400, detail=f"A mensalidade de {m['referencia_mes']} não está em aberto.")
        if a.valor > _restante(m) + 0.005:
            raise HTTPException(status_code=400, detail=(
                f"O valor para {m['referencia_mes']} (R$ {_brl(a.valor)}) passa do que falta pagar (R$ {_brl(_restante(m))})."))
        soma += a.valor
    soma = round(soma, 2)

    if soma > valor_comp + 0.005:
        raise HTTPException(status_code=400, detail=(
            f"A soma distribuída (R$ {_brl(soma)}) é maior que o valor do comprovante (R$ {_brl(valor_comp)})."))
    saldo = round(valor_comp - soma, 2)
    descricao = (dados.descricao or "").strip()
    if abs(saldo) > 0.005 and not descricao:
        raise HTTPException(status_code=400, detail=(
            f"Explique na descrição por que o comprovante (R$ {_brl(valor_comp)}) difere do total aplicado (R$ {_brl(soma)})."))

    # Nome do arquivo no Storage: quem enviou + mês do pagamento
    caminho, url = comp.get("arquivo_path"), comp.get("arquivo_url")
    try:
        nome = aluno[0]["nome"]
        if comp.get("responsavel_id"):
            r = supabase.table("responsaveis").select("nome").eq("id", comp["responsavel_id"]).execute().data
            nome = r[0]["nome"] if r else nome
        criado = comp.get("criado_em")
        padrao = date.fromisoformat(str(criado)[:10]) if criado else date.today()
        data_ref = armazenamento.data_do_comprovante((comp.get("dados_extraidos") or {}).get("data_hora"), padrao)
        novo = armazenamento.caminho_amigavel(nome, data_ref, comp["id"], comp.get("mime_type") or "image/jpeg")
        if caminho and armazenamento.mover(supabase, caminho, novo):
            caminho, url = novo, armazenamento.url_publica(supabase, novo)
    except Exception as e:  # noqa: BLE001
        print(f"[revisao] não consegui renomear o comprovante {comp['id']}: {e}")

    agora = datetime.now().isoformat()
    nota = f"Baixa manual (comprovante #{comp['id']}, R$ {_brl(valor_comp)})" + (f": {descricao}" if descricao else "")
    registro, quitadas = [], 0
    for a in dados.alocacoes:
        m = mensalidades[a.mensalidade_id]
        novo_pago = round(float(m.get("valor_pago") or 0) + a.valor, 2)
        quitada = novo_pago >= float(m["valor_devido"]) - 0.005
        campos = {
            "valor_pago": novo_pago, "forma_pagamento": forma, "comprovante_url": url,
            "id_transacao_pix": comp.get("id_transacao_pix"),
            "responsavel_pagador_id": comp.get("responsavel_id"),
            "observacoes": f"{m['observacoes']}\n{nota}" if m.get("observacoes") else nota,
        }
        if quitada:
            campos.update(status="PAGO", data_pagamento=agora)
            quitadas += 1
        supabase.table("mensalidades").update(campos).eq("id", m["id"]).execute()
        registro.append({"mensalidade_id": m["id"], "referencia_mes": m["referencia_mes"],
                         "modalidade_id": a.modalidade_id, "valor": a.valor, "quitada": quitada})

    supabase.table("comprovantes").update({
        "status": "RESOLVIDO", "aluno_id": dados.aluno_id, "valor_pago": valor_comp,
        "descricao_revisao": descricao or None, "alocacoes": registro, "revisado_em": agora,
        "arquivo_path": caminho, "arquivo_url": url,
    }).eq("id", comp["id"]).execute()

    return {"message": "Baixa realizada com sucesso!", "mensalidades_atualizadas": len(registro),
            "quitadas": quitadas, "saldo_nao_aplicado": saldo}


@router.post("/{comprovante_id}/descartar")
def descartar(comprovante_id: int, dados: DescarteEntrada):
    """Tira da revisão um comprovante que não é pagamento de mensalidade (ou foi enviado por engano)."""
    supabase = get_supabase()
    comp = _obter_comprovante(supabase, comprovante_id)
    _exigir_em_revisao(comp)
    supabase.table("comprovantes").update({
        "status": "DESCARTADO", "descricao_revisao": (dados.descricao or "").strip() or None,
        "revisado_em": datetime.now().isoformat(),
    }).eq("id", comprovante_id).execute()
    return {"message": "Comprovante descartado."}


@router.post("/{comprovante_id}/reprocessar")
def reprocessar(comprovante_id: int):
    """Manda o comprovante de volta para a fila para a IA tentar ler de novo."""
    supabase = get_supabase()
    comp = _obter_comprovante(supabase, comprovante_id)
    _exigir_em_revisao(comp)
    supabase.table("comprovantes").update({
        "status": "NA_FILA", "tentativas": 0, "erro": None, "motivo": None,
        "proxima_tentativa": datetime.now(timezone.utc).isoformat(),
    }).eq("id", comprovante_id).execute()
    acordar()
    return {"message": "Comprovante voltou para a fila de leitura."}
