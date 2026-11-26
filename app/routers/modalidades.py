from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from app.core.supabase import get_supabase
from app.services.previsao import sincronizar_seguro

router = APIRouter(prefix="/modalidades", tags=["Modalidades"])


class ModalidadeCreate(BaseModel):
    nome: str
    descricao: Optional[str] = None
    valor_mensalidade: float = Field(..., ge=0)

    @field_validator("nome")
    def validar_nome(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Informe o nome da modalidade.")
        return v


class ModalidadeUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    valor_mensalidade: Optional[float] = Field(None, ge=0)
    ativo: Optional[bool] = None

    @field_validator("nome")
    def validar_nome(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("O nome da modalidade não pode ficar vazio.")
        return v


def _alunos_da_modalidade(supabase, modalidade_id: int):
    linhas = supabase.table("matriculas").select("aluno_id").eq("modalidade_id", modalidade_id).execute().data or []
    return sorted({l["aluno_id"] for l in linhas})


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


@router.patch("/{modalidade_id}")
def atualizar_modalidade(modalidade_id: int, dados: ModalidadeUpdate):
    """
    Edita nome, descrição, valor da mensalidade ou o status (ativo) de uma modalidade.
    Só os campos enviados são alterados. Mudar o valor reajusta as mensalidades PENDENTES
    (mês atual e seguintes) dos alunos da modalidade. Mensalidades já pagas não mudam.
    """
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")

    supabase = get_supabase()
    check = supabase.table("modalidades").select("id").eq("id", modalidade_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Modalidade não encontrada.")

    res = supabase.table("modalidades").update(campos).eq("id", modalidade_id).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Não foi possível atualizar a modalidade.")

    # Preço novo ou modalidade desativada: reajusta as mensalidades AINDA NÃO PAGAS dos alunos dela
    if "valor_mensalidade" in campos or "ativo" in campos:
        sincronizar_seguro(supabase, _alunos_da_modalidade(supabase, modalidade_id))
    return {"message": "Modalidade atualizada com sucesso!", "modalidade": res.data[0]}


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
        sincronizar_seguro(supabase, _alunos_da_modalidade(supabase, modalidade_id))
        return {"message": f"Modalidade '{nome}' desativada com sucesso."}
