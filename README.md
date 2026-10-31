# Like + Repost (emulador)

Bot do **Quase Nada Bots** que **curte e republica** os posts do brechó com as contas do emulador,
tocando no app real do Instagram dentro de um emulador Android (MuMu Player) via `uiautomator2`.
Substitui o worker antigo do servidor (`like-repost-instagram`, que usava a API do IG).

## Como funciona

1. A lista de posts vem do app do Brechó (as peças com o link do post, **vendidas também**).
2. O modo escolhe quais entram:
   - **drop**: as peças do último drop **+ o pré-drop** (o post de divulgação sem `#p`, ex. "drop
     hoje às 21h!"). O pré-drop é achado lendo o topo do perfil do brechó uma vez por run: entra
     todo post sem `#p` publicado depois do último post do drop anterior (o fixado sai pela data).
     Se o pré-drop do próximo drop já saiu e as peças ainda não, a run faz só ele;
   - **recentes**: os N posts mais novos;
   - ou um **post inicial** (link ou código): daquele post em diante.
3. Em cada conta: abre cada post pelo link, **confere** que é do brechó e é a peça certa, e só então:
   - **curte** (o botão vira "Gostei" — se já estava curtido, não toca: tocar de novo descurte);
   - **republica** (prova = a foto da conta aparecendo sobre o post "<conta> partilhou"; tocar de
     novo desfaz). O modal do 1º repost da conta ("Republicaste…") é fechado em **Fechar**, nunca em
     "Anular".
4. Conta que ainda **não tem o botão Republicar** (o IG libera aos poucos) só curte.
5. Cada conta lembra o que já fez (`like_repost_feitos.json`) e só pega o que falta; a ordem dos
   posts é embaralhada por conta.
6. **Segurança do modo**: teto de posts por conta, teto de ações por dia, pausas entre posts e entre
   contas, pausa longa navegando no feed. Curtida/repost que não colou 2× seguidas = soft-block: a
   conta para e descansa N horas. Captcha/suspensão param a conta na hora.

## Onde roda

Pasta `bots/like_repost/` da base `quase-nada-bots-device` (agente do PC). Depende do
[Auto Follow (emulador)](https://github.com/ViniciusMateos/auto-follow-emulador) (`bots/auto_follow/`,
navegação e troca de conta), do [DM Followers](https://github.com/ViniciusMateos/dm-followers-emulador)
(`bots/dm_follow/`, pausas) e da raspagem do brechó (`bots/brecho_scraper/`, leitura do perfil pro
pré-drop):

```python
from bots.like_repost import like_repost as lr
posts = lr.escolher_posts(pecas, params)          # + lr.achar_pre_drop(d, pecas) no modo drop
r = lr.processar_like_repost(d, log, conta, posts, params, parar)
# → {ok, motivo, curtidas, repostadas, puladas, falhas}
```

O agente (`agente_emulador.py`) roda o lote conta a conta e reporta o saldo
`{curtidas, repostadas, puladas, falhas}` pro app.
