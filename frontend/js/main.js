import { api } from "./api.js";
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
  showAll: localStorage.getItem("showAll") === "1",
  selMatricula: null,
  selAloc: null,
  extraTail: 0,   // meses extras à direita, adicionados na tela para lançar horas além de tudo
  view: localStorage.getItem("view") || "alloc",       // alloc | cad | ver
  theme: localStorage.getItem("theme") || "auto",      // auto | light | dark
  sbCollapsed: localStorage.getItem("sbCollapsed") === "1",
  sbW: parseFloat(localStorage.getItem("sbW")) || 232,
  secW: parseFloat(localStorage.getItem("secW")) || 300,
  cadTab: "projetos",
  secProjId: null,   // projeto do painel de custo (projeto_id interno)
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
  if (per && cls.indexOf("cell") >= 0) td.dataset.per = per;
  if (extra) td.classList.add("extra");
  if (alt) td.classList.add("alt");
  if (colorClass) td.classList.add(colorClass);
  if (title) td.title = title;
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
  return monthCell("total", frag, { extra, ...a });
}

function colorClassFor(cores, periodo) {
  const c = cores && cores[periodo];
  return c === "vermelho" ? "over" : c === "amarelo" ? "under" : null;
}

// Linha de alocação que vale a pena mostrar: com "só ativos" ligado, esconde as
// puramente históricas (nenhuma hora do mês atual em diante). Linhas novas
// (fora da baseline) e removidas continuam visíveis — foram mexidas de propósito.
function filhoVisivel(f) {
  return S.showAll || f.novo || f.removido || f.tem_horas_futuras;
}


function renderGridProjeto() {
  const g = S.estado.grade;
  gridProj.innerHTML = "";
  gridProj.append(headerRow(PERIODOS, "Projeto · Tipo · Pessoa"));
  const tb = el("tbody");

  for (const proj of g.por_projeto) {
    if (!gpVisivel(proj.gestor_projetos)) continue;
    if (!S.showAll && !proj.tem_futuro) continue;
    const key = `p:${proj.projeto_id}`;
    const janela = new Set(proj.periodos_projeto);
    const acoes = el("span");
    acoes.append(
      proj.sujo ? el("span", { title: "alterações não exportadas", textContent: " ●" }) : "",
      el("span", {
        className: "tw-toggle", title: "exportar .xlsx", textContent: " ⭳",
        onclick: (e) => { e.stopPropagation(); doExport(proj.projeto_id); },
      }),
      el("span", {
        className: "tw-toggle", title: "descartar mudanças deste projeto (voltar à baseline)",
        textContent: " ↺",
        onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`Descartar as mudanças de "${proj.nome}"?\nVolta ao estado da baseline (BI).`)) return;
          try { S.estado = (await api.descartarProjeto(proj.projeto_id)).estado; render(); }
          catch (err) { log(err.message, true); }
        },
      }),
      el("span", {
        className: "tw-toggle", title: "definir a baseline = estado atual (commit local)",
        textContent: " ⌾",
        onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`Definir a linha de base de "${proj.nome}" como o estado atual?`)) return;
          try { S.estado = (await api.marcarBaseline(proj.projeto_id)).estado; render(); }
          catch (err) { log(err.message, true); }
        },
      }),
      el("span", {
        className: "tw-toggle", title: "remover projeto do banco", textContent: "✕",
        onclick: (e) => { e.stopPropagation(); doRemoveProjeto(proj.projeto_id, proj.nome); },
      }),
    );
    let nomeProj = proj.nome;
    if (proj.encerrado) nomeProj += "  (encerrado)";
    if (proj.gestor_projetos) nomeProj += `   ·   GP: ${proj.gestor_projetos}`;
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
      if (!visFilhos.length && !grp.modificado && !S.showAll) continue;
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
          tr.append(monthCell(rem ? "cell" : "cell editable", cellValueNodes(h, f.capacidade_mensal),
            { per: p, extra: !janela.has(p), colorClass: rem ? null : colorClassFor(f.cores, p), ...altInfo(f, p) }));
        }
        tb.append(tr);
      }
      const addg = el("tr", { className: "addrow" });
      addg.append(el("td", { className: "treecol ind2", colSpan: PERIODOS.length + 1, textContent: "+ adicionar pessoa",
        onclick: () => openAddAloc({ projeto_id: proj.projeto_id, tipo_alocacao: grp.tipo_alocacao }) }));
      tb.append(addg);
    }
    const add = el("tr", { className: "addrow" });
    add.append(el("td", { className: "treecol ind1", colSpan: PERIODOS.length + 1, textContent: "+ adicionar tipo/equipe",
      onclick: () => openAddAloc({ projeto_id: proj.projeto_id }) }));
    tb.append(add);
  }
  gridProj.append(tb);
}

function renderGridRecurso() {
  const g = S.estado.grade;
  gridRec.innerHTML = "";
  gridRec.append(headerRow(PERIODOS, "Pessoa · Projeto"));
  const tb = el("tbody");

  for (const pes of g.por_recurso) {
    if (!S.showAll && !pes.tem_futuro) continue;
    // O filtro de GP é um filtro de PROJETOS: age só na grade "Por Projeto".
    // "Por Recurso" sempre mostra todos os projetos da pessoa e o total real.
    // Esconde só as linhas puramente históricas (ver filhoVisivel).
    const filhos = pes.filhos.filter(filhoVisivel);
    const key = `r:${pes.matricula}`;
    const capLabel = el("span", {
      className: "tw-toggle", style: "width:auto;color:var(--muted)",
      title: "editar capacidade mensal",
      textContent: `  [cap: ${pes.capacidade_mensal ?? "?"}]`,
      onclick: (e) => { e.stopPropagation(); editCapacidade(pes); },
    });
    const tr0 = el("tr", { className: "lvl0", dataset: { matricula: pes.matricula } });
    tr0.append(treeCell(pes.nome, { level: 0, key, expandable: true, extra: capLabel }));
    for (const p of PERIODOS) {
      tr0.append(monthCell("total", cellValueNodes(pes.totais[p] || 0, pes.capacidade_mensal),
        { colorClass: colorClassFor(pes.cores, p), ...totAltInfo(pes, p) }));
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
    const add = el("tr", { className: "addrow" });
    add.append(el("td", { className: "treecol", colSpan: PERIODOS.length + 1, textContent: "+ adicionar alocação",
      onclick: () => openAddAloc({ matricula: pes.matricula, nome: pes.nome }) }));
    tb.append(add);
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

function gpVisivel(gestor) {
  return !S.gpFilter || gestor === S.gpFilter;
}
function projetoIdGestor() {
  const m = {};
  for (const p of S.estado.projetos) m[p.projeto_id] = p.gestor_projetos;
  return m;
}
function refreshGpFilter() {
  const sel = $("#gp-filter");
  const gps = [...new Set(S.estado.projetos.map((p) => p.gestor_projetos).filter(Boolean))].sort();
  sel.innerHTML = "";
  sel.append(el("option", { value: "", textContent: "Todos os GPs" }));
  for (const g of gps) sel.append(el("option", { value: g, textContent: g, selected: g === S.gpFilter }));
  if (S.gpFilter && !gps.includes(S.gpFilter)) { S.gpFilter = ""; localStorage.setItem("gpFilter", ""); }
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

function _render() {
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
          ALOC_INFO[f.alocacao_id] = { projeto_id: p.projeto_id, tipo: g.tipo_alocacao, matricula: f.matricula };
        }
  CONFIGURED = new Set(S.estado.grade.por_projeto.flatMap((p) => p.periodos_projeto));
  renderGridProjeto();
  renderGridRecurso();
  applySelection();
  if (EXCEL) EXCEL.rebuild();
  const pp = S.estado.grade.por_projeto;
  const visiveis = pp.filter((p) => gpVisivel(p.gestor_projetos) && (S.showAll || p.tem_futuro)).length;
  const sujos = pp.filter((p) => p.sujo).length;
  $("#proj-status").textContent =
    `${visiveis}/${pp.length} projeto(s)` + (sujos ? ` · ${sujos} não exportado(s)` : "");
  $("#btn-showall").textContent = S.showAll ? "todos" : "só ativos";
  const sbS = $("#sb-status");
  if (sbS) sbS.textContent = `${pp.length} projetos · ${(S.estado.pessoas || []).length} recursos`
    + (sujos ? ` · ${sujos} p/ exportar` : "");
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
  requestAnimationFrame(() => {
    panelProj.scrollLeft = alvo;
    panelRec.scrollLeft = alvo;
  });
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
  applySelection();
  pushSel();
  const flags = syncFlags(S.syncMode);
  if (sourceGrid === "projeto" && flags.down) syncPara("recurso", S.selAloc, S.selMatricula);
  if (sourceGrid === "recurso" && flags.up) syncPara("projeto", S.selAloc, S.selMatricula);

  let pid = projetoIdSelecionado();
  if (pid == null && tr.dataset.projetoId) pid = Number(tr.dataset.projetoId);
  if (pid != null) openCusto(pid);
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

// -- add allocation dialog -----------------------------------------
function openAddAloc(ctx) {
  const dlg = $("#dlg-aloc");
  const body = $("#da-body");
  body.innerHTML = "";
  const tipos = (S.estado.catalogos.tipo_alocacao || []).map((t) => t.texto);
  const selTipo = el("select", {}, ...tipos.map((t) => el("option", { value: t, textContent: t })));
  if (ctx.tipo_alocacao) selTipo.value = ctx.tipo_alocacao;

  let getPayload;
  if (ctx.projeto_id) {
    const pessoas = S.estado.pessoas;
    const selPes = el("select", {}, el("option", { value: "", textContent: "— nova matrícula —" }),
      ...pessoas.map((p) => el("option", { value: p.matricula, textContent: `${p.nome} (${p.matricula})` })));
    const inMat = el("input", { placeholder: "matrícula" });
    const inNome = el("input", { placeholder: "nome" });
    body.append(el("label", {}, "Pessoa"), selPes, el("label", {}, "Nova matrícula"), inMat,
      el("label", {}, "Nome"), inNome, el("label", {}, "Tipo alocação"), selTipo);
    getPayload = () => {
      const matricula = selPes.value || inMat.value.trim();
      if (!matricula) throw new Error("informe a pessoa");
      return { projeto_id: ctx.projeto_id, matricula, nome: selPes.value ? "" : inNome.value.trim(),
        tipo_alocacao: selTipo.value };
    };
  } else {
    const projs = S.estado.projetos;
    const selProj = el("select", {}, ...projs.map((p) => el("option", { value: p.projeto_id, textContent: p.nome })));
    body.append(el("label", {}, "Projeto"), selProj, el("label", {}, "Tipo alocação"), selTipo);
    getPayload = () => ({ projeto_id: Number(selProj.value), matricula: ctx.matricula, nome: ctx.nome,
      tipo_alocacao: selTipo.value });
  }

  const ok = $("#da-ok");
  ok.onclick = async () => {
    try {
      const res = await api.criarAlocacao(getPayload());
      S.estado = res.estado;
      dlg.close();
      render();
    } catch (err) { log(err.message, true); }
  };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

// -- capacity ------------------------------------------------------
async function editCapacidade(pes) {
  const v = prompt(`Capacidade mensal de ${pes.nome} (horas). Vazio = digitar carga diária.`,
    pes.capacidade_mensal ?? "");
  if (v === null) return;
  try {
    let payload;
    if (v.trim() === "") {
      const cd = prompt("Carga diária (8, 6, 4)…", pes.carga_diaria ?? "8");
      if (cd === null) return;
      payload = { carga_diaria: Number(cd) };
    } else {
      payload = { capacidade_mensal: Number(v) };
    }
    const res = await api.editarPessoa(pes.matricula, payload);
    S.estado = res.estado;
    render();
  } catch (err) { log(err.message, true); }
}

// -- toolbar actions --------------------------------------------------
const fileInput = Object.assign(document.createElement("input"), {
  type: "file", accept: ".xlsx", multiple: true, style: "display:none",
});
document.body.append(fileInput);
fileInput.addEventListener("change", async () => {
  if (!fileInput.files.length) return;
  const arq = $("#arq-log");
  try {
    log("enviando…");
    if (arq) arq.textContent = "enviando…";
    const res = await api.importarUpload(fileInput.files);
    S.estado = res.estado;
    const resumo = res.resultados.map(fmtResultado).join("\n");
    log(res.resultados.map(fmtResultado).join("  ·  "));
    if (arq) arq.textContent = resumo;
    render();
    if (S.view === "cad") cadLoad(S.cadTab);
    if (S.view === "ver") renderVer();
  } catch (err) { log(err.message, true); if (arq) arq.textContent = err.message; }
  fileInput.value = "";
});

function doImport() {
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

async function doExport(projeto_id) {
  const a = document.createElement("a");
  a.href = api.downloadUrl(projeto_id);
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
  log("gerando .xlsx para download…");
  setTimeout(async () => {
    try { S.estado = await api.estado(); render(); } catch {}
  }, 800);
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
      return { cb, sujo: !!p.sujo, gp: p.gestor_projetos || "" };
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

async function doRemoveProjeto(projeto_id, nome) {
  if (!confirm(`Remover "${nome}" do banco? (o arquivo .xlsx não é apagado)`)) return;
  try {
    const res = await api.removerProjeto(projeto_id);
    S.estado = res.estado;
    render();
  } catch (err) { log(err.message, true); }
}

function openNovoProjeto() {
  const dlg = $("#dlg-novo");
  $("#np-ok").onclick = async () => {
    try {
      const res = await api.criarProjeto({
        id_projeto_externo: $("#np-id").value.trim() || null,
        nome: $("#np-nome").value.trim(),
        empresa: $("#np-empresa").value.trim(),
        status: $("#np-status").value.trim(),
        matricula_gp: $("#np-gp").value.trim(),
        id_filial: Number($("#np-filial").value) || 62,
        mes_inicio: Number($("#np-mes").value),
        ano_inicio: Number($("#np-ano").value),
        meses: Number($("#np-meses").value),
      });
      S.estado = res.estado;
      dlg.close();
      render();
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
  for (const b of document.querySelectorAll(".sb-item[data-view]")) {
    const match = b.dataset.view === view &&
      (view !== "cad" || !b.dataset.tab || b.dataset.tab === S.cadTab);
    b.classList.toggle("active", match);
  }
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
  const btn = $("#rail-theme");
  if (btn) btn.title = "Tema: " + { auto: "automático", light: "claro", dark: "escuro" }[S.theme];
}
function cycleTheme() {
  S.theme = { auto: "light", light: "dark", dark: "auto" }[S.theme] || "auto";
  localStorage.setItem("theme", S.theme);
  applyTheme();
}

// -- larguras / recolher das barras laterais ----------------------
function applyLayout() {
  const app = $("#app");
  app.classList.toggle("sb-collapsed", S.sbCollapsed);
  app.style.setProperty("--sb-w", S.sbCollapsed ? "0px" : (S.sbW || 232) + "px");
  app.style.setProperty("--sec-w", (S.secW || 300) + "px");
  const c = $("#sb-collapse");
  if (c) c.textContent = S.sbCollapsed ? "›" : "‹";
  const r = $("#rail-sb");
  if (r) r.classList.toggle("on", !S.sbCollapsed);
}
const applySidebar = applyLayout;   // compat

function toggleSidebar() {
  S.sbCollapsed = !S.sbCollapsed;
  localStorage.setItem("sbCollapsed", S.sbCollapsed ? "1" : "0");
  applyLayout();
}

function startWResize(e, which) {
  e.preventDefault();
  const app = $("#app");
  const key = which === "sb" ? "--sb-w" : "--sec-w";
  const startX = e.clientX;
  const startW = parseFloat(getComputedStyle(app).getPropertyValue(key)) ||
    (which === "sb" ? 232 : 300);
  const [min, max] = which === "sb" ? [150, 520] : [220, 640];
  const handle = which === "sb" ? $("#sb-resize") : $("#sec-resize");
  handle.classList.add("dragging");
  document.body.style.cursor = "col-resize";
  if (which === "sb" && S.sbCollapsed) toggleSidebar();
  const onMove = (ev) => {
    const dx = ev.clientX - startX;
    const w = Math.max(min, Math.min(max, which === "sb" ? startW + dx : startW - dx));
    app.style.setProperty(key, w + "px");
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    handle.classList.remove("dragging");
    document.body.style.cursor = "";
    const w = Math.round(parseFloat(getComputedStyle(app).getPropertyValue(key)));
    if (which === "sb") { S.sbW = w; localStorage.setItem("sbW", w); }
    else { S.secW = w; localStorage.setItem("secW", w); }
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// ==================================================================
//  BARRA DIREITA (rail + painéis).  Custo do projeto = 1º painel.
// ==================================================================
const SEC_PANELS = {
  custo: { titulo: "Custo do projeto", render: () => renderCusto() },
  // futuros painéis entram aqui (resumo, capacidade da equipe, …)
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
  if (S.secPanel == null) { S.secPanel = "custo"; localStorage.setItem("secPanel", "custo"); }
  renderSecondary();
}
function maybeOpenCusto() {
  const pid = projetoIdSelecionado();
  if (pid != null) openCusto(pid);
}

const CUSTO_CORES = ["var(--accent)", "#e8b23a", "#6cc0a4", "#c07ad6", "#e0796b", "#7f9cd6"];
const _brl = (n) => n >= 1e6 ? `R$ ${(n / 1e6).toFixed(2)} M`
  : n >= 1e3 ? `R$ ${(n / 1e3).toFixed(0)} k` : `R$ ${Math.round(n)}`;

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
        <div><div class="lbl">Total (daqui pra frente)</div><div class="val">${_brl(totGeral)}</div></div>
        <div><div class="lbl">Horas</div><div class="val">${Math.round(horasGeral).toLocaleString("pt-BR")}</div></div>
      </div>
      <svg class="sec-donut" width="128" height="128" viewBox="0 0 42 42" role="img" aria-label="composição do custo por tipo">
        <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--disabled)" stroke-width="6"/>
        ${segs}
      </svg>
      <div class="sec-legend">${porTipo.map((t, i) =>
        `<span><i style="background:${CUSTO_CORES[i % CUSTO_CORES.length]}"></i>${t.nome} ${totGeral ? Math.round(t.custo / totGeral * 100) : 0}%</span>`).join("")}
      </div>
      <div class="sec-h">Custo por mês</div>
      <div class="sec-bars">${pers.map((p) => {
        const v = totMes[p] || 0;
        return `<div class="b"><span class="m">${fmtMes(p)}</span><span class="track"><span class="fill" style="width:${(v / maxMes * 100).toFixed(1)}%"></span></span><span class="n">${_brl(v)}</span></div>`;
      }).join("")}</div>`;
  } catch (err) {
    body.innerHTML = `<div class="sec-empty">falha ao carregar custo: ${err.message}</div>`;
  }
}

// ==================================================================
//  VIEW CADASTROS (projetos / recursos / catálogo)
// ==================================================================
const CAD_LABELS = {
  projeto_id: "ID interno", id_projeto_externo: "Id_projeto", nome: "Nome",
  empresa: "Empresa", status: "Status", id_status: "idStatus",
  matricula_gp: "Matrícula GP", id_filial: "Filial", gestor_projetos: "Gestor",
  mes_inicio: "Mês início", ano_inicio: "Ano início",
  cenario1: "Cenário 1", cenario2: "Cenário 2", cenario3: "Cenário 3",
  arquivo_origem: "Arquivo origem", criado_na_ferramenta: "Criado aqui",
  exportado_em: "Exportado em", alterado_em: "Alterado em",
  matricula: "Matrícula", carga_diaria: "Carga diária",
  capacidade_mensal: "Capacidade mensal", ativo: "Ativo", area: "Área",
  tipo_contrato: "Tipo contrato", situacao: "Situação", fim_contrato: "Fim contrato",
};
const CAD_NUM = new Set(["id_status", "id_filial", "mes_inicio", "ano_inicio",
  "cenario1", "cenario2", "cenario3", "carga_diaria", "capacidade_mensal"]);
let CAD = { tab: "projetos", cols: [], editaveis: [], rows: [] };

function cadCellInput(row, col) {
  let inp;
  if (col === "ativo") {
    inp = el("select", {}, ...[["Sim", "1"], ["Não", "0"]].map(([t, v]) =>
      el("option", { value: v, textContent: t, selected: String(row[col] ?? 1) === v })));
  } else {
    inp = el("input", { type: CAD_NUM.has(col) ? "number" : "text", value: row[col] ?? "" });
    if (col === "carga_diaria") inp.step = "0.5";
  }
  inp._orig = String(inp.value);
  const commit = async () => {
    if (String(inp.value) === inp._orig) return;
    const td = inp.closest("td");
    td.classList.remove("saved", "error");
    try {
      const key = CAD.tab === "projetos" ? row.projeto_id : row.matricula;
      const save = CAD.tab === "projetos" ? api.cadSalvarProjeto : api.cadSalvarPessoa;
      const { linha } = await save(key, { [col]: inp.value === "" ? null : inp.value });
      Object.assign(row, linha);
      inp._orig = String(inp.value);
      td.classList.add("saved");
      log(`${CAD_LABELS[col] || col} salvo.`);
      if (["nome", "gestor_projetos", "capacidade_mensal", "ativo", "situacao", "fim_contrato"].includes(col)) {
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
  for (const b of document.querySelectorAll("#cad-toolbar .cad-tabs button"))
    b.classList.toggle("on", b.dataset.tab === tab);
  for (const b of document.querySelectorAll('.sb-item[data-view="cad"]'))
    b.classList.toggle("active", b.dataset.tab === tab);
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
//  VIEW VERSÕES (baseline por projeto)
// ==================================================================
function renderVer() {
  const body = $("#ver-body");
  const projs = (S.estado?.projetos || []).slice().sort((a, b) => a.nome.localeCompare(b.nome));
  const sujos = projs.filter((p) => p.alterado_em && (!p.exportado_em || p.alterado_em > p.exportado_em));
  $("#ver-status").textContent = `${sujos.length} projeto(s) com alterações não exportadas`;
  $("#ver-descartar-tudo").disabled = !sujos.length;
  body.innerHTML = "";
  for (const p of projs) {
    const dirty = p.alterado_em && (!p.exportado_em || p.alterado_em > p.exportado_em);
    const row = el("div", { className: "ver-row" + (dirty ? " sujo" : "") });
    row.append(
      el("div", {},
        el("div", { className: "nome" }, p.nome),
        el("div", { className: "meta" },
          `alterado: ${p.alterado_em || "—"}  ·  exportado: ${p.exportado_em || "nunca"}`)),
      el("span", { className: "tag" + (dirty ? " dirty" : "") }, dirty ? "não exportado" : "em dia"),
      el("span", {}),
    );
    const acts = row.lastChild;
    if (dirty) {
      acts.append(el("button", {
        title: "descartar as mudanças deste projeto (volta à baseline)",
        textContent: "↺ descartar",
        onclick: async () => {
          if (!confirm(`Descartar as mudanças de "${p.nome}"?\nVolta ao estado da baseline.`)) return;
          try { S.estado = (await api.descartarProjeto(p.projeto_id)).estado; renderVer(); render(); log("descartado"); }
          catch (err) { log(err.message, true); }
        },
      }));
    }
    acts.append(el("button", {
      title: "definir a baseline = estado atual (commit local)",
      textContent: "⌾ commit",
      onclick: async () => {
        if (!confirm(`Definir a linha de base de "${p.nome}" como o estado atual?`)) return;
        try { S.estado = (await api.marcarBaseline(p.projeto_id)).estado; renderVer(); render(); log("baseline atualizada"); }
        catch (err) { log(err.message, true); }
      },
    }));
    body.append(row);
  }
}

// -- init ---------------------------------------------------------
let EXCEL = null;

function openArquivos() {
  const dlg = $("#dlg-arquivos");
  $("#arq-log").textContent = "";
  $("#arq-import").onclick = doImport;
  $("#arq-export").onclick = () => { dlg.close(); openExport(); };
  dlg.querySelector('button[value="cancel"]').onclick = () => dlg.close();
  dlg.showModal();
}

function wire() {
  // ---- rail / sidebar / tema ----
  for (const b of document.querySelectorAll(".rail-btn[data-view]"))
    b.onclick = () => setView(b.dataset.view);
  for (const b of document.querySelectorAll(".sb-item[data-view]"))
    b.onclick = () => setView(b.dataset.view, { tab: b.dataset.tab });
  $("#rail-files").onclick = openArquivos;
  $("#rail-theme").onclick = cycleTheme;
  $("#sb-collapse").onclick = toggleSidebar;
  $("#rail-sb").onclick = toggleSidebar;
  $("#sb-resize").addEventListener("mousedown", (e) => startWResize(e, "sb"));
  $("#sec-resize").addEventListener("mousedown", (e) => startWResize(e, "sec"));

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
  $("#btn-showall").onclick = () => {
    S.showAll = !S.showAll;
    localStorage.setItem("showAll", S.showAll ? "1" : "0");
    render();
  };
  for (const b of document.querySelectorAll("button[data-grid]"))
    b.onclick = () => setOpen(b.dataset.grid, b.dataset.act === "expand");

  gridProj.addEventListener("click", (e) => onRowClick(e, "projeto"));
  gridRec.addEventListener("click", (e) => onRowClick(e, "recurso"));
  bindScrollSync(panelProj, panelRec);

  // ---- view Cadastros ----
  for (const b of document.querySelectorAll("#cad-toolbar .cad-tabs button"))
    b.onclick = () => cadLoad(b.dataset.tab);
  $("#cad-busca").addEventListener("input", cadFilter);
  $("#cad-novo").onclick = openNovoProjeto;

  // ---- view Versões ----
  $("#ver-descartar-tudo").onclick = async () => {
    if (!confirm("Descartar TODAS as mudanças de TODOS os projetos?\nTudo volta ao estado da baseline (BI).")) return;
    try { S.estado = (await api.descartarTudo()).estado; log("mudanças descartadas"); renderVer(); render(); }
    catch (err) { log(err.message, true); }
  };

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

async function boot() {
  window.addEventListener("error", (e) => log("JS: " + e.message, true));
  window.addEventListener("unhandledrejection", (e) => log("JS: " + (e.reason && e.reason.message || e.reason), true));
  restoreLayout();
  applyTheme();
  applySidebar();
  wire();
  setView(S.view);
  refreshNavBtns();
  try {
    S.estado = await api.estado();
    render();
    if (S.view === "cad") cadLoad(S.cadTab);
    if (S.view === "ver") renderVer();
    log("pronto. Use o ícone de pasta (rail) para importar/exportar .xlsx.");
  } catch (err) {
    log("falha ao carregar estado: " + err.message, true);
  }
}

boot();
