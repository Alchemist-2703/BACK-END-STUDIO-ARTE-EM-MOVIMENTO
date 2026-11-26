"""
Previsão automática de mensalidades.

Para cada aluno ativo, o sistema mantém uma mensalidade PENDENTE para o mês atual e para os
próximos meses (MESES_PREVISAO). O valor de cada uma é:

    soma das modalidades do aluno  -  desconto do aluno (só vale com 2+ modalidades)

Sempre que algo muda (matrícula, desconto, preço da modalidade, aluno desativado) as mensalidades
ainda NÃO pagas são ajustadas. Mensalidades já pagas nunca são alteradas.
"""
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.config import settings

_trava = threading.Lock()
_ultima_garantia = 0.0
_INTERVALO_SEG = 3600  # a verificação automática roda no máximo 1 vez por hora

COLUNAS_ALUNO_CALCULO = (
    "id, ativo, desconto_mensal, "
    "matriculas(ativo, valor_acordado, modalidade:modalidades(id, valor_mensalidade, ativo))"
)


# --------------------------------------------------------------------------- meses e atraso
def mes_atual() -> str:
    return date.today().strftime("%Y-%m")


def somar_meses(referencia: str, quantidade: int) -> str:
    ano, mes = int(referencia[:4]), int(referencia[5:7])
    total = ano * 12 + (mes - 1) + quantidade
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def lista_meses(inicio: str, quantidade: int) -> List[str]:
    return [somar_meses(inicio, i) for i in range(quantidade)]


def data_limite(referencia_mes: str) -> str:
    """Dia a partir do qual a mensalidade pendente daquele mês conta como atrasada (padrão: dia 15)."""
    return f"{referencia_mes}-{settings.DIA_LIMITE_ATRASO:02d}"


def esta_atrasada(mensalidade: Dict[str, Any], hoje: Optional[date] = None) -> bool:
    """Atrasada = marcada como ATRASADO, ou PENDENTE e já chegou o dia 15 do mês de referência."""
    hoje = hoje or date.today()
    status = (mensalidade.get("status") or "").upper()
    if status == "ATRASADO":
        return True
    if status != "PENDENTE":
        return False
    try:
        return hoje >= date.fromisoformat(data_limite(mensalidade["referencia_mes"]))
    except (KeyError, ValueError, TypeError):
        return False


def situacao_mensalidade(mensalidade: Dict[str, Any], hoje: Optional[date] = None) -> str:
    status = (mensalidade.get("status") or "").upper()
    if status == "PENDENTE" and esta_atrasada(mensalidade, hoje):
        return "ATRASADO"
    return status


# --------------------------------------------------------------------------- valor do aluno
def _valor_da_matricula(matricula: Dict[str, Any]) -> Optional[float]:
    """Valor que a matrícula soma na mensalidade; None se ela não conta (inativa)."""
    if matricula.get("ativo") is False:
        return None
    modalidade = matricula.get("modalidade") or {}
    if modalidade.get("ativo") is False:
        return None
    valor = matricula.get("valor_acordado")
    if valor is None:
        valor = modalidade.get("valor_mensalidade")
    return float(valor or 0)


def calcular_valor_mensal(aluno: Dict[str, Any]) -> Dict[str, Any]:
    """Soma das modalidades, desconto aplicado (só com 2+ modalidades) e valor final do mês."""
    valores = [v for v in (_valor_da_matricula(m) for m in (aluno.get("matriculas") or [])) if v is not None]
    subtotal = round(sum(valores), 2)
    desconto = float(aluno.get("desconto_mensal") or 0) if len(valores) >= 2 else 0.0
    desconto = round(max(0.0, min(desconto, subtotal)), 2)
    return {
        "qtd_modalidades": len(valores),
        "subtotal_modalidades": subtotal,
        "desconto_aplicado": desconto,
        "valor_mensal": round(subtotal - desconto, 2),
    }


# --------------------------------------------------------------------------- sincronização
def _blocos(lista: List[Any], tamanho: int):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def sincronizar_previsao(supabase, aluno_ids: Optional[List[int]] = None) -> Dict[str, int]:
    with _trava:
        return _sincronizar(supabase, aluno_ids)


def _sincronizar(supabase, aluno_ids: Optional[List[int]]) -> Dict[str, int]:
    resultado = {"alunos": 0, "criadas": 0, "atualizadas": 0, "removidas": 0}
    if aluno_ids is not None and not aluno_ids:
        return resultado

    atual = mes_atual()
    meses = lista_meses(atual, max(1, settings.MESES_PREVISAO))

    consulta = supabase.table("alunos").select(COLUNAS_ALUNO_CALCULO)
    if aluno_ids is not None:
        consulta = consulta.in_("id", list(aluno_ids))
    alunos = consulta.execute().data or []
    resultado["alunos"] = len(alunos)
    if not alunos:
        return resultado

    # mensalidades que já existem do mês atual em diante
    existentes: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for bloco in _blocos([a["id"] for a in alunos], 100):
        linhas = supabase.table("mensalidades")\
            .select("id, aluno_id, referencia_mes, status, valor_devido")\
            .in_("aluno_id", bloco).gte("referencia_mes", atual).execute().data or []
        for l in linhas:
            existentes.setdefault(l["aluno_id"], {}).setdefault(l["referencia_mes"], []).append(l)

    inserir: List[Dict[str, Any]] = []
    atualizar: Dict[float, List[int]] = {}   # novo valor -> ids das mensalidades a ajustar
    remover: List[int] = []

    for aluno in alunos:
        calculo = calcular_valor_mensal(aluno)
        esperado = calculo["valor_mensal"]
        ativo = aluno.get("ativo") is not False
        por_mes = existentes.get(aluno["id"], {})

        if ativo and calculo["qtd_modalidades"] > 0 and esperado > 0:
            for mes in meses:
                linhas = [l for l in por_mes.get(mes, []) if l["status"] != "CANCELADO"]
                if not linhas:
                    inserir.append({
                        "aluno_id": aluno["id"], "referencia_mes": mes, "valor_devido": esperado,
                        "data_vencimento": data_limite(mes), "status": "PENDENTE",
                    })
                else:
                    for l in linhas:
                        if l["status"] in ("PENDENTE", "ATRASADO") and abs(float(l["valor_devido"] or 0) - esperado) > 0.004:
                            atualizar.setdefault(esperado, []).append(l["id"])
        else:
            # aluno desativado: some a previsão dos meses seguintes (o mês atual fica para o administrador resolver).
            # aluno ativo sem modalidade: some a previsão do mês atual também.
            for mes, linhas in por_mes.items():
                if ativo or mes > atual:
                    remover.extend(l["id"] for l in linhas if l["status"] == "PENDENTE")

    for bloco in _blocos(inserir, 200):
        supabase.table("mensalidades").insert(bloco).execute()
    resultado["criadas"] = len(inserir)

    for valor, ids in atualizar.items():
        for bloco in _blocos(ids, 100):
            supabase.table("mensalidades").update({"valor_devido": valor}).in_("id", bloco).execute()
    resultado["atualizadas"] = sum(len(v) for v in atualizar.values())

    for bloco in _blocos(remover, 100):
        supabase.table("mensalidades").delete().in_("id", bloco).execute()
    resultado["removidas"] = len(remover)

    return resultado


def sincronizar_seguro(supabase, aluno_ids: Optional[List[int]]) -> Optional[Dict[str, int]]:
    """Sincroniza sem nunca derrubar a ação do usuário: se falhar, só registra no log do servidor."""
    try:
        return sincronizar_previsao(supabase, aluno_ids)
    except Exception as e:  # noqa: BLE001
        print(f"[previsao] não foi possível atualizar as mensalidades previstas: {e}")
        return None


def garantir_previsao(supabase, forcar: bool = False) -> Optional[Dict[str, int]]:
    """Confere (no máximo 1x por hora) se todos os alunos ativos têm as mensalidades dos próximos meses."""
    global _ultima_garantia
    agora = time.time()
    if not forcar and agora - _ultima_garantia < _INTERVALO_SEG:
        return None
    try:
        resultado = sincronizar_previsao(supabase)
        _ultima_garantia = agora
        return resultado
    except Exception as e:  # noqa: BLE001
        _ultima_garantia = agora - _INTERVALO_SEG + 600  # tenta de novo em ~10 minutos
        print(f"[previsao] falha ao garantir a previsão: {e}")
        return None
