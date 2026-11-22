from supabase import create_client, Client
from app.core.config import settings

_supabase_client: Client = None

def get_supabase() -> Client:
    """
    Retorna a instância singleton do cliente Supabase.
    """
    global _supabase_client
    if _supabase_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise ValueError("SUPABASE_URL ou SUPABASE_KEY não configurados no arquivo .env!")
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _supabase_client
