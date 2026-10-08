# Central TikTok

Robô do Telegram que organiza os vídeos UGC do TikTok — **separado da AfiliDash e da mentoria MGD-Benetti**.
Roda 24h no VPS da Hostinger.

```
📥 Brutos  →  ✂️ Editados (sua aprovação)  →  ⏰ Hora de postar (agenda)  →  🚀 Postados
```

1. Você encaminha os vídeos crus pra **📥 Brutos**, cada um com o nome do arquivo logo abaixo
   (`CONJUNTO_BIQUINI_FEMININO_COM_SAIDA_G1C2A1_0510_….mp4`) — ou a legenda `nome do produto | preço`.
   Encaminhando só os vídeos (pela aba Mídias, sem as mensagens de nome)? Mande **antes** o nome do
   produto (ex.: `Calça pantalona | 79,90`) e depois os vídeos: todos ficam com esse nome (o robô reage ✍).
   Se vierem vídeos sem nome, depois de 30 segundos o robô faz **uma pergunta só** pra todos eles;
   responda uma vez e vale pra todos. O que chega em Brutos fica no log (`journalctl -u central-tiktok`).
2. O robô corta as paradas, escreve a **headline na tela** (estilo dos seus posts) e manda pra
   **✂️ Editados**. Você confere os cortes e a headline: **✅ Aprovar**, **🔄 Outra headline**
   (ou responda o vídeo com a sua), **❌ Descartar**, ou **✅✅ Aprovar todos deste produto**.
3. Aprovado, ele sai de Editados e entra na agenda: **6 por dia entre 17h e 22h**, horários quebrados,
   **o mesmo produto nunca no mesmo dia** (espalhado pelo mês) e headlines sem repetir no produto.
4. **⏰ Hora de postar é a agenda**: vídeos de **hoje e dos próximos 2 dias**, com um cabeçalho por dia
   (📆 Quinta, 09/10 — 6 vídeos) e em ordem de horário. No topo, fixada, a **📅 Agenda** dos dias
   seguintes, com 🚀 pra adiantar um vídeo.
   Cada vídeo tem **📋 Copiar legenda / Copiar chamada**, **✅ Postei**, **🔄 Outra headline**,
   **🔁 Reagendar** e **❌ Descartar**. No horário o robô avisa ("⏰ Hora de postar!") respondendo o vídeo.
   Pode postar antes: é só tocar em ✅ Postei.

> Fase 3 (depois que o app do TikTok for aprovado): no horário, o vídeo também
> vai direto pros **rascunhos do TikTok**.

## Instalação (uma vez só)
1. **Criar o robô:** no Telegram, **@BotFather** → `/newbot`. Guarde o **token**.
   Ainda no BotFather: `/setprivacy` → escolha o robô → **Disable** (pra ele ler o grupo).
2. **Seu ID:** mande `/start` pro **@userinfobot** e anote o `Id`.
3. **Instalar no VPS:** hPanel → VPS → **Terminal**, cole:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/julianebenetti/tik-tok/claude/ugc-tiktok-video-editing-7d11hx/central-tiktok/instalar.sh | bash
   ```
   Ele pede o token e o seu ID. No fim deve aparecer **✅ Central rodando!**
4. **Criar o grupo:** no Telegram, crie um grupo (ex.: "Central TikTok"), adicione o robô,
   vá em **Editar grupo → Tópicos → ativar**, e torne o robô **administrador**
   (com permissão de **gerenciar tópicos**, apagar e fixar mensagens).
5. No grupo, mande **/configurar**. O robô cria os 4 tópicos sozinho.

## Glossário (headlines, hashtags, chamadas)
A fonte é a página **Prompts do Método UGC** (claude.ai/artifact/RcWX2zmyEdn4P4voEwX1MV).
Continue alimentando ela ao longo do ano:

1. Todo dia às **5h52** a rotina **Central TikTok — Sincronizar glossário** lê a página e
   atualiza o `glossario.json` aqui no GitHub (só se algo mudou).
2. O robô baixa o `glossario.json` do GitHub a cada 6h. Pra valer na hora, mande `/glossario`
   no grupo.
3. Se o GitHub estiver fora do ar, o robô usa a última versão que deu certo.

## Comandos (no grupo)
- `/agenda` — o que está agendado e quantos esperam aprovação
- `/glossario` — baixa o glossário mais novo na hora
- `/limiar 1.5` — corta mais paradas · `/limiar 0.7` — corta menos
- `/minparado 0.3` — corta também paradas mais curtas
- `/ajuda`

## Arquivos
| Arquivo | O que faz |
|---|---|
| `central.py` | o robô (grupo, aprovação, agenda) |
| `glossario.json` | headlines, hashtags e chamadas (sincronizado da página do Método UGC) |
| `glossario.py` | regras: reconhece o produto, concorda gênero, escolhe headline/hashtags/chamada |
| `cortar_parados.py` | corta as paradas (também funciona sozinho: `python3 cortar_parados.py pasta/`) |
| `texto_tela.py` | escreve a headline no vídeo no estilo "Clássico" do TikTok (letra branca com contorno preto, 61% da altura, emoji colorido) |
| `fontes/` | TikTok Sans (fonte oficial do TikTok, licença OFL) |
| `instalar.sh` | instala/atualiza no VPS |

## Manutenção (Terminal do VPS)
- Ver erros: `journalctl -u central-tiktok -n 50`
- Reiniciar: `systemctl restart central-tiktok`
- Atualizar: rodar o mesmo `curl ... | bash` de novo
- Trocar token / liberar outra pessoa: `nano /etc/central-tiktok.env` e reiniciar

Limites do Telegram: o robô só baixa vídeos de até **20 MB**.
