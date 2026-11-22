from typing import Optional
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

def extrair_dados_comprovante_bytes(conteudo_bytes: bytes, mime_type: str = "image/jpeg") -> ComprovantePixSchema:
    """
    Recebe os bytes de um comprovante (imagem OU PDF) e usa o Gemini com Structured Outputs.
    O Gemini le PDF direto, entao nao precisa converter nada.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    prompt = """
    Você é um assistente financeiro do Studio Arte em Movimento especializado em conciliação de comprovantes Pix e bancários.
    Analise este comprovante e extraia as informações com precisão.
    Se algum campo não estiver claro ou visível, retorne null.
    """

    arquivo = types.Part.from_bytes(data=conteudo_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[arquivo, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ComprovantePixSchema,
            temperature=0.1,
        ),
    )

    return ComprovantePixSchema.model_validate_json(response.text)
