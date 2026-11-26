from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, date
from typing import Optional, Dict, Any
from app.core.supabase import get_supabase
from app.services.previsao import esta_atrasada, garantir_previsao

router = APIRouter(prefix="/dashboard", tags=["Dashboard Financeiro"])

@router.get("/resumo")
def obter_resumo_financeiro(
    mes_referencia: Optional[str] = Query(
        None, 
        description="Mês no formato 'YYYY-MM'. Se não informado, usa o mês atual (ex: 2026-11)"
    )
) -> Dict[str, Any]:
    """
    Retorna o painel financeiro consolidado do Studio Arte em Movimento:
    - Faturamento previsto, realizado e em atraso
    - Despesas operacionais e contas a pagar
    - Saldo líquido (lucro operacional)
    - Taxa de inadimplência e total de alunos
    """
    supabase = get_supabase()
    hoje = date.today()
    
    # Se o mês não for passado, assume o mês corrente
    if not mes_referencia:
        mes_referencia = hoje.strftime("%Y-%m")

    # Garante que os alunos ativos tenham as mensalidades previstas (mês atual e seguintes)
    garantir_previsao(supabase)

    # 1. Busca todas as mensalidades daquele mês
    mensalidades_res = supabase.table("mensalidades")\
        .select("id, aluno_id, referencia_mes, valor_devido, valor_pago, status, data_vencimento")\
        .eq("referencia_mes", mes_referencia)\
        .execute()
    
    mensalidades = mensalidades_res.data or []

    # 2. Busca as despesas cujo vencimento cai no mês selecionado
    # Ex: data_vencimento >= 2026-11-01 e <= 2026-11-31
    # (usa "primeiro dia do mês seguinte" como limite, porque "31" não existe em todos os meses)
    try:
        ano_ref, mes_ref = int(mes_referencia[:4]), int(mes_referencia[5:7])
        proximo_mes = date(ano_ref + (1 if mes_ref == 12 else 0), 1 if mes_ref == 12 else mes_ref + 1, 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Mês inválido. Use o formato AAAA-MM (ex: 2026-11).")
    inicio_mes = f"{mes_referencia}-01"

    despesas_res = supabase.table("despesas")\
        .select("id, valor, status, data_vencimento")\
        .gte("data_vencimento", inicio_mes)\
        .lt("data_vencimento", proximo_mes.isoformat())\
        .execute()
    
    despesas = despesas_res.data or []

    # 3. Total de alunos ativos no studio
    alunos_res = supabase.table("alunos").select("id", count="exact").eq("ativo", True).execute()
    total_alunos_ativos = alunos_res.count if alunos_res.count is not None else len(alunos_res.data or [])

    # =========================================================================
    # CÁLCULOS DE RECEITA (MENSALIDADES)
    # =========================================================================
    total_receita_prevista = 0.0
    total_receita_realizada = 0.0
    total_pendente = 0.0
    total_inadimplente = 0.0
    
    alunos_inadimplentes_set = set()

    for m in mensalidades:
        v_devido = float(m["valor_devido"] or 0)
        v_pago = float(m["valor_pago"] or 0)
        status = m.get("status", "").upper()
        if status == "CANCELADO":
            continue

        total_receita_prevista += v_devido

        if status == "PAGO":
            total_receita_realizada += v_pago if v_pago > 0 else v_devido
        elif esta_atrasada(m, hoje):  # pendente a partir do dia 15 do mês de referência
            total_inadimplente += v_devido
            alunos_inadimplentes_set.add(m["aluno_id"])
        else: # PENDENTE e dentro do prazo
            total_pendente += v_devido

    qtd_mensalidades = len(mensalidades)
    taxa_inadimplencia = 0.0
    if qtd_mensalidades > 0:
        taxa_inadimplencia = round((len(alunos_inadimplentes_set) / qtd_mensalidades) * 100, 1)

    # =========================================================================
    # CÁLCULOS DE DESPESAS (SAÍDAS)
    # =========================================================================
    total_despesas_previstas = 0.0
    total_despesas_pagas = 0.0
    total_despesas_pendentes = 0.0

    for d in despesas:
        val = float(d["valor"] or 0)
        st = d.get("status", "").upper()
        total_despesas_previstas += val
        
        if st == "PAGO":
            total_despesas_pagas += val
        else:
            total_despesas_pendentes += val

    # =========================================================================
    # FLUXO DE CAIXA E RESULTADO
    # =========================================================================
    saldo_caixa_real = round(total_receita_realizada - total_despesas_pagas, 2)
    saldo_projetado = round(total_receita_prevista - total_despesas_previstas, 2)

    return {
        "studio": "Studio Arte em Movimento",
        "mes_referencia": mes_referencia,
        "alunos_ativos": total_alunos_ativos,
        "receitas": {
            "total_previsto": round(total_receita_prevista, 2),
            "total_recebido": round(total_receita_realizada, 2),
            "total_a_vencer": round(total_pendente, 2),
            "total_atrasado_inadimplente": round(total_inadimplente, 2),
            "taxa_inadimplencia_percentual": f"{taxa_inadimplencia}%",
            "qtd_alunos_inadimplentes": len(alunos_inadimplentes_set)
        },
        "despesas": {
            "total_previsto": round(total_despesas_previstas, 2),
            "total_pago": round(total_despesas_pagas, 2),
            "total_pendente": round(total_despesas_pendentes, 2)
        },
        "resultado_financeiro": {
            "saldo_em_caixa_atual": saldo_caixa_real,
            "saldo_projetado_fim_do_mes": saldo_projetado,
            "situacao": "LUCRO" if saldo_caixa_real >= 0 else "PREJUIZO"
        }
    }
