import asyncio
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.security import exigir_login
from app.routers import alunos, modalidades, mensalidades, despesas, webhook, dashboard, admin, auth, comprovantes
from app.services.fila import laco_da_fila


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Liga o trabalhador da fila de comprovantes junto com o servidor."""
    tarefa = asyncio.create_task(laco_da_fila()) if settings.FILA_ATIVA else None
    yield
    if tarefa:
        tarefa.cancel()

# Em produção a documentação interativa (/docs) fica escondida
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend de Apuração Financeira e Conciliação com IA para o Studio Arte em Movimento.",
    version="1.0.0",
    lifespan=ciclo_de_vida,
    docs_url=None if settings.em_producao else "/docs",
    redoc_url=None if settings.em_producao else "/redoc",
    openapi_url=None if settings.em_producao else "/openapi.json",
)

# Só os endereços listados em CORS_ORIGINS podem chamar a API pelo navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_lista,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tudo abaixo exige login (token). Só /auth/login, a raiz e o webhook da Meta são públicos.
protegido = [Depends(exigir_login)]

app.include_router(auth.router)
app.include_router(dashboard.router, dependencies=protegido)
app.include_router(alunos.router, dependencies=protegido)
app.include_router(modalidades.router, dependencies=protegido)
app.include_router(mensalidades.router, dependencies=protegido)
app.include_router(despesas.router, dependencies=protegido)
app.include_router(admin.router, dependencies=protegido)
app.include_router(comprovantes.router, dependencies=protegido)
# webhook: a rota de upload se protege sozinha; /webhook/whatsapp é público porque a Meta
# precisa chamá-la, e ela só aceita avisos com a assinatura da Meta.
app.include_router(webhook.router)


@app.get("/", tags=["Saúde"])
def root():
    return {"status": "online"}
