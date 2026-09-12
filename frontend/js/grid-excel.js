// Camada "estilo Excel" para as grades: seleção de célula/bloco, navegação por
// teclado, digitar/F2 para editar, Enter/Tab, Delete zera, arrastar seleciona,
// alça de preenchimento, Ctrl+C / Ctrl+V.
//
// A seleção cobre TODA célula da grade — coluna 1 (árvore: projeto/tipo/pessoa),
// linhas de total (projeto/tipo/pessoa, não editáveis) e linhas "+ adicionar..."
// (células vazias, sem alocação) — não só as células de hora editáveis. Isso permite
// arrastar um bloco retangular que atravesse tudo (ex.: um projeto inteiro, com seus
// grupos e a linha "+ adicionar pessoa" no meio) e copiar pro Excel de uma vez. Só o
// destino de edição/paste/delete/preenchimento continua restrito às células
// realmente editáveis (`td.cell.editable`); as demais entram na seleção/cópia como
// texto (ou em branco, nas linhas "+ adicionar...") mas ignoram escrita.
//
// hooks = {
//   periodos: () => string[],                       // PERIODOS atual
//   batch:   async (edits) => void,                 // [{alocacaoId, periodo, valor}]
//   edit:    (td, {initial, after}) => void,        // abre editor de 1 célula
// }

let focused = null;

export function initExcel(grids, hooks) {
  const insts = grids.map((g) => new EG(g, hooks));
  document.addEventListener("keydown", (e) => focused && focused.onKey(e));
  document.addEventListener("copy", (e) => focused && focused.onCopy(e));
  document.addEventListener("paste", (e) => focused && focused.onPaste(e));
  document.addEventListener("mousemove", (e) => focused && focused.onMove(e));
  document.addEventListener("mouseup", () => insts.forEach((i) => i.endDrag()));
  return {
    rebuild: () => insts.forEach((i) => i.rebuild()),
    // pra menu de contexto (botão direito): célula clicada dentro da seleção atual ->
    // toda a seleção; fora dela (ou sem seleção) -> só aquela célula (e a seleção
    // pula pra ela, igual o Excel faz). Só devolve células de hora editáveis — o
    // menu de ajuste rápido de alocação não faz sentido em coluna 1 / total / addrow.
    cellsPara: (td) => {
      const inst = insts.find((i) => i.grid.contains(td));
      return inst ? inst.cellsPara(td) : null;
    },
  };
}

// Primeiro nó de texto direto (não-vazio) do td — ignora ícones/badges (elementos)
// e o "twin" (%/horas pequeno, que fica dentro de um <span> filho, não é filho de
// texto direto do td). Funciona tanto pra células de hora (ícone opcional + número)
// quanto pra coluna 1 (toggle ▸/▾ opcional + nome) quanto pra totais (só o número).
function cellText(td) {
  if (!td) return "";
  for (const n of td.childNodes) {
    if (n.nodeType === 3 && n.textContent.trim()) return n.textContent.trim();
  }
  return "";
}

// nível de indentação da coluna 1, a partir da classe indN do treeCell/addRowEscolha
// (ind0 = projeto/pessoa, ind1 = tipo/projeto, ind2 = pessoa). Usado só quando a
// própria coluna 1 é copiada, pra levar uma pista hierárquica sem quebrar o
// alinhamento das colunas de mês (por isso espaços, não tab — tab abriria coluna
// nova no Excel).
function nivelDe(label) {
  const m = label && label.className.match(/ind(\d)/);
  return m ? Number(m[1]) : 0;
}

class EG {
  constructor(grid, hooks) {
    this.grid = grid;
    this.hooks = hooks;
    this.rows = [];      // rows[r] = { label: <td>, cells: [<td>|null...], meta: alocacaoId|null }
    this.sel = null;     // { r, c, ar, ac } — c=0 é a coluna 1 (árvore); c>=1 são os meses (c-1 no array `periodos`)
    this.drag = null;    // 'range' | 'fill'
    this.fillTarget = null;
    this.handle = document.createElement("div");
    this.handle.className = "fill-handle";
    grid.addEventListener("mousedown", (e) => this.onDown(e));
    grid.addEventListener("dblclick", (e) => this.onDbl(e));
  }

  rebuild() {
    const per = this.hooks.periodos();
    this.rows = [];
    for (const tr of this.grid.querySelectorAll("tbody tr")) {
      const label = tr.querySelector("td.treecol");
      if (!label) continue;
      const cells = new Array(per.length).fill(null);
      for (const td of tr.querySelectorAll("td.month")) {
        const p = td.dataset.per;
        if (!p) continue;
        const c = per.indexOf(p);
        if (c >= 0) cells[c] = td;
      }
      const r = this.rows.length;
      label._rc = [r, 0];
      cells.forEach((td, c) => { if (td) td._rc = [r, c + 1]; });
      this.rows.push({ label, cells, meta: tr.dataset.alocacaoId || null });
    }
    if (this.sel) {
      const R = this.rows.length;
      const C = per.length + 1;
      if (!R || !C) this.sel = null;
      else {
        for (const k of ["r", "ar"]) this.sel[k] = Math.min(Math.max(0, this.sel[k]), R - 1);
        for (const k of ["c", "ac"]) this.sel[k] = Math.min(Math.max(0, this.sel[k]), C - 1);
      }
    }
    this.paint();
  }

  cellAt(r, c) {
    const row = this.rows[r];
    if (!row) return null;
    return c === 0 ? row.label : (row.cells[c - 1] || null);
  }
  isEditable(r, c) {
    if (c === 0) return false;
    const td = this.cellAt(r, c);
    return !!td && td.classList.contains("editable");
  }
  focus() { focused = this; }

  cellsPara(td) {
    if (!td._rc) return null;
    const [r, c] = td._rc;
    let dentro = false;
    if (this.sel) {
      const rect = this.rect();
      dentro = r >= rect.r0 && r <= rect.r1 && c >= rect.c0 && c <= rect.c1;
    }
    if (!dentro) { this.sel = { r, c, ar: r, ac: c }; this.paint(); }
    const { r0, r1, c0, c1 } = this.rect();
    const per = this.hooks.periodos();
    const cells = [];
    for (let rr = r0; rr <= r1; rr++)
      for (let cc = Math.max(1, c0); cc <= c1; cc++)
        if (this.isEditable(rr, cc) && this.rows[rr].meta) cells.push({ alocacaoId: this.rows[rr].meta, periodo: per[cc - 1] });
    return cells;
  }

  editActive(initial) {
    if (!this.sel || !this.isEditable(this.sel.r, this.sel.c)) return;
    const td = this.cellAt(this.sel.r, this.sel.c);
    if (td) this.hooks.edit(td, { initial, after: (dir) => this.move(...(dir || [1, 0]), false) });
  }

  rect() {
    const s = this.sel;
    return {
      r0: Math.min(s.r, s.ar), r1: Math.max(s.r, s.ar),
      c0: Math.min(s.c, s.ac), c1: Math.max(s.c, s.ac),
    };
  }

  paint(fillPreview) {
    for (const td of this.grid.querySelectorAll(".sel-active,.sel-range,.fill-target"))
      td.classList.remove("sel-active", "sel-range", "fill-target");
    if (!this.sel) { this.handle.remove(); return; }
    const { r0, r1, c0, c1 } = this.rect();
    for (let r = r0; r <= r1; r++)
      for (let c = c0; c <= c1; c++) {
        const td = this.cellAt(r, c);
        if (td) td.classList.add("sel-range");
      }
    const act = this.cellAt(this.sel.r, this.sel.c);
    // a alça de preenchimento só faz sentido a partir de uma célula editável — nas
    // demais (coluna 1, totais, addrow) a seleção existe (pra copiar) mas não preenche.
    if (act) {
      act.classList.add("sel-active");
      if (this.isEditable(this.sel.r, this.sel.c)) act.appendChild(this.handle);
      else this.handle.remove();
    }
    if (fillPreview && this.fillTarget) {
      const [tr, tc] = this.fillTarget;
      const a = Math.min(this.sel.r, tr), b = Math.max(this.sel.r, tr);
      const d = Math.min(this.sel.c, tc), e = Math.max(this.sel.c, tc);
      for (let r = a; r <= b; r++)
        for (let c = d; c <= e; c++) {
          const td = this.cellAt(r, c);
          if (td) td.classList.add("fill-target");
        }
    }
  }

  move(dr, dc, extend) {
    if (!this.sel) return;
    const per = this.hooks.periodos();
    const R = this.rows.length, C = per.length + 1;
    let r = this.sel.r, c = this.sel.c;
    for (let step = 0; step < Math.max(R, C); step++) {
      const nr = Math.max(0, Math.min(R - 1, r + dr));
      const nc = Math.max(0, Math.min(C - 1, c + dc));
      if (nr === r && nc === c) break;
      r = nr; c = nc;
      if (this.cellAt(r, c)) break;
    }
    this.sel.r = r; this.sel.c = c;
    if (!extend) { this.sel.ar = r; this.sel.ac = c; }
    this.paint();
    this.cellAt(r, c)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  onDown(e) {
    if (e.button !== 0) return;
    // toggle de expandir/recolher, ações da linha (remover/restaurar/mudar tipo) e o
    // <select> inline de "+ adicionar..." têm que continuar recebendo o clique deles
    // sozinhos — não iniciar seleção de célula por cima.
    if (e.target.closest(".tw-toggle, .rowacts, select")) return;
    this.focus();   // qualquer clique na grade -> foco de teclado nela
    if (e.target.closest(".fill-handle")) { this.drag = "fill"; e.preventDefault(); return; }
    const td = e.target.closest("td.treecol, td.month");
    if (!td || !td._rc) return;
    const [r, c] = td._rc;
    if (e.shiftKey && this.sel) { this.sel.r = r; this.sel.c = c; }
    else this.sel = { r, c, ar: r, ac: c };
    this.drag = "range";
    this.paint();
    e.preventDefault();
  }

  onMove(e) {
    if (this.drag !== "range" && this.drag !== "fill") return;
    const at = document.elementFromPoint(e.clientX, e.clientY);
    const cell = at && at.closest && at.closest("td.treecol, td.month");
    if (!cell || !cell._rc || cell.closest("table") !== this.grid) return;
    const [r, c] = cell._rc;
    if (this.drag === "range") { this.sel.r = r; this.sel.c = c; this.paint(); }
    else if (this.isEditable(r, c)) { this.fillTarget = [r, c]; this.paint(true); }
  }

  endDrag() {
    if (this.drag === "fill" && this.fillTarget) this.doFill(this.fillTarget);
    this.drag = null; this.fillTarget = null;
    if (this.sel) this.paint();
  }

  onDbl(e) {
    const td = e.target.closest("td.cell.editable");
    if (td && td._rc) {
      this.focus();
      this.sel = { r: td._rc[0], c: td._rc[1], ar: td._rc[0], ac: td._rc[1] };
      this.editActive();
    }
  }

  onKey(e) {
    if (document.activeElement && document.activeElement.tagName === "INPUT") return;
    if (!this.sel) {
      if (!this.rows.length) return;
      this.sel = { r: 0, c: 0, ar: 0, ac: 0 };
    }
    const nav = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const k = e.key;
    if (nav[k]) { e.preventDefault(); this.move(nav[k][0], nav[k][1], e.shiftKey); return; }
    if (k === "Tab") { e.preventDefault(); this.move(0, e.shiftKey ? -1 : 1); return; }
    if (k === "Enter") { e.preventDefault(); this.move(e.shiftKey ? -1 : 1, 0); return; }
    if (k === "Delete" || k === "Backspace") { e.preventDefault(); this.zero(); return; }
    if (k === "F2") { e.preventDefault(); this.editActive(); return; }
    if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (!this.isEditable(this.sel.r, this.sel.c)) return;
      e.preventDefault();
      this.editActive(k);
    }
  }

  values() {
    const { r0, r1, c0, c1 } = this.rect();
    const out = [];
    for (let r = r0; r <= r1; r++) {
      const line = [];
      for (let c = c0; c <= c1; c++) {
        let txt = cellText(this.cellAt(r, c));
        // recuo hierárquico (espaços, não tab) só quando a própria coluna 1 é a
        // borda esquerda da seleção — não bagunça o alinhamento das colunas de mês.
        if (c === 0 && c0 === 0 && txt) txt = "  ".repeat(nivelDe(this.rows[r].label)) + txt;
        line.push(txt);
      }
      out.push(line);
    }
    return out;
  }

  onCopy(e) {
    if (!this.sel) return;
    // se existe uma seleção de TEXTO nativa (o usuário arrastou o mouse fora da
    // grade, ex. no painel lateral) e ela não está dentro desta grade, deixa quieto
    // — outro handler (o do painel) ou o Ctrl+C padrão do navegador que cuide dela.
    const dom = window.getSelection();
    if (dom && !dom.isCollapsed && dom.rangeCount && !this.grid.contains(dom.getRangeAt(0).commonAncestorContainer)) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", this.values().map((l) => l.join("\t")).join("\n"));
  }

  onPaste(e) {
    if (!this.sel || (document.activeElement && document.activeElement.tagName === "INPUT")) return;
    e.preventDefault();
    const text = (e.clipboardData.getData("text/plain") || "").replace(/\r\n?/g, "\n");
    let lines = text.split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines = lines.slice(0, -1);
    const src = lines.map((l) => l.split("\t"));
    const sh = src.length;
    const sw = Math.max(1, ...src.map((r) => r.length));

    // destino: a seleção. Se ela for maior que o bloco, o bloco é ladrilhado
    // (1 célula -> preenche tudo; bloco 3x1 -> repete em cada coluna). Só grava nas
    // células realmente editáveis do destino — coluna 1 / totais / addrow ignoram.
    const { r0, r1, c0, c1 } = this.rect();
    const dh = Math.max(r1 - r0 + 1, sh);
    const dw = Math.max(c1 - c0 + 1, sw);
    const per = this.hooks.periodos();
    const edits = [];
    for (let i = 0; i < dh; i++)
      for (let j = 0; j < dw; j++) {
        const r = r0 + i, c = Math.max(1, c0) + j;
        if (!this.isEditable(r, c)) continue;
        const val = (src[i % sh][j % sw] ?? "").trim();
        edits.push({ alocacao_id: this.rows[r].meta, periodo: per[c - 1], valor: val });
      }
    if (edits.length) {
      this.sel = { r: r0, c: c0, ar: r0 + dh - 1, ac: c0 + dw - 1 };
      this.hooks.batch(edits);
    }
  }

  zero() {
    const { r0, r1, c0, c1 } = this.rect();
    const per = this.hooks.periodos();
    const edits = [];
    for (let r = r0; r <= r1; r++)
      for (let c = c0; c <= c1; c++)
        if (this.isEditable(r, c)) edits.push({ alocacao_id: this.rows[r].meta, periodo: per[c - 1], valor: "0" });
    if (edits.length) this.hooks.batch(edits);
  }

  doFill(target) {
    const [tr, tc] = target;
    if (!this.isEditable(this.sel.r, this.sel.c)) return;
    const src = this.cellAt(this.sel.r, this.sel.c);
    if (!src) return;
    const val = cellText(src);
    const per = this.hooks.periodos();
    const a = Math.min(this.sel.r, tr), b = Math.max(this.sel.r, tr);
    const d = Math.min(this.sel.c, tc), f = Math.max(this.sel.c, tc);
    const edits = [];
    for (let r = a; r <= b; r++)
      for (let c = d; c <= f; c++) {
        if (r === this.sel.r && c === this.sel.c) continue;
        if (this.isEditable(r, c)) edits.push({ alocacao_id: this.rows[r].meta, periodo: per[c - 1], valor: val });
      }
    if (edits.length) {
      this.sel = { r: a, c: d, ar: b, ac: f };
      this.hooks.batch(edits);
    }
  }
}
