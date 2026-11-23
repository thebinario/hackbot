# hackbot

Assistente local de reconhecimento ofensivo: um RAG que roda inteiramente na sua máquina
(Ollama + ChromaDB), indexa writeups/reports de bug bounty e gera hipóteses fundamentadas
para um cenário de recon.

## Status: migrando de experimentos para projeto único

Este código nasceu em repositórios de experimentação, que continuam existindo como
laboratório. Eles ficam versionados como submódulos Git em `external/`:

- **[rag-cyber-frameworks](https://github.com/thebinario/rag-cyber-frameworks)** — onde o
  pipeline de RAG para writeups foi prototipado e validado (extração, embeddings com prefixos
  do `nomic-embed-text`, query rewrite, API local). **Já migrado para cá.**
- **[auto-bug-bounty](https://github.com/thebinario/auto-bug-bounty)** — pipeline de recon CI
  (subfinder, amass, httpx, nmap, katana, ffuf, whatweb, nuclei) contra programas no
  HackerOne/Intigriti, e a ponte experimental `hunt_rag.py` que chama este serviço. **Ainda
  não migrado** — continua sendo consumido como submódulo em `external/auto-bug-bounty` até o
  fluxo de recon+bridge estar validado em produção, como o RAG de writeups já está.
- **[auto-vuln](https://github.com/thebinario/auto-vuln)** — validador ofensivo **read-only**:
  checks ativos (headers, banner/versões EOL, arquivos sensíveis, directory listing, error
  disclosure e SQLi por erro/boolean sem extrair dados) com gate de autorização. É o par
  ofensivo do recon, e alimenta achados para o RAG.

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
external/                # submódulos Git (ver .gitmodules)
├── auto-bug-bounty/     # recon experimental, chama a API via HTTP
├── rag-cyber-frameworks/# laboratório do RAG genérico
└── auto-vuln/           # validação ofensiva read-only (gera achados)
```

Isto é exatamente o que existia em `rag-cyber-frameworks` sob `app/writeups/`, mais o
mínimo de `app/ingest` e `app/rag` que ele realmente importa — sem o pipeline genérico de
RAG sobre frameworks (chunking, embeddings de documentos arbitrários, etc.), que não faz
parte do hackbot.

## Ferramentas irmãs (submódulos)

O fluxo completo usa três peças, cada uma num submódulo em `external/`:

```
auto-bug-bounty (recon)  ->  auto-vuln (validação ativa read-only)  ->  hackbot (RAG)
   mapeia a superfície         confirma fraquezas comuns                 fundamenta hipóteses
```

O `auto-vuln` é ofensivo por natureza, mas **read-only**: só `GET`/`HEAD`, sem exploração,
sem extração de dados e sem alterar o alvo. Exige `--authorized` e um allowlist de host:

```bash
cd external/auto-vuln
python -m autovuln --target http://businesscorp.com.br/ --authorized --allow businesscorp.com.br \
  --from-recon ../auto-bug-bounty/results/businesscorp-ctf/businesscorp.com.br \
  --out results/businesscorp
```

`businesscorp.com.br` é a empresa fictícia criada pela Desec Security para treinamento/CTF,
então é um alvo autorizado para esses testes.

## Setup

```bash
# clone já com os submódulos (ou rode `git submodule update --init --recursive` depois)
git clone --recurse-submodules https://github.com/thebinario/hackbot.git
cd hackbot

py -3.13 -m venv .venv
.venv\Scripts\activate   # linux: source .venv/bin/activate
pip install -r requirements.txt
```

> `./setup.ps1` / `./setup.sh` também inicializam os submódulos em `external/` automaticamente.

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
de recon ainda hospedado no submódulo `external/auto-bug-bounty`.

## Testes

```bash
python -m pytest
```

## Escopo e segurança

- Tudo roda local: nenhuma chamada de rede sai para o alvo que está sendo investigado.
- A saída é sempre **hipótese para validação manual** — nunca execução automática de
  exploração contra um alvo real. Isso vale para este repositório e para qualquer extensão
  futura dele.
