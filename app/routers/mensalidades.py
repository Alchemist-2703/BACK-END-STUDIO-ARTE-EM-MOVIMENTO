from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator
from app.core.supabase import get_supabase
from app.services.previsao import garantir_previsao, situacao_mensalidade, sincronizar_previsao

router = APIRouter(prefix="/mensalidades", tags=["Mensalidades & Inadimplência"])

FORMAS_PAGAMENTO = {"PIX", "DINHEIRO", "CARTAO", "BOLETO"}

def converter_para_data_iso(valor: Optional[str]) -> Optional[str]:
    if not valor or not valor.strip():
        return None
    valor = valor.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Formato de data inválido: '{valor}'. Use 'DD/MM/AAAA' ou 'AAAA-MM-DD'.")

class MensalidadeCreate(BaseModel):
    aluno_id: int
    referencia_mes: str # Formato 'YYYY-MM'
    valor_devido: float
    data_vencimento: str

    @field_validator("data_vencimento", mode="before")
    def validar_vencimento(cls, v):
        return converter_para_data_iso(v)

@router.get("/")
def listar_mensalidades(
    status: Optional[str] = Query(None, description="PENDENTE, PAGO, ATRASADO, NULO"),
    referencia_mes: Optional[str] = Query(None, description="Ex: 2026-11"),
    aluno_id: Optional[int] = Query(None, description="Só as mensalidades deste aluno")
):
    """
    Consulta o mapa de mensalidades com dados do aluno.
    Cada item traz 'situacao': PAGO, PENDENTE ou ATRASADO (pendente a partir do dia 15 do mês de referência).
    """
    supabase = get_supabase()
    garantir_previsao(supabase)  # garante as mensalidades previstas do mês atual e dos próximos (no máx. 1x por hora)

    query = supabase.table("mensalidades").select("*, aluno:alunos(nome, telefone)")
    if status:
        query = query.eq("status", status)
    if referencia_mes:
        query = query.eq("referencia_mes", referencia_mes)
    if aluno_id:
        query = query.eq("aluno_id", aluno_id)
    linhas = query.order("data_vencimento").execute().data or []
    for m in linhas:
        m["situacao"] = situacao_mensalidade(m)
        m["valor_restante"] = round(max(0.0, float(m.get("valor_devido") or 0) - float(m.get("valor_pago") or 0)), 2)
    return linhas

@router.post("/gerar-previsao")
def gerar_previsao():
    """Força a criação/ajuste das mensalidades PENDENTES do mês atual e dos próximos meses para todos os alunos ativos."""
    try:
        resultado = sincronizar_previsao(get_supabase())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Não foi possível gerar a previsão: {e}")
    return {"message": "Previsão de mensalidades atualizada.", **resultado}

@router.post("/", status_code=201)
def gerar_mensalidade(dados: MensalidadeCreate):
    """Gera uma nova mensalidade / fatura para cobrança."""
    supabase = get_supabase()
    res = supabase.table("mensalidades").insert(dados.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao gerar fatura.")
    return {"message": "Mensalidade gerada com sucesso!", "mensalidade": res.data[0]}

@router.patch("/{mensalidade_id}/baixa-manual")
def dar_baixa_manual(mensalidade_id: int, valor_pago: float, forma_pagamento: str = "DINHEIRO"):
    """
    Dá baixa manual em qualquer mensalidade que ainda não esteja paga (inclusive as dos meses seguintes).
    'valor_pago' é o que está sendo recebido agora; se já havia um pagamento parcial, os dois se somam.
    """
    forma = (forma_pagamento or "").strip().upper()
    if forma not in FORMAS_PAGAMENTO:
        raise HTTPException(status_code=400, detail=f"Forma de pagamento inválida. Use: {', '.join(sorted(FORMAS_PAGAMENTO))}.")
    if valor_pago <= 0:
        raise HTTPException(status_code=400, detail="Informe um valor pago maior que zero.")

    supabase = get_supabase()
    atual = supabase.table("mensalidades").select("id, status, valor_pago").eq("id", mensalidade_id).execute()
    if not atual.data:
        raise HTTPException(status_code=404, detail="Mensalidade não encontrada.")
    status = (atual.data[0].get("status") or "").upper()
    ja_recebido = float(atual.data[0].get("valor_pago") or 0)   # pagamento parcial anterior soma com este
    if status == "PAGO":
        raise HTTPException(status_code=409, detail="Esta mensalidade já está paga.")
    if status == "CANCELADO":
        raise HTTPException(status_code=400, detail="Esta mensalidade foi cancelada.")
    if status == "NULO":
        raise HTTPException(status_code=400, detail="Esta mensalidade está marcada como nula. Reative a cobrança antes de dar baixa.")

    res = supabase.table("mensalidades").update({
        "status": "PAGO",
        "valor_pago": round(ja_recebido + valor_pago, 2),
        "data_pagamento": datetime.now().isoformat(),
        "forma_pagamento": forma
    }).eq("id", mensalidade_id).execute()
    return {"message": "Baixa realizada com sucesso!", "mensalidade": res.data[0] if res.data else None}


class NuloEntrada(BaseModel):
    motivo: Optional[str] = None


@router.patch("/{mensalidade_id}/nulo")
def marcar_como_nula(mensalidade_id: int, dados: NuloEntrada):
    """
    Marca uma mensalidade em aberto como NULA: ela não entra na previsão nem nos recebimentos e não conta como
    pendente/atrasada. Útil, por exemplo, quando o aluno novo só começa a pagar no mês seguinte.
    """
    supabase = get_supabase()
    atual = supabase.table("mensalidades").select("id, status, valor_pago, observacoes").eq("id", mensalidade_id).execute()
    if not atual.data:
        raise HTTPException(status_code=404, detail="Mensalidade não encontrada.")
    m = atual.data[0]
    if (m.get("status") or "").upper() not in ("PENDENTE", "ATRASADO"):
        raise HTTPException(status_code=409, detail="Só mensalidades em aberto (pendentes ou atrasadas) podem ser marcadas como nulas.")
    if float(m.get("valor_pago") or 0) > 0:
        raise HTTPException(status_code=409, detail="Esta mensalidade já recebeu pagamento parcial; conclua a baixa em vez de anulá-la.")

    motivo = (dados.motivo or "").strip()
    nota = f"Marcada como nula: {motivo}" if motivo else "Marcada como nula."
    obs = f"{m['observacoes']}\n{nota}" if m.get("observacoes") else nota
    supabase.table("mensalidades").update({"status": "NULO", "observacoes": obs}).eq("id", mensalidade_id).execute()
    return {"message": "Mensalidade marcada como nula."}


@router.patch("/{mensalidade_id}/reativar-cobranca")
def reativar_cobranca(mensalidade_id: int):
    """Desfaz o estado NULO: a mensalidade volta a ser cobrada (pendente)."""
    supabase = get_supabase()
    atual = supabase.table("mensalidades").select("id, status").eq("id", mensalidade_id).execute()
    if not atual.data:
        raise HTTPException(status_code=404, detail="Mensalidade não encontrada.")
    if (atual.data[0].get("status") or "").upper() != "NULO":
        raise HTTPException(status_code=409, detail="Esta mensalidade não está marcada como nula.")
    supabase.table("mensalidades").update({"status": "PENDENTE"}).eq("id", mensalidade_id).execute()
    return {"message": "Cobrança reativada: a mensalidade voltou a ficar pendente."}
