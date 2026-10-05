# Material de treino noutros locais (fora de GitHub/Hugging Face): modelos, receitas de fine-tuning, cursos, benchmarks, feeds de sentimento e playbooks de agentes

> Nota metodológica: o proxy de saída desta sessão bloqueou **todos** os `WebFetch` tentados (ollama.com, docs.ollama.com, docs.unsloth.ai, unsloth.ai, modelscope.cn, arxiv.org, aclanthology.org, alphaxiv.org, suchow.io, fineval.readthedocs.io, docs.patronus.ai, quantconnect.com, lucylabs.gatech.edu, quantinsti.com, hudsonthames.org, build.nvidia.com, beancount.io, benchmarklist.com, harriman-house.com, ollama.readthedocs.io, developers.google.com, cdn.jsdelivr.net). Só a pesquisa web (snippets) funcionou. Por isso, licenças e números marcados como **"não verificada"** devem ser confirmados pelo programador antes de decidir. Datas: hoje é 2026-10-05.

---

## Pergunta-chave 1 — Existe algum GGUF pronto a usar, especializado em sinais de trading, que supere um Qwen2.5/Llama3 genérico com evidência documentada?

### Takeaway
Não encontrei nenhum GGUF especializado em *sinais intraday* com evidência comparativa publicada contra Qwen2.5-7B/Llama3-8B. Os candidatos "finance" disponíveis no Ollama e noutros hubs são afinados para QA/sentimento/NER (texto), não para decisões BUY/SELL/HOLD sobre barras de 5 min; o único modelo com evidência de retorno ajustado ao risco (Trading-R1) opera em horizonte diário/semanal e não encontrei pesos públicos em GGUF fora de GitHub/HF.

### Cited Findings
**Ollama (ollama.com) — modelos comunitários com tag finance**
- `martain7r/finance-llama-8b`: Llama 3.1 8B afinado em 500k exemplos (QA, raciocínio, sentimento, NER), multi-turn; tags `q4_k_m` (4,9 GB, contexto 128K) e `fp16` (16 GB). — [ollama.com/martain7r/finance-llama-8b](https://ollama.com/martain7r/finance-llama-8b); [tags](https://ollama.com/martain7r/finance-llama-8b/tags)
- Origem do mesmo modelo: `tarun7r/Finance-Llama-8B`, licença apache-2.0, base `unsloth/Meta-Llama-3.1-8B`, dataset `Josephgflowers/Finance-Instruct-500k`. — [featherless.ai/models/tarun7r/Finance-Llama-8B](https://featherless.ai/models/tarun7r/Finance-Llama-8B)
- `vanilj/palmyra-fin-70b-32k` (Ollama, ~2.828 pulls, 1 ano): quantizações IQ2_M, Q3_K_M. 70B ⇒ **>16 GB VRAM** mesmo em Q3 (≈ 30+ GB). — [ollama.com/vanilj/palmyra-fin-70b-32k](https://ollama.com/vanilj/palmyra-fin-70b-32k)
- `arcee-ai/llama3-sec`: Llama-3-70B-Instruct treinado em SEC filings (**>16 GB VRAM**). — [ollama.com/arcee-ai/llama3-sec](https://ollama.com/arcee-ai/llama3-sec)
- `jjansen/adapt-finance-llama2-7b` (AdaptLLM finance, Llama-2 7B, 2023 — base antiga). — [ollama.com/jjansen/adapt-finance-llama2-7b](https://ollama.com/jjansen/adapt-finance-llama2-7b)
- `ALIENTELLIGENCE/financialadvisor` (conselho pessoal: orçamento/reforma, irrelevante para trading); `tim2nearfield/finance`, `robinji/finance` (modelos básicos sem evidência). — [ollama.com/ALIENTELLIGENCE/financialadvisor](https://ollama.com/ALIENTELLIGENCE/financialadvisor); [ollama.com/tim2nearfield/finance](https://ollama.com/tim2nearfield/finance); [ollama.com/robinji/finance](https://ollama.com/robinji/finance)
- `chenyumo` publica "MoziAI-35B-A3B-MOE", LLM financeiro multimodal chinês (MoE 35B/3B ativos, visão + tool calling). — [ollama.com/chenyumo](https://ollama.com/chenyumo)
- Ollama consegue correr GGUF do HF diretamente: `ollama run hf.co/GNL324/finance-llama3-8b:Q4_K_M` e `ollama run hf.co/jhon53/Llama3_1_8B_Finance_QLoRA-GGUF:Q4_K_M` (modelos de **sentimento** 3 classes). — [huggingface.co/jhon53/Llama3_1_8B_Finance_QLoRA-GGUF](https://huggingface.co/jhon53/Llama3_1_8B_Finance_QLoRA-GGUF)
- Evidência do `jhon53/Llama3_1_8B_Finance_QLoRA`: QLoRA (NF4 + LoRA bf16) em `FinGPT/fingpt-sentiment-train` (~76k amostras); acc FPB 0,9748 (vs 0,8908 base) e FiQA-SA 0,9402 (vs 0,8120). Métrica de *sentimento*, não de sinal de trading. — [featherless.ai/models/jhon53/Llama3_1_8B_Finance_QLoRA-merged-16bit](https://featherless.ai/models/jhon53/Llama3_1_8B_Finance_QLoRA-merged-16bit)

**NVIDIA (build.nvidia.com / NIM)**
- `writer/palmyra-fin-70b-32k` disponível como NIM na API catalog da NVIDIA (hospedado; também em Baseten e HF). — [build.nvidia.com/writer/palmyra-fin-70b-32k/modelcard](https://build.nvidia.com/writer/palmyra-fin-70b-32k/modelcard); [fintechfutures.com](https://fintechfutures.com/?p=15363173)
- Palmyra-Fin-70B-32K: 72,7B parâmetros, afinado de Palmyra-X-003, contexto 32.768; licença "Writer open model license" (**não verificada** quanto a uso comercial); 73% na secção de escolha múltipla de um teste exemplo CFA Level III (GPT-4: 33%). Uso previsto: análise financeira, previsão de tendência, relatórios — **não** sinais intraday. — [featherless.ai/models/Writer/Palmyra-Fin-70B-32K](https://featherless.ai/models/Writer/Palmyra-Fin-70B-32K); [writer.com/palmyra-fin](https://writer.com/palmyra-fin)
- Pesquisa em build.nvidia.com/nim?q=Finance devolve essencialmente Palmyra-Fin; não encontrei outros NIM de trading. — [build.nvidia.com/nim?q=Finance](https://build.nvidia.com/nim?q=Finance)

**ModelScope (modelscope.cn)**
- Tongyi-Finance-14B: base Qwen-14B com pré-treino contínuo em corpora financeiros chineses; variante Int4 para GPUs com pouca VRAM; cobre QA, classificação, extração, raciocínio. Modelo de 2023, chinês, sem GGUF conhecido. — [skywork.ai (resumo)](https://skywork.ai/blog/?p=48706); [arxiv 2407.00365 (Financial Knowledge LLM)](https://arxiv.org/pdf/2407.00365)
- `Wangluochao/FinGPT_ChatGLM2-6B` em ModelScope (variante chinesa do FinGPT, base ChatGLM2). — [modelscope.cn/models/Wangluochao/FinGPT_ChatGLM2-6B](https://modelscope.cn/models/Wangluochao/FinGPT_ChatGLM2-6B)
- Benchmark chinês CFLUE avalia Tongyi-Finance e outros (texto, não trading). — [arxiv 2405.10542](https://arxiv.org/pdf/2405.10542)

**Featherless.ai (hub de inferência, lista modelos HF)** — variantes Qwen2.5 finance (todas safetensors, não GGUF, licença **não verificada**):
- `wuminxuan/Qwen2.5-7B-Instruct-Finance` (7,6B, 32k ctx); `abocide/Qwen2.5-7B-Instruct-R1-forfinance` (full fine-tune); `Tail-LS/Qwen2.5-3B-dpo-finance` (DPO em finance-alpaca + fingpt-sentiment); `yixuantt/Qwen2.5-3B-R1-Finance`; `sanaeai/Qwen2.5-14B-FinCausal-Rep`; `TheFinAI/Fino1-14B`. Nenhum reporta métricas de trading. — [featherless.ai/models/wuminxuan/Qwen2.5-7B-Instruct-Finance](https://featherless.ai/models/wuminxuan/Qwen2.5-7B-Instruct-Finance); [featherless.ai/models/abocide/Qwen2.5-7B-Instruct-R1-forfinance](https://featherless.ai/models/abocide/Qwen2.5-7B-Instruct-R1-forfinance); [featherless.ai/models/Tail-LS/Qwen2.5-3B-dpo-finance](https://featherless.ai/models/Tail-LS/Qwen2.5-3B-dpo-finance); [featherless.ai/models/TheFinAI/Fino1-14B](https://featherless.ai/models/TheFinAI/Fino1-14B)
- `ichanchiu/Llama-3.1-Omni-FinAI-8B`: pré-treino contínuo em 143B tokens (SEC, Reuters, arXiv, Reddit, Wikipedia) com NVIDIA NeMo em 64×H100; apresentado como **base** para fine-tuning (inclui "stock movement prediction" como caso de uso) — sem métricas publicadas nem licença visível nos snippets. — [featherless.ai/models/ichanchiu/Llama-3.1-Omni-FinAI-8B](https://featherless.ai/models/ichanchiu/Llama-3.1-Omni-FinAI-8B)

**OpenRouter / Replicate / Together / Kaggle Models**
- OpenRouter hospeda Writer `palmyra-x5` (1M ctx, $0,60/M in, $6/M out) mas **não** encontrei `palmyra-fin` nem outro modelo de trading. — [openrouter.ai/writer](https://openrouter.ai/writer); [openrouter.ai/writer/palmyra-x5-20250428](https://openrouter.ai/writer/palmyra-x5-20250428)
- Não encontrei modelos finance/trading em Replicate, Together ou Kaggle Models nas pesquisas efetuadas (ver Gaps).

**Evidência de desempenho em decisões de trading (modelos abertos pequenos vs grandes)**
- InvestorBench (ACL 2025): 13 LLMs backbone em tarefas de stocks, crypto e ETF; métricas CR, SR, AV, MDD; dados **diários**. — [aclanthology.org/2025.acl-long.126](https://aclanthology.org/2025.acl-long.126); [suchow.io/investorbench](https://suchow.io/investorbench/)
- Resultados: Qwen-2.5-Instruct-7B obteve CR médio em ações 29,515% e SR 0,722; o melhor (aparentemente Qwen2.5-72B-Instruct) 46,153% / 1,285; Buy & Hold 34,099% / 0,732; proprietários (GPT-4, GPT-4o, o1-preview) média CR 36,14%, SR 0,82. Ou seja, **Qwen2.5-7B ficou abaixo do Buy&Hold**. — [beancount.io (resumo)](https://beancount.io/bean-labs/research-logs/2026/06/02/investorbench-llm-agent-financial-decision-making); [benchmarklist.com/benchmarks/investorbench](https://benchmarklist.com/benchmarks/investorbench/)
- Trading-R1 (arXiv 2509.11420, set. 2025, Tauric Research + UCLA/UW/Stanford): SFT + RL em Tauric-TR1-DB (100k amostras, 18 meses, 14 ações, 5 fontes); avaliado em 6 ações/ETFs com melhor retorno ajustado ao risco e menor drawdown vs modelos open e proprietários; "Trading-R1 Terminal" anunciado para GitHub. Horizonte não é intraday. Pesos/licença **não verificados** (não encontrei release fora de GH/HF). — [arxiv.org/abs/2509.11420](https://arxiv.org/abs/2509.11420); [ar5iv](https://ar5iv.labs.arxiv.org/html/2509.11420)
- FinBen (NeurIPS 2024): primeira avaliação de *stock trading* num benchmark de LLMs; conclusão: LLMs são bons em extração/análise de texto e fracos em previsão/forecasting; GPT-4 destaca-se em trading, Gemini em forecasting. — [nips.cc/virtual/2024/poster/97525](https://nips.cc/virtual/2024/poster/97525); [slides](https://nips.cc/media/neurips-2024/Slides/97525.pdf)
- FinLlama (2025): família de LLMs financeiros com "trading signal generation" entre as tarefas, mas sem GGUF nem evidência intraday encontrados. — [arxiv 2507.01990](https://arxiv.org/html/2507.01990v1); [emergentmind.com/topics/finllama](https://www.emergentmind.com/topics/finllama)
- Não encontrei nenhum paper/benchmark de 2025 que avalie LLMs pequenos (Qwen/Llama) em sinais de barras de 1–5 min com indicadores técnicos. — pesquisa sem resultados relevantes (ver Gaps)

### Inferences
- Para a tarefa do bot (BUY/SELL/HOLD em 5 min com RSI/SMA/EMA/ATR), nenhum modelo "finance" existente aporta conhecimento relevante comprovado; os ganhos documentados são em sentimento/QA. O mais sensato é manter Qwen2.5-7B/Llama3.1-8B genérico como motor de raciocínio e tratar sentimento com um classificador dedicado (FinBERT, ou o GGUF `jhon53`/`GNL324` via `ollama run hf.co/...` se se aceitar HF como origem de descarga).
- Candidatos mais próximos, por ordem: (1) `martain7r/finance-llama-8b` (Ollama nativo, apache-2.0, cabe em 8 GB VRAM em Q4) — útil para QA/sentimento, não para sinais; (2) Trading-R1 — a única evidência de retorno ajustado ao risco, mas horizonte diário e sem GGUF; (3) Palmyra-Fin-70B — forte em conhecimento CFA, impraticável localmente (>16 GB).
- O InvestorBench sugere que, com agentes baseados em LLM, o salto de desempenho vem do tamanho do modelo (72B > 7B) e não da especialização financeira; num bot local de 7B isso favorece colocar a "inteligência" em regras/código e usar o LLM para raciocínio estruturado e explicação.

### Gaps
- Não consegui abrir ollama.com/library nem a pesquisa de ollama.com para listar pull counts e Modelfiles; os modelos acima vêm de snippets `site:ollama.com`.
- Licença e pesos públicos de Trading-R1: não verificados (o anúncio aponta para GitHub, fora do âmbito).
- Nenhuma métrica para Llama-3.1-Omni-FinAI-8B nem para as variantes Qwen2.5-finance do Featherless.
- Kaggle Models, Replicate e Together: nenhuma entrada finance/trading encontrada nas pesquisas; pode existir e não ter sido indexada.

---

## Pergunta-chave 2 — Qual é o caminho de fine-tuning mais prático (dataset → QLoRA → GGUF fundido → Ollama) usando recursos fora de GH/HF, com estimativas de hardware e tempo?

### Takeaway
O caminho documentado é Unsloth (QLoRA 4-bit) → `model.save_pretrained_gguf(..., quantization_method="q4_k_m"|"q8_0")` (funde o LoRA e converte via llama.cpp, gerando Modelfile automaticamente) → `ollama create`. Um 7B em QLoRA cabe em ~5–8 GB de VRAM (T4 do Colab gratuito serve); a conversão automática safetensors→GGUF dentro do `ollama create` deixou de existir (afirmação de blogue sobre Ollama 0.34.1, **não verificada**), pelo que a conversão deve ser feita no Unsloth/llama.cpp antes.

### Cited Findings
**Unsloth (docs.unsloth.ai / unsloth.ai/docs) — exportar para GGUF/Ollama**
- Guardar em GGUF usa llama.cpp: `model.save_pretrained_gguf("directory", tokenizer, quantization_method="q4_k_m")` ou `"q8_0"`; há `push_to_hub_gguf` (HF, opcional). — [docs.unsloth.ai/basics/saving-models/saving-to-ollama](https://docs.unsloth.ai/basics/saving-models/saving-to-ollama)
- O Unsloth cria automaticamente o `Modelfile` que o Ollama exige, incluindo o chat template usado no fine-tune; o tutorial "How to Finetune Llama-3 and Use In Ollama" cobre o fluxo completo. — [unsloth.ai/docs/basics/inference-and-deployment/saving-to-ollama](https://unsloth.ai/docs/basics/inference-and-deployment/saving-to-ollama)
- Requisitos (docs Unsloth): 7B com QLoRA 4-bit precisa de **5 GB VRAM** no mínimo absoluto. — [docs.unsloth.ai/get-started/beginner-start-here/unsloth-requirements](https://docs.unsloth.ai/get-started/beginner-start-here/unsloth-requirements)
- QLoRA int4 de 7B ocupa ~8 GB numa T4 (16 GB), suficiente; configuração exemplo: batch 2, grad-accum 4, gradient checkpointing. — [vrlatech.com](https://vrlatech.com/how-much-vram-do-you-need-for-llm-fine-tuning-in-2026/); [machinelearningplus.com](https://machinelearningplus.com/gen-ai/unsloth-fine-tuning/)
- Unsloth reivindica 2×–3,9× de aceleração e 50–74% menos memória em Colab. — [dev.co/ai/llms/unsloth-qwen2-5-7b-instruct](https://dev.co/ai/llms/unsloth-qwen2-5-7b-instruct)
- Integração TRL↔Unsloth documentada (SFTTrainer). — [huggingface.co/docs/trl/en/unsloth_integration](https://huggingface.co/docs/trl/en/unsloth_integration)

**Ollama — importar GGUF / adaptadores**
- Docs Ollama (import): suporta importar **GGUF** (Modelfile `FROM ./model.gguf` + `ollama create`) e adaptadores Safetensors/GGUF via `ADAPTER` (converter LoRA com `convert_lora_to_gguf.py` do llama.cpp). Página oficial bloqueada; conteúdo via espelhos. — [docs.ollama.com/import](https://docs.ollama.com/import); [ollama.readthedocs.io/en/import](https://ollama.readthedocs.io/en/import/); [mintlify.com/ollama/ollama/advanced/import](https://mintlify.com/ollama/ollama/advanced/import); [fossies.org import.mdx](https://fossies.org/linux/ollama/docs/import.mdx)
- Blogue (jacar.es): "desde Ollama 0.34.1, `ollama create` já não converte nem quantiza pesos safetensors" (release datada de 14 set. 2026, segundo o blogue); fluxo recomendado: `hf download` → `convert_hf_to_gguf.py` → `llama-quantize` → Modelfile. **Não verificada** nas release notes oficiais. — [jacar.es/?p=7369](https://jacar.es/?p=7369)
- Conflito: o programador afirma que o Ollama já não aceita adaptadores LoRA; os espelhos de docs ainda descrevem `ADAPTER`. Não consegui confirmar a remoção na documentação atual (bloqueada). — [docs.ollama.com/import.md](https://docs.ollama.com/import.md)
- Exemplo de pipeline "fundir LoRA → converter para Ollama" para agente de trading (Chainstack docs, crypto, mas o fluxo técnico é idêntico). — [docs.chainstack.com/docs/ai-trading-agent-fusing-llm-adapters-and-converting-to-ollama](https://docs.chainstack.com/docs/ai-trading-agent-fusing-llm-adapters-and-converting-to-ollama.md)

**Receitas de SFT financeiro fora de GH/HF**
- FinLoRA docs (readthedocs): tutorial de fine-tune LoRA em tarefas financeiras (sentimento, XBRL, etc.). — [finlora-docs.readthedocs.io/en/latest/tutorials/finetune.html](https://finlora-docs.readthedocs.io/en/latest/tutorials/finetune.html)
- Tese (LUISS) sobre fine-tuning de Llama para sentimento com QLoRA. — [tesi.luiss.it/id/eprint/45997](https://tesi.luiss.it/id/eprint/45997)
- Artigo Medium "Elevating sentiment analysis" (Llama-3 8B + Unsloth + export GGUF). — [seandearnaley.medium.com](https://seandearnaley.medium.com/elevating-sentiment-analysis-ad02a316df1d)
- Não encontrei notebooks Kaggle específicos "finance SFT → GGUF" nas pesquisas (ver Gaps).

**Dataset para sinais intraday**
- Nenhum dataset público de instruções "barras 5 min + indicadores → BUY/SELL/HOLD" foi encontrado fora de GH/HF; o InvestorBench/FinBen usam dados diários. — [aclanthology.org/2025.acl-long.126](https://aclanthology.org/2025.acl-long.126)

### Inferences
- Caminho prático recomendado: (1) gerar o dataset a partir do próprio SQLite de "lições" + backtest (triple-barrier sobre barras de 5 min, ver P3) em formato chat JSON com saída estritamente estruturada; (2) QLoRA com Unsloth sobre `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` ou Llama-3.1-8B, r=16, 1–3 épocas; (3) `save_pretrained_gguf(q4_k_m)` (já fundido) → `ollama create nome -f Modelfile`.
- Hardware/tempo estimados (inferência a partir dos requisitos citados, não de medição): T4 16 GB ou RTX 3060 12 GB chegam para 7B QLoRA; para ~10–20k exemplos curtos (≤512 tokens) uma época deve demorar na ordem de 1–3 h numa T4 e <1 h numa RTX 4090; a conversão/quantização GGUF demora minutos mas exige RAM de sistema para o modelo em 16-bit (~16 GB para 7–8B). Nada disto foi encontrado como número oficial.
- Como a conversão safetensors→GGUF já não é feita pelo `ollama create` (se a afirmação do blogue estiver correta), fazer sempre o merge+GGUF no Unsloth/llama.cpp e importar só GGUF — o que coincide com a restrição atual do bot.

### Gaps
- Tempo por época em Colab T4 para Qwen2.5-7B: não encontrado em documentação oficial.
- Release notes oficiais do Ollama 0.34.x: não acessíveis (GitHub excluído; docs.ollama.com bloqueado).
- Kaggle Models / notebooks Kaggle de SFT financeiro: pesquisa sem resultados concretos.

---

## Pergunta-chave 3 — Que fontes estruturadas (livros/cursos/docs) contêm regras suficientemente precisas para virar "seed lessons" com citação, e quais são demasiado vagas?

### Takeaway
As fontes com regras numéricas e reprodutíveis são Carver (*Systematic Trading* / *Advanced Futures Trading Strategies*: EWMAC, forecast scalar, cap ±20, volatility targeting), López de Prado (*AFML*: triple-barrier, meta-labeling — ideal para rotular as lições), Chan (*Algorithmic Trading*: mean-reversion com half-life, Bollinger, momentum intraday) e o curso GT ML4T (indicadores + Q-learning com projetos definidos); Kaufman é enciclopédico mas pouco prescritivo; cursos genéricos (NYIF/Coursera, Quantra, EPAT) e playbooks de agentes (OpenAI/Anthropic) dão processo, não regras de mercado.

### Cited Findings
**Livros**
- Carver, *Systematic Trading* (Harriman House). — [harriman-house.com/systematic-trading](https://harriman-house.com/systematic-trading)
- Regras de Carver reproduzidas em artigos QuantConnect (baseados em *Advanced Futures Trading Strategies*, 2023, estratégia #8): EWMAC(16,64) = EMA16 − EMA64; forecast scalar = 10 ÷ média do |forecast bruto| (4,1 para EWMAC 16/64; 23 para carry); forecast capado a [−20, 20]; posição N = (forecast capado × capital × IDM × peso × τ) / (10 × multiplicador × preço × σ%). — [quantconnect.com/research/15875](https://www.quantconnect.com/research/15875/futures-fast-trend-following-with-trend-strength/); [quantconnect.com/research/15989](https://www.quantconnect.com/research/15989/futures-trend-following-and-carry-in-different-risk-regimes/); [quantconnect.com/research/16001](https://www.quantconnect.com/research/16001/combined-carry-and-trend/)
- López de Prado, *Advances in Financial Machine Learning* (2018): triple-barrier — barreira inferior tocada primeiro ⇒ −1; barreira vertical (tempo) sem tocar horizontais ⇒ 0; meta-labeling: modelo primário decide o lado, modelo secundário binário {0,1} decide se agir/quanto arriscar. — [hudsonthames.org/does-meta-labeling-add-to-signal-efficacy](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/); [docs.jesse.trade/docs/research/ml/meta-labeling](https://docs.jesse.trade/docs/research/ml/meta-labeling); [finmlkit.readthedocs.io label.kit](https://finmlkit.readthedocs.io/en/latest/api/finmlkit.label.kit.html)
- Chan, *Algorithmic Trading: Winning Strategies and Their Rationale* (Wiley, 2013): capítulos "The Basics of Mean Reversion", "Implementing Mean Reversion Strategies", "Intraday Momentum Strategies"; código MATLAB de apoio. — [mathworks.com/academia/books/algorithmic-trading-chan](https://au.mathworks.com/academia/books/algorithmic-trading-chan.html); [TOC](https://seal.matrade.gov.my/neuaxis-e/Record/EBC1204071/TOC)
- Kaufman, *Trading Systems and Methods*, 6.ª ed. (Wiley, ISBN 9781119605355): exemplos com ações/ETFs/futuros, arbitragem, HFT, gestão de risco, técnicas de IA; sem regras únicas prescritivas. — [oreilly.com/library/view/-/9781119605355](https://www.oreilly.com/library/view/-/9781119605355/)
- Hudson & Thames (MlFinLab/ArbitrageLab) implementa AFML (triple-barrier, meta-labeling); modelo de acesso por subscrição — preço **não verificado** (site bloqueado). — [hudsonthames.org](https://hudsonthames.org/)

**Cursos**
- Georgia Tech CS7646 / Udacity ud501 "Machine Learning for Trading" (Tucker Balch): 3 mini-cursos (manipulação de dados financeiros em Python; computational investing; algoritmos de ML para trading), regressão linear, Q-Learning, KNN, regression trees aplicados a trading; gratuito ("Start Free Course"). — [udacity ud501 via my-mooc](https://www.my-mooc.com/en/mooc/machine-learning-for-trading--ud501); [lucylabs.gatech.edu/ml4t](https://lucylabs.gatech.edu/ml4t); [quantsoftware.gatech.edu](https://quantsoftware.gatech.edu/Machine_Learning_for_Trading_Course)
- Georgia Tech MGT 8803 "AI in Finance" (Fall 2026, Sudheer Chava): ML/DL, NLP, LLMs em finanças, LLMs de domínio e *small language models*; a edição Spring foca NLP/LLM/GenAI em finanças. Programa público em PDF. — [syllabus.gatech.edu NLP_GenAI_Finance_Fall2026.pdf](https://syllabus.gatech.edu/sites/default/files/2026-04/NLP_GenAI_Finance_Fall2026.pdf)
- Stanford MS&E 448 "Big Financial Data and Algorithmic Trading": curso-projeto com dados de alta frequência; projetos de execução ótima, market making, **estratégias intraday**, dinâmica bid-ask. Materiais públicos limitados. — [web.stanford.edu/class/msande448](https://web.stanford.edu/class/msande448/); [bulletin Stanford](https://22-23.bulletin.stanford.edu/courses/2152461)
- Coursera "Machine Learning for Trading" Specialization (NYIF + Google Cloud, 2020): 3 cursos; dados de mercado, modelos para trading quantitativo, DL/RL serverless em GCP; preço de subscrição Coursera (**não verificado**). — [coursera.org/specializations/machine-learning-trading](https://coursera.org/specializations/machine-learning-trading); [nyif.com](https://www.nyif.com/news/nyif-google-coursera-machine-learning-trading)
- QuantInsti EPAT: 6 meses, part-time, módulos de estatística, estratégias quantitativas, ML, financial computing; preço **não verificado** (site bloqueado). — [quantinsti.com/EPAT](https://www.quantinsti.com/EPAT)
- Quantra "Introduction to Machine Learning for Trading": self-paced, modelos supervisionados para previsão com dados de mercado. — [quantra.quantinsti.com](https://quantra.quantinsti.com/course/introduction-to-machine-learning-for-trading)
- NHH "Deep Learning and LLMs with Applications to Finance" e BSE "Large Language Models in Finance" (executive). — [nhh.no](https://nhh.no/en/courses/deep-learning-and-llms-with-applications-to-finance/); [bse.eu](https://bse.eu/executive-education/data-science/large-language-models-in-finance)
- DeepLearning.AI: não encontrei curso específico de finanças/trading com LLM. — pesquisa sem resultados

**Playbooks de agentes (relevantes para o loop de trading)**
- OpenAI, *A Practical Guide to Building Agents* (2025): componentes Model/Tools/Instructions; guardrails em camadas (classificadores de relevância/segurança, filtros, regras) combinando LLM e regras determinísticas. — [ibl.ai resumo](https://ibl.ai/blog/openai-a-practical-guide-to-building-agents); [babl.ai](https://babl.ai/?p=7426)
- Anthropic, *Building Effective Agents*: agente = sistema controlado que raciocina, chama ferramentas, inspeciona resultados, com fronteiras claras; a dificuldade é a fiabilidade. — [blog.promptlayer.com](https://blog.promptlayer.com/how-to-build-effective-anthropic-agents/)
- Structured outputs: OpenAI e Gemini usam decoding restringido (JSON válido garantido); Anthropic faz via tool use com JSON schema; OpenAI reportou subida de <40% para 100% de conformidade com schema estrito. Em modelos locais (Ollama) o equivalente é o parâmetro `format` (JSON schema) — não verificado nas docs bloqueadas. — [agenta.ai guide](https://agenta.ai/blog/the-guide-to-structured-outputs-and-function-calling-with-llms); [lablab.ai](https://lablab.ai/ai-articles/how-openais-structured-outputs-are-transforming-api-reliability-and-developer-control); [devtk.ai](https://devtk.ai/en/blog/ai-structured-output-guide-2026/)
- Exemplo concreto de "LLM trading com saída JSON estruturada" (backtest-kit docs). — [backtest-kit example_07_llm_trading](https://tripolskypetr-backtest-kit-docs.static.hf.space/documents/example_07_llm_trading.html)
- FinRobot (AI4Finance) docs: "Financial Chain-of-Thought prompting" — dados brutos → padrões → conclusões intermédias → decisão; agentes de Market Forecasting / Trading Strategies. — [FinRobot docs (algolia docsearch)](https://docsearch.algolia.com/mcp/docs/repo/ai4finance-foundation/finrobot); [arxiv 2405.14767](https://arxiv.org/html/2405.14767v1)
- "Quant GPTs": apenas produtos comerciais sem evidência (QuantPrompt A.I. PDF no Gumroad; "Quant Trader Assistant" GPT). — [quantpromptai.gumroad.com](https://quantpromptai.gumroad.com/l/hysxdb); [theresanaiforthat.com](https://theresanaiforthat.com/gpt/quant-trader-assistant/)

**Benchmarks (para avaliação, não para seed lessons)**
- FinEval (chinês): 8.351 questões em 4 áreas (académico 4.661 MCQ/34 disciplinas; indústria 1.434; segurança 1.640; agente 616); conhecimento, não sinais. — [fineval.readthedocs.io](https://fineval.readthedocs.io/); [arxiv 2308.09975](https://arxiv.org/html/2308.09975v2)
- FinanceBench (Patronus AI): 10.231 perguntas sobre 10-K/10-Q/8-K/earnings; GPT-4-Turbo+RAG falha 81%; QA de documentos, irrelevante para intraday. — [patronus.ai anúncio](https://patronus.ai/announcements/patronus-ai-launches-financebench-the-industrys-first-benchmark-for-llm-performance-on-financial-questions); [docs.patronus.ai](https://docs.patronus.ai/docs/research_and_differentiators/financebench)
- PIXIU/FLARE (NeurIPS 2023): FinMA (LLaMA afinado), 136K instruções, 5 tarefas NLP + 1 de previsão (stock movement diário). — [papers.nips.cc PIXIU](https://papers.nips.cc/paper_files/paper/2023/hash/6a386d703b50f1cf1f61ab02a15967bb-Abstract-Datasets_and_Benchmarks.html)
- Open Financial LLM Leaderboard (FINOS + HF): ~30 LLMs em ~50 tarefas (extração, sentimento, previsão de tendência); docs em readthedocs. — [finllm-leaderboard.readthedocs.io](https://finllm-leaderboard.readthedocs.io/en/latest/overview/introduction.html)
- Papers with Code foi descontinuado em 24 jul. 2025 (9.327 leaderboards deixaram de ser servidos); redireciona para HF Trending Papers; alternativas: CodeSOTA, pwc-archive. Logo, não há "Stock-Pred leaderboard" ativo lá. — [codesota.com/papers-with-code](https://www.codesota.com/papers-with-code); [hyper.ai](https://hyper.ai/en/news/42900)

**Feeds de sentimento/notícias como features (preço/licença)**
- Alpha Vantage NEWS_SENTIMENT: sentimento por artigo e por ticker (`ticker_sentiment_score`, `relevance_score`), escala de rótulos (bearish … bullish; neutro em −0,35..0,35); free tier 25 req/dia (snippets divergem: um diz "25 req/min e 5/dia"); premium desde $49,99/mês. — [wealth-lab.com/blog/news-sentiment](https://wealth-lab.com/blog/news-sentiment); [glama.ai mcp-avantage](https://glama.ai/mcp/servers/MissionSquad/mcp-avantage/tools/alphaIntelligence_newsSentiments); [qveris.ai](https://qveris.ai/guides/financial-news-api-for-ai-agents/)
- Finnhub: free ~60 chamadas/min, **licença pessoal/não comercial**; company-news REST no free; news sentiment e social sentiment (Reddit/Twitter) e WebSocket de notícias são premium; planos ~$50/mês (Starter) a $100–500+/mês. — [qveris.ai best-free](https://qveris.ai/guides/best-free-financial-news-api/); [apicostcalc.com/finnhub](https://apicostcalc.com/finnhub.html); [interactivebrokers.com campus](https://www.interactivebrokers.com/campus/ibkr-quant-news/exploring-the-finnhub-io-api/)
- Tiingo News: 1.000+ publishers, tags por ticker/tópico; planos free + pagos desde $30/mês; sem score de sentimento nativo (QuantConnect sugere calcular NLP sobre o texto). — [quantconnect.com Tiingo News Feed](https://www.quantconnect.com/data/tiingo-news-feed); [apievangelist Tiingo](https://providers.apievangelist.com/providers/tiingo/)
- Benzinga: Basic News API gratuita (título, teaser, link); planos Starter/Professional/Enterprise; Benzinga Pro 2026: Essential $99, Pro $197, Pro+ $457/mês (este último com API); via Alpaca News API (beta gratuita, 200 chamadas/min no free). — [docs.benzinga.com FAQ](https://docs.benzinga.com/introduction/faq); [subger.com Benzinga Pro](https://subger.com/en/service/benzinga-pro-sub); [alpaca.markets news API](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/)
- StockTwits: sem API pública self-serve documentada; acesso via parcerias (Alpaca Broker API, Quartr) ou scrapers Apify (ex. $0,03–0,90 por 1.000 mensagens). Sentimento bullish/bearish autodeclarado. — [alpaca.markets stocktwits](https://alpaca.markets/blog/provide-real-time-social-sentiment-to-customers-with-alpacas-stocktwits-integration/); [apify.com stocktwits-sentiment-scraper](https://apify.com/ghostgrid/stocktwits-sentiment-scraper)
- Google Trends: API oficial em alpha desde 24 jul. 2025, sem preço nem quotas publicadas; `pytrends` arquivado em 17 abr. 2025. Dados diários/semanais — inúteis para 5 min. — [developers.google.com trends-api](https://developers.google.com/search/blog/2025/07/trends-api); [ppc.land](https://ppc.land/google-opens-alpha-testing-for-new-trends-api-targeting-developers-and-journalists/); [scrapfly.io](https://scrapfly.io/blog/posts/google-trends-api-alternatives)

### Inferences
- **Precisas o suficiente para seed lessons com citação**: regras de Carver (fórmula EWMAC, scalar, cap ±20, dimensionamento por volatilidade — adaptáveis a EMA 5-min e ATR), triple-barrier/meta-labeling de AFML (define como rotular cada lição: TP/SL/tempo ⇒ {+1,0,−1} e um "agir/não agir"), Chan (half-life de mean-reversion e bandas, momentum intraday com janelas definidas), e o GT ML4T (indicadores normalizados + política Q-learning com estados discretos). Todas podem ser citadas por capítulo/página pelo programador que tenha os livros.
- **Demasiado vagas**: Kaufman (catálogo, exige escolha de parâmetros), Quantra/EPAT/NYIF (pedagogia geral), Palmyra-Fin/FinanceBench/FinEval (conhecimento de documentos), "Quant GPTs" comerciais (sem evidência), playbooks OpenAI/Anthropic (úteis para o *formato* das lições: saída JSON estrita, guardrails determinísticos, verificação por regras — não para conteúdo de mercado).
- Para features de sentimento num loop de 5 min, Alpha Vantage (scores por ticker prontos) e Finnhub premium são os únicos com score pré-calculado; Tiingo/Benzinga dão texto a classificar com FinBERT local; Google Trends e StockTwits não têm granularidade intraday fiável.
- Avaliação: nenhum benchmark externo mede qualidade de sinal intraday; o mais próximo (InvestorBench) é diário. O bot deve manter o seu próprio backtest com métricas CR/SR/MDD iguais às do InvestorBench para comparabilidade mínima.

### Gaps
- Preços exatos atuais de Finnhub, Tiingo, Hudson & Thames, EPAT, Coursera: páginas oficiais bloqueadas; valores acima vêm de agregadores (2025–2026).
- Não consegui confirmar texto/paginação exata das regras nos livros (sem acesso ao conteúdo); as fórmulas de Carver vêm de reproduções no QuantConnect.
- Não encontrei biblioteca de prompts financeira com evidência de desempenho (FinRobot descreve CoT mas sem métricas de trading em snippet).
- Quant GPTs no GPT Store: sem evidência verificável.
