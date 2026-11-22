import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import (
    criar_token, credenciais_corretas, esta_bloqueado, exigir_login,
    ip_do_cliente, limpar_falhas, login_configurado, registrar_falha,
)

router = APIRouter(prefix="/auth", tags=["Login"])


class LoginEntrada(BaseModel):
    usuario: str = Field(..., max_length=100)
    senha: str = Field(..., max_length=200)


@router.post("/login")
async def login(dados: LoginEntrada, request: Request):
    if not login_configurado():
        raise HTTPException(status_code=503, detail="Login não configurado no servidor.")

    ip = ip_do_cliente(request)
    if esta_bloqueado(ip):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos e tente de novo.")

    if not credenciais_corretas(dados.usuario, dados.senha):
        registrar_falha(ip)
        await asyncio.sleep(1)  # atrasa quem tenta adivinhar a senha
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos.")

    limpar_falhas(ip)
    return {
        "access_token": criar_token(dados.usuario),
        "token_type": "bearer",
        "expira_em_horas": settings.TOKEN_HORAS,
    }


@router.get("/me")
def quem_sou_eu(usuario: str = Depends(exigir_login)):
    """O front usa esta rota para saber se o login guardado ainda vale."""
    return {"usuario": usuario}
