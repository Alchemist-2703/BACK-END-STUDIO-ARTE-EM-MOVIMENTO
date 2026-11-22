from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, field_validator
from app.core.supabase import get_supabase

router = APIRouter(prefix="/alunos", tags=["Alunos & Responsáveis"])

def converter_para_data_iso(valor: Optional[str]) -> Optional[str]:
    """Converte 'DD/MM/AAAA', 'DD-MM-AAAA' ou mantém 'AAAA-MM-DD' para o formato aceito pelo PostgreSQL."""
    if not valor or not valor.strip():
        return None
    valor = valor.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Formato de data inválido: '{valor}'. Use 'DD/MM/AAAA' ou 'AAAA-MM-DD'.")

class ResponsavelCreate(BaseModel):
    nome: str
    telefone: str
    cpf: Optional[str] = None
    email: Optional[str] = None
    observacoes: Optional[str] = None

class AlunoCreate(BaseModel):
    nome: str
    data_nascimento: Optional[str] = None
    telefone: Optional[str] = None
    cpf: Optional[str] = None
    responsavel_ids: Optional[List[int]] = []
    modalidade_ids: Optional[List[int]] = [] # Modalidades em que o aluno será matriculado

    @field_validator("data_nascimento", mode="before")
    def validar_data_nascimento(cls, v):
        return converter_para_data_iso(v)

@router.get("/")
def listar_alunos(ativo: Optional[bool] = True):
    """Lista alunos com seus responsáveis e modalidades matriculadas."""
    supabase = get_supabase()
    query = supabase.table("alunos").select(
        "*, aluno_responsaveis(responsavel:responsaveis(*)), matriculas(modalidade:modalidades(*))"
    )
    if ativo is not None:
        query = query.eq("ativo", ativo)
    res = query.order("nome").execute()
    return res.data

@router.post("/", status_code=201)
def cadastrar_aluno(dados: AlunoCreate):
    """Cadastra um novo aluno e cria as matrículas iniciais nas modalidades escolhidas."""
    supabase = get_supabase()
    aluno_payload = {
        "nome": dados.nome,
        "data_nascimento": dados.data_nascimento,
        "telefone": dados.telefone,
        "cpf": dados.cpf,
        "ativo": True
    }
    try:
        res = supabase.table("alunos").insert(aluno_payload).execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao salvar aluno no banco: {str(e)}")

    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao cadastrar aluno.")
    
    aluno_id = res.data[0]["id"]

    # 1. Vínculo com responsáveis, se houver
    if dados.responsavel_ids:
        vinculos_resp = [
            {"aluno_id": aluno_id, "responsavel_id": r_id} 
            for r_id in dados.responsavel_ids
        ]
        supabase.table("aluno_responsaveis").insert(vinculos_resp).execute()

    # 2. Vínculo com modalidades (Matrículas N:N)
    if dados.modalidade_ids:
        vinculos_mat = [
            {
                "aluno_id": aluno_id, 
                "modalidade_id": m_id,
                "data_inicio": datetime.now().strftime("%Y-%m-%d"),
                "ativo": True
            }
            for m_id in dados.modalidade_ids
        ]
        supabase.table("matriculas").insert(vinculos_mat).execute()

    return {"message": "Aluno cadastrado e matriculado com sucesso!", "aluno": res.data[0]}

@router.post("/responsaveis", status_code=201)
def cadastrar_responsavel(dados: ResponsavelCreate):
    """Cadastra um responsável financeiro (pai, mãe ou tutor)."""
    supabase = get_supabase()
    res = supabase.table("responsaveis").insert(dados.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao cadastrar responsável.")
    return {"message": "Responsável cadastrado com sucesso!", "responsavel": res.data[0]}

@router.delete("/{aluno_id}")
def excluir_aluno(aluno_id: int, permanente: bool = False):
    """Exclui ou desativa um aluno."""
    supabase = get_supabase()
    check = supabase.table("alunos").select("id, nome").eq("id", aluno_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")
    
    nome_aluno = check.data[0]["nome"]

    if permanente:
        supabase.table("alunos").delete().eq("id", aluno_id).execute()
        return {"message": f"Aluno '{nome_aluno}' (ID {aluno_id}) excluído permanentemente do banco."}
    else:
        supabase.table("alunos").update({"ativo": False}).eq("id", aluno_id).execute()
        return {"message": f"Aluno '{nome_aluno}' (ID {aluno_id}) desativado com sucesso."}
