// matrícula de quem está usando o app neste navegador (docs/COLABORACAO.md
// "Identidade e papel de acesso") — escolhida uma vez no seletor de 1ª execução.
const AUTOR_KEY = "alocacao.autor";
export const getAutor = () => localStorage.getItem(AUTOR_KEY) || "";
export const setAutor = (matricula) => localStorage.setItem(AUTOR_KEY, matricula || "");

// filtro de período da tela (docs/ESPECIFICACAO.md §8) — "" = deixa o servidor decidir
// o padrão (período inicial = mês atual; período final = aberto). Mandado em toda
// chamada, não só GET /api/estado, senão a grade embutida na resposta de qualquer
// edição voltaria pro padrão a cada ação.
const PERIODO_INICIAL_KEY = "alocacao.periodoInicial";
const PERIODO_FINAL_KEY = "alocacao.periodoFinal";
// desligado = mostra tudo, sem corte nenhum (nem período inicial) — estado diferente de
// "ainda não configurado" (que cai no padrão de hoje). Default true (filtro ligado).
const PERIODO_ATIVO_KEY = "alocacao.periodoAtivo";
export const getPeriodoInicial = () => localStorage.getItem(PERIODO_INICIAL_KEY) || "";
export const setPeriodoInicial = (p) => localStorage.setItem(PERIODO_INICIAL_KEY, p || "");
export const getPeriodoFinal = () => localStorage.getItem(PERIODO_FINAL_KEY) || "";
export const setPeriodoFinal = (p) => localStorage.setItem(PERIODO_FINAL_KEY, p || "");
export const getPeriodoAtivo = () => localStorage.getItem(PERIODO_ATIVO_KEY) !== "0";
export const setPeriodoAtivo = (ativo) => localStorage.setItem(PERIODO_ATIVO_KEY, ativo ? "1" : "0");

function _autorHeaders(headers) {
  const a = getAutor();
  if (a) headers["X-Autor"] = a;
  if (!getPeriodoAtivo()) {
    headers["X-Periodo-Ativo"] = "0";
    return headers;
  }
  const pi = getPeriodoInicial();
  if (pi) headers["X-Periodo-Inicial"] = pi;
  const pf = getPeriodoFinal();
  if (pf) headers["X-Periodo-Final"] = pf;
  return headers;
}

async function req(method, url, body) {
  const opt = { method, headers: _autorHeaders({}) };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = { detail: txt }; }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

export const api = {
  estado: () => req("GET", "/api/estado"),
  varrer: (pasta) => req("GET", `/api/varrer?pasta=${encodeURIComponent(pasta)}`),
  importar: (payload) => req("POST", "/api/importar", payload),
  _upload: async (url, fileList) => {
    const fd = new FormData();
    for (const f of fileList) fd.append("arquivos", f, f.name);
    const r = await fetch(url, { method: "POST", body: fd, headers: _autorHeaders({}) });
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch { data = { detail: txt }; }
    if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
    return data;
  },
  importarUpload: (fileList) => api._upload("/api/importar-upload", fileList),
  importarProjeto: (fileList) => api._upload("/api/importar-projeto", fileList),
  importarBI: (fileList) => api._upload("/api/importar-bi", fileList),
  confirmarBI: () => req("POST", "/api/importar-bi/confirmar"),
  descartarBI: () => req("POST", "/api/importar-bi/descartar"),
  downloadUrl: (id) => `/api/projetos/${id}/download`,
  criarProjeto: (payload) => req("POST", "/api/projetos", payload),
  removerProjeto: (id) => req("DELETE", `/api/projetos/${id}`),
  exportar: (id, pasta) => req("POST", `/api/projetos/${id}/exportar`, pasta ? { pasta } : {}),
  descartarProjeto: (id) => req("POST", `/api/projetos/${id}/descartar`),
  descartarTudo: () => req("POST", "/api/descartar-tudo"),

  // versionamento (estilo Git)
  versaoGrafo: () => req("GET", "/api/versao/grafo"),
  versaoEstado: () => req("GET", "/api/versao/estado"),
  versaoDiff: (de, para) => {
    const q = new URLSearchParams();
    if (de != null) q.set("de", de);
    if (para != null) q.set("para", para);
    return req("GET", `/api/versao/diff?${q}`);
  },
  versaoCommit: (mensagem) => req("POST", "/api/versao/commit", { mensagem }),
  versaoBranch: (nome, a_partir, trocar, mover_pendencias) =>
    req("POST", "/api/versao/branch", {
      nome, a_partir: a_partir || null, trocar: !!trocar, mover_pendencias: !!mover_pendencias,
    }),
  versaoCheckout: (ref) => req("POST", "/api/versao/checkout", { ref }),
  versaoDeletarBranch: (nome) => req("DELETE", `/api/versao/branch/${encodeURIComponent(nome)}`),
  versaoMerge: (origem) => req("POST", "/api/versao/merge", { origem }),
  versaoMergeConcluir: (mensagem) => req("POST", "/api/versao/merge/concluir", { mensagem }),
  versaoMergeAbortar: () => req("POST", "/api/versao/merge/abortar"),
  criarAlocacao: (payload) => req("POST", "/api/alocacao", payload),
  mudarTipo: (id, tipo_alocacao) => req("PUT", `/api/alocacao/${id}/tipo`, { tipo_alocacao }),
  removerAlocacao: (id) => req("DELETE", `/api/alocacao/${id}`),
  restaurarAlocacao: (projetoId, matricula, tipo_alocacao) =>
    req("POST", `/api/projetos/${projetoId}/restaurar-alocacao`, { matricula, tipo_alocacao }),
  exportarVarios: async (ids, inicio) => {
    const r = await fetch("/api/exportar", {
      method: "POST",
      headers: _autorHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ projeto_ids: ids, inicio: inicio || null }),
    });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="([^"]+)"/);
    const nome = m ? m[1] : "export.zip";
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = nome;
    document.body.append(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    return nome;
  },
  editarMes: (id, periodo, valor) => req("PUT", `/api/alocacao/${id}/mes`, { periodo, valor }),
  editarMesLote: (edits) => req("PUT", "/api/alocacao/mes-lote", { edits }),
  editarPessoa: (matricula, payload) => req("PUT", `/api/pessoa/${encodeURIComponent(matricula)}`, payload),
  editarApelido: (matricula, apelido) =>
    req("PUT", `/api/pessoa/${encodeURIComponent(matricula)}/apelido`, { apelido }),

  // cadastros (tabelas projeto / pessoa / catálogo)
  cadProjetos: () => req("GET", "/api/cadastro/projetos"),
  cadSalvarProjeto: (id, patch) => req("PUT", `/api/cadastro/projetos/${id}`, patch),
  cadPessoas: () => req("GET", "/api/cadastro/pessoas"),
  cadSalvarPessoa: (mat, patch) => req("PUT", `/api/cadastro/pessoas/${encodeURIComponent(mat)}`, patch),
  cadCatalogo: () => req("GET", "/api/cadastro/catalogo"),

  // custo do BI por projeto/mês (opcionalmente subdividido)
  custoProjeto: (de, ate, por) => {
    const q = new URLSearchParams();
    if (de) q.set("de", de);
    if (ate) q.set("ate", ate);
    if (por) q.set("por", por);
    return req("GET", `/api/custo/projeto?${q}`);
  },
};
