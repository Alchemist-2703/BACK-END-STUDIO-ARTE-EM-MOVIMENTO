import hmac
import time
from typing import Dict, List

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

ALGORITMO = "HS256"
_bearer = HTTPBearer(auto_error=False)

# Anti "chute de senha": no máximo 5 erros por endereço a cada 15 minutos
_MAX_FALHAS = 5
_JANELA_SEG = 15 * 60
_falhas: Dict[str, List[float]] = {}


def login_configurado() -> bool:
    return bool(settings.ADMIN_PASSWORD) and len(settings.JWT_SECRET) >= 32


def ip_do_cliente(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def esta_bloqueado(ip: str) -> bool:
    agora = time.time()
    recentes = [t for t in _falhas.get(ip, []) if agora - t < _JANELA_SEG]
    _falhas[ip] = recentes
    return len(recentes) >= _MAX_FALHAS


def registrar_falha(ip: str):
    _falhas.setdefault(ip, []).append(time.time())


def limpar_falhas(ip: str):
    _falhas.pop(ip, None)


def credenciais_corretas(usuario: str, senha: str) -> bool:
    # compare_digest evita descobrir a senha pelo tempo de resposta
    ok_usuario = hmac.compare_digest(usuario.encode(), settings.ADMIN_USER.encode())
    ok_senha = hmac.compare_digest(senha.encode(), settings.ADMIN_PASSWORD.encode())
    return ok_usuario and ok_senha


def criar_token(usuario: str) -> str:
    agora = int(time.time())
    payload = {"sub": usuario, "iat": agora, "exp": agora + settings.TOKEN_HORAS * 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITMO)


def exigir_login(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    """Use como dependência: a rota só abre para quem manda um token válido."""
    if not login_configurado():
        raise HTTPException(status_code=503, detail="Login não configurado no servidor.")
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Faça login para continuar.",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        dados = jwt.decode(cred.credentials, settings.JWT_SECRET, algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sessão expirada ou inválida. Entre novamente.",
                            headers={"WWW-Authenticate": "Bearer"})
    return dados.get("sub", "")
