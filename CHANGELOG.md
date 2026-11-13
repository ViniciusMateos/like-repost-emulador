# Changelog

## [1.1.1] — 2026-11-13

### Modificado
- perf: a lista do perfil do brechó fica guardada — se os 4 primeiros posts são os mesmos da última leitura, usa a lista guardada e não lê o perfil inteiro de novo (vale 7 dias)

### Documentação
- docs: README com a lista guardada do perfil

## [1.1.0] — 2026-11-06

### Adicionado
- feat: os modos drop e recentes leem o **perfil do brechó** — o drop vai do post mais novo até o primeiro pré-drop (se o mais novo já é o pré-drop, faz só ele) e o recentes pega os N primeiros do perfil
- feat: peça sem link no app do Brechó (drop recém-postado) copia o link pelo botão de compartilhar

### Documentação
- docs: README com a lista lida do perfil

## [1.0.0] — 2026-10-31

### Adicionado
- feat: bot Like + Repost no emulador — curte e republica os posts do brechó com as contas segue, conferindo autor e peça, com memória por conta, modos drop/recentes/post inicial, teto por conta e por dia, pausas e descanso depois de soft-block
- feat: o drop inteiro entra (vendidas também)
- feat: pré-drop no modo drop — o post de divulgação do drop, achado no topo do perfil do brechó

### Documentação
- docs: README do bot
