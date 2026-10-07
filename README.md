# flight-alerts

Monitora preços de passagens (Skiplagged, sem chave, ou Google Flights via SerpApi), guarda histórico em SQLite e avisa por Telegram, Slack e e-mail quando aparece preço bom.

## Como funciona

1. `config.yaml` define rotas, janelas de datas e regras.
2. A cada execução o script escolhe as N buscas (rota + data) consultadas há mais tempo (rodízio), respeitando a cota mensal.
3. Cada preço vai para o histórico. Dispara alerta se: preço <= `max_price`, ou X% abaixo da mediana da rota, ou menor preço já visto.
4. Anti-spam: a mesma busca só alerta de novo se cair mais `realert_drop_pct`.

## Início rápido (um comando só)

```bash
python3 run.py        # ou ./voos
```

Na primeira vez ele cria o ambiente (.venv), instala as dependências e cria o `.env`. A fonte padrão é o Skiplagged, que não precisa de chave; a SerpApi só pede chave se você escolher essa fonte. Nas próximas só abre o programa. Outros comandos: `python3 run.py monitor` (monitor de alertas), `python3 run.py test` (testes), `python3 run.py --mock` (preços falsos), `python3 run.py --reset` (recria o ambiente).

## Windows

O código foi escrito para funcionar também no Windows (caminhos do `.venv`, UTF-8 e navegador tratados), mas **ainda não foi testado lá**. Use o Windows Terminal ou PowerShell:

```powershell
py run.py                 # no lugar de python3 run.py
py run.py monitor
```

Agendar às 8h e 20h (no lugar do launchd do Mac), ajustando a pasta:

```powershell
schtasks /Create /TN "FlightAlerts8" /SC DAILY /ST 08:00 /TR "cmd /c cd /d C:\caminho\flights && .venv\Scripts\python.exe -m flight_alerts >> run.log 2>&1"
schtasks /Create /TN "FlightAlerts20" /SC DAILY /ST 20:00 /TR "cmd /c cd /d C:\caminho\flights && .venv\Scripts\python.exe -m flight_alerts >> run.log 2>&1"
```

O `./voos` e o `com.joel.flights.plist` são só do Mac/Linux. A trava do git usa o Git Bash que vem com o Git para Windows.

## Chaves e canais de alerta

Tudo fica no `.env` (que não vai para o git). O monitor só envia pelos canais listados em `channels:` no `config.yaml`.

### Fonte de preços

- **Skiplagged** (padrão): servidor MCP público, sem chave e sem limite de cota. Mostra voos normais e *cidade escondida*, calendário de preços por dia e "destinos baratos". Não lista vendedores: abre a página de compra do Skiplagged.
- **SerpApi** (Google Flights): 250 buscas/mês grátis e lista onde comprar (companhia, agências).
- Troque dentro do app em **Fonte de preços**, com `--source serpapi|skiplagged|mock` ou `FLIGHT_SOURCE` no `.env`.
- Preços em dólar são convertidos com `FX_RATES` (ex.: `USD_BRL=5.40`); sem câmbio, aparecem em US$.
- **Cidade escondida**: você compra um voo com conexão e desce nela. Só ida, só bagagem de mão, e as companhias proíbem (já processaram clientes e podem cancelar a volta ou o milhas). Por isso fica **desligada** nos alertas (`allow_hidden_city: true` no `config.yaml` ou ao criar o alerta).
- O formato das respostas do Skiplagged não é documentado; rode `python3 scripts/probe_skiplagged.py GRU SSA 2027-02-10` para testar e gerar `skiplagged_probe.json`.

### SerpApi (busca de preços)

1. Crie a conta grátis em serpapi.com (250 buscas/mês).
2. No painel, copie a **Private API Key** (64 caracteres) e coloque em `SERPAPI_KEY`.

### Telegram (recomendado)

Pelo programa: `python3 run.py` e escolha **Telegram**. Ele guia, detecta o chat sozinho, salva no `.env` e manda uma mensagem de teste. À mão:

1. No Telegram, procure **@BotFather** e envie `/newbot`.
2. Escolha um nome e um usuário que termine em `bot`. Ele devolve o **token** (`123456789:AAH...`), que vai em `TELEGRAM_BOT_TOKEN`.
3. Abra o chat com o seu bot e envie qualquer mensagem (ex.: `oi`). O bot só consegue falar com quem falou com ele primeiro.
4. Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` no navegador e copie o número de `"chat":{"id":...}` para `TELEGRAM_CHAT_ID`.

### Slack

1. Acesse api.slack.com/apps e clique em **Create New App** > **From scratch**. Dê um nome (ex.: Passagens) e escolha o workspace.
2. No menu lateral, abra **Incoming Webhooks** e ligue **Activate Incoming Webhooks**.
3. Clique em **Add New Webhook to Workspace**, escolha o canal onde os alertas vão cair (ou crie um, ex.: `#passagens`) e autorize.
4. Copie a **Webhook URL** (`https://hooks.slack.com/services/...`) para `SLACK_WEBHOOK_URL`.
5. Teste: `curl -X POST -H 'Content-type: application/json' --data '{"text":"teste"}' "$SLACK_WEBHOOK_URL"` deve responder `ok` e a mensagem aparece no canal.

### E-mail

SMTP com senha de app (o Gmail exige): preencha `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` e `EMAIL_TO`.

## Modo interativo

```bash
python3 run.py              # abre o menu (faz o setup se precisar)
python3 run.py --mock       # preços falsos, não gasta cota
python3 run.py --plain      # interface simples, sem setas (também usada sem terminal)
```

Navegue com as setas e Enter; ctrl+c ou esc volta um passo. O menu tem:

- **Pesquisar voo**: cidade com autocompletar ("são paulo" busca em GRU, CGH e VCP), datas como `17/01`, ida ou ida e volta. Mostra os preços e, em "Ver onde comprar", lista os vendedores da opção com a diferença de preço e abre o checkout no navegador.
- **Achar o dia mais barato**: varre uma janela de datas (avisa quantas buscas vai gastar antes) e destaca o dia mais barato.
- **Meus alertas**: criar (a partir de qualquer pesquisa), listar e remover.
- **Histórico de preços**: variação da rota, mediana e gráfico em texto.
- **Destinos baratos** (Skiplagged): para onde for mais barato a partir da sua cidade.
- **Promoções do Passagens Imperdíveis**: lê os cartões de promoção da página pública do site (ele não tem RSS), lista as ofertas, marca as novas e abre a escolhida. Se o site recusar a consulta (403), o app avisa e não insiste.
- **Fonte de preços**: troca entre Skiplagged, SerpApi e teste.
- **Telegram**: conecta o bot, detecta seu chat e manda uma mensagem de teste.

Com a SerpApi, cada busca e cada consulta de "onde comprar" gasta 1 da cota mensal; a de vendedores fica em cache durante a sessão. Os alertas criados aqui ficam no SQLite e entram no rodízio do monitor (`python3 run.py monitor`), junto com as rotas do `config.yaml`.

## Promoções do Passagens Imperdíveis no monitor

No `config.yaml`, ligue o bloco `deals:` (`enabled: true`, `keywords`, `max_price`). A cada execução do monitor ele lê as páginas `/promocoes-recentes/` e inicial (uma consulta cada, respeitando o robots.txt) e avisa, nos seus canais, só as promoções **novas** que citem alguma palavra e estejam abaixo do teto. Na primeira execução ele só marca o que já existe, para não despejar o arquivo antigo. A lista por rota de finais de semana ("Curitiba → São Paulo, a partir de R$ 309") já vem por padrão; páginas extras entram em `pages:`. Só as ~16 rotas mais baratas vêm no HTML; o filtro por origem do site usa uma API que o `robots.txt` proíbe, então não é usada. Teste rápido: `python3 scripts/promos.py salvador`.

## Uso (monitor automático)

```bash
python -m flight_alerts --mock --dry-run -v   # simulação, sem gastar cota
python -m flight_alerts --status              # uso da cota do mês
python -m flight_alerts -v                    # execução real
```

## Agendar (cron)

Duas execuções por dia, 4 buscas cada = ~240/mês, dentro da cota grátis:

```cron
0 8,20 * * * cd /caminho/flight-alerts && .venv/bin/python -m flight_alerts >> run.log 2>&1
```

Na primeira ou segunda semana o histórico ainda é pequeno: as regras de mediana só entram após `min_samples` coletas, então no início só o `max_price` alerta.

## Git e segurança das chaves

As chaves são **opcionais**: o padrão (Skiplagged) não precisa de nenhuma. Só o Telegram (para receber avisos) e, se você quiser, a SerpApi usam o `.env`.

- O `.gitignore` deixa de fora `.env`, `config.yaml`, bancos `*.db`, logs e as saídas dos scripts de diagnóstico. Só o `.env.example` (sem valores) vai para o git.
- Ao rodar `python3 run.py` numa pasta com `git init`, ele instala um `pre-commit` que **bloqueia** o commit se achar `.env`, banco, token do Telegram, chave de 64 caracteres, webhook do Slack ou cookie `cf_clearance`. Para rodar à mão: `python3 scripts/git_guard.py`.
- Chave que já foi colada em chat, print ou commit deve ser trocada (revogue e gere outra).

## Estender

Nova fonte de preço: implemente `Provider.cheapest(query)` em `flight_alerts/providers/` e troque em `main.py`. Nota: Amadeus Self-Service foi encerrado em jul/2026 e Kiwi Tequila não aceita novos indivíduos; Duffel cobra por pedido e é voltado a venda, então SerpApi é o caminho mais simples hoje.
