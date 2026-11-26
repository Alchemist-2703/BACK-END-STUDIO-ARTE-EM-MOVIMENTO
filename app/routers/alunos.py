from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from app.core.supabase import get_supabase
from app.services.previsao import COLUNAS_ALUNO_CALCULO, calcular_valor_mensal, sincronizar_seguro

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
    raise ValueError(f"Data inválida: '{valor}'. Use o formato DD/MM/AAAA.")

def _so_digitos(valor: Optional[str]) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())

def _limpar(valor: Optional[str]) -> Optional[str]:
    """Texto vazio vira None (importante: CPF vazio repetido quebraria a regra de CPF único)."""
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None

def _telefone_opcional(valor: Optional[str], quem: str) -> Optional[str]:
    if not valor or not valor.strip():
        return None
    digitos = _so_digitos(valor)
    if len(digitos) < 10:
        raise ValueError(f"Telefone {quem} inválido. Informe o DDD e o número.")
    return digitos

def _cpf_opcional(valor: Optional[str], quem: str) -> Optional[str]:
    if not valor or not valor.strip():
        return None
    digitos = _so_digitos(valor)
    if len(digitos) != 11:
        raise ValueError(f"CPF {quem} inválido. Ele deve ter 11 números.")
    return digitos

def _traduzir_erro(e: Exception) -> str:
    """Troca mensagens técnicas do banco por frases que o usuário entende."""
    texto = str(e)
    baixo = texto.lower()
    if "duplicate key" in baixo or "23505" in baixo or "unique" in baixo:
        if "cpf" in baixo:
            return "Já existe um cadastro com este CPF."
        if "telefone" in baixo:
            return "Já existe um responsável com este telefone."
        return "Já existe um cadastro com estes dados."
    return texto

class ResponsavelCreate(BaseModel):
    nome: str
    telefone: str
    cpf: Optional[str] = None
    email: Optional[str] = None
    observacoes: Optional[str] = None

class ResponsavelNovo(BaseModel):
    """Responsável cadastrado junto com o aluno, na mesma tela."""
    nome: str
    telefone: str
    cpf: Optional[str] = None
    email: Optional[str] = None
    parentesco: Optional[str] = None  # 'Mãe', 'Pai', 'Tutor'...

    @field_validator("nome")
    def validar_nome(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Informe o nome do responsável.")
        return v

    @field_validator("telefone")
    def validar_telefone(cls, v):
        digitos = _so_digitos(v)
        if len(digitos) < 10:
            raise ValueError("Telefone do responsável inválido. Informe o DDD e o número.")
        return digitos

    @field_validator("cpf")
    def validar_cpf(cls, v):
        return _cpf_opcional(v, "do responsável")

class ResponsavelEdicao(ResponsavelNovo):
    """Responsável na tela de edição: com 'id' é um já cadastrado; sem 'id' é um novo."""
    id: Optional[int] = None

class AlunoCreate(BaseModel):
    nome: str
    data_nascimento: Optional[str] = None
    telefone: Optional[str] = None
    cpf: Optional[str] = None
    responsavel_ids: Optional[List[int]] = []
    responsaveis_novos: Optional[List[ResponsavelNovo]] = []  # Responsáveis criados junto com o aluno
    modalidade_ids: Optional[List[int]] = [] # Modalidades em que o aluno será matriculado
    desconto_mensal: Optional[float] = Field(0, ge=0)  # Desconto em R$ (só vale com 2+ modalidades)

    @field_validator("nome")
    def validar_nome(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Informe o nome do aluno.")
        return v

    @field_validator("data_nascimento", mode="before")
    def validar_data_nascimento(cls, v):
        return converter_para_data_iso(v)

    @field_validator("telefone")
    def validar_telefone(cls, v):
        return _telefone_opcional(v, "do aluno")

    @field_validator("cpf")
    def validar_cpf(cls, v):
        return _cpf_opcional(v, "do aluno")

class AlunoUpdate(BaseModel):
    """Edição do aluno. Só os campos enviados mudam. A data de cadastro NÃO é editável."""
    nome: Optional[str] = None
    data_nascimento: Optional[str] = None
    telefone: Optional[str] = None
    cpf: Optional[str] = None
    desconto_mensal: Optional[float] = Field(None, ge=0)
    responsaveis: Optional[List[ResponsavelEdicao]] = None  # lista FINAL de responsáveis do aluno

    @field_validator("nome")
    def validar_nome(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("O nome do aluno não pode ficar vazio.")
        return v

    @field_validator("data_nascimento", mode="before")
    def validar_data_nascimento(cls, v):
        return converter_para_data_iso(v) if v is not None else None

    @field_validator("telefone")
    def validar_telefone(cls, v):
        return _telefone_opcional(v, "do aluno")

    @field_validator("cpf")
    def validar_cpf(cls, v):
        return _cpf_opcional(v, "do aluno")

class MatriculaAdicionar(BaseModel):
    modalidade_ids: List[int]
    desconto_mensal: Optional[float] = Field(None, ge=0)  # se enviado, define o desconto do aluno

# --------------------------------------------------------------------------- desconto
def _decidir_desconto(aluno: Dict[str, Any], pedido: Optional[float]) -> float:
    """
    Descobre qual desconto guardar para o aluno:
      - 'pedido' informado: valida (precisa de 2+ modalidades e não pode passar da soma);
      - sem 'pedido': mantém o atual, mas zera se sobrou menos de 2 modalidades e limita à soma.
    """
    base = calcular_valor_mensal({**aluno, "desconto_mensal": 0})
    qtd, subtotal = base["qtd_modalidades"], base["subtotal_modalidades"]
    if pedido is not None:
        if pedido > 0 and qtd < 2:
            raise HTTPException(status_code=400, detail="O desconto só vale para alunos com 2 ou mais modalidades.")
        if pedido > subtotal + 0.004:
            valor = f"{subtotal:.2f}".replace(".", ",")
            raise HTTPException(
                status_code=400,
                detail=f"O desconto não pode ser maior que a soma das modalidades (R$ {valor})."
            )
        return round(pedido, 2)
    atual = float(aluno.get("desconto_mensal") or 0)
    return round(min(atual, subtotal) if qtd >= 2 else 0.0, 2)

def _aluno_com_calculo(supabase, aluno_id: int) -> Optional[Dict[str, Any]]:
    res = supabase.table("alunos").select(f"nome, {COLUNAS_ALUNO_CALCULO}").eq("id", aluno_id).execute()
    return res.data[0] if res.data else None

def _ajustar_desconto(supabase, aluno_id: int, pedido: Optional[float]) -> Dict[str, Any]:
    """Aplica a regra do desconto no banco e devolve o cálculo atualizado do aluno."""
    aluno = _aluno_com_calculo(supabase, aluno_id)
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")
    novo = _decidir_desconto(aluno, pedido)
    if abs(novo - float(aluno.get("desconto_mensal") or 0)) > 0.004:
        supabase.table("alunos").update({"desconto_mensal": novo}).eq("id", aluno_id).execute()
    return calcular_valor_mensal({**aluno, "desconto_mensal": novo})

# --------------------------------------------------------------------------- responsáveis
def _achar_responsavel_por_telefone(supabase, telefone: str):
    """Procura um responsável já cadastrado com o mesmo telefone (ex.: pai de dois irmãos)."""
    digitos = _so_digitos(telefone)
    if len(digitos) < 10:
        return None
    candidatos = supabase.table("responsaveis").select("id, nome, telefone")\
        .ilike("telefone", f"%{digitos[-4:]}%").execute().data or []
    for c in candidatos:
        if _so_digitos(c.get("telefone"))[-10:] == digitos[-10:]:
            return c
    return None

def _desfazer_cadastro(supabase, aluno_id: int, responsaveis_criados: List[int]):
    """Se algo falhar no meio do cadastro, apaga o que já foi criado (o banco não tem transação aqui)."""
    try:
        supabase.table("alunos").delete().eq("id", aluno_id).execute()  # leva vínculos e matrículas junto
    except Exception:
        pass
    for r_id in responsaveis_criados:
        try:
            supabase.table("responsaveis").delete().eq("id", r_id).execute()
        except Exception:
            pass

# --------------------------------------------------------------------------- rotas
@router.get("/")
def listar_alunos(ativo: Optional[bool] = True):
    """Lista alunos com responsáveis, modalidades e o valor de mensalidade esperado de cada um."""
    supabase = get_supabase()
    query = supabase.table("alunos").select(
        "*, aluno_responsaveis(parentesco, responsavel:responsaveis(*)), "
        "matriculas(ativo, valor_acordado, modalidade:modalidades(*))"
    )
    if ativo is not None:
        query = query.eq("ativo", ativo)
    alunos = query.order("nome").execute().data or []
    for a in alunos:
        a.update(calcular_valor_mensal(a))
    return alunos

@router.post("/", status_code=201)
def cadastrar_aluno(dados: AlunoCreate):
    """Cadastra um novo aluno, seus responsáveis (novos ou já existentes) e as matrículas iniciais."""
    supabase = get_supabase()

    # 1. Valida as modalidades e o desconto ANTES de salvar qualquer coisa
    modalidades = list(dict.fromkeys(dados.modalidade_ids or []))
    mods_encontradas: List[Dict[str, Any]] = []
    if modalidades:
        mods_encontradas = supabase.table("modalidades").select("id, valor_mensalidade, ativo")\
            .in_("id", modalidades).eq("ativo", True).execute().data or []
        if len(mods_encontradas) != len(modalidades):
            raise HTTPException(status_code=400, detail="Alguma modalidade escolhida não existe ou está desativada.")
    aluno_simulado = {
        "matriculas": [{"ativo": True, "modalidade": m} for m in mods_encontradas],
        "desconto_mensal": 0,
    }
    desconto = _decidir_desconto(aluno_simulado, dados.desconto_mensal or 0)

    aluno_payload = {
        "nome": dados.nome,
        "data_nascimento": dados.data_nascimento,
        "telefone": dados.telefone,
        "cpf": dados.cpf,
        "desconto_mensal": desconto,
        "ativo": True
    }
    try:
        res = supabase.table("alunos").insert(aluno_payload).execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao salvar aluno: {_traduzir_erro(e)}")

    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao cadastrar aluno.")

    aluno_id = res.data[0]["id"]
    responsaveis_criados: List[int] = []

    try:
        # 2. Vínculo com responsáveis (já cadastrados + novos desta tela)
        vinculos_resp = []
        ja_vinculados = set()

        for r_id in dados.responsavel_ids or []:
            if r_id not in ja_vinculados:
                ja_vinculados.add(r_id)
                vinculos_resp.append({"aluno_id": aluno_id, "responsavel_id": r_id})

        for i, r in enumerate(dados.responsaveis_novos or []):
            existente = _achar_responsavel_por_telefone(supabase, r.telefone)
            if existente:
                resp_id = existente["id"]
            else:
                criado = supabase.table("responsaveis").insert({
                    "nome": r.nome,
                    "telefone": r.telefone,
                    "cpf": r.cpf,
                    "email": _limpar(r.email),
                }).execute()
                if not criado.data:
                    raise RuntimeError("Não foi possível salvar o responsável.")
                resp_id = criado.data[0]["id"]
                responsaveis_criados.append(resp_id)

            if resp_id in ja_vinculados:
                continue
            ja_vinculados.add(resp_id)
            vinculos_resp.append({
                "aluno_id": aluno_id,
                "responsavel_id": resp_id,
                "parentesco": _limpar(r.parentesco) or "Responsável",
                "responsavel_financeiro_principal": i == 0,
            })

        if vinculos_resp:
            supabase.table("aluno_responsaveis").insert(vinculos_resp).execute()

        # 3. Vínculo com modalidades (Matrículas N:N)
        if modalidades:
            vinculos_mat = [
                {
                    "aluno_id": aluno_id,
                    "modalidade_id": m_id,
                    "data_inicio": datetime.now().strftime("%Y-%m-%d"),
                    "ativo": True
                }
                for m_id in modalidades
            ]
            supabase.table("matriculas").insert(vinculos_mat).execute()
    except Exception as e:
        _desfazer_cadastro(supabase, aluno_id, responsaveis_criados)
        raise HTTPException(
            status_code=400,
            detail=f"Não foi possível concluir o cadastro (nada foi salvo): {_traduzir_erro(e)}"
        )

    # 4. Gera as mensalidades PENDENTES do mês atual e dos próximos meses
    previsao = sincronizar_seguro(supabase, [aluno_id])
    calculo = calcular_valor_mensal({**aluno_simulado, "desconto_mensal": desconto})

    return {
        "message": "Aluno cadastrado e matriculado com sucesso!",
        "aluno": res.data[0],
        "valor_mensal": calculo["valor_mensal"],
        "previsao_mensalidades": previsao,
    }

@router.post("/responsaveis", status_code=201)
def cadastrar_responsavel(dados: ResponsavelCreate):
    """Cadastra um responsável financeiro (pai, mãe ou tutor)."""
    supabase = get_supabase()
    res = supabase.table("responsaveis").insert(dados.model_dump()).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Erro ao cadastrar responsável.")
    return {"message": "Responsável cadastrado com sucesso!", "responsavel": res.data[0]}

@router.patch("/{aluno_id}")
def atualizar_aluno(aluno_id: int, dados: AlunoUpdate):
    """
    Edita os dados do aluno, o desconto e/ou a lista de responsáveis.
    A data em que o aluno foi cadastrado não pode ser alterada.
    """
    supabase = get_supabase()
    campos = dados.model_dump(exclude_unset=True, exclude={"responsaveis", "desconto_mensal"})
    desconto_enviado = "desconto_mensal" in dados.model_fields_set
    responsaveis_enviados = dados.responsaveis is not None

    if not campos and not desconto_enviado and not responsaveis_enviados:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")

    existe = supabase.table("alunos").select("id").eq("id", aluno_id).execute()
    if not existe.data:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")

    # Confere o desconto ANTES de gravar qualquer coisa
    if desconto_enviado:
        aluno_calc = _aluno_com_calculo(supabase, aluno_id)
        _decidir_desconto(aluno_calc, dados.desconto_mensal)

    try:
        if campos:
            campos["atualizado_em"] = datetime.now().isoformat()
            supabase.table("alunos").update(campos).eq("id", aluno_id).execute()

        if responsaveis_enviados:
            atuais = supabase.table("aluno_responsaveis").select("responsavel_id")\
                .eq("aluno_id", aluno_id).execute().data or []
            ids_atuais = {v["responsavel_id"] for v in atuais}
            ids_mantidos = set()

            for r in dados.responsaveis:
                parentesco = _limpar(r.parentesco) or "Responsável"
                if r.id is not None:
                    if r.id not in ids_atuais:
                        raise HTTPException(status_code=400, detail="Um dos responsáveis não pertence a este aluno.")
                    supabase.table("responsaveis").update({
                        "nome": r.nome, "telefone": r.telefone, "cpf": r.cpf, "email": _limpar(r.email),
                    }).eq("id", r.id).execute()
                    supabase.table("aluno_responsaveis").update({"parentesco": parentesco})\
                        .eq("aluno_id", aluno_id).eq("responsavel_id", r.id).execute()
                    ids_mantidos.add(r.id)
                else:
                    existente = _achar_responsavel_por_telefone(supabase, r.telefone)
                    if existente:
                        resp_id = existente["id"]
                    else:
                        criado = supabase.table("responsaveis").insert({
                            "nome": r.nome, "telefone": r.telefone, "cpf": r.cpf, "email": _limpar(r.email),
                        }).execute()
                        resp_id = criado.data[0]["id"]
                    if resp_id in ids_atuais:
                        supabase.table("aluno_responsaveis").update({"parentesco": parentesco})\
                            .eq("aluno_id", aluno_id).eq("responsavel_id", resp_id).execute()
                    elif resp_id not in ids_mantidos:
                        supabase.table("aluno_responsaveis").insert({
                            "aluno_id": aluno_id, "responsavel_id": resp_id, "parentesco": parentesco,
                            "responsavel_financeiro_principal": False,
                        }).execute()
                    ids_mantidos.add(resp_id)

            for rid in ids_atuais - ids_mantidos:
                supabase.table("aluno_responsaveis").delete()\
                    .eq("aluno_id", aluno_id).eq("responsavel_id", rid).execute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao atualizar: {_traduzir_erro(e)}")

    calculo = None
    if desconto_enviado:
        calculo = _ajustar_desconto(supabase, aluno_id, dados.desconto_mensal)
        sincronizar_seguro(supabase, [aluno_id])

    return {"message": "Aluno atualizado com sucesso!", "valor_mensal": calculo["valor_mensal"] if calculo else None}

@router.post("/{aluno_id}/matriculas", status_code=201)
def matricular_aluno(aluno_id: int, dados: MatriculaAdicionar):
    """Matricula um aluno JÁ cadastrado em mais modalidades (e, se quiser, define o desconto)."""
    supabase = get_supabase()
    aluno = _aluno_com_calculo(supabase, aluno_id)
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")

    ids = list(dict.fromkeys(dados.modalidade_ids))
    if not ids:
        raise HTTPException(status_code=400, detail="Escolha ao menos uma modalidade.")

    mods = supabase.table("modalidades").select("id, valor_mensalidade, ativo")\
        .in_("id", ids).eq("ativo", True).execute().data or []
    if len(mods) != len(ids):
        raise HTTPException(status_code=400, detail="Alguma modalidade escolhida não existe ou está desativada.")

    existentes = supabase.table("matriculas").select("id, modalidade_id, ativo")\
        .eq("aluno_id", aluno_id).in_("modalidade_id", ids).execute().data or []
    por_modalidade = {m["modalidade_id"]: m for m in existentes}

    # Confere o desconto considerando como o aluno vai ficar DEPOIS de matricular
    ja_ativas = {m["modalidade"]["id"] for m in (aluno.get("matriculas") or [])
                 if m.get("ativo") is not False and m.get("modalidade")}
    mods_por_id = {m["id"]: m for m in mods}
    simulado = {**aluno, "matriculas": list(aluno.get("matriculas") or []) + [
        {"ativo": True, "modalidade": mods_por_id[i]} for i in ids if i not in ja_ativas
    ]}
    _decidir_desconto(simulado, dados.desconto_mensal)

    hoje = datetime.now().strftime("%Y-%m-%d")
    adicionadas, ja_matriculadas = [], []
    try:
        for m_id in ids:
            atual = por_modalidade.get(m_id)
            if atual and atual.get("ativo"):
                ja_matriculadas.append(m_id)
            elif atual:  # existia, mas estava inativa: reativa
                supabase.table("matriculas").update({"ativo": True, "data_inicio": hoje}).eq("id", atual["id"]).execute()
                adicionadas.append(m_id)
            else:
                supabase.table("matriculas").insert({
                    "aluno_id": aluno_id, "modalidade_id": m_id, "data_inicio": hoje, "ativo": True
                }).execute()
                adicionadas.append(m_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao matricular: {_traduzir_erro(e)}")

    calculo = _ajustar_desconto(supabase, aluno_id, dados.desconto_mensal)
    sincronizar_seguro(supabase, [aluno_id])

    return {
        "message": f"{len(adicionadas)} modalidade(s) adicionada(s) a {aluno['nome']}.",
        "adicionadas": adicionadas,
        "ja_matriculadas": ja_matriculadas,
        "valor_mensal": calculo["valor_mensal"],
    }

@router.delete("/{aluno_id}/matriculas/{modalidade_id}")
def remover_matricula(aluno_id: int, modalidade_id: int):
    """Tira um aluno de uma modalidade (não apaga o aluno nem as mensalidades já pagas)."""
    supabase = get_supabase()
    existe = supabase.table("matriculas").select("id")\
        .eq("aluno_id", aluno_id).eq("modalidade_id", modalidade_id).execute()
    if not existe.data:
        raise HTTPException(status_code=404, detail="Matrícula não encontrada.")
    supabase.table("matriculas").delete().eq("aluno_id", aluno_id).eq("modalidade_id", modalidade_id).execute()

    calculo = _ajustar_desconto(supabase, aluno_id, None)   # sem 2 modalidades, o desconto some
    sincronizar_seguro(supabase, [aluno_id])
    return {"message": "Aluno removido da modalidade.", "valor_mensal": calculo["valor_mensal"]}

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
        sincronizar_seguro(supabase, [aluno_id])  # remove a previsão dos meses seguintes
        return {"message": f"Aluno '{nome_aluno}' (ID {aluno_id}) desativado com sucesso."}
