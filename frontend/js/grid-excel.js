// Camada "estilo Excel" para as grades: seleção de célula/bloco, navegação por
// teclado, digitar/F2 para editar, Enter/Tab, Delete zera, arrastar seleciona,
// alça de preenchimento, Ctrl+C / Ctrl+V.
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
  return { rebuild: () => insts.forEach((i) => i.rebuild()) };
}

function cellText(td) {
  const n = td && td.firstChild;
  return n && n.nodeType === 3 ? n.textContent.trim() : "";
}

class EG {
  constructor(grid, hooks) {
    this.grid = grid;
    this.hooks = hooks;
    this.matrix = [];   // matrix[r][c] = <td> | null
    this.meta = [];      // meta[r] = alocacaoId
    this.sel = null;     // { r, c, ar, ac }
    this.drag = null;    // 'range' | 'fill'
    this.fillTarget = null;
    this.handle = document.createElement("div");
    this.handle.className = "fill-handle";
    grid.addEventListener("mousedown", (e) => this.onDown(e));
    grid.addEventListener("dblclick", (e) => this.onDbl(e));
  }

  rebuild() {
    const per = this.hooks.periodos();
    this.matrix = [];
    this.meta = [];
    for (const tr of this.grid.querySelectorAll("tbody tr")) {
      const aloc = tr.dataset.alocacaoId;
      if (!aloc) continue;
      const eds = tr.querySelectorAll("td.cell.editable");
      if (!eds.length) continue;
      const r = this.matrix.length;
      const row = new Array(per.length).fill(null);
      eds.forEach((td) => {
        const c = per.indexOf(td.dataset.per);
        if (c >= 0) { row[c] = td; td._rc = [r, c]; }
      });
      this.matrix.push(row);
      this.meta.push(aloc);
    }
    if (this.sel) {
      const R = this.matrix.length;
      const C = per.length;
      if (!R || !C) this.sel = null;
      else {
        for (const k of ["r", "ar"]) this.sel[k] = Math.min(Math.max(0, this.sel[k]), R - 1);
        for (const k of ["c", "ac"]) this.sel[k] = Math.min(Math.max(0, this.sel[k]), C - 1);
      }
    }
    this.paint();
  }

  cellAt(r, c) { return (this.matrix[r] || [])[c] || null; }
  focus() { focused = this; }

  editActive(initial) {
    if (!this.sel) return;
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
    if (act) { act.classList.add("sel-active"); act.appendChild(this.handle); }
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
    const R = this.matrix.length, C = per.length;
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
    this.focus();   // qualquer clique na grade -> foco de teclado nela
    if (e.target.closest(".fill-handle")) { this.drag = "fill"; e.preventDefault(); return; }
    const td = e.target.closest("td.cell.editable");
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
    const cell = at && at.closest && at.closest("td.cell.editable");
    if (!cell || !cell._rc || cell.closest("table") !== this.grid) return;
    const [r, c] = cell._rc;
    if (this.drag === "range") { this.sel.r = r; this.sel.c = c; this.paint(); }
    else { this.fillTarget = [r, c]; this.paint(true); }
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
      if (!this.matrix.length) return;
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
      e.preventDefault();
      this.editActive(k);
    }
  }

  values() {
    const { r0, r1, c0, c1 } = this.rect();
    const out = [];
    for (let r = r0; r <= r1; r++) {
      const line = [];
      for (let c = c0; c <= c1; c++) line.push(cellText(this.cellAt(r, c)));
      out.push(line);
    }
    return out;
  }

  onCopy(e) {
    if (!this.sel) return;
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
    // (1 célula -> preenche tudo; bloco 3x1 -> repete em cada coluna).
    const { r0, r1, c0, c1 } = this.rect();
    const dh = Math.max(r1 - r0 + 1, sh);
    const dw = Math.max(c1 - c0 + 1, sw);
    const per = this.hooks.periodos();
    const edits = [];
    for (let i = 0; i < dh; i++)
      for (let j = 0; j < dw; j++) {
        const r = r0 + i, c = c0 + j;
        if (!this.cellAt(r, c)) continue;
        const val = (src[i % sh][j % sw] ?? "").trim();
        edits.push({ alocacao_id: this.meta[r], periodo: per[c], valor: val });
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
        if (this.cellAt(r, c)) edits.push({ alocacao_id: this.meta[r], periodo: per[c], valor: "0" });
    if (edits.length) this.hooks.batch(edits);
  }

  doFill(target) {
    const [tr, tc] = target;
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
        if (this.cellAt(r, c)) edits.push({ alocacao_id: this.meta[r], periodo: per[c], valor: val });
      }
    if (edits.length) {
      this.sel = { r: a, c: d, ar: b, ac: f };
      this.hooks.batch(edits);
    }
  }
}
