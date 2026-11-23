import re
from fastapi import APIRouter, HTTPException
from typing import Optional, Literal
from datetime import datetime, date
from pydantic import BaseModel, Field, field_validator
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


def intervalo_do_mes(mes: str):
    """'2026-11' -> ('2026-11-01', '2026-12-01'). Evita datas inválidas como 31 de novembro."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes or ""):
        raise HTTPException(status_code=400, detail="Mês inválido. Use o formato AAAA-MM (ex: 2026-11).")
    ano, m = int(mes[:4]), int(mes[5:7])
    proximo = date(ano + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    return f"{mes}-01", proximo.isoformat()


def _texto_obrigatorio(v: str, mensagem: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(mensagem)
    return v


class DespesaCreate(BaseModel):
    descricao: str
    categoria: str  # 'Aluguel', 'Energia', 'Água', 'Limpeza', 'Professores', etc.
    valor: float = Field(..., gt=0)
    data_vencimento: str
    observacoes: Optional[str] = None

    @field_validator("descricao")
    def validar_descricao(cls, v):
        return _texto_obrigatorio(v, "Informe a descrição da despesa.")

    @field_validator("categoria")
    def validar_categoria(cls, v):
        return _texto_obrigatorio(v, "Informe a categoria da despesa.")

    @field_validator("data_vencimento", mode="before")
    def validar_vencimento(cls, v):
        convertida = converter_para_data_iso(v)
        if not convertida:
            raise ValueError("Informe a data de vencimento.")
        return convertida


class DespesaUpdate(BaseModel):
    descricao: Optional[str] = None
    categoria: Optional[str] = None
    valor: Optional[float] = Field(None, gt=0)
    data_vencimento: Optional[str] = None
    observacoes: Optional[str] = None
    status: Optional[Literal["PENDENTE", "PAGO", "ATRASADO"]] = None
    forma_pagamento: Optional[str] = None

    @field_validator("descricao")
    def validar_descricao(cls, v):
        return v if v is None else _texto_obrigatorio(v, "A descrição não pode ficar vazia.")

    @field_validator("categoria")
    def validar_categoria(cls, v):
        return v if v is None else _texto_obrigatorio(v, "A categoria não pode ficar vazia.")

    @field_validator("data_vencimento", mode="before")
    def validar_vencimento(cls, v):
        if v is None:
            return v
        convertida = converter_para_data_iso(v)
        if not convertida:
            raise ValueError("A data de vencimento não pode ficar vazia.")
        return convertida


@router.get("/")
def listar_despesas(status: Optional[str] = None, mes: Optional[str] = None):
    """Lista as contas a pagar. Filtros opcionais: status e mês de vencimento (AAAA-MM)."""
    supabase = get_supabase()
    query = supabase.table("despesas").select("*")
    if status:
        query = query.eq("status", status)
    if mes:
        inicio, proximo = intervalo_do_mes(mes)
        query = query.gte("data_vencimento", inicio).lt("data_vencimento", proximo)
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
    supabase.table("despesas").update({
        "status": "PAGO",
        "data_pagamento": datetime.now().isoformat(),
        "forma_pagamento": forma_pagamento
    }).eq("id", despesa_id).execute()
    return {"message": "Despesa quitada com sucesso!"}


@router.patch("/{despesa_id}")
def atualizar_despesa(despesa_id: int, dados: DespesaUpdate):
    """
    Edita uma despesa. Só os campos enviados são alterados.
    Ao marcar como PAGO a data do pagamento é registrada; ao voltar para PENDENTE ela é limpa.
    """
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")

    supabase = get_supabase()
    atual = supabase.table("despesas").select("*").eq("id", despesa_id).execute()
    if not atual.data:
        raise HTTPException(status_code=404, detail="Despesa não encontrada.")
    despesa = atual.data[0]

    novo_status = campos.get("status")
    if novo_status == "PAGO" and not despesa.get("data_pagamento"):
        campos["data_pagamento"] = datetime.now().isoformat()
    elif novo_status in ("PENDENTE", "ATRASADO"):
        campos["data_pagamento"] = None
        campos["forma_pagamento"] = None

    res = supabase.table("despesas").update(campos).eq("id", despesa_id).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Não foi possível atualizar a despesa.")
    return {"message": "Despesa atualizada com sucesso!", "despesa": res.data[0]}


@router.delete("/{despesa_id}")
def excluir_despesa(despesa_id: int):
    """Exclui um registro de despesa lançado incorretamente."""
    supabase = get_supabase()
    check = supabase.table("despesas").select("id, descricao").eq("id", despesa_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Despesa não encontrada.")

    supabase.table("despesas").delete().eq("id", despesa_id).execute()
    return {"message": f"Despesa '{check.data[0]['descricao']}' excluída com sucesso."}
