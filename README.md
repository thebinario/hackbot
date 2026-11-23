# hackbot

Assistente local de reconhecimento ofensivo: um RAG que roda inteiramente na sua máquina
(Ollama + ChromaDB), indexa writeups/reports de bug bounty e gera hipóteses fundamentadas
para um cenário de recon.

## Status: migrando de experimentos para projeto único

Este código nasceu em dois repositórios de experimentação, que continuam existindo como
laboratório:

- **[rag-cyber-frameworks](https://github.com/thebinario/rag-cyber-frameworks)** — onde o
  pipeline de RAG para writeups foi prototipado e validado (extração, embeddings com prefixos
  do `nomic-embed-text`, query rewrite, API local). **Já migrado para cá.**
- **[auto-bug-bounty](https://github.com/thebinario/auto-bug-bounty)** — pipeline de recon CI
  (subfinder, amass, httpx, nmap, katana, ffuf, whatweb, nuclei) contra programas no
  HackerOne/Intigriti, e a ponte experimental `hunt_rag.py` que chama este serviço. **Ainda
  não migrado** — continua sendo consumido como repo irmão até o fluxo de recon+bridge estar
  validado em produção, como o RAG de writeups já está.

A regra é: os dois repos continuam como PoC/laboratório; só o que já funciona de ponta a
ponta é trazido para cá, e só o necessário — sem duplicar o resto do framework genérico de
RAG que não é usado pelo hackbot.

## O que tem aqui hoje

```
app/
├── runtime.py          # checagem de versão do Python
├── ingest/
│   ├── cleaner.py       # limpeza de texto antes da extração
│   ├── ollama_client.py # cliente HTTP (stdlib) para geração e embeddings no Ollama
│   └── source_loaders.py# carrega .md/.txt/.html/.pdf
├── rag/
│   ├── generator.py     # strip_think_tags + constantes do Ollama
│   └── vector_store.py  # caminho do índice Chroma local
└── writeups/            # o módulo em si: schema, extractor, store, analyzer,
                          # query_rewrite, api, hackerone (fetcher de reports)
scripts/
├── ingest_writeups.py   # extrai e indexa reports locais
├── analyze_scenario.py  # pergunta sobre um cenário via CLI
├── serve_api.py         # sobe a API local (porta 8765)
└── fetch_writeups.py    # baixa reports públicos/disclosed do HackerOne
tests/                   # 90 testes, cobrindo todo o módulo writeups
```

Isto é exatamente o que existia em `rag-cyber-frameworks` sob `app/writeups/`, mais o
mínimo de `app/ingest` e `app/rag` que ele realmente importa — sem o pipeline genérico de
RAG sobre frameworks (chunking, embeddings de documentos arbitrários, etc.), que não faz
parte do hackbot.

## Setup

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate   # linux: source .venv/bin/activate
pip install -r requirements.txt
```

Precisa de [Ollama](https://ollama.com) instalado à parte, com os modelos:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

## Uso

```bash
# 1. indexar reports (coloque .md/.txt/.html/.pdf em data/raw/writeups/)
python scripts/ingest_writeups.py

# 2. perguntar sobre um cenário
python scripts/analyze_scenario.py "mutation updateOrder aceita orderId de outro usuário"

# 3. ou subir a API local para outra ferramenta consultar (ex.: hunt_rag.py do auto-bug-bounty)
python scripts/serve_api.py
```

Veja [`docs/architecture.md`](docs/architecture.md) para como isso se encaixa com o pipeline
de recon ainda hospedado em `auto-bug-bounty`.

## Testes

```bash
python -m pytest
```

## Escopo e segurança

- Tudo roda local: nenhuma chamada de rede sai para o alvo que está sendo investigado.
- A saída é sempre **hipótese para validação manual** — nunca execução automática de
  exploração contra um alvo real. Isso vale para este repositório e para qualquer extensão
  futura dele.
