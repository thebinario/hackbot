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
clonar `rag-cyber-frameworks` ao lado para funcionar. `auto-bug-bounty` continua sendo o
laboratório onde o recon e a ponte `hunt_rag.py` são testados; ele chama a API deste repo
por HTTP, sempre em `127.0.0.1`.

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

Enquanto o recon ainda mora em `auto-bug-bounty`, o fluxo de ponta a ponta precisa dos dois
repositórios lado a lado:

```
workspace/
├── hackbot/            (este repo)
└── auto-bug-bounty/    (clonado à parte)
```

```bash
# neste repo
python scripts/serve_api.py &

# no auto-bug-bounty
python scripts/recon/hunt.py --host example.com --program my-prog --platform hackerone
python scripts/recon/hunt_rag.py --host example.com --program my-prog --platform hackerone
```

## Segurança por design

- O único tráfego de rede entre os dois lados é uma chamada HTTP para `127.0.0.1` — nunca
  para o host investigado.
- A saída é sempre hipótese textual para leitura humana. Não existe, e não deve existir,
  nenhum caminho de código que pegue uma hipótese e dispare uma ação contra o alvo.
