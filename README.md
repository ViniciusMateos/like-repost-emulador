# Like + Repost (emulador)

Bot do **Quase Nada Bots** que **curte e republica** os posts do brechó com as contas do emulador,
tocando no app real do Instagram dentro de um emulador Android (MuMu Player) via `uiautomator2`.
Substitui o worker antigo do servidor (`like-repost-instagram`, que usava a API do IG).

## Como funciona

1. A lista de posts sai do **perfil do brechó**, lido uma vez por run na primeira conta (**vendidas
   também**). Peça com `#p` usa o nome e o link do app do Brechó; o que faltar (drop recém-postado
   ainda não tem link no app) o bot copia pelo botão de compartilhar. Os fixados saem pela data.
   A leitura fica guardada em `like_repost_perfil.json`: se os 4 primeiros posts do perfil são os
   mesmos da última vez, o bot usa a lista guardada e não lê o resto (vale por 7 dias).
2. O modo escolhe quais entram:
   - **drop**: do post mais novo até o primeiro **pré-drop**, ele incluso (pré-drop = legenda sem
     `#p` que fala de "drop"). Se o mais novo já é um pré-drop, o drop dele ainda não saiu e a run
     faz só ele. Sem pré-drop à vista, usa as peças do último drop do app do Brechó;
   - **recentes**: os N primeiros do perfil, de qualquer tipo;
   - ou um **post inicial** (link ou código, pela lista do app do Brechó): daquele post em diante.
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
