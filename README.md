# Studio Arte em Movimento - API Financeira & Conciliação com IA

Backend modular desenvolvido em **FastAPI** para gestão financeira, controle de mensalidades, contas a pagar e conciliação automática de comprovantes bancários (Pix) via **Google Gemini 3.8 Flash** e **Supabase**.

---

## 📁 Estrutura de Pastas

```
projeto-artemovimento/
├── app/
│   ├── core/
│   │   ├── config.py         # Configurações do ambiente (.env)
│   │   └── supabase.py       # Conexão com o Supabase
│   ├── services/
│   │   ├── ai_parser.py      # Extração de dados de comprovantes com Gemini 3.8 Flash
│   │   └── conciliacao.py    # Regra de conciliação bancária e baixa automática
│   ├── routers/
│   │   ├── alunos.py         # Gestão de alunos e responsáveis
│   │   ├── modalidades.py    # Cadastro de modalidades
│   │   ├── mensalidades.py   # Controle de cobranças e inadimplência
│   │   ├── despesas.py       # Contas a pagar (água, luz, aluguel, etc.)
│   │   └── webhook.py        # Recepção de comprovantes (WhatsApp / Upload)
│   └── main.py               # Entrada principal da API
├── schema.sql                # DDL do banco de dados (PostgreSQL/Supabase)
├── requirements.txt          # Dependências Python
├── .env.example              # Modelo de credenciais
└── iniciar_servidor.bat      # Script para iniciar o servidor com 2 cliques
```

---

## 🚀 Como Executar

### 1. Configurar o `.env`
Copie o `.env.example` para `.env` e preencha:
```env
GEMINI_API_KEY=sua_chave_do_aistudio
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua_chave_service_role_ou_anon
```

### 2. Iniciar o Servidor
Execute com 2 cliques no arquivo [`iniciar_servidor.bat`](file:///C:/Users/User/Documents/PROGRAMACAO/BACK/projeto-artemovimento/iniciar_servidor.bat) ou via terminal:
```bash
python -m uvicorn app.main:app --reload
```

Acesse a interface interativa no navegador:
- **Painel Swagger Docs:** `http://localhost:8000/docs`
