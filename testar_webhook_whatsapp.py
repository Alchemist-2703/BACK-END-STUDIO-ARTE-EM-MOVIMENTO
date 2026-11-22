import base64
import requests
import json
import os

def testar_webhook():
    url_webhook = "http://127.0.0.1:8000/webhook/whatsapp"
    
    # Caminho da imagem de comprovante
    caminho_imagem = r"C:\Users\User\.gemini\antigravity\scratch\studio-financeiro\comprovante_teste.png"
    
    if not os.path.exists(caminho_imagem):
        print(f"Erro: Imagem não encontrada em {caminho_imagem}")
        return

    # Lê a imagem e converte para Base64
    with open(caminho_imagem, "rb") as f:
        imagem_base64 = base64.b64encode(f.read()).decode("utf-8")

    # Payload idêntico ao que a Evolution API dispara no evento messages.upsert
    payload_evolution = {
        "event": "messages.upsert",
        "instance": "studio-arte-movimento",
        "data": {
            "key": {
                "remoteJid": "5511999998888@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_PIX_TESTE_12345"
            },
            "pushName": "Carlos Eduardo",
            "message": {
                "imageMessage": {
                    "mimetype": "image/png",
                    "caption": "Segue o comprovante do Pix da mensalidade!"
                }
            },
            "base64": imagem_base64
        }
    }

    print("================================================================")
    print("🚀 DISPARANDO EVENTO DO WHATSAPP -> BACKEND (/webhook/whatsapp)")
    print("================================================================")
    print("Remetente (Telefone): 5511999998888")
    print("Mídia: Comprovante Pix (Base64)")
    print("Aguardando IA processar...\n")

    try:
        resposta = requests.post(url_webhook, json=payload_evolution, timeout=30)
        print(f"Status Code: {resposta.status_code}")
        print("\nResultado Retornado pelo Backend:")
        print(json.dumps(resposta.json(), indent=2, ensure_ascii=False))
    except requests.exceptions.ConnectionError:
        print("\n❌ ERRO: O servidor backend (FastAPI) não está rodando em http://127.0.0.1:8000")
        print("Inicie o backend com: python -m uvicorn app.main:app --reload")
    except Exception as e:
        print(f"\n❌ Erro na requisição: {e}")

if __name__ == "__main__":
    testar_webhook()
