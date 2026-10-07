# Central TikTok

Robô do Telegram que organiza os vídeos UGC do TikTok — **separado da AfiliDash e da mentoria MGD-Benetti**.
Roda 24h no VPS da Hostinger.

```
📥 Brutos  →  ✂️ Editados (aprovação)  →  ⏰ Hora de postar  →  🚀 Postados
```

1. Você encaminha os vídeos crus pra **📥 Brutos**. O nome do produto pode vir de 3 jeitos:
   - **do jeito que o gerador manda**: vídeo e, logo abaixo, a mensagem com o nome do arquivo
     (`CONJUNTO_BIQUINI_FEMININO_COM_SAIDA_G1C2A1_0510_….mp4`). O robô junta cada nome com o
     vídeo de cima e guarda a sequência (G1C2A1 = gancho 1 + corpo 2 + CTA 1);
   - na legenda do vídeo: `nome do produto no TikTok | preço` (preço opcional);
   - se não vier nada em 30 segundos, o robô pergunta.
2. O robô corta os trechos em que a modelo fica parada, escreve a **headline na tela**
   (do glossário do Método UGC) e sugere **5 hashtags** + a **chamada do link**.
   Ele posta em **✂️ Editados** com os botões **Aprovar · Outra headline · Descartar**.
   Pra usar uma headline sua, é só responder o vídeo com o texto. Dá pra trocar a headline
   em qualquer momento — antes de aprovar, já agendado ou em ⏰ Hora de postar — sem perder o horário.
3. Aprovados entram na agenda: **6 por dia entre 17h e 22h**, em horários quebrados.
   **Vídeos do mesmo produto nunca caem no mesmo dia** e são espalhados pelo mês
   (11 vídeos → um a cada 2 dias; 5 vídeos → um a cada 6 dias; no máximo 7 dias de intervalo).
   O botão **✅✅ Aprovar todos deste produto** agenda todos de uma vez, já espaçados.
   As headlines não se repetem entre os vídeos do mesmo produto.
4. No horário, o vídeo chega em **⏰ Hora de postar** com a legenda pra copiar.
   Você posta no TikTok, escolhe o produto do TikTok Shop e toca em **Postei**.

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
