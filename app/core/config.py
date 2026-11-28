import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Carrega o arquivo .env se presente
load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "Studio Arte em Movimento - API Financeira"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")   # use "production" no Render

    # Google Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    # Modelos de reserva: se o principal estiver sobrecarregado ou sem cota, o sistema usa o próximo da lista
    # automaticamente, sem avisar ninguém. Separe por vírgula, do mais preferido para o menos.
    GEMINI_MODELS_RESERVA: str = os.getenv("GEMINI_MODELS_RESERVA", "gemini-3.5-flash,gemini-2.5-flash,gemini-2.5-flash-lite")

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_BUCKET_COMPROVANTES: str = os.getenv("SUPABASE_BUCKET_COMPROVANTES", "comprovantes")

    # Login do painel (donos do studio)
    ADMIN_USER: str = os.getenv("ADMIN_USER", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")      # obrigatória: sem ela o login fica desligado
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")              # obrigatória: texto aleatório com 32+ caracteres
    TOKEN_HORAS: int = int(os.getenv("TOKEN_HORAS", "24"))     # quanto tempo o login vale

    # Regras de cobrança
    DIA_LIMITE_ATRASO: int = int(os.getenv("DIA_LIMITE_ATRASO", "15"))   # a partir desse dia do mês, a mensalidade pendente conta como atrasada
    MESES_PREVISAO: int = int(os.getenv("MESES_PREVISAO", "12"))         # quantos meses (contando o atual) ficam com mensalidades previstas

    # Fila de comprovantes (leitura pela IA, um de cada vez)
    FILA_ATIVA: bool = os.getenv("FILA_ATIVA", "true").lower() == "true"
    FILA_INTERVALO_SEG: float = float(os.getenv("FILA_INTERVALO_SEG", "3"))     # pausa entre um comprovante e outro
    FILA_MAX_TENTATIVAS: int = int(os.getenv("FILA_MAX_TENTATIVAS", "6"))       # depois disso vai para a revisão manual

    # Quem pode chamar a API pelo navegador (endereços do front, separados por vírgula)
    CORS_ORIGINS: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
    )

    # Rota perigosa que apaga o banco: fica DESLIGADA, a menos que você ligue de propósito
    ALLOW_DB_RESET: bool = os.getenv("ALLOW_DB_RESET", "false").lower() == "true"

    # WhatsApp Cloud API (Meta) - só necessário quando o WhatsApp for ligado
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")
    GRAPH_API_VERSION: str = os.getenv("GRAPH_API_VERSION", "v23.0")

    @property
    def modelos_gemini(self) -> list:
        lista = [self.GEMINI_MODEL] + [m.strip() for m in self.GEMINI_MODELS_RESERVA.split(",")]
        vistos, resultado = set(), []
        for m in lista:
            if m and m not in vistos:
                vistos.add(m)
                resultado.append(m)
        return resultado

    @property
    def cors_lista(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def em_producao(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
