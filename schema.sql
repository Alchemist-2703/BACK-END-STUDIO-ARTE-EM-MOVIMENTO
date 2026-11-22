# ==============================================================================
# SCHEMA DE BANCO DE DADOS - STUDIO ARTE EM MOVIMENTO
# Compatível com PostgreSQL 14+ / Supabase
# ==============================================================================

-- 1. TABELA DE RESPONSÁVEIS
-- Armazena os contatos financeiros (pais, mães, tutores ou o próprio aluno adulto)
CREATE TABLE IF NOT EXISTS responsaveis (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    telefone VARCHAR(20) NOT NULL UNIQUE, -- Crucial para identificar remetente no WhatsApp
    cpf VARCHAR(14) UNIQUE,
    email VARCHAR(120),
    observacoes TEXT,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. TABELA DE ALUNOS
CREATE TABLE IF NOT EXISTS alunos (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    data_nascimento DATE,
    telefone VARCHAR(20),                -- Opcional para menores, próprio para alunos adultos
    cpf VARCHAR(14) UNIQUE,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. VÍNCULO N:N ENTRE ALUNOS E RESPONSÁVEIS
CREATE TABLE IF NOT EXISTS aluno_responsaveis (
    id SERIAL PRIMARY KEY,
    aluno_id INT NOT NULL REFERENCES alunos(id) ON DELETE CASCADE,
    responsavel_id INT NOT NULL REFERENCES responsaveis(id) ON DELETE CASCADE,
    parentesco VARCHAR(50) DEFAULT 'Responsável', -- 'Pai', 'Mãe', 'Tutor', 'Próprio'
    responsavel_financeiro_principal BOOLEAN DEFAULT TRUE,
    UNIQUE (aluno_id, responsavel_id)
);

-- 4. TABELA DE MODALIDADES
CREATE TABLE IF NOT EXISTS modalidades (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    descricao TEXT,
    valor_mensalidade NUMERIC(10, 2) NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. MATRÍCULAS (VÍNCULO N:N ENTRE ALUNOS E MODALIDADES)
CREATE TABLE IF NOT EXISTS matriculas (
    id SERIAL PRIMARY KEY,
    aluno_id INT NOT NULL REFERENCES alunos(id) ON DELETE CASCADE,
    modalidade_id INT NOT NULL REFERENCES modalidades(id) ON DELETE RESTRICT,
    data_inicio DATE NOT NULL DEFAULT CURRENT_DATE,
    valor_acordado NUMERIC(10, 2), -- Permite bolsas ou descontos por combo de modalidades
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (aluno_id, modalidade_id)
);

-- 6. MENSALIDADES / FATURAS
CREATE TABLE IF NOT EXISTS mensalidades (
    id SERIAL PRIMARY KEY,
    aluno_id INT NOT NULL REFERENCES alunos(id) ON DELETE CASCADE,
    responsavel_pagador_id INT REFERENCES responsaveis(id) ON DELETE SET NULL,
    referencia_mes VARCHAR(7) NOT NULL, -- Formato 'YYYY-MM' (Ex: '2026-11')
    valor_devido NUMERIC(10, 2) NOT NULL,
    valor_pago NUMERIC(10, 2),
    data_vencimento DATE NOT NULL,
    data_pagamento TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE', -- 'PENDENTE', 'PAGO', 'ATRASADO', 'CANCELADO', 'REVISAO_MANUAL'
    forma_pagamento VARCHAR(50),                   -- 'PIX', 'DINHEIRO', 'CARTAO', 'BOLETO'
    comprovante_url TEXT,                          -- URL do comprovante salvo no Bucket Supabase
    id_transacao_pix VARCHAR(150),                 -- Protocolo E2E do Pix extraído pela IA
    observacoes TEXT,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. DESPESAS DO STUDIO (CONTAS A PAGAR)
CREATE TABLE IF NOT EXISTS despesas (
    id SERIAL PRIMARY KEY,
    descricao VARCHAR(150) NOT NULL,
    categoria VARCHAR(60) NOT NULL,                -- 'Aluguel', 'Energia', 'Água', 'Internet', 'Equipamentos', 'Limpeza', 'Professores'
    valor NUMERIC(10, 2) NOT NULL,
    data_vencimento DATE NOT NULL,
    data_pagamento TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE', -- 'PENDENTE', 'PAGO', 'ATRASADO'
    forma_pagamento VARCHAR(50),
    comprovante_url TEXT,
    observacoes TEXT,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ÍNDICES DE PERFORMANCE
CREATE INDEX IF NOT EXISTS idx_responsaveis_telefone ON responsaveis(telefone);
CREATE INDEX IF NOT EXISTS idx_alunos_nome ON alunos(nome);
CREATE INDEX IF NOT EXISTS idx_mensalidades_status_ref ON mensalidades(status, referencia_mes);
CREATE INDEX IF NOT EXISTS idx_despesas_vencimento_status ON despesas(data_vencimento, status);
