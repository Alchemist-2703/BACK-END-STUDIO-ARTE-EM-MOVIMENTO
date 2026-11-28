"""Guarda os comprovantes no Storage do Supabase com nomes fáceis de reconhecer."""
import re
import unicodedata
import uuid
from datetime import date, datetime
from typing import Optional

from app.core.config import settings

EXTENSAO_POR_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def slugify(texto: Optional[str], padrao: str = "sem-nome") -> str:
    """'Maria da Conceição' -> 'maria-da-conceicao'."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:60].strip("-") or padrao


def data_do_comprovante(texto: Optional[str], padrao: date) -> date:
    """Tenta descobrir a data da transação a partir do texto lido pela IA; se não der, usa 'padrao'."""
    if not texto:
        return padrao
    m = re.search(r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return padrao


def caminho_entrada(mime_type: str) -> str:
    """Nome provisório, enquanto o comprovante ainda não foi lido."""
    ext = EXTENSAO_POR_MIME.get(mime_type, "bin")
    return f"entrada/{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}.{ext}"


def caminho_amigavel(nome: Optional[str], data_ref: date, comprovante_id: int, mime_type: str) -> str:
    """Ex.: 2025-04/maria-bispo-04-2025-37.pdf  (pasta do mês / nome de quem enviou / mês-ano / número)."""
    ext = EXTENSAO_POR_MIME.get(mime_type, "bin")
    return f"{data_ref:%Y-%m}/{slugify(nome)}-{data_ref:%m-%Y}-{comprovante_id}.{ext}"


def _bucket(supabase):
    return supabase.storage.from_(settings.SUPABASE_BUCKET_COMPROVANTES)


def enviar(supabase, caminho: str, conteudo: bytes, mime_type: str) -> str:
    _bucket(supabase).upload(path=caminho, file=conteudo, file_options={"content-type": mime_type})
    return url_publica(supabase, caminho)


def baixar(supabase, caminho: str) -> bytes:
    return _bucket(supabase).download(caminho)


def url_publica(supabase, caminho: str) -> str:
    return _bucket(supabase).get_public_url(caminho)


def mover(supabase, origem: str, destino: str) -> bool:
    """Renomeia o arquivo no Storage. Se falhar, o arquivo continua no lugar antigo (nada se perde)."""
    if origem == destino:
        return True
    try:
        _bucket(supabase).move(origem, destino)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[armazenamento] não consegui renomear '{origem}' para '{destino}': {e}")
        return False
