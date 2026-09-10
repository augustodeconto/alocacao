async function req(method, url, body) {
  const opt = { method, headers: {} };
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
  importarUpload: async (fileList) => {
    const fd = new FormData();
    for (const f of fileList) fd.append("arquivos", f, f.name);
    const r = await fetch("/api/importar-upload", { method: "POST", body: fd });
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch { data = { detail: txt }; }
    if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
    return data;
  },
  downloadUrl: (id) => `/api/projetos/${id}/download`,
  criarProjeto: (payload) => req("POST", "/api/projetos", payload),
  removerProjeto: (id) => req("DELETE", `/api/projetos/${id}`),
  exportar: (id, pasta) => req("POST", `/api/projetos/${id}/exportar`, pasta ? { pasta } : {}),
  descartarProjeto: (id) => req("POST", `/api/projetos/${id}/descartar`),
  descartarTudo: () => req("POST", "/api/descartar-tudo"),

  // versionamento (estilo Git)
  versaoGrafo: () => req("GET", "/api/versao/grafo"),
  versaoEstado: () => req("GET", "/api/versao/estado"),
  versaoCommit: (mensagem) => req("POST", "/api/versao/commit", { mensagem }),
  versaoBranch: (nome, a_partir, trocar) =>
    req("POST", "/api/versao/branch", { nome, a_partir: a_partir || null, trocar: !!trocar }),
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
      headers: { "Content-Type": "application/json" },
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
