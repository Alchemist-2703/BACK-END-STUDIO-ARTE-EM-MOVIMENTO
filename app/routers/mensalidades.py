from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator
from app.core.supabase import get_supabase

router = APIRouter(prefix="/mensalidades", tags=["Mensalidades & Inadimplência"])

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
    status: Optional[str] = Query(None, description="PENDENTE, PAGO, ATRASADO"),
    referencia_mes: Optional[str] = Query(None, description="Ex: 2026-11")
):
    """Consulta o mapa de mensalidades com dados do aluno associado."""
    supabase = get_supabase()
    query = supabase.table("mensalidades").select("*, aluno:alunos(nome, telefone)")
    if status:
        query = query.eq("status", status)
    if referencia_mes:
        query = query.eq("referencia_mes", referencia_mes)
    res = query.order("data_vencimento").execute()
    return res.data

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
    """Permite dar baixa manual no balcão da academia/studio."""
    supabase = get_supabase()
    from datetime import datetime
    res = supabase.table("mensalidades").update({
        "status": "PAGO",
        "valor_pago": valor_pago,
        "data_pagamento": datetime.now().isoformat(),
        "forma_pagamento": forma_pagamento
    }).eq("id", mensalidade_id).execute()
    return {"message": "Baixa realizada com sucesso!"}
