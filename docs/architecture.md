# Arquitetura

## Visão geral

```
                         ┌─────────────────────────────┐
                         │        hackbot (este repo)  │
                         │  setup.ps1/.sh               │
                         │  run_pipeline.ps1/.sh         │
                         └──────────────┬───────────────┘
                                         │ orquestra
                 ┌───────────────────────┴────────────────────────┐
                 │                                                  │
                 ▼                                                  ▼
┌─────────────────────────────────┐              ┌──────────────────────────────────┐
│  auto-bug-bounty                │              │  rag-cyber-frameworks             │
│  (recon, privado, CI no GitHub) │              │  (RAG local, público)             │
│                                  │   HTTP        │                                    │
│  scripts/recon/hunt.py          │──localhost──▶│  scripts/serve_api.py (porta 8765) │
│  scripts/recon/hunt_rag.py      │  127.0.0.1    │  POST /analyze                     │
│                                  │◀─────────────│                                    │
│  results/<program>/<host>/      │   resposta    │  Ollama (qwen2.5:7b-instruct,     │
│    hunt_profile.json            │   JSON        │           nomic-embed-text)        │
│    hunt_hypotheses.json         │              │  ChromaDB (data/vectorstore/)      │
│    hunt_playbook.md             │              │  writeups indexados (HackerOne)    │
│    hunt_rag_analysis.json/.md   │              │                                    │
└─────────────────────────────────┘              └──────────────────────────────────┘
```

## Fluxo de dados, passo a passo

1. **CI do `auto-bug-bounty`** escaneia hosts dentro do escopo (`scopes/<platform>/<program>.json`) e commita os resultados em `results/<program>/<host>/` (subdomínios, portas, crawl, fuzz, tech-detect). Isso roda independente deste orquestrador, no GitHub Actions.
2. **`hunt.py`** (já existente em `auto-bug-bounty`) lê esses artefatos committed e monta `hunt_profile.json` — um resumo estruturado do host (stack detectada, endpoints, findings de fuzzing/vuln-scan). Gera também hipóteses por regras (`hunt_hypotheses.json`, `hunt_playbook.md`), sem depender do hackbot.
3. **`serve_api.py`** (em `rag-cyber-frameworks`) sobe uma API HTTP local, restrita a `127.0.0.1` por padrão, que recebe um cenário e devolve os writeups mais parecidos (via embeddings no ChromaDB) mais uma análise gerada pelo LLM local, citando só esses reports.
4. **`hunt_rag.py`** (em `auto-bug-bounty`) lê o `hunt_profile.json` do host, monta um payload de cenário (stack, endpoints, findings — nada de credenciais, nada além do que já estava no profile) e faz **uma única chamada HTTP para `127.0.0.1:8765`**. Nunca contata o host alvo.
5. A resposta é gravada em `hunt_rag_analysis.md`/`.json` ao lado dos outros artefatos do host, com um aviso fixo no topo do markdown dizendo que são hipóteses para validação manual, não instruções para executar automaticamente.
6. **`hackbot/run_pipeline.*`** só automatiza os passos 3–5 localmente, um host de cada vez, para quem não quer digitar os três comandos na mão toda vez.

## Por que isso é seguro por design

- O único tráfego de rede do `hunt_rag.py` é para `127.0.0.1` — nunca para o host sendo investigado.
- O LLM e o índice vetorial só existem na máquina local de quem roda; por isso essa ponte **não** faz parte do pipeline de CI do `auto-bug-bounty` (não existe Ollama rodando nos runners do GitHub Actions).
- A saída é sempre hipótese textual para leitura humana — não há nenhum caminho de código que pegue uma hipótese e dispare uma requisição contra o alvo. Isso é intencional e não deve ser alterado: este orquestrador, assim como os dois repos que ele une, é recon/análise, não exploração automatizada.

## Onde cada coisa mora

| Pergunta | Repositório |
|---|---|
| Como adicionar um programa/escopo novo? | `auto-bug-bounty` |
| Como adicionar um novo módulo de recon? | `auto-bug-bounty` |
| Como indexar novos writeups? | `rag-cyber-frameworks` |
| Como ajustar a qualidade do retrieval (prefixos, rewrite, bônus de classe)? | `rag-cyber-frameworks` |
| Como rodar os dois juntos para um host? | `hackbot` (este repo) |
