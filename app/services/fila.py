"""
Fila de comprovantes.

Quem recebe o arquivo (webhook do WhatsApp, upload pelo painel, outra API no futuro) só chama
`enfileirar_comprovante`. A leitura pela IA e a baixa acontecem aqui, UM comprovante por vez, em segundo plano,
com novas tentativas se a IA estiver ocupada. Nada aqui depende do WhatsApp nem de um modelo de IA específico.

Para avisar o aluno/remetente de volta, cada origem registra um "notificador" (ex.: o WhatsApp).
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from app.core.config import settings
from app.core.supabase import get_supabase
from app.services import armazenamento
from app.services.ai_parser import extrair_dados_comprovante_bytes
from app.services.conciliacao import _digitos, analisar_conciliacao, aplicar_baixa_automatica

STATUS_EM_REVISAO = ("REVISAO_MANUAL", "ERRO")

_notificadores: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], None]] = {}
_evento: Optional[asyncio.Event] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def registrar_notificador(origem: str, funcao: Callable[[Dict[str, Any], Dict[str, Any]], None]) -> None:
    _notificadores[origem] = funcao


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def acordar() -> None:
    """Avisa o trabalhador da fila que chegou comprovante novo."""
    if _loop is not None and _evento is not None:
        _loop.call_soon_threadsafe(_evento.set)


# --------------------------------------------------------------------------- entrada
def enfileirar_comprovante(supabase, conteudo: bytes, nome_original: str, mime_type: str,
                           origem: str = "UPLOAD", telefone: Optional[str] = None,
                           processar_ja: bool = False) -> Dict[str, Any]:
    """Guarda o arquivo no Storage e coloca o comprovante na fila (ou já o reserva para processar na hora)."""
    caminho = armazenamento.caminho_entrada(mime_type)
    url = armazenamento.enviar(supabase, caminho, conteudo, mime_type)
    res = supabase.table("comprovantes").insert({
        "origem": origem,
        "status": "PROCESSANDO" if processar_ja else "NA_FILA",
        "telefone_remetente": telefone,
        "nome_original": nome_original,
        "mime_type": mime_type,
        "arquivo_path": caminho,
        "arquivo_url": url,
        "tentativas": 0,
        "proxima_tentativa": _agora().isoformat(),
    }).execute()
    if not res.data:
        raise RuntimeError("Não foi possível registrar o comprovante.")
    if not processar_ja:
        acordar()
    return res.data[0]


# --------------------------------------------------------------------------- processamento
def _notificar(comp: Dict[str, Any], resultado: Dict[str, Any]) -> None:
    notificador = _notificadores.get(comp.get("origem") or "")
    if not notificador:
        return
    try:
        notificador(comp, resultado)
    except Exception as e:  # noqa: BLE001
        print(f"[fila] falha ao avisar o remetente: {e}")


def _falha(supabase, comp: Dict[str, Any], erro: Exception) -> Dict[str, Any]:
    tentativas = int(comp.get("tentativas") or 0) + 1
    if tentativas >= settings.FILA_MAX_TENTATIVAS:
        campos = {"status": "ERRO", "tentativas": tentativas, "erro": str(erro)[:500], "proxima_tentativa": None}
        mensagem = "Não foi possível ler o comprovante automaticamente. Ele foi para a revisão manual."
        status_conc = "ERRO_LEITURA"
    else:
        espera = min(30 * 2 ** (tentativas - 1), 1800)  # 30s, 1min, 2min, 4min... no máximo 30 min
        campos = {"status": "NA_FILA", "tentativas": tentativas, "erro": str(erro)[:500],
                  "proxima_tentativa": (_agora() + timedelta(seconds=espera)).isoformat()}
        mensagem = "O comprovante entrou na fila e será lido automaticamente em instantes."
        status_conc = "NA_FILA"
    supabase.table("comprovantes").update(campos).eq("id", comp["id"]).execute()
    print(f"[fila] comprovante {comp['id']} -> {campos['status']} (tentativa {tentativas}): {str(erro)[:200]}")
    resultado = {"comprovante_id": comp["id"], "status": campos["status"], "status_conciliacao": status_conc,
                 "mensagem": mensagem, "url_comprovante": comp.get("arquivo_url"), "dados_extraidos": None,
                 "mensalidades_baixadas": []}
    if campos["status"] == "ERRO":
        _notificar({**comp, **campos}, resultado)
    return resultado


def _processar_registro(supabase, comp: Dict[str, Any]) -> Dict[str, Any]:
    """Lê, concilia e guarda o resultado. A linha já deve estar reservada (status PROCESSANDO)."""
    try:
        conteudo = armazenamento.baixar(supabase, comp["arquivo_path"])
        dados = extrair_dados_comprovante_bytes(conteudo, comp["mime_type"])
        plano = analisar_conciliacao(supabase, comp, dados)

        # nome fácil de reconhecer: pasta do mês / nome de quem enviou / mês-ano / número
        nome = plano.get("nome_identificado") or dados.pagador_nome or _digitos(comp.get("telefone_remetente")) or None
        data_ref = armazenamento.data_do_comprovante(dados.data_hora, date.today())
        novo = armazenamento.caminho_amigavel(nome, data_ref, comp["id"], comp["mime_type"])
        caminho, url = comp["arquivo_path"], comp.get("arquivo_url")
        if armazenamento.mover(supabase, caminho, novo):
            caminho, url = novo, armazenamento.url_publica(supabase, novo)

        ids_baixados = []
        if plano["status"] == "CONCILIADO":
            ids_baixados = aplicar_baixa_automatica(supabase, plano, url, dados)

        campos = {
            "status": plano["status"], "arquivo_path": caminho, "arquivo_url": url,
            "aluno_id": plano.get("aluno_id"), "responsavel_id": plano.get("responsavel_id"),
            "pagador_nome": dados.pagador_nome, "valor_pago": dados.valor_pago,
            "id_transacao_pix": dados.id_transacao_e2e, "dados_extraidos": dados.model_dump(),
            "motivo": plano.get("motivo"), "erro": None, "proxima_tentativa": None,
            "tentativas": int(comp.get("tentativas") or 0) + 1,
            "processado_em": _agora().isoformat(),
        }
        supabase.table("comprovantes").update(campos).eq("id", comp["id"]).execute()
    except Exception as e:  # noqa: BLE001
        return _falha(supabase, comp, e)

    resultado = {
        "comprovante_id": comp["id"], "status": plano["status"], "status_conciliacao": plano["status_conciliacao"],
        "mensagem": plano["mensagem"], "url_comprovante": url, "dados_extraidos": dados.model_dump(),
        "mensalidades_baixadas": ids_baixados,
    }
    _notificar({**comp, **campos}, resultado)
    return resultado


def processar_agora(supabase, comprovante_id: int) -> Dict[str, Any]:
    """Processa um comprovante já reservado (usado pelo upload manual, que mostra o resultado na hora)."""
    comp = supabase.table("comprovantes").select("*").eq("id", comprovante_id).execute().data[0]
    return _processar_registro(supabase, comp)


def processar_proximo(supabase) -> bool:
    """Pega o comprovante mais antigo da fila e o processa. Devolve False se não havia nada para fazer."""
    res = supabase.table("comprovantes").select("*").eq("status", "NA_FILA")\
        .lte("proxima_tentativa", _agora().isoformat()).order("criado_em").limit(1).execute()
    if not res.data:
        return False
    comp = res.data[0]
    reservado = supabase.table("comprovantes").update({"status": "PROCESSANDO"})\
        .eq("id", comp["id"]).eq("status", "NA_FILA").execute()
    if not reservado.data:
        return True  # outro processo pegou primeiro
    _processar_registro(supabase, {**comp, "status": "PROCESSANDO"})
    return True


def recuperar_travados(supabase, minutos: int = 10) -> int:
    """Se o servidor reiniciou no meio de uma leitura, devolve o comprovante para a fila."""
    limite = (_agora() - timedelta(minutes=minutos)).isoformat()
    res = supabase.table("comprovantes").update({"status": "NA_FILA", "proxima_tentativa": _agora().isoformat()})\
        .eq("status", "PROCESSANDO").lt("criado_em", limite).execute()
    return len(res.data or [])


async def laco_da_fila() -> None:
    """Trabalhador de segundo plano: processa a fila um comprovante por vez."""
    global _evento, _loop
    _evento = asyncio.Event()
    _loop = asyncio.get_running_loop()
    try:
        await asyncio.to_thread(recuperar_travados, get_supabase())
    except Exception as e:  # noqa: BLE001
        print(f"[fila] não consegui recuperar comprovantes travados: {e}")

    while True:
        try:
            trabalhou = await asyncio.to_thread(processar_proximo, get_supabase())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[fila] erro no trabalhador: {e}")
            trabalhou = False

        if trabalhou:
            await asyncio.sleep(settings.FILA_INTERVALO_SEG)  # respeita o limite de uso da IA
            continue
        _evento.clear()
        try:
            await asyncio.wait_for(_evento.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
