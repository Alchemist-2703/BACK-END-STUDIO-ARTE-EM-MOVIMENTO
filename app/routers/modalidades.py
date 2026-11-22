from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
from app.core.supabase import get_supabase

router = APIRouter(prefix="/modalidades", tags=["Modalidades"])

class ModalidadeCreate(BaseModel):
    nome: str
    descricao: Optional[str] = None
    valor_mensalidade: float

@router.get("/")
def listar_modalidades(ativo: bool = True):
    """Lista as modalidades oferecidas (ex: Muay Thai, Jiu-Jitsu, Pilates, Dança)."""
    supabase = get_supabase()
    res = supabase.table("modalidades").select("*").eq("ativo", ativo).order("nome").execute()
    return res.data

@router.post("/", status_code=201)
def cadastrar_modalidade(dados: ModalidadeCreate):
    """Cadastra uma nova modalidade com seu valor base."""
    supabase = get_supabase()
    res = supabase.table("modalidades").insert(dados.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao criar modalidade.")
    return {"message": "Modalidade criada com sucesso!", "modalidade": res.data[0]}

@router.delete("/{modalidade_id}")
def excluir_modalidade(modalidade_id: int, permanente: bool = False):
    """
    Exclui ou desativa uma modalidade.
    - permanente=False (padrão): Desativa (não aparece em novas matrículas).
    - permanente=True: Remove definitivamente do banco (apenas se não houver matrículas atreladas).
    """
    supabase = get_supabase()
    
    check = supabase.table("modalidades").select("id, nome").eq("id", modalidade_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Modalidade não encontrada.")
    
    nome = check.data[0]["nome"]

    if permanente:
        try:
            supabase.table("modalidades").delete().eq("id", modalidade_id).execute()
        except Exception as e:
            raise HTTPException(
                status_code=400, 
                detail=f"Não foi possível remover a modalidade permanentemente pois existem alunos matriculados nela. Use permanente=false para desativá-la. Erro: {str(e)}"
            )
        return {"message": f"Modalidade '{nome}' excluída permanentemente."}
    else:
        supabase.table("modalidades").update({"ativo": False}).eq("id", modalidade_id).execute()
        return {"message": f"Modalidade '{nome}' desativada com sucesso."}

