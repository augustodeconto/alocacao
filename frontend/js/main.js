import {
  api, getAutor, setAutor,
  getPeriodoInicial, setPeriodoInicial, getPeriodoFinal, setPeriodoFinal,
  getPeriodoAtivo, setPeriodoAtivo,
} from "./api.js";
import { bindScrollSync, nextSyncMode, syncFlags, scrollRowIntoView, SYNC_MODES } from "./scroll-sync.js";
import { initExcel } from "./grid-excel.js";

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
    else if (k in n) n[k] = v;
    else n.setAttribute(k, v);
  }
  for (const k of kids) n.append(k && k.nodeType ? k : document.createTextNode(k));
  return n;
};

const S = {
  estado: null,
  displayUnit: localStorage.getItem("displayUnit") || "horas",
  syncMode: SYNC_MODES.includes(localStorage.getItem("syncMode")) ? localStorage.getItem("syncMode") : "↓↑",
  open: new Set(JSON.parse(localStorage.getItem("open") || "[]")),  // nós expandidos; vazio = tudo recolhido
  gpFilter: localStorage.getItem("gpFilter") || "",
  selMatricula: null,
  selAloc: null,
  extraTail: 0,   // meses extras à direita, adicionados na tela para lançar horas além de tudo
  view: localStorage.getItem("view") || "alloc",       // alloc | cad | ver
  theme: localStorage.getItem("theme") || "auto",      // auto | light | dark
  secW: parseFloat(localStorage.getItem("secW")) || 300,
  cadTab: "projetos",
  secProjId: null,   // projeto do painel lateral (modo 'projeto')
  secPessoaMat: null,   // matrícula do painel lateral (modo 'pessoa')
  secModo: "projeto",   // 'projeto' | 'pessoa' — o que o painel lateral está mostrando
  secPanel: localStorage.getItem("secPanel") || null,   // null | 'custo'
};

let PERIODOS = [];  // meses exibidos = união do servidor + extraTail

function addMonthISO(iso, n) {
  const [y, m] = iso.split("-").map(Number);
  const d = new Date(y, m - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function computePeriodos() {
  const base = (S.estado.grade.periodos || []).slice();
  let tail = base.length ? base[base.length - 1] : currentMonthISO();
  for (let i = 0; i < S.extraTail; i++) {
    tail = addMonthISO(tail, 1);
    base.push(tail);
  }
  return base;
}

const panelProj = $("#panel-projeto");
const panelRec = $("#panel-recurso");
const gridProj = $("#grid-projeto");
const gridRec = $("#grid-recurso");

// -- helpers -----------------------------------------------------------
function fmtMes(periodo) {
  const [y, m] = periodo.split("-");
  return `${m}/${y.slice(2)}`;
}
function log(msg, isErr) {
  $("#log").innerHTML = "";
  $("#log").append(el("span", { className: isErr ? "err" : "" }, msg));
}
function persistOpen() {
  localStorage.setItem("open", JSON.stringify([...S.open]));
}
function isCollapsed(key) { return !S.open.has(key); }
function toggle(key) {
  S.open.has(key) ? S.open.delete(key) : S.open.add(key);
  persistOpen();
  render();
}
function keysFor(which) {
  const gr = S.estado.grade;
  const ks = [];
  if (which === "projeto") {
    for (const p of gr.por_projeto) {
      ks.push(`p:${p.projeto_id}`);
      for (const grp of p.grupos) ks.push(`g:${p.projeto_id}:${grp.tipo_alocacao}`);
    }
  } else {
    for (const r of gr.por_recurso) ks.push(`r:${r.matricula}`);
  }
  return ks;
}
function setOpen(which, open) {
  const ks = new Set(keysFor(which));
  if (open) ks.forEach((k) => S.open.add(k));
  else [...S.open].forEach((k) => ks.has(k) && S.open.delete(k));
  persistOpen();
  render();
}

function pct(horas, cap) { return cap ? Math.round((horas / cap) * 100) + "%" : "–"; }
function pctNum(horas, cap) { return cap ? Math.round((horas / cap) * 100) : ""; }

function cellValueNodes(horas, cap) {
  const main = S.displayUnit === "horas" ? String(horas) : pct(horas, cap);
  const twin = S.displayUnit === "horas" ? (cap ? pct(horas, cap) : "") : String(horas);
  const frag = document.createDocumentFragment();
  frag.append(document.createTextNode(main));
  if (twin) frag.append(el("span", { className: "twin" }, twin));
  return frag;
}

// -- rendering -------------------------------------------------------
let CONFIGURED = new Set();  // meses dentro da janela de algum projeto

function headerRow(periodos, titulo) {
  const tr = el("tr");
  const th = el("th", { className: "treecol" }, titulo || "—");
  th.append(el("span", { className: "grip", title: "arraste para redimensionar a coluna",
    onmousedown: startColResize }));
  tr.append(th);
  for (const p of periodos) {
    const cls = "month" + (CONFIGURED.has(p) ? "" : " extra");
    tr.append(el("th", { className: cls, dataset: { periodo: p } }, fmtMes(p)));
  }
  // "+ mês": coluna extra no fim — ao clicar, nasce um mês e o botão anda pra direita
  tr.append(el("th", { className: "mes-add" },
    el("button", { title: "acrescentar mês ao final", textContent: "＋",
      onclick: () => { S.extraTail++; render(); } })));
  return el("thead", {}, tr);
}

function treeCell(text, { level = 0, key, expandable, extra } = {}) {
  const td = el("td", { className: `treecol ind${level}` });
  if (expandable) {
    td.append(el("span", {
      className: "tw-toggle",
      textContent: isCollapsed(key) ? "▸" : "▾",
      onclick: (e) => { e.stopPropagation(); toggle(key); },
    }));
  } else if (level >= 1) {
    td.append(el("span", { className: "tw-toggle", textContent: "" }));
  }
  td.append(document.createTextNode(" " + text));
  if (extra) td.append(extra);
  return td;
}

function rowActions(f, projetoId, label, { comTipo = false } = {}) {
  const wrap = el("span", { className: "rowacts" });
  if (f.removido) {
    wrap.append(el("span", {
      className: "tw-toggle", title: "restaurar (desfazer remoção)", textContent: " ↩",
      onclick: async (e) => {
        e.stopPropagation();
        try {
          S.estado = (await api.restaurarAlocacao(projetoId, f.matricula, f.tipo_alocacao)).estado;
          render();
        } catch (err) { log(err.message, true); }
      },
    }));
    return wrap;
  }
  if (comTipo) {
    const tipos = (S.estado.catalogos.tipo_alocacao || []).map((t) => t.texto);
    if (tipos.length && f.alocacao_id) {
      const sel = el("select", {
        className: "tipo-sel", title: "mover para outro tipo",
        onclick: (e) => e.stopPropagation(),
        onchange: async (e) => {
          try { S.estado = (await api.mudarTipo(f.alocacao_id, e.target.value)).estado; render(); }
          catch (err) { log(err.message, true); render(); }
        },
      }, ...tipos.map((t) => el("option", { value: t, textContent: t, selected: t === f.tipo_alocacao })));
      wrap.append(sel);
    }
  }
  wrap.append(el("span", {
    className: "tw-toggle", title: "remover do projeto", textContent: " ✕",
    onclick: async (e) => {
      e.stopPropagation();
      if (!confirm(`Remover ${label} deste projeto?`)) return;
      try { S.estado = (await api.removerAlocacao(f.alocacao_id)).estado; render(); }
      catch (err) { log(err.message, true); }
    },
  }));
  return wrap;
}

function monthCell(cls, contentFrag, { extra, colorClass, alt, title, per } = {}) {
  const td = el("td", { className: `month ${cls}` });
  // `per` fica em toda célula de mês (editável, total ou addrow vazia) — é o que
  // permite à camada de seleção (grid-excel.js) tratar QUALQUER célula da grade
  // como selecionável/copiável, não só as editáveis.
  if (per) td.dataset.per = per;
  if (extra) td.classList.add("extra");
  if (alt) td.classList.add("alt");
  if (colorClass) td.classList.add(colorClass);
  const sevTitle = colorClass && SEV_TITLE[colorClass];
  const fullTitle = title && sevTitle ? `${sevTitle} · ${title}` : title || sevTitle;
  if (fullTitle) td.title = fullTitle;
  if (contentFrag != null) td.append(contentFrag);
  return td;
}

// marca de célula alterada vs. baseline + tooltip
function altInfo(f, p) {
  if (f.removido || !f.alterado || !f.alterado.includes(p)) return {};
  const base = f.base_horas ? (f.base_horas[p] || 0) : 0;
  return { alt: true, title: f.novo ? "nova alocação" : `era: ${base}` };
}
// idem para linhas de total (projeto / tipo / pessoa) — mudança indireta
function totAltInfo(node, p) {
  if (!node.totais_alterado || !node.totais_alterado.includes(p)) return {};
  const base = node.totais_base ? (node.totais_base[p] || 0) : 0;
  return { alt: true, title: `era: ${base}` };
}
function totalCell(node, p, { extra } = {}) {
  const a = totAltInfo(node, p);
  const has = p in node.totais;
  const frag = (has || a.alt) ? document.createTextNode(String(has ? node.totais[p] : 0)) : null;
  return monthCell("total", frag, { extra, per: p, ...a });
}

// subalocação (vermelho) é mais grave que superalocação (amarelo) pra planejamento de
// capacidade — recurso ocioso preocupa mais que recurso sobrecarregado (ver
// aggregate.cor_pessoa_mes). "over"/"under" aqui são nomes semânticos (não de cor).
function colorClassFor(cores, periodo) {
  const c = cores && cores[periodo];
  return c === "amarelo" ? "over" : c === "vermelho" ? "under" : null;
}

// sub/superalocação: barra colorida na borda inferior da célula (ver monthCell/CSS
// `.month.over`/`.month.under`) — substitui o ícone antigo, pesado demais nos 62px de
// largura da coluna de mês. O texto do tooltip fica centralizado aqui.
const SEV_TITLE = {
  over: "superalocado — acima da capacidade",
  under: "subalocado — abaixo da capacidade",
};

// Linha de alocação que vale a pena mostrar: some só se nenhum mês dentro da janela do
// filtro de período (docs/ESPECIFICACAO.md §8) tiver hora dela — mesmo critério das
// colunas, então nunca existe total visível sem a linha que o explica. Com o filtro
// desligado a janela é o histórico inteiro, então isso já mostra tudo sozinho. Linhas
// novas (fora da baseline) e removidas continuam visíveis — foram mexidas de propósito.
function filhoVisivel(f) {
  return f.novo || f.removido || f.tem_horas_no_periodo;
}


function renderGridProjeto() {
  const g = S.estado.grade;
  gridProj.innerHTML = "";
  gridProj.append(headerRow(PERIODOS, "Projeto · Tipo · Pessoa"));
  const tb = el("tbody");

  for (const proj of g.por_projeto) {
    if (!gpVisivel(proj)) continue;
    if (!proj.visivel_no_periodo) continue;
    const key = `p:${proj.projeto_id}`;
    const janela = new Set(proj.periodos_projeto);
    const acoes = el("span");
    acoes.append(
      proj.sujo ? el("span", { title: "alterações não salvas", textContent: " ●" }) : "",
      el("span", {
        className: "tw-toggle", title: "descarta as alterações não salvas deste projeto (reset)",
        textContent: " ↺",
        onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`Descartar as alterações não salvas de "${proj.nome}"?\nVolta ao estado da última versão salva.`)) return;
          try { S.estado = (await api.descartarProjeto(proj.projeto_id)).estado; render(); }
          catch (err) { log(err.message, true); }
        },
      }),
    );
    let nomeProj = proj.nome;
    if (proj.encerrado) nomeProj += "  (encerrado)";
    const tr0 = el("tr", {
      className: "lvl0" + (proj.encerrado ? " encerrado" : ""),
      dataset: { projetoId: proj.projeto_id },
    });
    tr0.append(treeCell(nomeProj, { level: 0, key, expandable: true, extra: acoes }));
    for (const p of PERIODOS) tr0.append(totalCell(proj, p, { extra: !janela.has(p) }));
    tb.append(tr0);
    if (isCollapsed(key)) continue;

    for (const grp of proj.grupos) {
      const gkey = `g:${proj.projeto_id}:${grp.tipo_alocacao}`;
      const visFilhos = grp.filhos.filter(filhoVisivel);
      if (!visFilhos.length && !grp.modificado) continue;
      const trg = el("tr", { className: "lvl1 grupo" });
      trg.append(treeCell(grp.tipo_alocacao, {
        level: 1, key: gkey, expandable: true,
        extra: grp.modificado ? el("span", { className: "badge-mod", textContent: "●", title: "tem alterações não exportadas" }) : null,
      }));
      for (const p of PERIODOS) trg.append(totalCell(grp, p, { extra: !janela.has(p) }));
      tb.append(trg);
      if (isCollapsed(gkey)) continue;

      for (const f of visFilhos) {
        const rem = !!f.removido;
        const tr = el("tr", {
          className: "lvl2" + (rem ? " removido" : "") + (f.novo ? " novo" : ""),
          dataset: rem ? { matricula: f.matricula } : { matricula: f.matricula, alocacaoId: f.alocacao_id },
        });
        tr.append(treeCell(f.nome, {
          level: 2, extra: rowActions(f, proj.projeto_id, `${f.nome} — ${f.tipo_alocacao}`, { comTipo: true }),
        }));
        for (const p of PERIODOS) {
          const h = f.horas[p] || 0;
          const sev = rem ? null : colorClassFor(f.cores, p);
          tr.append(monthCell(rem ? "cell" : "cell editable", cellValueNodes(h, f.capacidade_mensal),
            { per: p, extra: !janela.has(p), colorClass: sev, ...altInfo(f, p) }));
        }
        tb.append(tr);
      }
      tb.append(addRowEscolha("+ adicionar pessoa", "ind2", {
        projeto_id: proj.projeto_id, tipo_alocacao: grp.tipo_alocacao,
        excluir: new Set(grp.filhos.filter((f) => !f.removido).map((f) => f.matricula)),
      }));
    }
    tb.append(addRowNovoTipo("+ adicionar tipo", "ind1", proj));
  }
  gridProj.append(tb);
}

function renderGridRecurso() {
  const g = S.estado.grade;
  gridRec.innerHTML = "";
  gridRec.append(headerRow(PERIODOS, "Pessoa · Projeto"));
  const tb = el("tbody");

  for (const pes of g.por_recurso) {
    if (!pes.visivel_no_periodo) continue;
    // O filtro de GP é um filtro de PROJETOS: age só na grade "Por Projeto".
    // "Por Recurso" sempre mostra todos os projetos da pessoa e o total real.
    // Esconde só as linhas puramente históricas (ver filhoVisivel).
    const filhos = pes.filhos.filter(filhoVisivel);
    const key = `r:${pes.matricula}`;
    const tr0 = el("tr", { className: "lvl0", dataset: { matricula: pes.matricula } });
    tr0.append(treeCell(pes.nome, { level: 0, key, expandable: true }));
    for (const p of PERIODOS) {
      const sev = colorClassFor(pes.cores, p);
      tr0.append(monthCell("total", cellValueNodes(pes.totais[p] || 0, pes.capacidade_mensal),
        { colorClass: sev, ...totAltInfo(pes, p) }));
    }
    tb.append(tr0);

    if (isCollapsed(key)) continue;
    for (const f of filhos) {
      const janela = new Set(f.periodos_projeto);
      const tr = el("tr", {
        className: "lvl1" + (f.novo ? " novo" : ""),
        dataset: { matricula: pes.matricula, alocacaoId: f.alocacao_id },
      });
      tr.append(treeCell(`${f.projeto_nome} / ${f.tipo_alocacao}`, {
        level: 1, extra: rowActions(f, f.projeto_id, `${pes.nome} — ${f.projeto_nome} / ${f.tipo_alocacao}`, { comTipo: true }),
      }));
      for (const p of PERIODOS) {
        const h = f.horas[p] || 0;
        tr.append(monthCell("cell editable", cellValueNodes(h, pes.capacidade_mensal),
          { per: p, extra: !janela.has(p), ...altInfo(f, p) }));
      }
      tb.append(tr);
    }
    tb.append(addRowEscolha("+ adicionar projeto", "ind1", { matricula: pes.matricula, nome: pes.nome }));
  }
  gridRec.append(tb);
}

function render() {
  if (!S.estado) return;
  try {
    _render();
  } catch (err) {
    log("erro ao renderizar: " + err.message, true);
    console.error(err);
  }
}

// -- filtro de GP: chave = matrícula do GP quando existe; senão, o nome (a
// importação do BI só traz `gestor_projetos` — nome — nunca a matrícula) --------------
const GP_SEM = "__sem_gp__";                 // valor da opção "— sem GP —"
const GP_NOME_PFX = "nome:";                 // prefixo pra não colidir com chave-matrícula
// chave do GP do projeto; "" quando o projeto não tem GP nenhum
function gpDe(proj) {
  const m = proj && proj.matricula_gp;
  if (m != null && String(m).trim() !== "") return String(m).trim();
  const n = proj && proj.gestor_projetos;
  return n == null || String(n).trim() === "" ? "" : GP_NOME_PFX + String(n).trim();
}

function gpNome(chave) {
  if (!chave) return "(sem GP)";
  if (chave.startsWith(GP_NOME_PFX)) return chave.slice(GP_NOME_PFX.length);
  const pe = (S.estado.pessoas || []).find((p) => String(p.matricula) === chave);
  if (pe && pe.nome) return pe.nome;
  const pr = (S.estado.projetos || []).find((p) => gpDe(p) === chave && p.gestor_projetos);
  return (pr && pr.gestor_projetos) || chave;   // sem nome conhecido: mostra a própria matrícula
}

function gpVisivel(proj) {
  if (!S.gpFilter) return true;
  if (S.gpFilter === GP_SEM) return !gpDe(proj);
  return gpDe(proj) === S.gpFilter;
}

function refreshGpFilter() {
  const sel = $("#gp-filter");
  const mats = [...new Set(S.estado.projetos.map(gpDe).filter(Boolean))]
    .sort((a, b) => gpNome(a).localeCompare(gpNome(b)));
  const temSem = S.estado.projetos.some((p) => !gpDe(p));
  sel.innerHTML = "";
  sel.append(el("option", { value: "", textContent: "Todos os GPs" }));
  if (temSem) sel.append(el("option", { value: GP_SEM, textContent: "— sem GP —" }));
  for (const m of mats) {
    const nome = gpNome(m);
    const temMatricula = !m.startsWith(GP_NOME_PFX);
    sel.append(el("option", {
      value: m, selected: m === S.gpFilter,
      textContent: temMatricula && nome !== m ? `${nome}  ·  ${m}` : nome,
    }));
  }
  const validos = new Set(["", GP_SEM, ...mats]);
  if (!validos.has(S.gpFilter)) { S.gpFilter = ""; localStorage.setItem("gpFilter", ""); }
  sel.value = S.gpFilter;
}

// -- undo/redo de edições de célula -------------------------------------
const UNDO = [], REDO = [];
let HORAS_IDX = {};   // alocacao_id -> { periodo: horas } (do estado atual)
let ALOC_INFO = {};   // alocacao_id -> { projeto_id, tipo, matricula }

function horasAtual(id, per) { return (HORAS_IDX[id] || {})[per] || 0; }

// anexa a identidade (projeto_id/matricula/tipo) para o backend poder recriar a
// alocação num undo/redo/colar quando ela tiver sido zerada por completo.
function comIdent(e) {
  const info = ALOC_INFO[e.alocacao_id] || {};
  return {
    alocacao_id: e.alocacao_id, periodo: e.periodo, valor: e.valor,
    projeto_id: e.projeto_id ?? info.projeto_id,
    matricula: e.matricula ?? info.matricula,
    tipo_alocacao: e.tipo_alocacao ?? info.tipo,
  };
}

function snapshot(edits) {
  return edits.map((e) => ({
    ...comIdent(e),
    valor: String(horasAtual(e.alocacao_id, e.periodo)),
  }));
}

async function applyBatch(edits, { undoable = true } = {}) {
  if (!edits.length) return;
  if (undoable) {
    UNDO.push(snapshot(edits));
    if (UNDO.length > 100) UNDO.shift();
    REDO.length = 0;
  }
  try {
    S.estado = (await api.editarMesLote(edits.map(comIdent))).estado;
    render();
  } catch (err) { log(err.message, true); render(); }
}

async function undo() {
  if (!UNDO.length) { log("nada para desfazer"); return; }
  const before = UNDO.pop();
  REDO.push(snapshot(before));
  try { S.estado = (await api.editarMesLote(before)).estado; log("desfeito"); render(); }
  catch (err) { log(err.message, true); render(); }
}

async function redo() {
  if (!REDO.length) { log("nada para refazer"); return; }
  const after = REDO.pop();
  UNDO.push(snapshot(after));
  try { S.estado = (await api.editarMesLote(after)).estado; log("refeito"); render(); }
  catch (err) { log(err.message, true); render(); }
}

// -- menu de contexto: ajuste rápido de alocação (botão direito numa célula) --------
// ícone "nível" (barrinha), reaproveitado no botão largo (100%, cheio) e nos 5
// pequenos (100/75/50/25/0%, cada um com o preenchimento proporcional).
function _iconNivel(frac) {
  const w = Math.max(0, Math.min(12, 12 * frac)).toFixed(1);
  return `<svg viewBox="0 0 16 10" width="20" height="13" aria-hidden="true">
    <rect x="0.75" y="0.75" width="14.5" height="8.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.1"/>
    <rect x="2" y="2" width="${w}" height="6" rx="0.6" fill="currentColor"/>
  </svg>`;
}
const ALOC_CTX_NIVEIS = [100, 75, 50, 25, 0];

function hideAlocCtx() { const m = $("#aloc-ctx"); if (m) m.hidden = true; }

// `cells` = [{alocacaoId, periodo}] — a seleção inteira, ou só a célula clicada (ver
// EG.cellsPara em grid-excel.js). Cada célula pode ser de uma pessoa diferente, então
// o ajuste (quantas horas = 100%) é calculado por célula, nunca um valor só pra todas.
// total da pessoa (todos os projetos/tipos) naquele mês — já vem pronto do servidor
// em por_recurso (aggregate.build_grade), é o mesmo número comparado à capacidade.
function totalPessoaNoMes(matricula, periodo) {
  const rec = (S.estado?.grade?.por_recurso || []).find((r) => r.matricula === matricula);
  return (rec && rec.totais[periodo]) || 0;
}

function abrirAjusteAlocacao(cells, ev) {
  const validas = cells
    .map((c) => ({
      ...c,
      cap: ALOC_INFO[c.alocacaoId]?.capacidade_mensal,
      matricula: ALOC_INFO[c.alocacaoId]?.matricula,
    }))
    .filter((c) => c.cap);
  if (!validas.length) return;   // ninguém na seleção tem capacidade cadastrada

  // "Normalizar" (100%) é CIENTE do total da pessoa em TODOS os projetos — completa
  // só o que falta pra fechar 100% no total dela, nunca sugere passar disso (corrigido
  // em 2026-09-12: antes sugeria cap inteiro mesmo com outras alocações já ocupando a
  // capacidade). `outras` = tudo que já é dela naquele mês FORA desta célula.
  const outras = (c) => totalPessoaNoMes(c.matricula, c.periodo) - horasAtual(c.alocacaoId, c.periodo);
  const alvoNormalizar = (c) => Math.max(0, Math.round(c.cap - outras(c)));

  // Os botões de nível (100/75/50/25/0%) são um ajuste DIRETO desta linha — o
  // usuário está decidindo de propósito quanto ela vale, então (diferente de
  // "Normalizar") NÃO descontam outras alocações da pessoa. Bug corrigido em
  // 2026-09-13: usar a conta ciente-do-total aqui também fazia os níveis sempre
  // sugerirem 0h quando a pessoa já estava em 100% em OUTRO projeto, mesmo
  // quando o usuário queria fixar esta linha num valor específico de propósito.
  const alvoNivel = (c, pct) => Math.max(0, Math.round(c.cap * pct / 100));

  const aplicarNormalizar = async () => {
    const edits = validas.map((c) => ({
      alocacao_id: c.alocacaoId, periodo: c.periodo, valor: String(alvoNormalizar(c)),
    }));
    hideAlocCtx();
    await applyBatch(edits);
  };
  const aplicarNivel = async (pct) => {
    const edits = validas.map((c) => ({
      alocacao_id: c.alocacaoId, periodo: c.periodo, valor: String(alvoNivel(c, pct)),
    }));
    hideAlocCtx();
    await applyBatch(edits);
  };

  // "(+35h)" no botão de normalizar: quanto ESTA linha precisa mudar pra fechar o
  // total da pessoa em 100% da capacidade naquele mês (considerando as outras
  // alocações dela) — mesmo delta pra todo mundo na seleção, ou "vários" se variar.
  const deltas = validas.map((c) => alvoNormalizar(c) - horasAtual(c.alocacaoId, c.periodo));
  const deltaTxt = new Set(deltas).size === 1 ? `${deltas[0] > 0 ? "+" : ""}${deltas[0]}h` : "vários";
  // já está em 100% (somando tudo) pra toda a seleção -> normalizar seria um no-op.
  const jaNormalizado = deltas.every((d) => d === 0);

  const m = $("#aloc-ctx");
  m.innerHTML = "";
  const btnNorm = el("button", {
    className: "aloc-normalizar",
    title: "Ajusta para alocação plena (100% do total da pessoa, considerando outras alocações dela)",
    disabled: jaNormalizado,
    onclick: aplicarNormalizar,
  });
  // quebra fixa em 2 linhas de propósito — sem isso, a largura do "(+35h)"/"(0h)"
  // varia e às vezes deixa "Normalizar alocação" numa linha só, às vezes quebra;
  // isso mudava a altura do botão dependendo do valor. Forçando sempre 2 linhas, a
  // altura fica igual à fileira de baixo não importa o texto do delta.
  btnNorm.innerHTML = `${_iconNivel(1)}<span class="txt">Normalizar<br>alocação</span><span class="delta">(${deltaTxt})</span>`;
  m.append(btnNorm);

  const row = el("div", { className: "aloc-niveis" });
  for (const pct of ALOC_CTX_NIVEIS) {
    const alvos = validas.map((c) => alvoNivel(c, pct));
    const alvosIguais = new Set(alvos).size === 1;
    const rotulo = S.displayUnit === "pct" ? `${pct}%`
      : alvosIguais ? `${alvos[0]}h` : `${pct}%`;
    const btn = el("button", {
      title: "Define esta linha diretamente — não desconta outras alocações da pessoa",
      onclick: () => aplicarNivel(pct),
    });
    btn.innerHTML = `${_iconNivel(pct / 100)}<span>${rotulo}</span>`;
    row.append(btn);
  }
  m.append(row);

  m.hidden = false;
  const mw = m.offsetWidth || 220, mh = m.offsetHeight || 90;
  m.style.left = Math.min(ev.clientX, window.innerWidth - mw - 6) + "px";
  m.style.top = Math.min(ev.clientY, window.innerHeight - mh - 6) + "px";
}

function _render() {
  renderUsuarioBadge();
  renderBranchBadge($("#branch-badge-alloc"), S.estado.versao);
  renderBranchBadge($("#branch-badge-cad"), S.estado.versao);
  const sp = { pl: panelProj.scrollLeft, pt: panelProj.scrollTop, rl: panelRec.scrollLeft, rt: panelRec.scrollTop };
  PERIODOS = computePeriodos();
  refreshGpFilter();
  HORAS_IDX = {};
  ALOC_INFO = {};
  for (const p of S.estado.grade.por_projeto)
    for (const g of p.grupos)
      for (const f of g.filhos)
        if (f.alocacao_id != null) {
          HORAS_IDX[f.alocacao_id] = f.horas;
          ALOC_INFO[f.alocacao_id] = {
            projeto_id: p.projeto_id, tipo: g.tipo_alocacao, matricula: f.matricula,
            capacidade_mensal: f.capacidade_mensal,
          };
        }
  CONFIGURED = new Set(S.estado.grade.por_projeto.flatMap((p) => p.periodos_projeto));
  renderGridProjeto();
  renderGridRecurso();
  applySelection();
  if (EXCEL) EXCEL.rebuild();
  const pp = S.estado.grade.por_projeto;
  const visiveis = pp.filter((p) => gpVisivel(p) && p.visivel_no_periodo).length;
  const sujos = pp.filter((p) => p.sujo).length;
  $("#proj-status").textContent =
    `${visiveis}/${pp.length} projeto(s)` + (sujos ? ` · ${sujos} não exportado(s)` : "");
  atualizarBtnPeriodo(g);
  if (S.view === "ver") renderVer();
  if (!S.scrollInit && PERIODOS.length) {
    S.scrollInit = true;
    scrollToCurrentMonth();
  } else {
    panelProj.scrollLeft = sp.pl; panelProj.scrollTop = sp.pt;
    panelRec.scrollLeft = sp.rl; panelRec.scrollTop = sp.rt;
  }
}

function currentMonthISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function fmtMesCurto(iso) {
  if (!iso) return null;
  const [y, m] = iso.split("-");
  return `${m}/${y.slice(2)}`;
}

// Rótulo do botão reflete o filtro efetivamente aplicado pelo servidor (grade.periodo_*),
// não o que está nos campos do popover — evita rótulo desatualizado se outra aba/sessão
// mudou o padrão, e cobre o caso "ainda não configurado" == mês atual.
function atualizarBtnPeriodo(g) {
  const btn = $("#btn-periodo");
  if (!btn) return;
  if (!g.periodo_ativo) {
    btn.textContent = "Período: todos";
    btn.classList.remove("ativo");
    return;
  }
  const de = fmtMesCurto(g.periodo_inicial);
  const ate = g.periodo_final ? fmtMesCurto(g.periodo_final) : "aberto";
  btn.textContent = `Período: ${de}→${ate}`;
  btn.classList.add("ativo");
}

function hidePeriodoCtx() { const m = $("#periodo-ctx"); if (m) m.hidden = true; }

// Copiar os resumos do painel lateral (custo/pessoa) pro Excel: os trechos tabelados
// (KPIs, legenda do donut, barras de custo/mês, projetos, mês a mês) marcam suas linhas
// com `data-copy-row` e cada campo com `data-copy-cell` (mais `data-copy-value` quando o
// valor a copiar é diferente do texto exibido — ex. "1.234,56" formatado na tela vira o
// número puro "1234.56" na cópia; horas/percentual idem, sem "h"/"%"). Só entra em ação
// se a seleção nativa do navegador (arrastar o mouse) estiver dentro de #sec-body — a
// seleção retangular das duas grades (grid-excel.js) usa outro mecanismo e se anula
// sozinha quando detecta essa mesma seleção nativa fora dela.
function _copySecBody(e) {
  const sec = $("#sec-body");
  if (!sec) return;
  const dom = window.getSelection();
  if (!dom || dom.isCollapsed || !dom.rangeCount) return;
  if (!sec.contains(dom.getRangeAt(0).commonAncestorContainer)) return;
  const linhas = Array.from(sec.querySelectorAll("[data-copy-row]")).filter((r) => dom.containsNode(r, true));
  if (!linhas.length) return;
  e.preventDefault();
  const tsv = linhas.map((r) =>
    Array.from(r.querySelectorAll("[data-copy-cell]"))
      .map((c) => (c.dataset.copyValue ?? c.textContent).trim())
      .join("\t")
  ).join("\n");
  e.clipboardData.setData("text/plain", tsv);
}

// Popover ao lado do botão "hoje" (docs/ESPECIFICACAO.md §8): período inicial/final da
// janela de meses exibida nas duas grades, mais a opção de desligar o filtro inteiro
// (mostra tudo, sem corte nenhum — nem período inicial). Os campos partem do que o
// servidor resolveu (g.periodo_*), não de S, pra sempre refletir o estado real aplicado.
function abrirPeriodoCtx(ev) {
  const m = $("#periodo-ctx");
  const g = S.estado.grade;
  const chk = $("#periodo-ativo-chk");
  const ini = $("#periodo-inicial-inp");
  const fim = $("#periodo-final-inp");
  chk.checked = g.periodo_ativo;
  ini.value = g.periodo_inicial || "";
  fim.value = g.periodo_final || "";
  ini.disabled = fim.disabled = !g.periodo_ativo;

  chk.onchange = async () => {
    setPeriodoAtivo(chk.checked);
    ini.disabled = fim.disabled = !chk.checked;
    try { S.estado = await api.estado(); render(); } catch (err) { log(err.message, true); }
  };
  const aplicarDatas = async () => {
    setPeriodoInicial(ini.value);
    setPeriodoFinal(fim.value);
    try { S.estado = await api.estado(); render(); } catch (err) { log(err.message, true); }
  };
  ini.onchange = aplicarDatas;
  fim.onchange = aplicarDatas;

  m.hidden = false;
  const r = ev.currentTarget.getBoundingClientRect();
  const mw = m.offsetWidth || 220;
  m.style.left = Math.min(r.left, window.innerWidth - mw - 6) + "px";
  m.style.top = (r.bottom + 4) + "px";
}

const MONTH_W = 62;  // largura fixa da coluna de mês (ver CSS)

function scrollToCurrentMonth() {
  if (!PERIODOS.length) return;
  const cur = currentMonthISO();
  let idx = PERIODOS.indexOf(cur);
  if (idx < 0) idx = PERIODOS.findIndex((p) => p >= cur);
  if (idx < 0) idx = PERIODOS.length - 1;
  // scrollLeft em coords de conteúdo: idx colunas de mês à esquerda do alvo
  // (a 1ª coluna é fixa e não conta). Deixa o mês atual logo após ela.
  const alvo = Math.max(0, idx * MONTH_W);
  const aplicar = () => { panelProj.scrollLeft = alvo; panelRec.scrollLeft = alvo; };
  // 1 rAF às vezes não é suficiente (fonte carregando, sticky recalculando, sync de
  // scroll entre os dois painéis) — dá 2 frames + reforça uma vez mais tarde.
  requestAnimationFrame(() => requestAnimationFrame(aplicar));
  setTimeout(aplicar, 80);
}

// -- selection sync --------------------------------------------------
function applySelection() {
  for (const tr of document.querySelectorAll("tr.selected")) tr.classList.remove("selected");
  let sel;
  if (S.selAloc != null) sel = `tr[data-alocacao-id="${S.selAloc}"]`;
  else if (S.selMatricula) sel = `tr.lvl0[data-matricula="${CSS.escape(S.selMatricula)}"]`;
  if (sel) for (const tr of document.querySelectorAll(sel)) tr.classList.add("selected");
}

// linha para ONDE ROLAR = o container de 1º nível (pessoa no "Por Recurso",
// projeto no "Por Projeto"). O destaque fica na linha da alocação (applySelection).
function scrollAnchor(grid, qual, aloc, mat) {
  if (qual === "recurso" && mat)
    return grid.querySelector(`tr.lvl0[data-matricula="${CSS.escape(mat)}"]`);
  const pid = aloc != null && ALOC_INFO[aloc] ? ALOC_INFO[aloc].projeto_id : null;
  if (pid != null) return grid.querySelector(`tr.lvl0[data-projeto-id="${pid}"]`);
  return null;
}

function syncPara(qual, aloc, mat) {
  const grid = qual === "recurso" ? gridRec : gridProj;
  const panel = qual === "recurso" ? panelRec : panelProj;

  let mudou = false;
  const abrir = (k) => { if (!S.open.has(k)) { S.open.add(k); mudou = true; } };
  const fechar = (k) => { if (S.open.has(k)) { S.open.delete(k); mudou = true; } };

  if (qual === "recurso" && mat) {
    abrir(`r:${mat}`);
  } else if (qual === "projeto" && aloc != null && ALOC_INFO[aloc]) {
    const { projeto_id: pid, tipo } = ALOC_INFO[aloc];
    abrir(`p:${pid}`);
    // abre só o grupo-alvo; recolhe os irmãos para o título do projeto não subir
    const proj = S.estado.grade.por_projeto.find((p) => p.projeto_id === pid);
    for (const g of proj ? proj.grupos : []) {
      const gk = `g:${pid}:${g.tipo_alocacao}`;
      if (g.tipo_alocacao === tipo) abrir(gk); else fechar(gk);
    }
  }
  if (mudou) { persistOpen(); render(); }

  const alvo = scrollAnchor(grid, qual, aloc, mat);
  if (alvo) scrollRowIntoView(panel, alvo);
}

// -- histórico de navegação de seleção (botões ← →) -------------------
const SELHIST = [];
let SELPOS = -1;

function pushSel() {
  const cur = { selAloc: S.selAloc, selMatricula: S.selMatricula };
  const prev = SELHIST[SELPOS];
  if (prev && prev.selAloc === cur.selAloc && prev.selMatricula === cur.selMatricula) return;
  SELHIST.splice(SELPOS + 1);
  SELHIST.push(cur);
  if (SELHIST.length > 100) SELHIST.shift();
  SELPOS = SELHIST.length - 1;
  refreshNavBtns();
}

function navSel(dir) {
  const next = SELPOS + dir;
  if (next < 0 || next >= SELHIST.length) return;
  SELPOS = next;
  const s = SELHIST[SELPOS];
  S.selAloc = s.selAloc;
  S.selMatricula = s.selMatricula;
  applySelection();
  const flags = syncFlags(S.syncMode);
  if (flags.down) syncPara("recurso", S.selAloc, S.selMatricula);
  if (flags.up) syncPara("projeto", S.selAloc, S.selMatricula);
  maybeOpenCusto();
  refreshNavBtns();
}

function refreshNavBtns() {
  const b = $("#btn-back"), f = $("#btn-fwd");
  if (b) b.disabled = SELPOS <= 0;
  if (f) f.disabled = SELPOS >= SELHIST.length - 1;
}

function projetoIdSelecionado() {
  if (S.selAloc != null && ALOC_INFO[S.selAloc]) return ALOC_INFO[S.selAloc].projeto_id;
  return null;
}

function onRowClick(e, sourceGrid) {
  if (e.target.closest("input, select, .tw-toggle, .addrow, .fill-handle, .rowacts")) return;
  const tr = e.target.closest("tr[data-alocacao-id], tr.lvl0[data-matricula], tr.lvl0[data-projeto-id]");
  if (!tr) return;
  S.selAloc = tr.dataset.alocacaoId ? Number(tr.dataset.alocacaoId) : null;
  S.selMatricula = tr.dataset.matricula || null;

  // clicar no projeto (nível 0, Por Projeto) já expande os tipos de alocação dele —
  // evita um segundo clique só pra ver quem está alocado.
  const ehLinhaProjeto = tr.classList.contains("lvl0") && tr.dataset.projetoId != null && !tr.dataset.alocacaoId;
  let precisaRender = false;
  if (ehLinhaProjeto) {
    const pid = Number(tr.dataset.projetoId);
    const pkey = `p:${pid}`;
    if (isCollapsed(pkey)) { S.open.add(pkey); precisaRender = true; }
    const proj = (S.estado.grade.por_projeto || []).find((p) => p.projeto_id === pid);
    for (const grp of proj?.grupos || []) {
      const gkey = `g:${pid}:${grp.tipo_alocacao}`;
      if (isCollapsed(gkey)) { S.open.add(gkey); precisaRender = true; }
    }
    if (precisaRender) persistOpen();
  }
  if (precisaRender) render(); else applySelection();
  pushSel();
  const flags = syncFlags(S.syncMode);
  if (sourceGrid === "projeto" && flags.down) syncPara("recurso", S.selAloc, S.selMatricula);
  if (sourceGrid === "recurso" && flags.up) syncPara("projeto", S.selAloc, S.selMatricula);

  // painel lateral: uma pessoa clicada (linha de alocação, em qualquer uma das duas
  // grades, ou a linha de topo da pessoa em Por Recurso) manda mais que o projeto — só a
  // linha de topo do projeto (sem pessoa nenhuma envolvida) abre o custo do projeto.
  if (S.selMatricula) openResumoPessoa(S.selMatricula);
  else if (ehLinhaProjeto) openCusto(Number(tr.dataset.projetoId));
}

// -- cell editing --------------------------------------------------
function beginEdit(td, { initial, after } = {}) {
  if (td.querySelector("input") || td.classList.contains("disabled")) return;
  const tr = td.closest("tr");
  const alocacaoId = tr.dataset.alocacaoId;
  if (!alocacaoId) return;
  const periodo = td.dataset.per || PERIODOS[[...tr.children].indexOf(td) - 1];
  const current = td.firstChild && td.firstChild.nodeType === 3 ? td.firstChild.textContent : "";
  const input = el("input", { value: initial != null ? initial : current.replace("%", "") });
  td.textContent = "";
  td.append(input);
  input.focus();
  if (initial == null) input.select();
  let done = false;
  const commit = async (dir) => {
    if (done) return;
    done = true;
    let raw = input.value.trim();
    if (S.displayUnit === "pct" && raw && !raw.endsWith("%")) raw += "%";
    await applyBatch([{ alocacao_id: Number(alocacaoId), periodo, valor: raw }]);
    if (after) after(dir);
  };
  const DIRS = {
    Enter: [1, 0], ArrowDown: [1, 0], ArrowUp: [-1, 0],
    ArrowRight: [0, 1], ArrowLeft: [0, -1],
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { done = true; render(); return; }
    if (e.key === "Tab") { e.preventDefault(); commit([0, e.shiftKey ? -1 : 1]); return; }
    if (DIRS[e.key]) { e.preventDefault(); commit(e.key === "Enter" && e.shiftKey ? [-1, 0] : DIRS[e.key]); }
  });
  input.addEventListener("blur", () => commit());
}

// -- adicionar pessoa/projeto: dropdown inline na própria linha (não popup) ------
// mesma regra de "ativa" usada em aggregate.cor_pessoa_mes (situação ausente conta
// como Ativo; Desligado/Planejado ficam de fora — não dá pra alocar gente que já saiu
// ou que ainda nem começou).
function pessoaAtiva(p) {
  if (p.ativo != null && !Number(p.ativo)) return false;
  const sit = (p.situacao || "Ativo").trim().toLowerCase();
  return sit !== "desligado" && sit !== "planejado";
}
// projeto "ativo" = não encerrado (mesmo critério de aggregate.py: status texto).
function projetoAtivo(p) {
  return (p.status || "").trim().toLowerCase() !== "encerrado";
}

// popup só pra escolher o tipo de alocação (2ª etapa, quando ainda não é conhecido —
// ex. "+ adicionar tipo"; já vem pronto quando se adiciona dentro de um grupo).
function abrirTipoAlocacao() {
  return new Promise((resolve) => {
    const dlg = $("#dlg-tipo");
    const sel = $("#tipo-sel");
    const tipos = (S.estado.catalogos.tipo_alocacao || []).map((t) => t.texto);
    sel.innerHTML = "";
    for (const t of tipos) sel.append(el("option", { value: t, textContent: t }));
    $("#tipo-ok").onclick = () => { dlg.close(); resolve(sel.value); };
    dlg.querySelector('button[value="cancel"]').onclick = () => { dlg.close("cancel"); resolve(null); };
    if (dlg._cancelHandler) dlg.removeEventListener("cancel", dlg._cancelHandler);
    dlg._cancelHandler = () => resolve(null);
    dlg.addEventListener("cancel", dlg._cancelHandler);
    dlg.showModal();
  });
}

async function _finalizarAlocacao(projeto_id, matricula, nome, tipoFixo) {
  let tipo_alocacao = tipoFixo;
  if (!tipo_alocacao) {
    tipo_alocacao = await abrirTipoAlocacao();
    if (!tipo_alocacao) { render(); return; }   // cancelou o popup de tipo — some o dropdown
  }
  try {
    const res = await api.criarAlocacao({ projeto_id, matricula, nome, tipo_alocacao });
    S.estado = res.estado;
    render();
  } catch (err) { log(err.message, true); render(); }
}

// opções de pessoa pro dropdown: só ativas, e sem quem já está no grupo/tipo alvo
// (não dá pra alocar a mesma pessoa duas vezes no mesmo projeto+tipo).
function _opcoesPessoas(excluirMatriculas) {
  const excl = excluirMatriculas || new Set();
  const opcoes = S.estado.pessoas.filter((p) => pessoaAtiva(p) && !excl.has(p.matricula))
    .slice().sort((a, b) => a.nome.localeCompare(b.nome))
    .map((p) => ({ value: p.matricula, texto: `${p.nome} (${p.matricula})` }));
  opcoes.unshift({ value: "__nova__", texto: "+ pessoa nova (matrícula ainda não cadastrada)" });
  return opcoes;
}

// troca o conteúdo de `td` por um <select> inline (dropdown, não popup); some (via
// render() completo) se perder o foco sem escolher nada.
function _selectInline(td, placeholder, opcoes, onEscolher) {
  const sel = el("select", {},
    el("option", { value: "", textContent: placeholder }),
    ...opcoes.map((o) => el("option", { value: o.value, textContent: o.texto })));
  td.innerHTML = "";
  td.append(sel);
  sel.onchange = () => { if (sel.value) onEscolher(sel.value); };
  sel.onblur = () => { if (!sel.value) render(); };
  sel.focus();
  if (sel.showPicker) { try { sel.showPicker(); } catch { /* nem todo browser tem */ } }
  return sel;
}

// Linha "+ adicionar pessoa" (tipo já fixo — dentro de um grupo) ou "+ adicionar
// projeto" (Por Recurso, tipo escolhido depois no popup). Dropdown inline, não popup.
// células vazias (uma por mês, não um colspan só) — é o que permite arrastar uma
// seleção que atravesse a linha "+ adicionar..." no meio de um bloco maior (ex.:
// selecionar/copiar um projeto inteiro, grupos e tudo) sem ela quebrar a grade de
// seleção retangular (ver grid-excel.js).
function addRowCelulas() {
  return PERIODOS.map((p) => el("td", { className: "month addfill", dataset: { per: p } }));
}

function addRowEscolha(label, indentClass, ctx) {
  const tr = el("tr", { className: "addrow" });
  const td = el("td", { className: "treecol " + (indentClass || "ind0") }, label);
  tr.append(td, ...addRowCelulas());

  td.onclick = () => {   // só a coluna 1 (árvore) abre o dropdown — o resto da linha não
    if (td.querySelector("select")) return;   // já aberto — deixa o <select> nativo agir
    if (ctx.projeto_id) {
      _selectInline(td, "— escolher pessoa —", _opcoesPessoas(ctx.excluir), async (v) => {
        if (v === "__nova__") {
          const mat = (prompt("Matrícula da nova pessoa:") || "").trim();
          if (!mat) { render(); return; }
          const nome = (prompt("Nome (opcional):") || "").trim();
          await _finalizarAlocacao(ctx.projeto_id, mat, nome, ctx.tipo_alocacao);
        } else {
          await _finalizarAlocacao(ctx.projeto_id, v, "", ctx.tipo_alocacao);
        }
      });
    } else {
      const opcoes = S.estado.projetos.filter(projetoAtivo).slice().sort((a, b) => a.nome.localeCompare(b.nome))
        .map((p) => ({ value: String(p.projeto_id), texto: p.nome }));
      _selectInline(td, "— escolher projeto —", opcoes, async (v) => {
        await _finalizarAlocacao(Number(v), ctx.matricula, ctx.nome, ctx.tipo_alocacao);
      });
    }
  };
  return tr;
}

// Linha "+ adicionar tipo" (nível do projeto): primeiro escolhe o TIPO (só os que o
// projeto ainda não tem), depois — na mesma célula — a pessoa pra esse tipo novo.
function addRowNovoTipo(label, indentClass, proj) {
  const tr = el("tr", { className: "addrow" });
  const td = el("td", { className: "treecol " + (indentClass || "ind0") }, label);
  tr.append(td, ...addRowCelulas());

  td.onclick = () => {   // só a coluna 1 (árvore) abre o dropdown — o resto da linha não
    if (td.querySelector("select")) return;
    const existentes = new Set(proj.grupos.map((g) => g.tipo_alocacao));
    const tipos = (S.estado.catalogos.tipo_alocacao || []).map((t) => t.texto).filter((t) => !existentes.has(t));
    const opcoesTipo = tipos.map((t) => ({ value: t, texto: t }));
    _selectInline(td, "— escolher tipo —", opcoesTipo, (tipo) => {
      _selectInline(td, "— escolher pessoa —", _opcoesPessoas(), async (v) => {
        if (v === "__nova__") {
          const mat = (prompt("Matrícula da nova pessoa:") || "").trim();
          if (!mat) { render(); return; }
          const nome = (prompt("Nome (opcional):") || "").trim();
          await _finalizarAlocacao(proj.projeto_id, mat, nome, tipo);
        } else {
          await _finalizarAlocacao(proj.projeto_id, v, "", tipo);
        }
      });
    });
  };
  return tr;
}

// -- toolbar actions --------------------------------------------------
const fileInput = Object.assign(document.createElement("input"), {
  type: "file", accept: ".xlsx", multiple: true, style: "display:none",
});
document.body.append(fileInput);
const RESUMO_BI_LABELS = {
  projeto: "projetos com campo alterado", pessoa: "pessoas com campo alterado",
  alocacoes_novas: "alocações novas", alocacoes_removidas: "alocações removidas",
  celulas: "células de horas alteradas", janela: "janelas de mês alteradas",
  projetos_no_bi: "projetos com alocação no BI", linhas_custo: "linhas no extrato de custo",
  linhas_sem_matricula: "linhas sem matrícula (ignoradas)",
  horas_totais: "horas no extrato", horas_atribuidas: "horas atribuídas a alguém",
};

function renderRelatorioBI(resumo) {
  const body = $("#bi-relatorio-body");
  body.innerHTML = "";
  if (!resumo.mudou) {
    body.append(el("p", {}, "Nenhuma mudança em relação ao Publicado atual — nada para salvar."));
    return;
  }
  const pct = resumo.horas_totais ? Math.round(100 * resumo.horas_atribuidas / resumo.horas_totais) : 0;
  body.append(el("p", {}, `Cobertura do extrato: ${resumo.horas_atribuidas}/${resumo.horas_totais} horas (${pct}%)` +
    (resumo.linhas_sem_matricula ? ` · ${resumo.linhas_sem_matricula} linhas sem matrícula` : "")));
  const ul = el("ul", {});
  for (const [k, label] of Object.entries(RESUMO_BI_LABELS)) {
    if (k === "linhas_sem_matricula" || k === "horas_totais" || k === "horas_atribuidas") continue;
    if (resumo[k] != null) ul.append(el("li", {}, `${resumo[k]} ${label}`));
  }
  body.append(ul);
  body.append(el("p", { className: "muted" }, "Há mudanças prontas para aplicar."));
}

function pedirConfirmacaoBI(resumo) {
  // Sempre abre uma janela — com "Confirmar/Descartar" se mudou algo, ou só
  // "Fechar" se a leitura deu igual ao que já está commitado.
  return new Promise((resolve) => {
    const dlg = $("#dlg-bi-relatorio");
    renderRelatorioBI(resumo);
    const okBtn = $("#bi-relatorio-confirmar");
    const cancelBtn = $("#bi-relatorio-cancelar");
    okBtn.hidden = !resumo.mudou;
    cancelBtn.textContent = resumo.mudou ? "Descartar" : "Fechar";
    okBtn.onclick = () => { dlg.close(); resolve(true); };
    cancelBtn.onclick = () => { dlg.close("cancel"); resolve(false); };
    dlg.addEventListener("cancel", () => resolve(false), { once: true });
    dlg.showModal();
  });
}

fileInput.addEventListener("change", async () => {
  if (!fileInput.files.length) return;
  const arq = $("#arq-log");
  const modo = fileInput.dataset.modo || "projeto";
  try {
    log("lendo…");
    if (arq) arq.textContent = "lendo…";
    if (modo === "bi") {
      const res = await api.importarBI(fileInput.files);
      S.estado = res.estado;
      if (arq) arq.textContent = res.resultados.map(fmtResultado).join("\n");
      if (res.resumo) {
        const confirmou = await pedirConfirmacaoBI(res.resumo);
        if (res.resumo.mudou) {
          const res2 = confirmou ? await api.confirmarBI() : await api.descartarBI();
          S.estado = res2.estado;
          log(confirmou ? `versão consolidada no Publicado: #${res2.commit_bi}` : "importação do BI descartada");
        } else {
          log("BI lido — sem mudanças, nada para salvar");
        }
      } else {
        log("sem colab-mes-custo no lote — nada para relatar ainda");
      }
    } else {
      const res = await api.importarProjeto(fileInput.files);
      S.estado = res.estado;
      const resumo = res.resultados.map(fmtResultado).join("\n");
      log(res.resultados.map(fmtResultado).join("  ·  "));
      if (arq) arq.textContent = resumo;
    }
    render();
    if (S.view === "cad") cadLoad(S.cadTab);
    if (S.view === "ver") renderVer();
  } catch (err) { log(err.message, true); if (arq) arq.textContent = err.message; }
  fileInput.value = "";
});

function doImport(modo) {
  fileInput.dataset.modo = modo || "projeto";
  fileInput.click();
}

function fmtResultado(x) {
  const nome = x.nome || x.arquivo || "?";
  const extra = [];
  if (x.detail) extra.push(x.detail);
  if (x.linhas != null) extra.push(`${x.linhas} linhas`);
  if (x.novos != null) extra.push(`${x.novos} novos, ${x.atualizados} atualizados`);
  if (x.projetos != null) extra.push(`${x.projetos} projetos`);
  if (x.sem_matricula) extra.push(`${x.sem_matricula} sem matrícula`);
  return `${nome}: ${x.status}${extra.length ? " (" + extra.join("; ") + ")" : ""}`;
}

const MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];

function projetoTemDados(proj, inicioISO) {
  return Object.entries(proj.totais || {}).some(([per, v]) => v > 0 && per >= inicioISO);
}

function openExport() {
  const dlg = $("#dlg-export");
  const body = $("#ex-body");
  const porProjeto = S.estado.grade.por_projeto;
  if (!porProjeto.length) { log("nenhum projeto para exportar", true); return; }

  const hoje = new Date();
  let mes = hoje.getMonth() + 1;       // 1..12
  let ano = hoje.getFullYear();
  const inicioISO = () => `${ano}-${String(mes).padStart(2, "0")}-01`;

  // -- seletor mês / ano --------------------------------------------------
  const selMes = el("select", {},
    ...MESES_PT.map((m, i) => el("option", { value: i + 1, textContent: m, selected: i + 1 === mes })));
  const anos = [];
  for (let y = ano - 2; y <= ano + 3; y++) anos.push(y);
  const selAno = el("select", {},
    ...anos.map((y) => el("option", { value: y, textContent: y, selected: y === ano })));
  const when = el("div", { className: "xp-when" },
    el("span", {}, "Início da exportação"), selMes, selAno);

  // -- barra de ações ---------------------------------------------------
  const bTodos = el("button", { type: "button", title: "selecionar todos da lista" }, "Todos");
  const bNenhum = el("button", { type: "button", title: "limpar seleção" }, "Nenhum");
  const bMod = el("button", { type: "button", title: "selecionar só os não exportados (●)" }, "Modificados");
  const bGp = el("button", { type: "button", title: "selecionar os do GP filtrado na tela" }, "Do GP");
  const cont = el("span", { className: "xp-count" });
  const tools = el("div", { className: "xp-tools" }, bTodos, bNenhum, bMod, bGp, cont);

  const lista = el("div", { className: "xp-list" });
  let checks = [];

  function atualizaContagem() {
    const n = checks.filter((c) => c.cb.checked).length;
    cont.textContent = checks.length ? `${n} de ${checks.length}` : "";
  }
  function marcar(fn) { checks.forEach((c) => (c.cb.checked = fn(c))); atualizaContagem(); }

  function redraw() {
    lista.innerHTML = "";
    const ini = inicioISO();
    const visiveis = porProjeto
      .filter((p) => projetoTemDados(p, ini) || p.sujo)
      .sort((a, b) => a.nome.localeCompare(b.nome));
    checks = visiveis.map((p) => {
      const cb = el("input", { type: "checkbox", value: String(p.projeto_id), checked: !!p.sujo });
      const lbl = el("label", {});
      lbl.append(cb, el("span", {}, p.nome),
        p.sujo ? el("span", { className: "dot", title: "não exportado" }, "●") : "");
      lista.append(lbl);
      return { cb, sujo: !!p.sujo, gp: gpDe(p) };
    });
    if (!visiveis.length) {
      lista.append(el("div", { className: "xp-empty" }, "Nenhum projeto com dados a partir desse mês."));
    }
    bGp.disabled = !S.gpFilter;
    if (S.gpFilter) bGp.title = `selecionar os projetos de ${S.gpFilter}`;
    atualizaContagem();
  }

  selMes.onchange = () => { mes = Number(selMes.value); redraw(); };
  selAno.onchange = () => { ano = Number(selAno.value); redraw(); };
  bTodos.onclick = () => marcar(() => true);
  bNenhum.onclick = () => marcar(() => false);
  bMod.onclick = () => marcar((c) => c.sujo);
  bGp.onclick = () => marcar((c) => c.gp === S.gpFilter);
  lista.addEventListener("change", atualizaContagem);

  body.innerHTML = "";
  body.append(when, tools, lista);
  redraw();

  $("#ex-ok").onclick = async () => {
    const ids = checks.filter((c) => c.cb.checked).map((c) => Number(c.cb.value));
    if (!ids.length) { log("selecione ao menos um projeto", true); return; }
    try {
      log("gerando…");
      const nome = await api.exportarVarios(ids, inicioISO());
      dlg.close();
      log(`baixado: ${nome}`);
      S.estado = await api.estado();
      render();
    } catch (err) { log(err.message, true); }
  };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

function openNovoProjeto() {
  const dlg = $("#dlg-novo");
  const selStatus = $("#np-status");
  const opts = (S.estado?.catalogos?.status || []).slice().sort((a, b) => a.id - b.id);
  selStatus.innerHTML = "";
  selStatus.append(el("option", { value: "", textContent: "—" }),
    ...opts.map((o) => el("option", { value: String(o.id), textContent: o.texto })));
  $("#np-ok").onclick = async () => {
    try {
      const res = await api.criarProjeto({
        id_projeto_externo: $("#np-id").value.trim() || null,
        nome: $("#np-nome").value.trim(),
        empresa: $("#np-empresa").value.trim(),
        id_status: selStatus.value ? Number(selStatus.value) : null,
        matricula_gp: $("#np-gp").value.trim(),
        id_filial: Number($("#np-filial").value) || 62,
      });
      S.estado = res.estado;
      dlg.close();
      render();
      if (S.view === "cad") await cadLoad(S.cadTab);   // atualiza a tabela de cadastro
      log(`projeto "${$("#np-nome").value.trim()}" criado`);
      for (const i of ["np-id", "np-nome", "np-empresa", "np-gp"]) $("#" + i).value = "";
      selStatus.value = "";
    } catch (err) { log(err.message, true); }
  };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

function toggleUnidade() {
  S.displayUnit = S.displayUnit === "horas" ? "pct" : "horas";
  localStorage.setItem("displayUnit", S.displayUnit);
  $("#btn-unidade").textContent = "Exibir: " + (S.displayUnit === "horas" ? "Horas" : "%");
  render();
  renderSecondary();   // o painel lateral (resumo de pessoa) também segue horas/%
}

function cycleSync() {
  S.syncMode = nextSyncMode(S.syncMode);
  localStorage.setItem("syncMode", S.syncMode);
  $("#syncmode").textContent = S.syncMode;
}

// -- resize: coluna da árvore (nas duas grades, mesma variável CSS) ---
function startColResize(e) {
  e.preventDefault();
  e.stopPropagation();
  const startX = e.clientX;
  const startW = parseInt(getComputedStyle(document.body).getPropertyValue("--treecol-w")) || 280;
  const onMove = (ev) => {
    const w = Math.min(640, Math.max(120, startW + ev.clientX - startX));
    document.body.style.setProperty("--treecol-w", w + "px");
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    localStorage.setItem("treecolW", getComputedStyle(document.body).getPropertyValue("--treecol-w").trim());
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// -- resize: divisória entre as duas grades --------------------------
function startSplitDrag(e) {
  e.preventDefault();
  const panels = $(".panels");
  const wrapA = $("#wrap-projeto");
  const wrapB = $("#wrap-recurso");
  const onMove = (ev) => {
    const rect = panels.getBoundingClientRect();
    let frac = (ev.clientY - rect.top) / rect.height;
    frac = Math.min(0.92, Math.max(0.08, frac));
    wrapA.style.flexGrow = frac;
    wrapB.style.flexGrow = 1 - frac;
    localStorage.setItem("splitFrac", String(frac));
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function restoreLayout() {
  const w = parseInt(localStorage.getItem("treecolW"));
  if (w >= 120 && w <= 640) document.body.style.setProperty("--treecol-w", w + "px");
  const frac = parseFloat(localStorage.getItem("splitFrac"));
  if (frac > 0 && frac < 1) {
    $("#wrap-projeto").style.flexGrow = frac;
    $("#wrap-recurso").style.flexGrow = 1 - frac;
  }
}

// ==================================================================
//  SHELL: rail + sidebar + views + tema + painéis
// ==================================================================
const VIEWS = ["alloc", "cad", "ver"];

function setView(view, opts = {}) {
  if (!VIEWS.includes(view)) view = "alloc";
  S.view = view;
  localStorage.setItem("view", view);
  for (const v of VIEWS) $("#view-" + v).hidden = v !== view;
  for (const b of document.querySelectorAll(".rail-btn[data-view]"))
    b.classList.toggle("on", b.dataset.view === view);
  renderSecondary();
  if (view === "cad") cadLoad(opts.tab || S.cadTab);
  if (view === "ver") renderVer();
  if (view === "alloc" && EXCEL) EXCEL.rebuild();
}

// -- tema (auto / claro / escuro) -----------------------------------
function applyTheme() {
  const root = document.documentElement;
  if (S.theme === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", S.theme);
  // o botão do rail agora é Configurações (engrenagem) — o tema mora lá dentro
  // (#cfg-tema), o rail só mostra o badge de quem está conectado (renderUsuarioBadge).
}
function cycleTheme() {
  S.theme = { auto: "light", light: "dark", dark: "auto" }[S.theme] || "auto";
  localStorage.setItem("theme", S.theme);
  applyTheme();
}

// -- largura da barra da direita --------------------------------------
function applyLayout() {
  $("#app").style.setProperty("--sec-w", (S.secW || 300) + "px");
}

function startWResize(e) {   // só a barra secundária (direita)
  e.preventDefault();
  const app = $("#app");
  const startX = e.clientX;
  const startW = parseFloat(getComputedStyle(app).getPropertyValue("--sec-w")) || 300;
  const handle = $("#sec-resize");
  handle.classList.add("dragging");
  document.body.style.cursor = "col-resize";
  const onMove = (ev) => {
    const w = Math.max(220, Math.min(640, startW - (ev.clientX - startX)));
    app.style.setProperty("--sec-w", w + "px");
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    handle.classList.remove("dragging");
    document.body.style.cursor = "";
    S.secW = Math.round(parseFloat(getComputedStyle(app).getPropertyValue("--sec-w")));
    localStorage.setItem("secW", S.secW);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// ==================================================================
//  BARRA DIREITA (rail + painéis).  Custo do projeto = 1º painel.
// ==================================================================
const SEC_PANELS = {
  // 1 painel só por ora: mostra o resumo do que foi clicado por último — projeto (com
  // custo) ou pessoa (sem custo, só alocação). Ver docs/ESPECIFICACAO.md.
  custo: { titulo: "Resumo", render: () => (S.secModo === "pessoa" ? renderResumoPessoa() : renderCusto()) },
  // futuros painéis entram aqui (capacidade da equipe, …)
};

function renderSecondary() {
  const alloc = S.view === "alloc";
  $("#secondary").hidden = !alloc;
  if (!alloc) return;
  for (const b of document.querySelectorAll(".secrail-btn[data-panel]"))
    b.classList.toggle("on", b.dataset.panel === S.secPanel);
  const painel = $("#sec-panel");
  const def = SEC_PANELS[S.secPanel];
  painel.hidden = !def;
  if (!def) return;
  $("#sec-title").textContent = def.titulo;
  def.render();
}

function toggleSecPanel(name) {
  S.secPanel = S.secPanel === name ? null : name;
  localStorage.setItem("secPanel", S.secPanel || "");
  renderSecondary();
}
function closeSecPanel() {
  S.secPanel = null;
  localStorage.setItem("secPanel", "");
  renderSecondary();
}
function openCusto(projetoId) {
  S.secProjId = projetoId;
  S.secModo = "projeto";
  if (S.secPanel == null) { S.secPanel = "custo"; localStorage.setItem("secPanel", "custo"); }
  renderSecondary();
}
function openResumoPessoa(matricula) {
  S.secPessoaMat = matricula;
  S.secModo = "pessoa";
  if (S.secPanel == null) { S.secPanel = "custo"; localStorage.setItem("secPanel", "custo"); }
  renderSecondary();
}
function maybeOpenCusto() {
  // mesma prioridade do clique: pessoa manda mais que projeto (ver onRowClick)
  if (S.selMatricula) { openResumoPessoa(S.selMatricula); return; }
  const pid = projetoIdSelecionado();
  if (pid != null) openCusto(pid);
}

const CUSTO_CORES = ["var(--accent)", "#e8b23a", "#6cc0a4", "#c07ad6", "#e0796b", "#7f9cd6"];
const _brl = (n) => n >= 1e6 ? `R$ ${(n / 1e6).toFixed(2)} M`
  : n >= 1e3 ? `R$ ${(n / 1e3).toFixed(0)} k` : `R$ ${Math.round(n)}`;
// valor completo, no centavo — pra tabela/lista (o KPI grande em cima pode ficar abreviado,
// mas a lista mês a mês e o total da tabela têm que mostrar o valor exato)
const _brlFull = (n) => n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

async function renderCusto() {
  const body = $("#sec-body");
  if (S.secProjId == null) {
    body.innerHTML = `<div class="sec-empty">Selecione um projeto na grade para ver o custo.</div>`;
    return;
  }
  const proj = (S.estado?.projetos || []).find((p) => p.projeto_id === S.secProjId);
  if (!proj) {
    body.innerHTML = `<div class="sec-empty">Projeto não encontrado.</div>`;
    return;
  }
  $("#sec-title").textContent = "Custo · " + proj.nome;
  const ext = proj.id_projeto_externo;
  if (!ext) {
    body.innerHTML = `<div class="sec-empty">Projeto sem Id externo — sem dados de custo do BI.</div>`;
    return;
  }
  body.innerHTML = `<div class="sec-empty">carregando…</div>`;
  try {
    const hoje = currentMonthISO().slice(0, 7);
    const { periodos, linhas } = await api.custoProjeto(hoje, null, "tipo_alocacao");
    const meus = linhas.filter((l) => String(l.id_projetos) === String(ext));
    if (S.secProjId !== proj.projeto_id) return;   // seleção mudou durante o fetch
    if (!meus.length) {
      body.innerHTML = `<div class="sec-empty">Sem custo lançado no BI para este projeto de ${hoje} em diante.</div>`;
      return;
    }
    // totais por período (todos os tipos) e por tipo
    const totMes = {};
    let totGeral = 0, horasGeral = 0;
    const porTipo = [];
    for (const l of meus) {
      let ct = 0;
      for (const [per, v] of Object.entries(l.custo || {})) { totMes[per] = (totMes[per] || 0) + v; ct += v; }
      for (const v of Object.values(l.horas || {})) horasGeral += v;
      totGeral += ct;
      porTipo.push({ nome: l.grupo || "—", custo: ct });
    }
    porTipo.sort((a, b) => b.custo - a.custo);
    const pers = periodos.filter((p) => p >= currentMonthISO());
    const maxMes = Math.max(1, ...pers.map((p) => totMes[p] || 0));

    // donut
    let acc = 0;
    const segs = porTipo.map((t, i) => {
      const frac = totGeral ? (t.custo / totGeral) * 100 : 0;
      const s = `<circle cx="21" cy="21" r="15.9" fill="none" stroke="${CUSTO_CORES[i % CUSTO_CORES.length]}" stroke-width="6" stroke-dasharray="${frac.toFixed(1)} ${(100 - frac).toFixed(1)}" stroke-dashoffset="${(25 - acc).toFixed(1)}"/>`;
      acc += frac;
      return s;
    }).join("");

    body.innerHTML = `
      <div class="sec-kpis">
        <div data-copy-row><div class="lbl" data-copy-cell>Total (mês atual em diante)</div><div class="val" title="${_brlFull(totGeral)}" data-copy-cell data-copy-value="${totGeral}">${_brl(totGeral)}</div></div>
        <div data-copy-row><div class="lbl" data-copy-cell>Horas</div><div class="val" data-copy-cell data-copy-value="${horasGeral}">${Math.round(horasGeral).toLocaleString("pt-BR")}</div></div>
      </div>
      <svg class="sec-donut" width="128" height="128" viewBox="0 0 42 42" role="img" aria-label="composição do custo por tipo">
        <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--disabled)" stroke-width="6"/>
        ${segs}
      </svg>
      <div class="sec-legend">${porTipo.map((t, i) =>
        `<span data-copy-row><i style="background:${CUSTO_CORES[i % CUSTO_CORES.length]}"></i><span data-copy-cell>${t.nome}</span> <span data-copy-cell data-copy-value="${totGeral ? Math.round(t.custo / totGeral * 100) : 0}">${totGeral ? Math.round(t.custo / totGeral * 100) : 0}%</span></span>`).join("")}
      </div>
      <div class="sec-h">Custo por mês <span class="sec-h-total">· total ${_brlFull(totGeral)}</span></div>
      <div class="sec-bars">${pers.map((p) => {
        const v = totMes[p] || 0;
        return `<div class="b" data-copy-row><span class="m" data-copy-cell>${fmtMes(p)}</span><span class="track"><span class="fill" style="width:${(v / maxMes * 100).toFixed(1)}%"></span></span><span class="n" data-copy-cell data-copy-value="${v}">${_brlFull(v)}</span></div>`;
      }).join("")}</div>`;
  } catch (err) {
    body.innerHTML = `<div class="sec-empty">falha ao carregar custo: ${err.message}</div>`;
  }
}

const _fmtData = (iso) => {
  if (!iso) return null;
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
};
const CUSTO_COR_PESSOA = { vermelho: "#e0796b", amarelo: "#e8b23a" };

// Resumo de uma pessoa: ficha (vínculo/carga/fim de contrato), alocação no tempo (mês
// atual em diante) e principais projetos — nunca custo (ver docs/TERMINOLOGIA.md/ESPECIFICACAO.md,
// resumo de pessoa é só alocação).
function renderResumoPessoa() {
  const body = $("#sec-body");
  const mat = S.secPessoaMat;
  if (mat == null) {
    body.innerHTML = `<div class="sec-empty">Selecione uma pessoa na grade para ver o resumo.</div>`;
    return;
  }
  const pes = (S.estado?.pessoas || []).find((p) => p.matricula === mat);
  const rec = (S.estado?.grade?.por_recurso || []).find((r) => r.matricula === mat);
  const nome = pes?.nome || rec?.nome || mat;
  $("#sec-title").textContent = "Resumo · " + nome;
  if (!rec) {
    body.innerHTML = `<div class="sec-empty">${nome} não tem alocação lançada.</div>`;
    return;
  }

  const area = pes?.area || "";
  const cargo = pes?.tipo_contrato || "";
  const ficha = [];
  if (pes?.situacao && pes.situacao !== cargo) ficha.push(pes.situacao);
  if (pes?.carga_diaria) ficha.push(`${pes.carga_diaria}h/dia`);
  if (pes?.capacidade_mensal) ficha.push(`${Math.round(pes.capacidade_mensal)}h/mês`);
  const fim = pes?.fim_contrato;
  const fimFmt = _fmtData(fim);
  const fimVencido = !!(fim && fim.slice(0, 10) < new Date().toISOString().slice(0, 10));

  const hoje = currentMonthISO();
  const periodos = (S.estado?.grade?.periodos || PERIODOS || []).filter((p) => p >= hoje);
  const totais = rec.totais || {};
  const cores = rec.cores || {};
  const cap = pes?.capacidade_mensal || 0;
  const maxH = Math.max(1, cap, ...periodos.map((p) => totais[p] || 0));
  const unidade = S.displayUnit;   // "horas" | "pct" — segue o botão "Exibir" da toolbar

  // agrega horas por projeto e período (uma pessoa pode ter mais de um tipo_alocacao
  // no mesmo projeto — soma tudo debaixo do nome do projeto)
  const porProjetoPeriodo = {};
  for (const f of rec.filhos || []) {
    const alvo = porProjetoPeriodo[f.projeto_nome] || (porProjetoPeriodo[f.projeto_nome] = {});
    for (const [per, v] of Object.entries(f.horas || {})) alvo[per] = (alvo[per] || 0) + v;
  }
  const somaDe = (obj) => periodos.reduce((s, p) => s + (obj[p] || 0), 0);
  // só projetos com alguma hora de fato no período mostrado (mês atual em diante) — um
  // projeto em que ela só trabalhou no passado (ex. "Férias" de anos atrás) não entra
  // nem no gráfico nem na legenda, mesmo que apareça em rec.filhos.
  const projetos = Object.keys(porProjetoPeriodo)
    .map((n) => [n, somaDe(porProjetoPeriodo[n])])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  const projNomes = projetos.map(([n]) => n);
  const totalHoras = projetos.reduce((a, [, v]) => a + v, 0);
  const cores2 = projNomes.map((_, i) => CUSTO_CORES[i % CUSTO_CORES.length]);

  // gráfico de área empilhado (uma camada por projeto), em horas ou % da capacidade.
  // Sem legenda própria — a cor de cada projeto já aparece de novo bem abaixo, no
  // quadradinho ao lado do nome em "Principais projetos" (mesmo `cores2`), então uma
  // legenda aqui só repetiria os mesmos nomes duas vezes.
  let areaSvg = `<div class="sec-empty">sem alocação futura em nenhum projeto</div>`;
  if (periodos.length && projNomes.length) {
    const valorDe = (h) => unidade === "pct" ? (cap ? h / cap * 100 : 0) : h;
    const W = 280, H = 110, PB = 4, PT = 6;
    const n = periodos.length;
    const xOf = (i) => n > 1 ? (i / (n - 1)) * W : W / 2;
    const cumu = periodos.map(() => 0);
    let maxY = unidade === "pct" ? 100 : (cap || maxH);
    const camadas = projNomes.map((nomeProj, i) => {
      const serie = periodos.map((p, idx) => {
        const v = Math.max(0, valorDe(porProjetoPeriodo[nomeProj][p] || 0));
        const base = cumu[idx];
        cumu[idx] = base + v;
        maxY = Math.max(maxY, cumu[idx]);
        return { base, topo: base + v };
      });
      return { nome: nomeProj, cor: cores2[i], serie };
    });
    const yOf = (v) => H - PB - (v / maxY) * (H - PB - PT);
    const pathDe = (serie) => {
      const topo = serie.map((s, i) => `${i === 0 ? "M" : "L"}${xOf(i).toFixed(1)} ${yOf(s.topo).toFixed(1)}`).join(" ");
      const baixo = serie.slice().reverse().map((s, i) => `L${xOf(n - 1 - i).toFixed(1)} ${yOf(s.base).toFixed(1)}`).join(" ");
      return `${topo} ${baixo} Z`;
    };
    const marcasX = periodos.map((p, i) => {
      const passo = Math.max(1, Math.ceil(n / 6));
      if (i % passo !== 0 && i !== n - 1) return "";
      return `<span style="left:${(xOf(i) / W * 100).toFixed(1)}%">${fmtMes(p)}</span>`;
    }).join("");
    // Linha tracejada = capacidade (100% / cap horas), na altura REAL dela — não no
    // topo do gráfico. Bug corrigido: antes desenhava em yOf(maxY), que é sempre o
    // topo por definição (maxY é o próprio teto da escala); numa superalocação
    // (ex.: 221h alocadas com capacidade 176h) isso empurrava a linha pro topo junto
    // com o pico, escondendo que ele ultrapassou a capacidade. Agora maxY só define a
    // escala; a linha de capacidade fica onde ela de fato está dentro dela, e o pico
    // que ultrapassa aparece visivelmente ACIMA da linha.
    const tetoVal = unidade === "pct" ? 100 : cap;
    const pctY = (v) => (yOf(v) / H) * 100;
    const labelTopo = unidade === "pct" ? `${Math.round(maxY)}%` : `${Math.round(maxY)}h`;
    const labelCap = unidade === "pct" ? "100%" : `${Math.round(tetoVal)}h`;
    // só mostra o rótulo da capacidade separado do topo se ela não coincidir com ele
    // (senão os dois textos ficam colados um em cima do outro)
    const capSeparadoDoTopo = tetoVal != null && Math.abs(pctY(tetoVal) - pctY(maxY)) > 12;
    areaSvg = `
      <div class="sec-area-wrap">
        <div class="sec-area-y">
          <span style="top:${pctY(maxY).toFixed(1)}%">${labelTopo}</span>
          ${capSeparadoDoTopo ? `<span class="sec-area-y-cap" style="top:${pctY(tetoVal).toFixed(1)}%">${labelCap}</span>` : ""}
          <span style="top:${pctY(0).toFixed(1)}%">0</span>
        </div>
        <svg class="sec-area" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="alocação ao longo do tempo, por projeto">
          ${tetoVal != null ? `<line x1="0" y1="${yOf(tetoVal).toFixed(1)}" x2="${W}" y2="${yOf(tetoVal).toFixed(1)}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3"/>` : ""}
          ${camadas.map((c) => `<path d="${pathDe(c.serie)}" fill="${c.cor}" fill-opacity=".82"/>`).join("")}
        </svg>
      </div>
      <div class="sec-area-eixo">${marcasX}</div>`;
  }

  body.innerHTML = `
    <div class="sec-pessoa-nome">${nome}</div>
    ${area ? `<div class="sec-pessoa-area">${area}</div>` : ""}
    ${cargo ? `<div class="sec-pessoa-cargo">${cargo}</div>` : ""}
    ${ficha.length || fimFmt ? `<div class="sec-pessoa-ficha">${ficha.join(" · ")}${ficha.length && fimFmt ? " · " : ""}${
      fimFmt ? `<span class="${fimVencido ? "sec-vencido" : ""}">contrato até ${fimFmt}</span>` : ""}</div>` : ""}
    <div class="sec-h">Alocação ao longo do tempo <span class="sec-h-total">· mês atual em diante</span></div>
    ${areaSvg}
    ${projetos.length ? `
    <div class="sec-h">Principais projetos <span class="sec-h-total">· mês atual em diante</span></div>
    <div class="sec-bars">${projetos.slice(0, 6).map(([nomeProj, v]) =>
      `<div class="b sec-b-proj" data-copy-row><i class="sw" style="background:${cores2[projNomes.indexOf(nomeProj)]}"></i><span class="m sec-m-proj" title="${nomeProj}" data-copy-cell>${nomeProj}</span><span class="track"><span class="fill" style="width:${(totalHoras ? v / totalHoras * 100 : 0).toFixed(1)}%;background:${cores2[projNomes.indexOf(nomeProj)]}"></span></span><span class="n" data-copy-cell data-copy-value="${v}">${Math.round(v)}h</span></div>`).join("")}
    </div>` : `<div class="sec-empty">sem alocação futura em nenhum projeto</div>`}
    <div class="sec-h">Alocação mês a mês</div>
    <div class="sec-bars">${periodos.length ? periodos.map((p) => {
      const v = totais[p] || 0;
      const cor = CUSTO_COR_PESSOA[cores[p]] || "var(--accent)";
      return `<div class="b" data-copy-row><span class="m" data-copy-cell>${fmtMes(p)}</span><span class="track"><span class="fill" style="width:${(v / maxH * 100).toFixed(1)}%;background:${cor}"></span></span><span class="n" data-copy-cell data-copy-value="${v}">${Math.round(v)}h <span class="sec-twin" data-copy-cell data-copy-value="${pctNum(v, cap)}">${pct(v, cap)}</span></span></div>`;
    }).join("") : `<div class="sec-empty">sem meses à frente carregados</div>`}</div>
  `;
}

// ==================================================================
//  VIEW CADASTROS (projetos / recursos / catálogo)
// ==================================================================
const CAD_LABELS = {
  projeto_id: "ID interno", id_projeto_externo: "Id_projeto", nome: "Nome",
  empresa: "Empresa", status: "Status", id_status: "idStatus",
  matricula_gp: "Matrícula GP", id_filial: "Filial", gestor_projetos: "Gestor",
  cenario1: "Cenário 1", cenario2: "Cenário 2", cenario3: "Cenário 3",
  arquivo_origem: "Arquivo origem", criado_na_ferramenta: "Criado aqui",
  exportado_em: "Exportado em", alterado_em: "Alterado em",
  matricula: "Matrícula", carga_diaria: "Carga diária",
  capacidade_mensal: "Capacidade mensal", ativo: "Ativo", area: "Área",
  tipo_contrato: "Tipo contrato", situacao: "Situação", fim_contrato: "Fim contrato",
  formacao: "Formação",
};
// campos de pessoa/projeto que já têm catálogo por trás mas gravam o TEXTO direto
// (não um id — diferente do id_status, que é FK numérica de verdade). value do
// <option> = o próprio texto do catálogo.
const CAD_CATALOGO_DE = { area: "area", tipo_contrato: "contrato", formacao: "ensino" };

function _pessoaLabel(p) { return `${p.nome} (${p.matricula})`; }
// resolve o texto digitado/escolhido no <input list="cad-pessoas-dl"> de volta pra
// matrícula: casa com o rótulo "Nome (matrícula)" do catálogo de pessoas, ou aceita a
// matrícula crua digitada direto (compatibilidade com o que já estava salvo).
function _matriculaFromLabel(label) {
  const pessoas = S.estado?.pessoas || [];
  const alvo = (label || "").trim();
  const exato = pessoas.find((p) => _pessoaLabel(p) === alvo);
  if (exato) return exato.matricula;
  const porMatricula = pessoas.find((p) => String(p.matricula) === alvo);
  return porMatricula ? porMatricula.matricula : alvo;
}
function _refreshPessoasDatalist() {
  const dl = $("#cad-pessoas-dl");
  if (!dl) return;
  dl.innerHTML = "";
  for (const p of (S.estado?.pessoas || []).slice().sort((a, b) => a.nome.localeCompare(b.nome)))
    dl.append(el("option", { value: _pessoaLabel(p) }));
}
const CAD_NUM = new Set(["id_status", "id_filial",
  "cenario1", "cenario2", "cenario3", "carga_diaria", "capacidade_mensal"]);
let CAD = { tab: "projetos", cols: [], editaveis: [], rows: [] };

function cadCellInput(row, col) {
  let inp;
  if (col === "ativo") {
    inp = el("select", {}, ...[["Sim", "1"], ["Não", "0"]].map(([t, v]) =>
      el("option", { value: v, textContent: t, selected: String(row[col] ?? 1) === v })));
  } else if (col === "id_status") {
    // id_status é a FK "lógica" pro catálogo — edita por nome, não por número cru
    // (a coluna "Status" ao lado é só leitura, derivada disso).
    const opts = (S.estado?.catalogos?.status || []).slice().sort((a, b) => a.id - b.id);
    inp = el("select", {},
      el("option", { value: "", textContent: "—", selected: row[col] == null }),
      ...opts.map((o) => el("option", {
        value: String(o.id), textContent: o.texto, selected: String(row[col]) === String(o.id),
      })));
  } else if (col === "matricula_gp") {
    // FK real (pessoa.matricula), mas edita por nome — busca com <datalist>, grava a
    // matrícula resolvida no commit (ver _matriculaFromLabel). Mostra o rótulo
    // "Nome (matrícula)" quando reconhece a pessoa; senão, o valor cru salvo.
    const pessoa = (S.estado?.pessoas || []).find((p) => String(p.matricula) === String(row[col]));
    const label = pessoa ? _pessoaLabel(pessoa) : (row[col] ?? "");
    // "list" é só-leitura como propriedade do DOM (referencia o <datalist>, não
    // aceita atribuição via `.list=`) — precisa ir por setAttribute, não pelo `el()`
    // genérico (que tentaria `n.list = ...` e lançaria erro em módulo ES/strict mode).
    inp = el("input", { type: "text", value: label, placeholder: "buscar por nome…" });
    inp.setAttribute("list", "cad-pessoas-dl");
  } else if (col in CAD_CATALOGO_DE) {
    // área / tipo de contrato / formação: catálogo fixo, mas grava o TEXTO direto
    // (sem id próprio nessas 3 — diferente de id_status).
    const opts = (S.estado?.catalogos?.[CAD_CATALOGO_DE[col]] || []).map((o) => o.texto);
    inp = el("select", {},
      el("option", { value: "", textContent: "—", selected: !row[col] }),
      ...opts.map((t) => el("option", { value: t, textContent: t, selected: row[col] === t })));
  } else if (col === "situacao") {
    // catálogo só tem Desligado/Planejado — "Ativo" é o padrão implícito (ver
    // aggregate.ativa: `situacao` vazia == Ativo), então entra como opção explícita.
    const opts = ["Ativo", ...(S.estado?.catalogos?.ativo || []).map((o) => o.texto)];
    inp = el("select", {}, ...opts.map((t) => el("option", {
      value: t, textContent: t, selected: (row[col] || "Ativo") === t,
    })));
  } else {
    inp = el("input", { type: CAD_NUM.has(col) ? "number" : "text", value: row[col] ?? "" });
    if (col === "carga_diaria") inp.step = "0.5";
  }
  inp._orig = String(inp.value);
  const commit = async () => {
    if (String(inp.value) === inp._orig) return;
    const td = inp.closest("td");
    td.classList.remove("saved", "error");
    const valor = col === "matricula_gp" ? _matriculaFromLabel(inp.value) : inp.value;
    try {
      const key = CAD.tab === "projetos" ? row.projeto_id : row.matricula;
      const save = CAD.tab === "projetos" ? api.cadSalvarProjeto : api.cadSalvarPessoa;
      const { linha } = await save(key, { [col]: valor === "" ? null : valor });
      Object.assign(row, linha);
      if (col === "matricula_gp") {
        const pessoa = (S.estado?.pessoas || []).find((p) => String(p.matricula) === String(row[col]));
        inp.value = pessoa ? _pessoaLabel(pessoa) : (row[col] ?? "");
      }
      inp._orig = String(inp.value);
      td.classList.add("saved");
      log(`${CAD_LABELS[col] || col} salvo.`);
      if (col === "id_status" || col === "matricula_gp") {
        // coluna derivada só-leitura ao lado (Status / Gestor) — atualiza o texto dela
        // também, senão fica mostrando o valor antigo até trocar de aba.
        const derivada = col === "id_status" ? "status" : "gestor_projetos";
        const tr = td.closest("tr");
        const idx = CAD.cols.indexOf(derivada);
        if (idx >= 0 && tr.children[idx]) tr.children[idx].textContent = row[derivada] ?? "";
      }
      if (["nome", "gestor_projetos", "matricula_gp", "capacidade_mensal", "ativo", "situacao", "fim_contrato", "id_status"].includes(col)) {
        try { S.estado = await api.estado(); } catch {}
      }
    } catch (e) {
      td.classList.add("error");
      inp.value = inp._orig;
      log(e.message, true);
    }
  };
  inp.addEventListener("blur", commit);
  inp.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); inp.blur(); }
    if (ev.key === "Escape") { inp.value = inp._orig; inp.blur(); }
  });
  if (inp.tagName === "SELECT") inp.addEventListener("change", commit);
  return inp;
}

function cadRenderTable() {
  const thead = $("#cad-tabela thead"), tbody = $("#cad-tabela tbody");
  thead.innerHTML = ""; tbody.innerHTML = "";
  const trh = el("tr");
  for (const c of CAD.cols) trh.append(el("th", {}, CAD_LABELS[c] || c));
  thead.append(trh);
  for (const row of CAD.rows) {
    const tr = el("tr");
    tr._text = CAD.cols.map((c) => String(row[c] ?? "")).join(" ").toLowerCase();
    for (const c of CAD.cols) {
      const td = el("td");
      if (CAD.editaveis.includes(c)) td.append(cadCellInput(row, c));
      else { td.className = "readonly"; td.textContent = row[c] ?? ""; }
      tr.append(td);
    }
    tbody.append(tr);
  }
  cadFilter();
}
function cadFilter() {
  const q = $("#cad-busca").value.trim().toLowerCase();
  for (const tr of $("#cad-tabela tbody").children)
    tr.classList.toggle("hidden", q && !tr._text.includes(q));
}
function cadRenderCatalogo(cat) {
  const wrap = $("#cad-catalogo");
  wrap.innerHTML = "";
  for (const tipo of Object.keys(cat).sort()) {
    const card = el("div", { className: "cat-card" }, el("h3", {}, tipo));
    const ul = el("ul");
    for (const it of cat[tipo])
      ul.append(el("li", {}, it.texto, it.id != null ? el("span", {}, `  · ${it.id}`) : ""));
    card.append(ul);
    wrap.append(card);
  }
}

async function cadLoad(tab) {
  CAD.tab = S.cadTab = tab;
  _refreshPessoasDatalist();
  for (const b of document.querySelectorAll("#cad-toolbar .cad-tabs button"))
    b.classList.toggle("on", b.dataset.tab === tab);
  $("#cad-novo").hidden = tab !== "projetos";
  const tabela = $("#cad-tabela"), cat = $("#cad-catalogo"), busca = $("#cad-busca");
  log("carregando…");
  try {
    if (tab === "catalogo") {
      tabela.hidden = true; cat.hidden = false; busca.disabled = true;
      cadRenderCatalogo((await api.cadCatalogo()).catalogo);
      $("#cad-hint").textContent = "somente leitura";
      log("");
      return;
    }
    tabela.hidden = false; cat.hidden = true; busca.disabled = false;
    const data = tab === "projetos" ? await api.cadProjetos() : await api.cadPessoas();
    CAD.cols = data.colunas; CAD.editaveis = data.editaveis; CAD.rows = data.linhas;
    cadRenderTable();
    $("#cad-hint").textContent = `${data.linhas.length} linha(s) — clique numa célula; Enter salva`;
    log("");
  } catch (e) { log(e.message, true); }
}

// ==================================================================
//  VIEW VERSÕES — grafo estilo Git
// ==================================================================
const GG = { RH: 34, LW: 16, PAD: 14, R: 4 };
const LANE_CORES = ["#2f8fed", "#e0679a", "#d9a441", "#6cc0a4", "#a077e0", "#e0796b", "#7f9cd6", "#59b36a"];
const laneCor = (l) => LANE_CORES[((l % LANE_CORES.length) + LANE_CORES.length) % LANE_CORES.length];
const ggX = (l) => GG.PAD + l * GG.LW;

let VER = { grafo: null, sel: null };

function startVerSplit(e) {
  e.preventDefault();
  const details = $("#ver-details");
  const startY = e.clientY;
  const startH = details.getBoundingClientRect().height;
  const onMove = (ev) => {
    const h = Math.max(90, Math.min(window.innerHeight * 0.7, startH - (ev.clientY - startY)));
    details.style.flexBasis = h + "px";
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

async function renderVer() {
  try {
    VER.grafo = await api.versaoGrafo();
    paintVer();
  } catch (e) {
    $("#ver-body").innerHTML = `<div class="gg-empty">falha ao renderizar versões: ${e.message}</div>`;
    console.error(e);
  }
}

// atribui uma "raia" (coluna) a cada commit — algoritmo de git log --graph
function layoutGrafo(commits) {
  let lanes = [];  // raia -> commit_id que ela está seguindo (pai esperado), ou null
  const firstFree = () => { const k = lanes.indexOf(null); return k < 0 ? lanes.length : k; };
  const rows = [];
  let maxLane = 0;
  for (const c of commits) {
    const topLanes = lanes.slice();
    const mine = [];
    topLanes.forEach((v, i) => { if (v === c.commit_id) mine.push(i); });
    const col = mine.length ? mine[0] : firstFree();
    for (const j of mine) lanes[j] = null;
    if (col >= lanes.length) lanes.length = col + 1;
    lanes[col] = null;

    const parents = [c.parent_id, c.merge_parent_id].filter((x) => x != null);
    const parentCols = [];
    if (parents.length) {
      lanes[col] = parents[0];
      parentCols.push({ col, cid: parents[0] });
      for (let k = 1; k < parents.length; k++) {
        let lc = lanes.indexOf(parents[k]);
        if (lc < 0) lc = firstFree();
        if (lc >= lanes.length) lanes.length = lc + 1;
        lanes[lc] = parents[k];
        parentCols.push({ col: lc, cid: parents[k] });
      }
    }
    const botLanes = lanes.slice();
    maxLane = Math.max(maxLane, topLanes.length, botLanes.length, col + 1);
    rows.push({ c, col, topLanes, botLanes, parentCols });
  }
  return { rows, lanes: Math.max(1, maxLane) };
}

// nome da branch (técnico) -> nome do cenário na interface (ver docs/TERMINOLOGIA.md)
const nomeCenario = (nome) => (nome === "main" ? "Principal" : nome === "BI" ? "Publicado" : nome);
// contagem primeiro, concordância em número/gênero, sem parênteses: "1 alteração não
// salva" / "3 alterações não salvas" (ver docs/TERMINOLOGIA.md §15)
const alteracoesTxt = (n) => `${n} alteraç${n === 1 ? "ão" : "ões"} não salva${n === 1 ? "" : "s"}`;

function ggEdge(x0, y0, x1, y1) {
  if (x0 === x1) return `M${x0} ${y0}L${x0} ${y1}`;
  const my = (y0 + y1) / 2;
  return `M${x0} ${y0}C${x0} ${my},${x1} ${my},${x1} ${y1}`;
}

// indicador de cenário atual — compartilhado entre o `#ver-cur` de dentro da tela
// Versões (só informativo) e os badges clicáveis nas toolbars de Alocação/Cadastros
// (`info` = qualquer objeto com {branch, protegida}: serve tanto pra VER.grafo quanto
// pra S.estado.versao, que têm exatamente essa forma — ver versao.estado_repo).
function renderBranchBadge(elCur, info) {
  if (!elCur || !info) return;
  elCur.innerHTML = "";
  elCur.append(
    el("span", { className: "dot", style: info.protegida ? "background:var(--muted)" : "" }),
    document.createTextNode(" em: "),
    el("b", {}, nomeCenario(info.branch)),
    info.protegida ? el("span", { className: "lock", title: "Principal é protegida — não recebe versão direta; salve num cenário" }, " 🔒") : "",
  );
}

function paintVer() {
  const g = VER.grafo;
  const body = $("#ver-body");
  const refsPorCommit = {};
  for (const r of g.refs) (refsPorCommit[r.commit_id] ||= []).push(r.nome);

  // indicador da branch atual (sem ação — troca é pelo botão direito)
  renderBranchBadge($("#ver-cur"), g);

  const pend = g.pendente || {};
  const totalPend = (pend.projeto || 0) + (pend.pessoa || 0) + (pend.alocacoes_novas || 0)
    + (pend.alocacoes_removidas || 0) + (pend.celulas || 0) + (pend.janela || 0);
  $("#ver-status").textContent = g.sujo
    ? `${alteracoesTxt(totalPend)} em ${nomeCenario(g.branch)}`
    : `${nomeCenario(g.branch)} — em dia`;

  const cb = $("#ver-commit");
  if (g.protegida) {
    cb.innerHTML = "↪&nbsp;Consolidar num cenário…";
    cb.title = `'${nomeCenario(g.branch)}' é protegida — mova o trabalho para um cenário novo`;
    cb.onclick = verSalvarEmBranch;
    cb.disabled = !g.sujo || !!g.merge;
  } else {
    cb.innerHTML = "✔&nbsp;Consolidar alterações…";
    cb.title = "Cria uma versão (commit) no cenário atual.";
    cb.onclick = verCommit;
    cb.disabled = !g.sujo || !!g.merge;
  }
  $("#ver-descartar-tudo").disabled = !g.sujo;
  $("#ver-merge").disabled = !!g.merge || g.refs.length < 2;

  // banner de incorporação (merge) em andamento
  const banner = $("#ver-merge-banner");
  if (g.merge) {
    banner.hidden = false;
    banner.innerHTML = "";
    banner.append(
      el("span", {}, `Incorporação de "${nomeCenario(g.merge.origem)}" em andamento — ${g.merge.resolvidos}/${g.merge.conflitos} conflitos resolvidos.`),
      el("button", { textContent: "Concluir", disabled: g.merge.resolvidos < g.merge.conflitos,
        onclick: async () => {
          const m = prompt("Mensagem da versão de incorporação:", `Incorporar ${nomeCenario(g.merge.origem)} em ${nomeCenario(g.branch)}`);
          if (m == null) return;
          try { await api.versaoMergeConcluir(m.trim() || `Incorporar ${nomeCenario(g.merge.origem)}`); await recarregar(); log("cenário incorporado"); }
          catch (err) { log(err.message, true); }
        } }),
      el("button", { textContent: "Abortar", onclick: async () => {
        if (!confirm("Abortar a incorporação? As resoluções serão perdidas.")) return;
        try { await api.versaoMergeAbortar(); await recarregar(); log("incorporação abortada"); }
        catch (err) { log(err.message, true); }
      } }),
    );
  } else {
    banner.hidden = true;
  }

  // --- grafo ---
  const commits = g.commits;
  if (!commits.length) { body.innerHTML = `<div class="gg-empty">Sem versões.</div>`; return; }
  const { rows, lanes } = layoutGrafo(commits);
  const idxDe = {};
  commits.forEach((c, i) => { idxDe[c.commit_id] = i; });

  const hasWorking = g.sujo;
  const headIdx = idxDe[g.head_commit];
  const headCol = headIdx != null ? rows[headIdx].col : 0;
  const offset = hasWorking ? 1 : 0;
  const nRows = commits.length + offset;
  const W = ggX(lanes) + 4;
  const H = nRows * GG.RH;

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "gg-svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  const add = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    svg.append(e);
    return e;
  };

  // working: nó vazado ligado ao HEAD
  if (hasWorking && headIdx != null) {
    const y0 = GG.RH / 2, y1 = (headIdx + 1) * GG.RH + GG.RH / 2;
    add("path", { d: ggEdge(ggX(headCol), y0, ggX(headCol), y1), stroke: laneCor(headCol), "stroke-width": 2, fill: "none" });
    add("circle", { cx: ggX(headCol), cy: y0, r: GG.R, fill: "var(--bg)", stroke: "var(--accent)", "stroke-width": 2, "stroke-dasharray": "2 2" });
  }

  rows.forEach((row, i) => {
    const y = (i + offset) * GG.RH + GG.RH / 2;
    // linhas que entram pelo topo
    row.topLanes.forEach((v, L) => {
      if (v == null) return;
      if (v === row.c.commit_id) {
        // chega no nó: mantém a cor da raia de origem (a linhagem que está
        // convergindo), não a da raia de destino — senão o traço muda de cor
        // bem no ponto em que duas raias se juntam num commit.
        add("path", { d: ggEdge(ggX(L), y - GG.RH / 2, ggX(row.col), y), stroke: laneCor(L), "stroke-width": 2, fill: "none" });
      } else {
        // passagem: essa raia não é a do commit desta linha — continua reta na
        // mesma coluna até a raia (`col`) do commit-alvo, que faz a convergência
        // visual só quando o alvo aparece de fato, não antes.
        add("path", { d: ggEdge(ggX(L), y - GG.RH / 2, ggX(L), y + GG.RH / 2), stroke: laneCor(L), "stroke-width": 2, fill: "none" });
      }
    });
    // linhas que saem para os pais
    for (const pc of row.parentCols) {
      add("path", { d: ggEdge(ggX(row.col), y, ggX(pc.col), y + GG.RH / 2), stroke: laneCor(pc.col), "stroke-width": 2, fill: "none" });
    }
    // nó
    const isHead = row.c.commit_id === g.head_commit;
    add("circle", {
      cx: ggX(row.col), cy: y, r: isHead ? GG.R + 1.5 : GG.R,
      fill: laneCor(row.col),
      stroke: isHead ? "var(--fg)" : "var(--bg)", "stroke-width": isHead ? 2 : 1.5,
    });
  });

  // --- lista à direita ---
  const rowsEl = el("div", { className: "gg-rows" });
  VER.rowsEl = rowsEl;

  const chave = (id) => (id == null ? "working" : "c" + id);
  const cliqueCommit = (id, ev) => {
    const combinar = ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey);
    if (combinar && VER.sel && VER.sel.para !== id) {
      // compara os dois: para = mais novo (working, ou maior id), de = mais antigo
      let a = VER.sel.para, b = id;
      if (a == null) selecionarVer({ de: b, para: null });          // working vs commit
      else if (b == null) selecionarVer({ de: a, para: null });
      else selecionarVer({ de: Math.min(a, b), para: Math.max(a, b) });
    } else {
      selecionarVer({ de: null, para: id });
    }
  };

  if (hasWorking) {
    const wr = el("div", { className: "gg-row working head", dataset: { sel: "working" } });
    wr.append(
      el("div", { className: "gg-msg" },
        el("span", { className: "gg-badge branch" }, nomeCenario(g.branch)),
        el("span", { className: "txt" }, alteracoesTxt(totalPend))),
      el("div", { className: "gg-col" }, "agora"),
      el("div", { className: "gg-col" }, ""),
      el("div", { className: "gg-hash" }, "•"),
    );
    wr.onclick = (ev) => cliqueCommit(null, ev);
    rowsEl.append(wr);
  }
  for (const row of rows) {
    const c = row.c;
    const badges = el("span", { style: "display:flex;gap:4px;flex:0 0 auto" });
    for (const nome of (refsPorCommit[c.commit_id] || [])) {
      const isCur = nome === g.branch;
      const b = el("span", { className: "gg-badge " + (isCur ? "head" : "branch"), title: "botão direito: opções do cenário" }, nomeCenario(nome));
      b.oncontextmenu = (ev) => { ev.preventDefault(); ev.stopPropagation(); ctxBranch(nome, ev); };
      badges.append(b);
    }
    if (c.origem && c.origem !== "manual")
      badges.append(el("span", { className: "gg-badge origem" }, c.origem));

    const rd = el("div", {
      className: "gg-row" + (c.commit_id === g.head_commit && !hasWorking ? " head" : ""),
      dataset: { sel: "c" + c.commit_id },
    });
    const msg = el("div", { className: "gg-msg" });
    if (badges.childNodes.length) msg.append(badges);
    msg.append(el("span", { className: "txt", title: c.mensagem || "" }, c.mensagem || "(sem mensagem)"));

    rd.append(
      msg,
      el("div", { className: "gg-col" }, (c.criado_em || "").replace("T", " ").slice(0, 16)),
      el("div", { className: "gg-col", title: c.autor || "" }, c.autor || "—"),
      el("div", { className: "gg-hash" }, "#" + c.commit_id),
    );
    rd.onclick = (ev) => cliqueCommit(c.commit_id, ev);
    rd.oncontextmenu = (ev) => { ev.preventDefault(); ctxCommit(c, ev); };
    rowsEl.append(rd);
  }

  body.innerHTML = "";
  body.append(el("div", { className: "gg" }, svg, rowsEl));

  // seleção / diff inicial: mantém a de antes se os commits ainda existem
  let want = VER.sel;
  const existe = (id) => id == null ? hasWorking : rowsEl.querySelector(`.gg-row[data-sel="c${id}"]`);
  if (!want || !existe(want.para) || (want.de != null && !existe(want.de)))
    want = { de: null, para: hasWorking ? null : g.head_commit };
  selecionarVer(want);
}

// aplica VER.sel: marca as linhas e carrega o diff correspondente
function selecionarVer(sel) {
  VER.sel = { de: sel.de ?? null, para: "para" in sel ? sel.para : null };
  const rowsEl = VER.rowsEl;
  if (rowsEl) {
    for (const x of rowsEl.querySelectorAll(".sel, .sel2")) x.classList.remove("sel", "sel2");
    const p = rowsEl.querySelector(`.gg-row[data-sel="${VER.sel.para == null ? "working" : "c" + VER.sel.para}"]`);
    if (p) p.classList.add("sel");
    if (VER.sel.de != null) {
      const dq = rowsEl.querySelector(`.gg-row[data-sel="c${VER.sel.de}"]`);
      if (dq) dq.classList.add("sel2");
    }
  }
  carregarDiff({ de: VER.sel.de, para: VER.sel.para });
}

// ---- diff pane -------------------------------------------------------
async function carregarDiff({ de, para } = {}) {
  const bd = $("#vd-body");
  bd.innerHTML = `<div class="vd-empty">carregando…</div>`;
  try {
    const d = await api.versaoDiff(de, para);
    paintDiff(d);
  } catch (e) {
    bd.innerHTML = `<div class="vd-empty">falha ao carregar diff: ${e.message}</div>`;
  }
}

function paintDiff(d) {
  const p = d.para, dd = d.de;
  const explicito = VER.sel && VER.sel.de != null;
  const alvo = p.commit_id == null ? "alterações não salvas" : "#" + p.commit_id;
  const base = dd.commit_id == null ? dd.mensagem : "#" + dd.commit_id;
  $("#vd-msg").textContent = explicito
    ? `${base}  →  ${alvo}`
    : (p.commit_id == null ? "Alterações não salvas" : `#${p.commit_id} · ${p.mensagem || "(sem mensagem)"}`);
  const sub = $("#vd-sub");
  sub.textContent =
    (p.commit_id == null || explicito ? "" : `${p.autor || "—"} · ${(p.criado_em || "").replace("T", " ").slice(0, 16)} · `)
    + (explicito ? `${d.resumo.celulas + d.resumo.projetos + d.resumo.pessoas + d.resumo.janela} alterações entre os dois`
                 : `comparado com ${base}`);
  if (explicito) {
    sub.append(el("button", {
      className: "vd-reset", textContent: " ↩ vs pai",
      onclick: () => selecionarVer({ de: null, para: p.commit_id }),
    }));
  }

  const r = d.resumo;
  const bd = $("#vd-body");
  bd.innerHTML = "";

  const grupo = (nome, count, mini, render) => {
    const has = count > 0;
    const g = el("div", { className: "vd-grp" + (has ? "" : " closed") });
    const miniEl = el("span", { className: "mini" });
    if (mini.a) miniEl.append(el("span", { className: "a" }, "+" + mini.a));
    if (mini.d) miniEl.append(el("span", { className: "d" }, "−" + mini.d));
    if (mini.c) miniEl.append(el("span", { className: "c" }, "~" + mini.c));
    const h = el("div", { className: "vd-grp-h" },
      el("span", { className: "tw" }, has ? "▾" : "▸"),
      el("span", { className: "name" }, nome),
      el("span", { className: "count" }, String(count)),
      miniEl);
    const bdy = el("div", { className: "vd-grp-body" });
    if (has) render(bdy); else bdy.append(el("div", { className: "vd-item", style: "color:var(--muted)" }, "sem alterações"));
    h.onclick = () => {
      g.classList.toggle("closed");
      h.querySelector(".tw").textContent = g.classList.contains("closed") ? "▸" : "▾";
    };
    g.append(h, bdy);
    bd.append(g);
  };

  const campos = (arr) => {
    const w = el("div", { className: "vd-fields" });
    for (const f of arr)
      w.append(el("span", {},
        el("span", { className: "f" }, f.campo + " "),
        el("span", { className: "from" }, f.de == null || f.de === "" ? "∅" : String(f.de)),
        document.createTextNode(" → "),
        el("span", { className: "to" }, f.para == null || f.para === "" ? "∅" : String(f.para))));
    return w;
  };
  const linhaItem = (lbl, tag, extra) => {
    const it = el("div", { className: "vd-item" });
    it.append(el("div", { className: "vd-item-h" },
      el("span", { className: "lbl", title: lbl }, lbl),
      el("span", { className: "vd-tag " + tag }, tag)));
    if (extra) it.append(extra);
    return it;
  };

  grupo("Projetos", r.projetos, { c: r.projetos }, (b) => {
    for (const x of d.projetos) b.append(linhaItem(x.nome, x.tag, x.campos.length ? campos(x.campos) : null));
  });
  grupo("Pessoas", r.pessoas, { c: r.pessoas }, (b) => {
    for (const x of d.pessoas) b.append(linhaItem(x.nome, x.tag, x.campos.length ? campos(x.campos) : null));
  });
  grupo("Alocações", r.alocacoes, { a: r.alocacoes_add, d: r.alocacoes_rem, c: r.celulas }, (b) => {
    for (const x of d.alocacoes) {
      let cells = null;
      if (x.meses.length) {
        cells = el("div", { className: "vd-cells" });
        for (const m of x.meses)
          cells.append(el("span", {},
            el("span", { className: "m" }, fmtMes(m.periodo) + " "),
            el("span", { className: "from" }, String(m.de)),
            el("span", { className: "ar" }, "→"),
            el("span", { className: "to" }, String(m.para))));
      }
      b.append(linhaItem(`${x.projeto} · ${x.pessoa} · ${x.tipo_alocacao}`, x.tag, cells));
    }
  });
  grupo("Janela de meses", r.janela, { a: d.janela.filter((j) => j.tag === "adicionada").length,
                                       d: d.janela.filter((j) => j.tag === "removida").length }, (b) => {
    for (const x of d.janela) b.append(linhaItem(`${x.projeto} · ${fmtMes(x.periodo)}`, x.tag, null));
  });
}

// ---- menus de contexto --------------------------------------------
function ctxMenu(items, ev) {
  const m = $("#ver-ctx");
  m.innerHTML = "";
  for (const it of items) {
    if (it.sep) { m.append(el("div", { className: "sep" })); continue; }
    m.append(el("button", {
      className: it.danger ? "danger" : "", disabled: !!it.disabled,
      textContent: it.label, title: it.title || "",
      onclick: () => { hideCtx(); it.onClick && it.onClick(); },
    }));
  }
  m.hidden = false;
  const mw = m.offsetWidth || 210, mh = m.offsetHeight || 40;
  m.style.left = Math.min(ev.clientX, window.innerWidth - mw - 6) + "px";
  m.style.top = Math.min(ev.clientY, window.innerHeight - mh - 6) + "px";
}
function hideCtx() { const m = $("#ver-ctx"); if (m) m.hidden = true; }

// commit_id é ancestral (ou o próprio topo) da branch aberta agora (g.head_commit)?
// Segue parent_id E merge_parent_id (mesma ideia de versao._ancestrais, só que sobre
// a lista de commits que já veio no grafo, sem round-trip novo ao servidor).
function ehAncestralDoTopo(g, commitId) {
  const porId = new Map(g.commits.map((c) => [c.commit_id, c]));
  const vistos = new Set();
  const fila = [g.head_commit];
  while (fila.length) {
    const x = fila.shift();
    if (x === commitId) return true;
    if (x == null || vistos.has(x)) continue;
    vistos.add(x);
    const c = porId.get(x);
    if (!c) continue;
    if (c.parent_id != null) fila.push(c.parent_id);
    if (c.merge_parent_id != null) fila.push(c.merge_parent_id);
  }
  return false;
}

async function verResetar(c) {
  const g = VER.grafo;
  // lista as versões que saem do histórico — segue só o 1º pai (mesmo critério de
  // materializar/checkout), do topo atual até (exclusive) o alvo do reset.
  const porId = new Map(g.commits.map((cc) => [cc.commit_id, cc]));
  const cadeia = [];
  let x = g.head_commit;
  while (x != null && x !== c.commit_id) {
    const cc = porId.get(x);
    if (!cc) break;
    cadeia.push(cc);
    x = cc.parent_id;
  }
  let resumoTxt = "";
  try {
    const { resumo: r } = await api.versaoDiff(c.commit_id, g.head_commit);
    const partes = [];
    if (r.projetos) partes.push(`${r.projetos} projeto(s)`);
    if (r.pessoas) partes.push(`${r.pessoas} pessoa(s)`);
    if (r.celulas) partes.push(`${r.celulas} célula(s)`);
    if (r.janela) partes.push(`${r.janela} janela(s)`);
    if (partes.length) resumoTxt = `\n\nNo total: ${partes.join(", ")}.`;
  } catch { /* confirmação segue sem o resumo se o diff falhar */ }
  const lista = cadeia.map((cc) => `#${cc.commit_id} — ${cc.mensagem}`).join("\n") || "(nenhuma — já é o topo)";
  const ok = confirm(
    `Voltar "${nomeCenario(g.branch)}" pra #${c.commit_id} ("${c.mensagem}")?\n\n` +
    `Estas versões saem do histórico e voltam como alterações pendentes (nada se perde):\n${lista}${resumoTxt}`
  );
  if (!ok) return;
  try {
    await api.versaoResetar(g.branch, c.commit_id);
    await recarregar();
    log(`"${nomeCenario(g.branch)}" voltou pra #${c.commit_id}`);
  } catch (err) { avisarErroVersao(err, "voltar pra uma versão anterior"); }
}

function ctxCommit(c, ev) {
  const g = VER.grafo;
  const brs = g.refs.filter((r) => r.commit_id === c.commit_id).map((r) => r.nome);
  const items = [];
  for (const nome of brs)
    if (nome !== g.branch)
      items.push({ label: `↪ Abrir "${nomeCenario(nome)}"`, onClick: () => verCheckout(nome) });
  items.push({ label: "⑂ Criar cenário aqui…", onClick: () => criarBranchDe(c) });
  if (c.commit_id !== g.head_commit && ehAncestralDoTopo(g, c.commit_id))
    items.push({
      label: `↩ Voltar "${nomeCenario(g.branch)}" pra esta versão`,
      title: "As versões entre aqui e o topo saem do histórico e voltam como alterações pendentes. (reset)",
      onClick: () => verResetar(c),
    });
  items.push({ sep: true });
  items.push({ label: "⇆ Ver alterações (vs pai)",
    onClick: () => selecionarVer({ de: null, para: c.commit_id }) });
  // comparar com o commit já selecionado (se for outro)
  const outro = VER.sel && VER.sel.de == null ? VER.sel.para : null;
  if (outro != null && outro !== c.commit_id)
    items.push({
      label: `⇆ Comparar com #${outro}`,
      onClick: () => selecionarVer({ de: Math.min(outro, c.commit_id), para: Math.max(outro, c.commit_id) }),
    });
  if (VER.grafo.sujo)
    items.push({
      label: "⇆ Comparar com as alterações não salvas",
      onClick: () => selecionarVer({ de: c.commit_id, para: null }),
    });
  items.push({ sep: true });
  items.push({ label: "⧉ Copiar #id", onClick: () => {
    navigator.clipboard && navigator.clipboard.writeText("#" + c.commit_id);
    log("#" + c.commit_id + " copiado");
  } });
  ctxMenu(items, ev);
}

function ctxBranch(nome, ev) {
  const g = VER.grafo;
  const items = [];
  if (nome !== g.branch) {
    items.push({ label: `↪ Abrir "${nomeCenario(nome)}"`, onClick: () => verCheckout(nome) });
    items.push({ label: `⇄ Incorporar "${nomeCenario(nome)}" em "${nomeCenario(g.branch)}"`, disabled: !!g.merge,
      onClick: () => verMerge(nome) });
  }
  items.push({ sep: true });
  items.push({
    label: `🗑 Apagar "${nomeCenario(nome)}"…`, danger: true,
    disabled: g.protegidas.includes(nome),
    onClick: () => openDelBranch(nome),
  });
  ctxMenu(items, ev);
}

async function criarBranchDe(c) {
  if (VER.grafo.sujo) { log("há alterações não salvas — consolide uma versão ou descarte antes de abrir outro cenário", true); return; }
  const nome = prompt(`Criar cenário a partir da versão #${c.commit_id} ("${(c.mensagem || "").slice(0, 40)}") e abrir:`,
    `v${c.commit_id}`);
  if (!nome || !nome.trim()) return;
  try { await api.versaoBranch(nome.trim(), c.commit_id, true); await recarregar(); log(`agora em "${nome.trim()}"`); }
  catch (err) { log(err.message, true); }
}

function openDelBranch(nome) {
  const g = VER.grafo;
  const dlg = $("#dlg-delbranch");
  const isCur = nome === g.branch;
  $("#db-txt").textContent = isCur
    ? `Você está em "${nomeCenario(nome)}". Ao apagar, troco para outro cenário antes. O que só existe em "${nomeCenario(nome)}" é perdido.`
    : `O cenário "${nomeCenario(nome)}" será apagado. O que só existe nele é perdido.`;
  const ok = $("#db-ok");
  ok.textContent = `Apagar "${nomeCenario(nome)}"`;
  ok.onclick = async () => { dlg.close(); await apagarBranch(nome, isCur); };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

async function recarregar() {
  S.estado = await api.estado();
  render();
  await renderVer();
}

async function verSalvarEmBranch() {
  const hoje = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const nome = prompt("Nome do cenário novo:", `trabalho-${hoje}`);
  if (!nome || !nome.trim()) return;
  try {
    await api.versaoBranch(nome.trim(), null, false, true);   // mover_pendencias
    await recarregar();
    log(`agora em "${nome.trim()}" com as pendências`);
    if (VER.grafo && VER.grafo.sujo) {
      const m = prompt("Consolidar as alterações agora? Mensagem:");
      if (m && m.trim()) {
        await api.versaoCommit(m.trim());
        await recarregar();
        log("versão consolidada em " + nome.trim());
      }
    }
  } catch (err) { log(err.message, true); }
}

async function verCommit() {
  const m = prompt("Mensagem da versão:");
  if (m == null) return;
  if (!m.trim()) { log("mensagem obrigatória", true); return; }
  try { await api.versaoCommit(m.trim()); await recarregar(); log("versão consolidada"); }
  catch (err) { log(err.message, true); }
}
async function verNovaBranch() {
  const nome = prompt("Nome do cenário novo:");
  if (!nome || !nome.trim()) return;
  try { await api.versaoBranch(nome.trim(), null, false, true); await recarregar(); log(`agora em "${nome.trim()}"`); }
  catch (err) { log(err.message, true); }
}
// Erro de "working sujo" (checkout/resetar bloqueados) é fácil de não perceber só
// no `#log` discreto — vira um alert() que a pessoa não deixa de ver. Detecta pela
// mensagem porque `req()` (api.js) não repassa o status HTTP, só o texto do erro.
function avisarErroVersao(err, acao) {
  if (/mudanças não commitadas/.test(err.message || "")) {
    const cenario = nomeCenario((VER.grafo && VER.grafo.branch) || "");
    alert(`Há alterações pendentes em "${cenario}".\nConsolide uma versão ou descarte antes de ${acao}.`);
  } else {
    log(err.message, true);
  }
}

async function verCheckout(ref) {
  if (!ref || ref === (VER.grafo && VER.grafo.branch)) return;
  try { await api.versaoCheckout(ref); await recarregar(); log(`agora em ${nomeCenario(ref)}`); }
  catch (err) { avisarErroVersao(err, "abrir outro cenário"); }
}
async function verMerge(origemPre) {
  const g = VER.grafo;
  const outras = g.refs.map((r) => r.nome).filter((n) => n !== g.branch);
  const origem = origemPre || prompt(`Incorporar qual cenário em "${nomeCenario(g.branch)}"?\n(${outras.join(", ")})`, outras[0] || "");
  if (!origem) return;
  try {
    const res = await api.versaoMerge(origem.trim());
    await recarregar();
    log({ "em-dia": "já está em dia", "fast-forward": "incorporado (sem conflitos)", ok: "cenário incorporado",
          conflito: `incorporação com ${(res.conflitos || []).length} conflito(s) — resolva na grade` }[res.status] || "incorporado");
  } catch (err) { log(err.message, true); }
}
async function apagarBranch(nome, isCur) {
  const g = VER.grafo;
  if (g.protegidas.includes(nome)) { log(`"${nomeCenario(nome)}" é protegida`, true); return; }
  try {
    if (isCur) {
      if (g.sujo) { log(`há alterações não salvas em "${nomeCenario(nome)}" — consolide uma versão ou descarte antes`, true); return; }
      const destino = g.refs.map((r) => r.nome).filter((n) => n !== nome).concat("main")[0];
      await api.versaoCheckout(destino);
      await api.versaoDeletarBranch(nome);
      await recarregar();
      log(`cenário "${nomeCenario(nome)}" apagado; agora em "${nomeCenario(destino)}"`);
    } else {
      await api.versaoDeletarBranch(nome);
      await recarregar();
      log(`cenário "${nomeCenario(nome)}" apagado`);
    }
  } catch (err) { log(err.message, true); await renderVer(); }
}
async function verDescartar() {
  const g = VER.grafo;
  const n = g && g.pendente ? Object.values(g.pendente).reduce((a, b) => a + b, 0) : 0;
  if (!confirm(`Descartar ${alteracoesTxt(n)} em "${g ? nomeCenario(g.branch) : ""}"?\n`
    + "Volta ao estado da última versão salva. Não afeta o histórico.")) return;
  try { S.estado = (await api.descartarTudo()).estado; render(); await renderVer(); log("alterações não salvas descartadas"); }
  catch (err) { log(err.message, true); }
}

// -- init ---------------------------------------------------------
let EXCEL = null;

function openArquivos() {
  const dlg = $("#dlg-arquivos");
  $("#arq-log").textContent = "";
  $("#arq-import-bi").onclick = () => doImport("bi");
  $("#arq-import-proj").onclick = () => doImport("projeto");
  $("#arq-export").onclick = () => { dlg.close(); openExport(); };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

// -- identidade: quem está usando o app (docs/COLABORACAO.md "Identidade e papel de
// acesso") — matrícula em localStorage (api.js), badge discreto no rail, painel de
// Configurações (tema + usuário atual + apelido). ------------------------------------
function renderUsuarioBadge() {
  const u = S.estado && S.estado.usuario_atual;
  const dot = $("#rail-user-dot");
  const btn = $("#rail-config");
  if (!dot || !btn) return;
  if (u && u.matricula) {
    dot.hidden = false;
    dot.textContent = (u.nome_exibicao || "?").charAt(0).toUpperCase();
    btn.title = `Configurações — ${u.nome_exibicao} (${u.papel})`;
  } else {
    dot.hidden = true;
    btn.title = "Configurações";
  }
}

function abrirSeletorUsuario({ bloqueante = false } = {}) {
  return new Promise((resolve) => {
    const dlg = $("#dlg-usuario");
    const busca = $("#usr-busca");
    const lista = $("#usr-lista");
    const cancelar = $("#usr-cancelar");
    cancelar.hidden = bloqueante;
    dlg.querySelector("h3").textContent = bloqueante ? "Quem é você?" : "Trocar usuário";

    const pessoas = (S.estado?.pessoas || []).slice().sort((a, b) => a.nome.localeCompare(b.nome));
    const pinta = () => {
      const q = (busca.value || "").trim().toLowerCase();
      const filtradas = q
        ? pessoas.filter((p) => p.nome.toLowerCase().includes(q) || String(p.matricula).includes(q))
        : pessoas;
      lista.innerHTML = "";
      if (!filtradas.length) { lista.append(el("div", { className: "sec-empty" }, "ninguém encontrado")); return; }
      for (const p of filtradas) {
        // nome original (o que vem do BI), não o apelido — pra reconhecer quem é quem
        // na lista sem ambiguidade; o apelido é só um jeito de exibir depois de escolher.
        const row = el("div", { className: "pick-item" },
          el("span", {}, p.nome),
          el("span", { className: "mat" }, p.matricula));
        row.onclick = () => { dlg.close(); resolve(p.matricula); };
        lista.append(row);
      }
    };
    busca.value = "";
    pinta();
    busca.oninput = pinta;
    cancelar.onclick = () => { dlg.close("cancel"); resolve(null); };
    // sem {once:true} de propósito: no modo bloqueante precisa barrar TODA tentativa de
    // Esc, não só a 1ª — o dialog só fecha de fato pelo clique numa pessoa da lista. Tira
    // o listener da chamada anterior antes de pôr o novo (senão acumula — um seletor
    // bloqueante de antes ficaria barrando Esc pra sempre nas próximas aberturas).
    if (dlg._cancelHandler) dlg.removeEventListener("cancel", dlg._cancelHandler);
    dlg._cancelHandler = (ev) => { if (bloqueante) ev.preventDefault(); else resolve(null); };
    dlg.addEventListener("cancel", dlg._cancelHandler);
    dlg.showModal();
    busca.focus();
  });
}

async function trocarUsuario(mat) {
  setAutor(mat);
  S.estado = await api.estado();
  render();
  renderUsuarioBadge();
}

function abrirConfig() {
  const dlg = $("#dlg-config");
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  const btnTema = $("#cfg-tema");
  const refreshTema = () => {
    btnTema.textContent = "Tema: " + { auto: "automático", light: "claro", dark: "escuro" }[S.theme];
  };
  refreshTema();
  btnTema.onclick = () => { cycleTheme(); refreshTema(); };

  const u = S.estado && S.estado.usuario_atual;
  const divUsr = $("#cfg-usuario");
  divUsr.innerHTML = "";
  const apelidoInp = $("#cfg-apelido");
  if (u && u.matricula) {
    divUsr.append(el("b", {}, u.nome_exibicao), el("span", { className: "papel" }, u.papel));
    const pe = (S.estado.pessoas || []).find((p) => p.matricula === u.matricula);
    apelidoInp.value = (pe && pe.apelido) || "";
    apelidoInp.disabled = false;
  } else {
    divUsr.append(el("span", { className: "muted" }, "não identificado"));
    apelidoInp.value = "";
    apelidoInp.disabled = true;
  }
  apelidoInp.onblur = async () => {
    if (!u || !u.matricula) return;
    try {
      S.estado = (await api.editarApelido(u.matricula, apelidoInp.value.trim())).estado;
      render(); renderUsuarioBadge();
      log("apelido salvo.");
    } catch (err) { log(err.message, true); }
  };
  apelidoInp.onkeydown = (ev) => { if (ev.key === "Enter") apelidoInp.blur(); };

  $("#cfg-trocar-usuario").onclick = async () => {
    dlg.close();
    const mat = await abrirSeletorUsuario({ bloqueante: false });
    if (mat) await trocarUsuario(mat);
    abrirConfig();
  };

  dlg.showModal();
}

function wire() {
  // ---- rail / sidebar / tema ----
  for (const b of document.querySelectorAll(".rail-btn[data-view]"))
    b.onclick = () => setView(b.dataset.view);
  $("#rail-files").onclick = openArquivos;
  $("#rail-config").onclick = abrirConfig;
  $("#sec-resize").addEventListener("mousedown", startWResize);

  // ---- badge de cenário atual (Alocação/Cadastros) — clique leva pra Versões ----
  for (const id of ["branch-badge-alloc", "branch-badge-cad"]) {
    const b = $("#" + id);
    b.onclick = () => setView("ver");
    b.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setView("ver"); }
    };
  }

  // ---- toolbar da Alocação ----
  $("#btn-unidade").onclick = toggleUnidade;
  $("#btn-unidade").textContent = "Exibir: " + (S.displayUnit === "horas" ? "Horas" : "%");
  $("#btn-hoje").onclick = scrollToCurrentMonth;
  $("#btn-undo").onclick = undo;
  $("#btn-redo").onclick = redo;
  $("#btn-back").onclick = () => navSel(-1);
  $("#btn-fwd").onclick = () => navSel(1);
  $("#syncmode").textContent = S.syncMode;
  $("#syncmode").onclick = cycleSync;
  $("#divider").addEventListener("mousedown", startSplitDrag);
  $("#gp-filter").onchange = (e) => {
    S.gpFilter = e.target.value;
    localStorage.setItem("gpFilter", S.gpFilter);
    render();
  };
  $("#btn-periodo").onclick = (e) => { e.stopPropagation(); abrirPeriodoCtx(e); };
  $("#periodo-ctx").addEventListener("click", (e) => e.stopPropagation());
  for (const b of document.querySelectorAll("button[data-grid]"))
    b.onclick = () => setOpen(b.dataset.grid, b.dataset.act === "expand");

  gridProj.addEventListener("click", (e) => onRowClick(e, "projeto"));
  gridRec.addEventListener("click", (e) => onRowClick(e, "recurso"));
  // botão direito numa célula de horas (Por Projeto E Por Recurso) -> ajuste rápido de
  // alocação (normalizar / 100-75-50-25-0%). Age sobre a seleção inteira se a célula
  // clicada estiver dentro dela (ver EG.cellsPara em grid-excel.js).
  const onCtxAloc = (e) => {
    const td = e.target.closest("td.cell.editable");
    if (!td) return;
    e.preventDefault();
    const cells = EXCEL && EXCEL.cellsPara(td);
    if (cells && cells.length) abrirAjusteAlocacao(cells, e);
  };
  gridProj.addEventListener("contextmenu", onCtxAloc);
  gridRec.addEventListener("contextmenu", onCtxAloc);
  bindScrollSync(panelProj, panelRec);

  // ---- view Cadastros ----
  for (const b of document.querySelectorAll("#cad-toolbar .cad-tabs button"))
    b.onclick = () => cadLoad(b.dataset.tab);
  $("#cad-busca").addEventListener("input", cadFilter);
  $("#cad-novo").onclick = openNovoProjeto;

  // ---- view Versões (grafo Git) ----
  // #ver-commit.onclick é setado em paintVer (depende de branch protegida)
  $("#ver-branch-novo").onclick = verNovaBranch;
  $("#ver-merge").onclick = () => verMerge();
  $("#ver-descartar-tudo").onclick = verDescartar;
  $("#ver-split").addEventListener("mousedown", startVerSplit);
  document.addEventListener("click", hideCtx);
  document.addEventListener("scroll", hideCtx, true);
  document.addEventListener("click", hideAlocCtx);
  document.addEventListener("scroll", hideAlocCtx, true);
  document.addEventListener("click", hidePeriodoCtx);
  document.addEventListener("scroll", hidePeriodoCtx, true);
  document.addEventListener("copy", _copySecBody);

  // ---- barra direita (rail + painéis) ----
  for (const b of document.querySelectorAll(".secrail-btn[data-panel]"))
    b.onclick = () => toggleSecPanel(b.dataset.panel);
  $("#sec-close").onclick = closeSecPanel;

  EXCEL = initExcel([gridProj, gridRec], {
    periodos: () => PERIODOS,
    edit: (td, opts) => beginEdit(td, opts),
    batch: (edits) => applyBatch(edits),
  });

  document.addEventListener("keydown", (e) => {
    if (document.activeElement && document.activeElement.tagName === "INPUT") return;
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
  });
}

// -- marca de ambiente ---------------------------------------------
// Produção = ambiente final, sem marca nenhuma. Qualquer coisa que NÃO
// seja produção ganha uma moldura âmbar + badge.
function applyAmbiente(amb) {
  const env = ((amb && amb.env) || "").toLowerCase();
  const frame = document.getElementById("env-frame");
  if (env === "prod") {
    if (frame) frame.remove();
    document.documentElement.style.removeProperty("--env-cor");
    document.body.dataset.env = "prod";
    document.title = "Planejamento de Alocação";
    return;
  }
  const nome = { dev: "DESENVOLVIMENTO", test: "TESTE" }[env] || (env ? env.toUpperCase() : "NÃO-PRODUÇÃO");
  document.documentElement.style.setProperty("--env-cor", "#d98a1f");
  document.body.dataset.env = env || "?";
  let f = frame;
  if (!f) {
    f = el("div", { id: "env-frame" }, el("span", { className: "env-badge" }));
    document.body.append(f);
  }
  f.querySelector(".env-badge").textContent = nome + (amb && amb.db ? "  ·  " + amb.db : "");
  document.title = `[${nome.slice(0, 3)}] Planejamento de Alocação`;
}

async function boot() {
  window.addEventListener("error", (e) => log("JS: " + e.message, true));
  window.addEventListener("unhandledrejection", (e) => log("JS: " + (e.reason && e.reason.message || e.reason), true));
  restoreLayout();
  applyTheme();
  applyLayout();
  // palpite imediato pela porta (run.sh: 8731=prod, 8732=dev), antes do fetch
  applyAmbiente({ env: { "8732": "dev", "8731": "prod" }[location.port] || "" });
  wire();
  setView(S.view);
  refreshNavBtns();
  try {
    S.estado = await api.estado();
    applyAmbiente(S.estado.ambiente);
    if (!getAutor()) {
      // 1ª execução neste navegador: seletor bloqueante — sem matrícula não dá pra
      // identificar autor de commit nenhum (docs/COLABORACAO.md "Identidade").
      const mat = await abrirSeletorUsuario({ bloqueante: true });
      if (mat) {
        setAutor(mat);
        S.estado = await api.estado();   // refaz com X-Autor pra vir usuario_atual certo
      }
    }
    render();
    if (S.view === "cad") cadLoad(S.cadTab);
    if (S.view === "ver") renderVer();
    log("pronto. Use o ícone de pasta (rail) para importar/exportar .xlsx.");
  } catch (err) {
    log("falha ao carregar estado: " + err.message, true);
  }
}

boot();
