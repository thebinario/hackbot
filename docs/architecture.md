# Arquitetura

## Estado atual

```
┌───────────────────────────────────────────┐        ┌──────────────────────────────────┐
│  hackbot (este repo — projeto real)        │        │  auto-bug-bounty (laboratório)    │
│                                             │  HTTP   │                                    │
│  app/writeups/* (schema, extractor,        │◀───────│  scripts/recon/hunt.py            │
│    store, analyzer, query_rewrite,         │ 127.0.0.1│  scripts/recon/hunt_rag.py        │
│    api, hackerone fetcher)                 │ :8765   │                                    │
│                                             │────────▶│  results/<program>/<host>/        │
│  scripts/serve_api.py (porta 8765)         │ resposta│    hunt_profile.json              │
│  scripts/ingest_writeups.py                │  JSON   │    hunt_rag_analysis.json/.md      │
│  scripts/analyze_scenario.py               │        │                                    │
│  scripts/fetch_writeups.py                 │        │  (CI no GitHub, scopes/, scanners) │
│                                             │        │                                    │
│  Ollama (qwen2.5:7b-instruct,              │        └──────────────────────────────────┘
│           nomic-embed-text)                │
│  ChromaDB (data/vectorstore/)              │
└───────────────────────────────────────────┘
```

`hackbot` já hospeda o módulo de RAG de writeups como código próprio — não depende mais de
clonar `rag-cyber-frameworks` ao lado para funcionar. `rag-cyber-frameworks` e
`auto-bug-bounty` ficam versionados como submódulos Git em `external/`; o recon e a ponte
`hunt_rag.py` continuam sendo testados em `external/auto-bug-bounty`, que chama a API deste
repo por HTTP, sempre em `127.0.0.1`.

## Por que a migração é incremental

`rag-cyber-frameworks` guarda também um pipeline genérico de RAG sobre frameworks ofensivos
(chunking de documentos arbitrários, embeddings em lote, busca em `chunk_embeddings`) que o
hackbot nunca usou. Trazer o repositório inteiro traria código morto para o projeto real.
Em vez disso, cada migração:

1. Identifica o que já está validado de ponta a ponta (testes + uso manual real).
2. Copia só os arquivos que esse pedaço de fato importa.
3. Corta o que sobra (ex.: `app/rag/vector_store.py` aqui tem só a constante do diretório do
   Chroma, não as funções de indexação de chunks genéricos, que ficam só no laboratório).
4. Roda a suíte de testes migrada para confirmar que o recorte não quebrou nada.

O mesmo vai acontecer com `auto-bug-bounty` quando o fluxo recon → `hunt.py` → `hunt_rag.py`
estiver validado em uso real (hoje só tem cobertura por testes com HTTP mockado, não uma
rodada de ponta a ponta na máquina do operador). Quando isso acontecer, a parte que fizer
sentido trazer para cá entra como um módulo próprio do hackbot, e o resto continua no
laboratório de recon.

## Rodando o fluxo completo hoje

Enquanto o recon ainda mora em `auto-bug-bounty`, o fluxo de ponta a ponta precisa dos
submódulos inicializados:

```
hackbot/
├── external/
│   ├── auto-bug-bounty/       (submódulo — recon experimental)
│   └── rag-cyber-frameworks/  (submódulo — laboratório do RAG genérico)
```

```bash
# neste repo
python scripts/serve_api.py &

# no submódulo de recon
cd external/auto-bug-bounty
python scripts/recon/hunt.py --host example.com --program my-prog --platform hackerone
python scripts/recon/hunt_rag.py --host example.com --program my-prog --platform hackerone
```

## Validação ofensiva (auto-vuln)

Entre o recon e o RAG existe o `external/auto-vuln`, um validador **read-only**. Ele lê a
superfície já mapeada (`crawl_urls.txt`, `crawl_api_endpoints.txt` do recon) e faz checks
ativos de baixo risco — headers, banner/versões EOL, arquivos sensíveis, directory listing,
error disclosure e SQLi por erro/boolean — gravando `findings.json` e `report.md`. Nunca
explora, nunca extrai dados e recusa rodar sem `--authorized` + allowlist de host.

## Segurança por design

- O `hackbot` (RAG) fala apenas com `127.0.0.1`; nunca contata o alvo investigado.
- A saída do `hackbot` é sempre hipótese textual para leitura humana — não existe caminho que
  pegue uma hipótese e dispare ação contra o alvo.
- O único componente que contata o alvo é o `auto-vuln`, e só sob autorização explícita
  (`--authorized` + allowlist), em modo read-only (`GET`/`HEAD`), com rate limit e teto de
  requisições. Ele nunca explora, altera estado ou extrai dados.
