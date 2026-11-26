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
    status: Optional[str] = Query(None, description="PENDENTE, PAGO, ATRASADO"),
    referencia_mes: Optional[str] = Query(None, description="Ex: 2026-11")
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
    linhas = query.order("data_vencimento").execute().data or []
    for m in linhas:
        m["situacao"] = situacao_mensalidade(m)
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
    """
    forma = (forma_pagamento or "").strip().upper()
    if forma not in FORMAS_PAGAMENTO:
        raise HTTPException(status_code=400, detail=f"Forma de pagamento inválida. Use: {', '.join(sorted(FORMAS_PAGAMENTO))}.")
    if valor_pago <= 0:
        raise HTTPException(status_code=400, detail="Informe um valor pago maior que zero.")

    supabase = get_supabase()
    atual = supabase.table("mensalidades").select("id, status").eq("id", mensalidade_id).execute()
    if not atual.data:
        raise HTTPException(status_code=404, detail="Mensalidade não encontrada.")
    status = (atual.data[0].get("status") or "").upper()
    if status == "PAGO":
        raise HTTPException(status_code=409, detail="Esta mensalidade já está paga.")
    if status == "CANCELADO":
        raise HTTPException(status_code=400, detail="Esta mensalidade foi cancelada.")

    res = supabase.table("mensalidades").update({
        "status": "PAGO",
        "valor_pago": valor_pago,
        "data_pagamento": datetime.now().isoformat(),
        "forma_pagamento": forma
    }).eq("id", mensalidade_id).execute()
    return {"message": "Baixa realizada com sucesso!", "mensalidade": res.data[0] if res.data else None}
