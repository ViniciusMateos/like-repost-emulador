"""Like + Repost no EMULADOR — curte e REPUBLICA os posts do brechó com as contas segue, tocando no
app real (uiautomator2). Substitui o worker `like-repost-instagram` do servidor (mutations GraphQL).

O que faz (igual ao antigo, onde faz sentido):
  - a lista de posts vem do app do Brechó (as peças com o shortcode, VENDIDAS TAMBÉM — 29/10: curte e
    republica o drop inteiro), não do perfil;
  - seleção por modo: 'drop' = peças do último drop; 'recentes' = os N posts mais novos;
    'post inicial' (opcional) = daquele post em diante;
  - cada conta lembra o que já fez (like_repost_feitos.json) e só pega o que falta;
  - o post só conta como feito quando cumpriu tudo o que o modo pede.

Diferenças forçadas pela UI (medidas ao vivo em 28/10/2026, ver memória reference_ig_repost_ui):
  - curtir: o botão `row_feed_button_like` muda o desc de "Gosto" pra "Gostei" (o estado é só isso);
  - republicar: o botão "Republicar" NÃO muda; a prova é o facepile "<conta> partilhou uma nota"
    sobre a mídia (ou o número ao lado do ícone subir). É toggle: tocar de novo DESFAZ;
  - o 1º repost da conta abre um modal "Republicaste…" → tocar "Fechar", NUNCA "Anular";
  - nem toda conta tem o botão (rollout do IG: a segue26 não tem) → essa conta só curte.
Ao contrário do antigo, bloqueio no repost para a conta (o antigo engolia e seguia).
"""
import json
import os
import random
import re
import time

from bots.auto_follow import auto_follow as af
from bots.auto_follow.auto_follow import _bounds, _dump, _log, _sleep, rid

MARCA = "brechoquasenadaa"

# MODO. O backend manda os campos do modo escolhido em params; o que não vier usa estes. 0 desliga.
PADRAO = {
    "selecao": "drop",              # 'drop' (peças do último drop) | 'recentes' (N posts mais novos)
    "quantos_posts": 12,            # recentes: N; drop: teto (0 = o drop inteiro)
    "curtir": True,
    "repostar": True,
    "max_posts_conta": 0,           # posts tratados por conta nesta run
    "max_acoes_dia": 60,            # curtidas + reposts por conta em 24h (janela rolante)
    "delay_acao_ui": (2, 5),        # depois de abrir o post / entre curtir e republicar
    "delay_post": (20, 60),         # entre um post e outro
    "pausa_longa_cada": 8,          # a cada N posts feitos, pausa longa navegando no feed
    "pausa_longa": (90, 240),
    "resfriar_conta_h": 12,         # depois de bloqueio/soft-block a conta descansa N horas
}
MAX_FALHAS_SEGUIDAS = 2             # 2 curtidas/reposts seguidos que não colaram = soft-block

_ESTADO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "like_repost_feitos.json")
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


# ─────────────────────────────────── estado ───────────────────────────────────
# {conta: {"posts": {code: {"like": ts, "repost": ts, "sem_repost": ts}}, "pausada_ate": ts}}
def _estado_carregar():
    try:
        with open(_ESTADO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _estado_salvar(est):
    try:
        tmp = _ESTADO_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(est, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _ESTADO_PATH)
    except Exception:
        pass


def _marcar(est, conta, code, acao):
    c = est.setdefault(conta, {}).setdefault("posts", {})
    c.setdefault(code, {})[acao] = time.time()
    _estado_salvar(est)


def _feito(est, conta, code, p):
    """O post já cumpriu tudo o que o modo pede nesta conta?"""
    e = ((est.get(conta) or {}).get("posts") or {}).get(code) or {}
    return ((not p["curtir"] or "like" in e)
            and (not p["repostar"] or "repost" in e or "sem_repost" in e))


def _acoes_desde(est, conta, seg):
    lim = time.time() - seg
    return sum(1 for e in ((est.get(conta) or {}).get("posts") or {}).values()
               for k in ("like", "repost") if (e.get(k) or 0) >= lim)


def _resfriar(est, conta, horas, log, motivo):
    if not horas:
        return
    est.setdefault(conta, {})["pausada_ate"] = time.time() + horas * 3600
    _estado_salvar(est)
    _log(log, f"@{conta} vai descansar {horas:g}h ({motivo}) — as próximas runs pulam ela até lá")


def _modo(params):
    p = dict(PADRAO)
    params = params or {}
    for k, v in params.items():
        if k in p and v is not None:
            p[k] = tuple(v) if isinstance(v, list) else v
    # nomes do worker antigo (modo ainda não migrado)
    if "quantos_posts" not in params and params.get("primeira_vez_ultimos") is not None:
        p["quantos_posts"] = params["primeira_vez_ultimos"]
    if "max_posts_conta" not in params and params.get("max_por_run") is not None:
        p["max_posts_conta"] = params["max_por_run"]
    p["curtir"], p["repostar"] = bool(p["curtir"]), bool(p["repostar"])
    return p


# ─────────────────────────────────── seleção ───────────────────────────────────
def ts_do_shortcode(code):
    """Shortcode → epoch (s) da publicação: o id do post carrega o timestamp (validado 15/15)."""
    try:
        n = 0
        for ch in str(code)[:11]:
            n = n * 64 + _B64.index(ch)
        return ((n >> 23) + 1314220021721) / 1000.0
    except Exception:
        return 0.0


def _code_de(v):
    """Aceita o link (/p/, /reel/, /tv/) ou o shortcode puro."""
    s = str(v or "").strip()
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9_-]", "", s)


def escolher_posts(pecas, params, log=None):
    """Lista ordenada (do mais antigo pro mais novo) de {code, nome, ts} que entram nesta run."""
    p = _modo(params)
    posts = []
    for x in pecas or []:
        code = str(x.get("code") or "").strip()
        if code:
            posts.append({"code": code, "nome": (x.get("nome") or "").strip(),
                          "drop": x.get("drop_id"), "ts": ts_do_shortcode(code)})
    n = int(p["quantos_posts"] or 0)
    inicio = _code_de((params or {}).get("start_from"))
    if inicio:
        t0 = ts_do_shortcode(inicio)
        sel = [x for x in posts if x["ts"] >= t0]
        _log(log, f"post inicial {inicio}: {len(sel)} peça(s) dali em diante")
    elif str(p["selecao"]).lower() == "recentes":
        sel = sorted(posts, key=lambda x: -x["ts"])[:n or None]
        _log(log, f"modo recentes: os {len(sel)} posts mais novos do brechó")
    else:
        drops = [x["drop"] for x in posts if x["drop"]]
        ult = max(drops) if drops else None
        sel = [x for x in posts if x["drop"] == ult] if ult else []
        sel = sorted(sel, key=lambda x: -x["ts"])[:n or None]
        _log(log, f"modo drop: {len(sel)} peça(s) do último drop" + (f" (#{ult})" if ult else "")
             + " — vendidas também")
    return sorted(sel, key=lambda x: x["ts"])


def achar_pre_drop(d, pecas, log=None, folga=6):
    """Posts de PRÉ-DROP do brechó (30/10, pedido do Vinicius): a divulgação do drop ("drop hoje às
    21h! ATIVEM AS NOTIFICAÇÕES") é um post SEM #p — o app do Brechó não guarda (a raspagem descarta).
    Lê o topo do perfil com a conta ativa e devolve [{code, nome:"", rotulo:"pré-drop", ts}] dos posts
    sem #p publicados DEPOIS do último post do drop ANTERIOR ao atual — pega o pré-drop do drop atual
    e, se já saiu, o do próximo (aí, sem peças novas, a run faz só ele). O fixado ("seja bem-vindo",
    fev/2025) sai pela data. Para ao chegar numa peça do drop anterior. Nunca levanta: falhou → []."""
    from bots.brecho_scraper import brecho_scraper as bs
    com = [p for p in pecas or [] if p.get("code") and p.get("drop_id")]
    if not com:
        return []
    ult = max(p["drop_id"] for p in com)
    ant = [p for p in com if p["drop_id"] < ult]
    prev = max((p["drop_id"] for p in ant), default=None)
    ts_prev = max((ts_do_shortcode(p["code"]) for p in ant if p["drop_id"] == prev), default=0.0)
    nums_prev = {p.get("num") for p in ant if p["drop_id"] == prev and p.get("num")}
    conhecidos = {p["code"] for p in com}
    teto = sum(1 for p in com if p["drop_id"] == ult) + folga
    achados = {}
    try:
        bs.abrir_perfil(d, log=None)
        bs.entrar_no_feed(d, None)
        nav = bs.Navegador(d, log=None)
        for _ in range(teto):
            if nav.garantir_post():
                num = bs._num_peca(bs.ler_legenda_atual(d, None) or "")
                if num is not None and num in nums_prev:
                    break                                   # chegou no drop anterior
                if num is None:                             # divulgação (ou legenda que não leu)
                    code = bs._copiar_shortcode(d, None)
                    if code and code not in conhecidos and ts_do_shortcode(code) > ts_prev:
                        achados[code] = {"code": code, "nome": "", "rotulo": "pré-drop",
                                         "ts": ts_do_shortcode(code)}
            if not nav.avancar():
                break
    except Exception as e:
        _log(log, f"não consegui ler o perfil do brechó pra achar o pré-drop ({e}) — sigo só com as peças")
    for _ in range(3):
        d.press("back")
        _sleep(0.6, 1.0)
    _log(log, f"pré-drop: {len(achados)} post(s) de divulgação depois do drop anterior"
              + (f" ({', '.join(achados)})" if achados else ""))
    return sorted(achados.values(), key=lambda x: x["ts"])


_RE_DROP = re.compile(r"\bdrop\b", re.I)
FIXADOS_MAX = 3                     # o IG deixa fixar até 3 posts no topo do perfil


def posts_do_perfil(d, pecas, selecao="drop", n=0, log=None, parar=None, teto=80):
    """Lista de posts LIDA DO PERFIL do brechó (05/11, regra do Vinicius) — o que está publicado de
    verdade, não o que o app do Brechó tem cadastrado (o 'recentes' nem abria o perfil e o pré-drop
    não é peça):
      - drop: do post mais novo até o 1º PRÉ-DROP, ele incluso. Se o mais novo (tirando os fixados)
        já é um pré-drop, o drop dele ainda não saiu → só o pré-drop. Rodando de novo depois do drop:
        as peças novas + o pré-drop (o que cada conta já fez ela pula).
      - recentes: os N mais novos, de qualquer tipo.
    Pré-drop = legenda SEM #p que fala de "drop". Fixados entram pela DATA do shortcode: tudo é
    ordenado por data, então o fixado antigo ("seja bem-vindo", fev/2025) cai lá pra trás e não entra.
    Peça com #p conhecida no app do Brechó usa o code de lá; o resto copia o link pelo share.
    Devolve do mais antigo pro mais novo [{code, nome, ts, rotulo?}]; None se não achou pré-drop no
    modo drop (o chamador cai pro app do Brechó). Nunca levanta."""
    from bots.brecho_scraper import brecho_scraper as bs
    sel_drop = str(selecao).lower() != "recentes"
    n = int(n or 0)
    # TODAS as peças com #p (com ou sem link ainda): drop recém-postado está no app sem o code (05/11
    # run-588) — o nome vem de lá; o code, se faltar, copia pelo share
    por_num = {p.get("num"): p for p in pecas or [] if p.get("num")}
    lidos = []
    try:
        bs.abrir_perfil(d, log=None)
        bs.entrar_no_feed(d, None)
        nav = bs.Navegador(d, log=None)
        for idx in range(teto):
            if parar and parar():
                break
            if nav.garantir_post():
                leg = bs.ler_legenda_atual(d, None) or ""
                num = bs._num_peca(leg)
                peca = por_num.get(num) if num is not None else None
                code = (peca or {}).get("code") or bs._copiar_shortcode(d, None)
                if code:
                    pre = num is None and bool(_RE_DROP.search(leg))
                    nome = ""
                    if num is not None:
                        # SEM o "#pN " na frente: o ler_post confere o nome DENTRO da legenda e
                        # "#p485 boné s" nunca bate → "o link não abriu" em toda peça (05/11 run-588)
                        nome = ((peca or {}).get("nome")
                                or re.sub(r"^#p\d+\s*", "", bs._desc_peca(leg, num)[0]))
                    x = {"code": code, "nome": nome, "ts": ts_do_shortcode(code), "idx": idx}
                    if pre:
                        x["rotulo"] = "pré-drop"
                    lidos.append(x)
                    _log(log, f"  perfil {idx + 1}: " + ("PRÉ-DROP" if pre else (f"#p{num}" if num else "post"))
                         + f" {code}")
            if sel_drop:
                pres = [x for x in lidos if x.get("rotulo")]
                if pres:
                    b = max(pres, key=lambda x: x["ts"])
                    # passou do pré-drop na linha do tempo (fora da zona dos fixados) → já tem tudo
                    if b["idx"] >= FIXADOS_MAX or any(x["idx"] >= FIXADOS_MAX and x["ts"] < b["ts"] for x in lidos):
                        break
            elif len(lidos) >= (n or PADRAO["quantos_posts"]) + FIXADOS_MAX:
                break
            if not nav.avancar():
                break
    except Exception as e:
        _log(log, f"não consegui ler o perfil do brechó ({e})")
    for _ in range(3):
        d.press("back")
        _sleep(0.6, 1.0)
    crono = sorted(lidos, key=lambda x: -x["ts"])
    if sel_drop:
        sel = []
        for x in crono:
            sel.append(x)
            if x.get("rotulo"):
                break
        else:
            _log(log, f"modo drop: li {len(lidos)} post(s) do perfil e não achei pré-drop")
            return None
        if n:
            sel = sel[:n]
        n_pre = sum(1 for x in sel if x.get("rotulo"))
        if len(sel) == 1 and n_pre:
            _log(log, "modo drop: o post mais novo é o PRÉ-DROP (o drop ainda não saiu) → só ele")
        else:
            _log(log, f"modo drop: {len(sel) - n_pre} post(s) do drop" + (" + o pré-drop dele" if n_pre else ""))
    else:
        # na ORDEM DO PERFIL (o que ele vê): peças postadas no mesmo minuto não batem certinho com a
        # data do shortcode. Fixado = um dos 3 do topo bem mais velho (>6h) que um post abaixo dele.
        def fixado(x):
            return x["idx"] < FIXADOS_MAX and any(y["idx"] > x["idx"] and y["ts"] - x["ts"] > 6 * 3600
                                                  for y in lidos)
        linha = [x for x in sorted(lidos, key=lambda x: x["idx"]) if not fixado(x)]
        sel = linha[:n or PADRAO["quantos_posts"]]
        _log(log, f"modo recentes: os {len(sel)} posts mais novos do perfil do brechó")
    for x in sel:
        x.pop("idx", None)
    return sorted(sel, key=lambda x: x["ts"])


# ─────────────────────────────────── tela do post ───────────────────────────────────
def _norm(s):
    import html
    import unicodedata
    s = unicodedata.normalize("NFKD", html.unescape(str(s or "")).replace("’", "'"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(s.split())


def _nos(xml):
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        bb = _bounds(t)
        if bb:
            g = lambda a: (re.search(a + r'="([^"]*)"', t) or [None, ""])[1]
            yield {"rid": g("resource-id"), "desc": g("content-desc"), "txt": g("text"), "bb": bb}


def ler_post(d, conta, nome=None):
    """O post ABERTO é do brechó (e é a peça `nome`)? Devolve o estado DESSE post (o 1º de cima — a
    tela de publicação mostra o seguinte logo abaixo) ou None:
    {like: bb, curtido, repost: bb|None, republicado, n_repost}."""
    try:
        xml = d.dump_hierarchy()
    except Exception:
        return None
    nos = list(_nos(xml))
    autores = [n for n in nos if n["rid"].endswith(":id/row_feed_photo_profile_name")]
    if not autores:
        return None
    topo = min(autores, key=lambda n: n["bb"][1])
    if not _norm(topo["txt"] or topo["desc"]).startswith(MARCA):
        return None
    y0 = topo["bb"][1]
    prox = [n["bb"][1] for n in autores if n["bb"][1] > y0]
    y1 = min(prox) if prox else 10 ** 6
    dentro = [n for n in nos if y0 <= n["bb"][1] < y1]
    if nome:
        alvo = _norm(nome)[:12]
        legs = sorted((n for n in dentro if _norm(n["txt"]).startswith(MARCA + " |")), key=lambda n: n["bb"][1])
        if alvo and legs and alvo not in _norm(legs[0]["txt"]):
            return None
    like = next((n for n in dentro if n["rid"].endswith(":id/row_feed_button_like")), None)
    if like is None:
        return None
    rep = next((n for n in dentro if n["desc"] == "Republicar"), None)
    n_rep = None
    if rep is not None:   # o número ao lado do ícone (Button com texto só de dígitos, à direita dele)
        lado = [n for n in dentro if n["txt"].isdigit() and n["bb"][0] >= rep["bb"][2] - 4
                and abs(n["bb"][1] - rep["bb"][1]) < 20 and n["bb"][0] - rep["bb"][2] < 30]
        n_rep = int(lado[0]["txt"]) if lado else 0
    eu = _norm(conta) + " partilhou"
    return {"like": like["bb"], "curtido": like["desc"].strip().lower() == "gostei",
            "repost": rep["bb"] if rep else None, "n_repost": n_rep,
            "republicado": any(_norm(n["desc"]).startswith(eu) for n in dentro), "xml": xml}


def _fechar_modal_repost(d):
    """O modal do 1º repost ("Republicaste a publicação…"): toca FECHAR. Nunca o "Anular"."""
    if not d(resourceId=rid("igds_headline_headline")).exists:
        return False
    for sel in (d(text="Fechar"), d(description="Fechar")):
        if sel.exists:
            sel.click()
            _sleep(1.0, 1.5)
            return True
    return False


def abrir_post(d, code, conta, nome, log=None, _de_novo=False):
    """Abre o post pelo link e CONFERE que é a peça certa do brechó. Devolve o estado (ler_post) ou None."""
    _fechar_modal_repost(d)
    # sai de qualquer tela com post (senão a espera "acha" o post anterior antes do link abrir)
    if d(resourceId=rid("row_feed_button_like")).exists and d(resourceId=rid("profile_tab")).exists:
        d(resourceId=rid("profile_tab")).click()
        _sleep(1.0, 1.5)
    d.shell(f"am start -a android.intent.action.VIEW -d https://www.instagram.com/p/{code}/ "
            f"-p com.instagram.android")
    fim = time.time() + 20
    while time.time() < fim:
        time.sleep(1.0)
        st = ler_post(d, conta, nome)
        if st:
            return st
    if not _de_novo:
        _log(log, "  o link não abriu o post da peça — tentando de novo")
        d.press("back")
        _sleep(1.0, 1.5)
        return abrir_post(d, code, conta, nome, log, _de_novo=True)
    return None


def _tocar(d, bb):
    d.click((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2)


def _esperar_toast_sumir(d, seg=10):
    """O aviso "Republicaste… Anular" fica uns segundos embaixo: espera sumir sem tocar nele (um
    Story logo depois na mesma conta tocaria o "Anular" e desfaria o repost)."""
    fim = time.time() + seg
    while time.time() < fim:
        if not d(text="Anular").exists and not d(description="Anular").exists:
            return
        time.sleep(1.0)


def curtir(d, conta, nome, st, log):
    """Toca no coração UMA vez e confere ("Gostei"). → 'ok' | 'falha' | 'bloqueio:<txt>'."""
    _tocar(d, st["like"])
    _sleep(2.5, 3.5)
    for _ in range(2):
        novo = ler_post(d, conta, nome)
        if novo and novo["curtido"]:
            return "ok"
        blk = af._bloqueio(_dump(d))
        if blk:
            return f"bloqueio:{blk}"
        time.sleep(2.0)
    return "falha"   # NÃO toca de novo: se colou atrasado, um 2º toque descurtiria


def republicar(d, conta, nome, st, log):
    """Toca em Republicar UMA vez, fecha o modal do 1º repost e confere (facepile da conta ou o número
    subir). → 'ok' | 'falha' | 'bloqueio:<txt>'."""
    antes = st.get("n_repost")
    _tocar(d, st["repost"])
    _sleep(2.0, 3.0)
    for _ in range(3):
        _fechar_modal_repost(d)
        blk = af._bloqueio(_dump(d))
        if blk:
            return f"bloqueio:{blk}"
        novo = ler_post(d, conta, nome)
        if novo and (novo["republicado"] or (antes is not None and (novo.get("n_repost") or 0) > antes)):
            return "ok"
        time.sleep(2.0)
    return "falha"   # NÃO toca de novo: o Republicar é toggle


# ─────────────────────────────────── orquestração ───────────────────────────────────
def processar_like_repost(d, log, conta, posts, params=None, parar_flag=None, rotulo=""):
    """Roda na conta JÁ ATIVA. Devolve {ok, motivo, curtidas, repostadas, puladas, falhas}."""
    t0 = time.time()
    try:
        return _processar(d, log, conta, posts, params, parar_flag, rotulo)
    except Exception as e:
        c = ((_estado_carregar().get(conta) or {}).get("posts") or {}).values()
        cur = sum(1 for x in c if (x.get("like") or 0) >= t0)
        rep = sum(1 for x in c if (x.get("repost") or 0) >= t0)
        _log(log, f"  erro no meio do Like + Repost ({e}) — {cur} curtida(s) e {rep} repost(s) já tinham saído")
        return {"ok": False, "motivo": f"erro:{e}", "curtidas": cur, "repostadas": rep, "puladas": 0, "falhas": 0}


def _processar(d, log, conta, posts, params=None, parar_flag=None, rotulo=""):
    from bots.dm_follow import dm_follow as dmf   # voltar_inicio / pausa_humana (mesmos do DM)
    p = _modo(params)
    dry = bool((params or {}).get("dry_run"))
    parar = parar_flag or (lambda: False)
    cont = {"curtidas": 0, "repostadas": 0, "puladas": 0, "falhas": 0}
    res = lambda ok, motivo: dict(cont, ok=ok, motivo=motivo)
    resfriar = float(p["resfriar_conta_h"] or 0)

    _log(log, f"Conta: @{conta}")
    if not (p["curtir"] or p["repostar"]):
        _log(log, "o modo está com curtir e republicar desligados — nada a fazer")
        return res(True, "fim")
    est = _estado_carregar()
    ate = (est.get(conta) or {}).get("pausada_ate") or 0
    if ate > time.time() and not dry:
        _log(log, f"@{conta} está descansando até {time.strftime('%d/%m %H:%M', time.localtime(ate))} "
                  f"(depois de bloqueio/soft-block) — pulada")
        return res(True, "resfriando")
    max_dia = int(p["max_acoes_dia"] or 0)
    no_dia = _acoes_desde(est, conta, 86400)
    if max_dia and no_dia >= max_dia and not dry:
        _log(log, f"@{conta} já fez {no_dia} ação(ões) nas últimas 24h — no limite do modo, pulada")
        return res(True, "limite_dia")

    pend = [x for x in posts if not _feito(est, conta, x["code"], p)]
    cont["puladas"] = len(posts) - len(pend)
    if cont["puladas"]:
        _log(log, f"{cont['puladas']} post(s) já feitos por esta conta — ficam de fora")
    if not pend:
        _log(log, "Nada novo pra curtir/republicar por enquanto.")
        return res(True, "fim")
    random.shuffle(pend)   # cada conta numa ordem (10 contas do mesmo aparelho na mesma sequência = padrão)
    cap = int(p["max_posts_conta"] or 0)
    total = min(len(pend), cap) if cap else len(pend)
    rot = (rotulo + " ") if rotulo else ""
    _log(log, f"Na fila: {len(pend)} post(s){f', até {cap} nesta run' if cap else ''}")
    _log(log, f"[progress] 0 {total} {rot}iniciando")

    sem_botao = False           # conta sem o "Republicar" (rollout do IG) → só curte
    falhas_like = falhas_rep = nao_abriu = 0
    feitos = desde_longa = 0
    plc = int(p["pausa_longa_cada"] or 0)
    for i, x in enumerate(pend, start=1):
        if parar():
            return res(True, "parado")
        if cap and feitos >= cap:
            _log(log, f"chegou no limite de {cap} post(s) desta run — o resto fica pra próxima")
            break
        if max_dia and no_dia >= max_dia and not dry:
            _log(log, f"chegou no limite de {max_dia} ações em 24h — o resto fica pra próxima")
            break
        code, nome = x["code"], x["nome"]
        rotulo = x.get("rotulo") or nome or code   # pré-drop não tem nome de peça
        _log(log, f"[progress] {feitos} {total} {rot}{rotulo}")
        st = abrir_post(d, code, conta, nome, log)
        if st is None:
            nao_abriu += 1
            cont["falhas"] += 1
            _log(log, f"! o post {code} ({nome}) não abriu — pulando ({nao_abriu} seguidos)")
            if af.desafio_humano(d):
                _log(log, f"⛔ @{conta} caiu na verificação humana (\"confirma que és uma pessoa\") — paro esta conta")
                return res(False, "desafio_humano")
            if nao_abriu >= 2:
                _log(log, f"! 2 posts seguidos não abriram pelo link nesta conta — paro a conta "
                          f"(o link do IG não está abrindo post nela)")
                dmf.voltar_inicio(d)
                return res(False, "link_nao_abre")
            continue
        nao_abriu = 0
        _sleep(*p["delay_acao_ui"])
        fez = False
        if p["curtir"]:
            if st["curtido"]:
                _log(log, f"• {rotulo} já estava curtido")
                if not dry:
                    _marcar(est, conta, code, "like")
            elif dry:
                _log(log, f"│ [dry] curtir {code} ({nome})")
                cont["curtidas"] += 1
            else:
                r = curtir(d, conta, nome, st, log)
                if r.startswith("bloqueio"):
                    _log(log, f"⛔ BLOQUEIO do Instagram ao curtir — parando a conta: {r.split(':', 1)[1]}")
                    _resfriar(est, conta, resfriar, log, "bloqueio")
                    dmf.voltar_inicio(d)
                    return res(False, r)
                if r == "ok":
                    _log(log, f"✓ curti {rotulo}")
                    _marcar(est, conta, code, "like")
                    cont["curtidas"] += 1
                    no_dia += 1
                    falhas_like, fez = 0, True
                else:
                    falhas_like += 1
                    cont["falhas"] += 1
                    _log(log, f"! a curtida em {rotulo} não colou ({falhas_like} seguidas)")
                    if af.desafio_humano(d):
                        _log(log, f"⛔ @{conta} caiu na verificação humana — paro esta conta")
                        return res(False, "desafio_humano")
                    if falhas_like >= MAX_FALHAS_SEGUIDAS:
                        _log(log, "⛔ BLOQUEIO provável: o IG desfez as curtidas (soft-block) — a conta descansa")
                        _resfriar(est, conta, resfriar, log, "soft-block de curtida")
                        dmf.voltar_inicio(d)
                        return res(False, "softblock_like")
        if p["repostar"]:
            st = ler_post(d, conta, nome) or st
            if st["republicado"]:
                _log(log, f"• {rotulo} já estava republicado")
                if not dry:
                    _marcar(est, conta, code, "repost")
            elif st["repost"] is None:
                if not sem_botao:
                    _log(log, f"! @{conta} não tem o botão Republicar (o IG ainda não liberou pra ela) — "
                              f"esta conta só curte")
                    sem_botao = True
                if not dry:
                    _marcar(est, conta, code, "sem_repost")
            elif dry:
                _log(log, f"│ [dry] republicar {code} ({nome})")
                cont["repostadas"] += 1
            else:
                _sleep(*p["delay_acao_ui"])
                r = republicar(d, conta, nome, st, log)
                if r.startswith("bloqueio"):
                    _log(log, f"⛔ BLOQUEIO do Instagram ao republicar — parando a conta: {r.split(':', 1)[1]}")
                    _resfriar(est, conta, resfriar, log, "bloqueio")
                    dmf.voltar_inicio(d)
                    return res(False, r)
                if r == "ok":
                    _log(log, f"✓ republiquei {rotulo}")
                    _marcar(est, conta, code, "repost")
                    cont["repostadas"] += 1
                    no_dia += 1
                    falhas_rep, fez = 0, True
                else:
                    falhas_rep += 1
                    cont["falhas"] += 1
                    _log(log, f"! o repost de {rotulo} não colou ({falhas_rep} seguidos)")
                    if af.desafio_humano(d):
                        _log(log, f"⛔ @{conta} caiu na verificação humana — paro esta conta")
                        return res(False, "desafio_humano")
                    if falhas_rep >= MAX_FALHAS_SEGUIDAS:
                        _log(log, "⛔ BLOQUEIO provável: os reposts não colaram (soft-block) — a conta descansa")
                        _resfriar(est, conta, resfriar, log, "soft-block de repost")
                        dmf.voltar_inicio(d)
                        return res(False, "softblock_repost")
                _esperar_toast_sumir(d)
        if fez or dry:
            feitos += 1
        _log(log, f"[progress] {feitos} {total} {rot}{rotulo}")
        if i == len(pend) or (cap and feitos >= cap):
            break
        if fez:
            desde_longa += 1
        if plc and fez and desde_longa >= plc and not dry:
            desde_longa = 0
            dmf.pausa_humana(d, log, parar)
            seg, motivo = random.uniform(*p["pausa_longa"]), "pausa longa"
        else:
            seg, motivo = (random.uniform(*p["delay_post"]) if not dry else 1.0), "entre posts"
        _log(log, f"dormindo {int(seg)}s ({motivo})")
        if not dmf._dormir(seg, motivo, log, parar):
            return res(True, "parado")
    _esperar_toast_sumir(d)
    dmf.voltar_inicio(d)
    _log(log, f"[progress] {total} {total} {rot}concluído")
    return res(True, "fim")
