from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, Any, List
from app.core.supabase import get_supabase
from app.core.config import settings

router = APIRouter(prefix="/admin", tags=["Administração & Limpeza de Dados"])

class ResetConfirmacao(BaseModel):
    confirmacao_texto: str  # Exige que digite "RESETAR_DADOS_STUDIO" para segurança
    limpar_apenas_financeiro: bool = False # Se True, limpa só mensalidades e despesas, mantendo cadastros de alunos

@router.post("/reset-database")
def resetar_banco_de_dados(dados: ResetConfirmacao) -> Dict[str, Any]:
    """
    ATENÇÃO: Rota de limpeza para quando você for entregar o projeto ao Studio Arte em Movimento.
    - Exige no corpo da requisição: {"confirmacao_texto": "RESETAR_DADOS_STUDIO"}
    - limpar_apenas_financeiro=True: apaga mensalidades e despesas de teste (mantém alunos e modalidades).
    - limpar_apenas_financeiro=False: limpa TUDO (mensalidades, matrículas, alunos, responsáveis e despesas).
    """
    if not settings.ALLOW_DB_RESET:
        raise HTTPException(
            status_code=403,
            detail="Rota desativada. Para usá-la, ligue ALLOW_DB_RESET=true no servidor (e desligue depois)."
        )

    if dados.confirmacao_texto != "RESETAR_DADOS_STUDIO":
        raise HTTPException(
            status_code=400,
            detail="Ação não autorizada. Para confirmar a limpeza, envie exatamente: 'RESETAR_DADOS_STUDIO'"
        )

    supabase = get_supabase()
    tabelas_limpas: List[str] = []

    try:
        # 1. Limpa faturas/mensalidades (tabela filha)
        # O supabase-py não permite .delete() sem filtro, usamos .neq('id', 0) ou .gte('id', 1)
        supabase.table("mensalidades").delete().gte("id", 0).execute()
        tabelas_limpas.append("mensalidades")

        # 2. Limpa despesas
        supabase.table("despesas").delete().gte("id", 0).execute()
        tabelas_limpas.append("despesas")

        # Se for limpeza total (entrega limpa)
        if not dados.limpar_apenas_financeiro:
            # 3. Limpa matrículas (N:N alunos x modalidades)
            supabase.table("matriculas").delete().gte("id", 0).execute()
            tabelas_limpas.append("matriculas")

            # 4. Limpa vínculos alunos x responsáveis
            supabase.table("aluno_responsaveis").delete().gte("id", 0).execute()
            tabelas_limpas.append("aluno_responsaveis")

            # 5. Limpa alunos
            supabase.table("alunos").delete().gte("id", 0).execute()
            tabelas_limpas.append("alunos")

            # 6. Limpa responsáveis
            supabase.table("responsaveis").delete().gte("id", 0).execute()
            tabelas_limpas.append("responsaveis")

            # Nota: Modalidades normalmente são mantidas, mas se quiser limpar também:
            # supabase.table("modalidades").delete().gte("id", 0).execute()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro durante a limpeza do banco: {str(e)}")

    return {
        "status": "SUCESSO",
        "mensagem": "Banco de dados limpo e preparado com sucesso para o Studio Arte em Movimento!",
        "tabelas_limpas": tabelas_limpas,
        "modo": "LIMPEZA_FINANCEIRA_APENAS" if dados.limpar_apenas_financeiro else "LIMPEZA_TOTAL"
    }
