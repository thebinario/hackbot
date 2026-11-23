# hackbot

Orquestrador local que liga dois repositórios já existentes:

- **[rag-cyber-frameworks](https://github.com/thebinario/rag-cyber-frameworks)** — a base de conhecimento: RAG local (Ollama + ChromaDB) que indexa writeups/reports de bug bounty e responde perguntas sobre um cenário, com hipóteses fundamentadas nos reports mais próximos.
- **[auto-bug-bounty](https://github.com/thebinario/auto-bug-bounty)** — o pipeline de recon: scanners (subfinder, amass, httpx, nmap, katana, ffuf, whatweb, nuclei) rodando em CI contra alvos dentro do escopo de programas no HackerOne/Intigriti.

Este repositório **não duplica código** de nenhum dos dois. Ele só:

1. Clona os dois repositórios como pastas irmãs.
2. Sobe o Ollama e a API local de análise (`rag-cyber-frameworks`).
3. Roda o recon + a ponte `hunt_rag.py` (`auto-bug-bounty`) para um host específico.
4. Documenta como as peças se encaixam.

## Por que um terceiro repositório?

`rag-cyber-frameworks` é público e genérico (framework de conhecimento ofensivo, não específico de bug bounty). `auto-bug-bounty` é privado e tem um histórico grande de `results/` auto-commitado pela CI. Misturar os dois complicaria licenciamento e visibilidade. Em vez disso, cada um continua independente e este repo guarda só a "cola": scripts de setup e o passo a passo de como rodar os dois juntos.

## Layout esperado no disco

```
workspace/
├── hackbot/                 (este repo)
├── rag-cyber-frameworks/    (clonado pelo setup)
└── auto-bug-bounty/         (clonado pelo setup)
```

## Setup

### Windows (PowerShell)

```powershell
.\setup.ps1
```

### Linux / macOS

```bash
./setup.sh
```

O script:
- Clona `rag-cyber-frameworks` e `auto-bug-bounty` como pastas irmãs de `hackbot/` (pula se já existirem).
- Cria o virtualenv de cada repo e instala as dependências (`requirements.txt`).
- Confere se o Ollama está instalado e puxa os modelos usados (`qwen2.5:7b-instruct`, `nomic-embed-text`).

Pré-requisitos: Git, Python 3.11+, [Ollama](https://ollama.com) instalado à parte (o script só verifica/puxa os modelos, não instala o Ollama em si).

## Rodando a pipeline completa para um host

```powershell
.\run_pipeline.ps1 -TargetHost example.com -Program my-prog -Platform hackerone
```

```bash
./run_pipeline.sh --host example.com --program my-prog --platform hackerone
```

Isso faz, em sequência:

1. Sobe `serve_api.py` em background (`rag-cyber-frameworks`), se ainda não estiver rodando.
2. Roda `hunt.py` (`auto-bug-bounty`) para gerar `hunt_profile.json` a partir dos resultados de recon já commitados para aquele host.
3. Roda `hunt_rag.py` (`auto-bug-bounty`), que envia o resumo do recon (stack, endpoints, findings — nada além do que `hunt_profile.json` já tem) para a API local e grava `hunt_rag_analysis.md`/`.json` nos resultados do host.

Veja [`docs/architecture.md`](docs/architecture.md) para o diagrama completo do fluxo de dados.

## Escopo e segurança

- Tudo roda local: o único tráfego de rede do `hunt_rag.py` é uma chamada HTTP para `127.0.0.1`, nunca para o alvo.
- O recon em si (`auto-bug-bounty`) só escaneia hosts listados em `scopes/`, respeitando as regras de cada programa.
- A saída do hackbot é **hipótese para validação manual**, nunca execução automática de exploração. Isso vale também para qualquer extensão futura deste orquestrador.
