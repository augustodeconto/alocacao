// Horizontal scroll sync between the two panels + selection sync with a 4-state mode.
// A seta segue a direção visual: "↓" (1ª posição) = propaga Por Projeto -> Por
// Recurso (de cima pra baixo); "↑" (2ª posição) = o inverso (de baixo pra cima).
export const SYNC_MODES = ["↓↑", "✕↑", "↓✕", "✕✕"];

export function nextSyncMode(m) {
  const i = SYNC_MODES.indexOf(m);
  return SYNC_MODES[(i < 0 ? 0 : (i + 1) % SYNC_MODES.length)];
}

// down = topo->baixo permitido (projeto -> recurso)
// up   = baixo->topo permitido (recurso -> projeto)
export function syncFlags(mode) {
  return { down: mode[0] === "↓", up: mode[1] === "↑" };
}

export function bindScrollSync(a, b) {
  let lock = false;
  const pair = (src, dst) => {
    src.addEventListener("scroll", () => {
      if (lock) return;
      lock = true;
      dst.scrollLeft = src.scrollLeft;
      lock = false;
    });
  };
  pair(a, b);
  pair(b, a);
}

export function scrollRowIntoView(panel, row) {
  if (!row) return;
  const pr = panel.getBoundingClientRect();
  const rr = row.getBoundingClientRect();
  // leva a linha para logo abaixo do cabeçalho fixo (~28px)
  panel.scrollTop = Math.max(0, panel.scrollTop + (rr.top - pr.top) - 30);
}
