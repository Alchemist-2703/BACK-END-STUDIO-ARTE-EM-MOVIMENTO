import time
from typing import Dict, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app.core.config import settings


class ComprovantePixSchema(BaseModel):
    banco_origem: Optional[str] = Field(None, description="Nome do banco remetente (ex: Nubank, Itaú, Inter, Bradesco, Caixa)")
    valor_pago: float = Field(..., description="Valor numérico pago em reais")
    data_hora: Optional[str] = Field(None, description="Data e hora da transação informada no comprovante")
    pagador_nome: Optional[str] = Field(None, description="Nome completo do pagador")
    pagador_cpf: Optional[str] = Field(None, description="CPF do pagador se visível")
    beneficiario_nome: Optional[str] = Field(None, description="Nome de quem recebeu o valor (Studio Arte em Movimento)")
    beneficiario_chave_pix: Optional[str] = Field(None, description="Chave Pix informada")
    id_transacao_e2e: Optional[str] = Field(None, description="Identificador da transação bancária / Código E2E do Pix")
    confianca_leitura: str = Field(..., description="Nível de nitidez: ALTA, MEDIA ou BAIXA")
    observacoes: Optional[str] = Field(None, description="Notas extras encontradas no comprovante")


class IAIndisponivel(Exception):
    """Todos os modelos de IA falharam agora (cota, sobrecarga etc.). O comprovante deve voltar para a fila."""


PROMPT = """
Você é um assistente financeiro do Studio Arte em Movimento especializado em conciliação de comprovantes Pix e bancários.
Analise este comprovante e extraia as informações com precisão.
Se algum campo não estiver claro ou visível, retorne null.
"""

# modelo -> instante (em segundos) até o qual ele fica "de molho" depois de falhar
_de_molho: Dict[str, float] = {}


def _e_erro_de_credencial(erro: Exception) -> bool:
    """Chave inválida/sem permissão: trocar de modelo não adianta."""
    t = str(erro).lower()
    return any(x in t for x in ("api key not valid", "api_key_invalid", "permission_denied", "unauthenticated"))


def _segundos_de_molho(erro: Exception) -> int:
    t = str(erro).lower()
    if "not found" in t or "404" in t or "not supported" in t:
        return 6 * 3600          # modelo que não existe nesta conta
    if "429" in t or "resource_exhausted" in t or "quota" in t or "rate limit" in t:
        return 300               # uso excessivo / cota
    return 60                    # sobrecarga, timeout ou resposta inválida


def extrair_dados_comprovante_bytes(conteudo_bytes: bytes, mime_type: str = "image/jpeg") -> ComprovantePixSchema:
    """
    Lê um comprovante (imagem OU PDF) com o Gemini e devolve os dados estruturados.
    Se o modelo principal estiver sobrecarregado ou sem cota, tenta AUTOMATICAMENTE o próximo da lista
    (GEMINI_MODEL + GEMINI_MODELS_RESERVA), sem avisar ninguém. Se todos falharem, levanta IAIndisponivel.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    arquivo = types.Part.from_bytes(data=conteudo_bytes, mime_type=mime_type)

    modelos = settings.modelos_gemini
    agora = time.time()
    livres = [m for m in modelos if _de_molho.get(m, 0) <= agora]
    ordem = livres + [m for m in modelos if m not in livres]   # se todos estiverem de molho, tenta mesmo assim

    erros = []
    for modelo in ordem:
        try:
            response = client.models.generate_content(
                model=modelo,
                contents=[arquivo, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ComprovantePixSchema,
                    temperature=0.1,
                ),
            )
            dados = ComprovantePixSchema.model_validate_json(response.text)
            _de_molho.pop(modelo, None)
            return dados
        except Exception as e:  # noqa: BLE001
            if _e_erro_de_credencial(e):
                raise
            _de_molho[modelo] = time.time() + _segundos_de_molho(e)
            erros.append(f"{modelo}: {str(e)[:150]}")
            print(f"[ia] modelo '{modelo}' indisponível, tentando o próximo: {str(e)[:200]}")

    raise IAIndisponivel("Nenhum modelo de IA respondeu agora. " + " | ".join(erros))
