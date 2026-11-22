from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator
from app.core.supabase import get_supabase

router = APIRouter(prefix="/despesas", tags=["Contas a Pagar / Despesas"])

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

class DespesaCreate(BaseModel):
    descricao: str
    categoria: str # 'Aluguel', 'Energia', 'Água', 'Limpeza', 'Professores', etc.
    valor: float
    data_vencimento: str
    observacoes: Optional[str] = None

    @field_validator("data_vencimento", mode="before")
    def validar_vencimento(cls, v):
        return converter_para_data_iso(v)

@router.get("/")
def listar_despesas(status: Optional[str] = None):
    """Lista as contas a pagar e custos operacionais do studio."""
    supabase = get_supabase()
    query = supabase.table("despesas").select("*")
    if status:
        query = query.eq("status", status)
    res = query.order("data_vencimento").execute()
    return res.data

@router.post("/", status_code=201)
def cadastrar_despesa(dados: DespesaCreate):
    """Registra uma conta a pagar (aluguel, água, luz, etc.)."""
    supabase = get_supabase()
    res = supabase.table("despesas").insert(dados.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao registrar despesa.")
    return {"message": "Despesa registrada com sucesso!", "despesa": res.data[0]}

@router.patch("/{despesa_id}/pagar")
def pagar_despesa(despesa_id: int, forma_pagamento: str = "PIX"):
    """Marca uma despesa como paga."""
    supabase = get_supabase()
    from datetime import datetime
    supabase.table("despesas").update({
        "status": "PAGO",
        "data_pagamento": datetime.now().isoformat(),
        "forma_pagamento": forma_pagamento
    }).eq("id", despesa_id).execute()
    return {"message": "Despesa quitada com sucesso!"}

@router.delete("/{despesa_id}")
def excluir_despesa(despesa_id: int):
    """Exclui um registro de despesa lançado incorretamente."""
    supabase = get_supabase()
    check = supabase.table("despesas").select("id, descricao").eq("id", despesa_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Despesa não encontrada.")
    
    supabase.table("despesas").delete().eq("id", despesa_id).execute()
    return {"message": f"Despesa '{check.data[0]['descricao']}' excluída com sucesso."}

