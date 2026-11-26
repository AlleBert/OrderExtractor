import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";

const columns = [
  ["article", "Articolo"], ["lot", "Lotto"], ["description", "Descrizione"],
  ["quantity", "Quantità"], ["unit_price", "Prezzo unitario"]
];
const manualEdges = [["top","Inizio righe"], ["bottom","Fine righe"], ["right","Limite destro"], ...columns,
  ["source_row_number","Numero riga originale"], ["line_total","Totale riga"]];
const requiredEdges = new Set(["top","bottom","right","article","description","quantity","unit_price"]);
const steps = ["Carica", "Rivedi", "Esporta"];
const statusNames = {needs_review: "Da rivedere", approved: "Approvato", exported: "Esportato", cancelled: "Annullato"};
const date = value => value ? new Intl.DateTimeFormat("it-IT", {dateStyle: "medium", timeStyle: "short"}).format(new Date(value * 1000)) : "—";

function Icon({name, size=20}) {
  const paths = {
    file: <><path d="M6 3h8l4 4v14H6zM14 3v5h5"/><path d="M9 12h6m-6 4h6"/></>,
    upload: <><path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5"/></>,
    history: <><path d="M3 12a9 9 0 1 1 3 7M3 5v7h7m2-5v5l3 2"/></>,
    settings: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></>,
    shield: <><path d="m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6z"/><path d="m9 12 2 2 4-4"/></>,
    right: <path d="m9 5 7 7-7 7"/>, left: <path d="m15 5-7 7 7 7"/>,
    check: <path d="m5 12 4 4L19 6"/>, close: <path d="m6 6 12 12M6 18 18 6"/>,
    plus: <path d="M12 5v14M5 12h14"/>, download: <path d="M12 4v12m-4-4 4 4 4-4M4 16v4h16v-4"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

async function api(path, options={}) {
  const headers = options.body && typeof options.body === "string" ? {"Content-Type": "application/json"} : {};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Operazione non riuscita. Riprova.");
  }
  return response.json();
}

function App() {
  const [status, setStatus] = useState(null);
  const [release, setRelease] = useState(undefined);
  const [updateDownload, setUpdateDownload] = useState("");
  const [tab, setTab] = useState("work");
  const [job, setJob] = useState(null);
  const [draft, setDraft] = useState(null);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [history, setHistory] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [serial, setSerial] = useState("");
  const [label, setLabel] = useState("");
  const [profileName, setProfileName] = useState("");
  const [bindLayout, setBindLayout] = useState(false);
  const [mapping, setMapping] = useState(null);
  const [capture, setCapture] = useState("top");
  const fileInput = useRef(null);
  const confirmation = useRef(null);
  const ready = job && ["needs_review", "approved", "exported"].includes(job.status);
  const stage = job?.status === "exported" ? 2 : ready ? 1 : 0;
  const issueCount = job?.issues?.filter(issue => issue.severity === "blocking").length || 0;
  const suggestedProfile = job?.status === "needs_review" ?
    profiles.find(profile => profile.id === job.layout_profile?.id && profile.layouts > 0) : undefined;
  const savedColumns = job?.mapping_suggestions?.find(item => item.page === page &&
    profiles.some(profile => profile.id === item.profile_id && profile.column_layouts > 0));

  function receive(data) {
    setJob(data);
    setDraft(data && {revision: data.revision, rows: data.rows, number_style: data.number_style, currency: data.currency});
  }

  async function run(operation, task) {
    setBusy(operation);
    setError("");
    try { await task(); } catch (problem) { setError(problem.message); }
    finally { setBusy(""); }
  }

  useEffect(() => {
    Promise.all([api("/api/status"), api("/api/jobs/current"), api("/api/profiles")])
      .then(([current, work, saved]) => { setStatus(current); receive(work); setProfiles(saved); })
      .catch(problem => setError(problem.message));
  }, []);

  useEffect(() => {
    if (status?.startup_check) {
      api("/api/startup/ready", {method:"POST",body:"{}"}).catch(problem => setError(problem.message));
    }
  }, [status?.startup_check]);

  useEffect(() => {
    setBindLayout(false);
  }, [job?.token, job?.can_learn_layout]);

  useEffect(() => {
    setMapping(null);
  }, [page, job?.token]);

  useEffect(() => {
    if (job?.status !== "running") return;
    const timer = setInterval(async () => {
      try {
        const next = await api("/api/jobs/" + job.token);
        if (next.status === "running") setJob(next);
        else receive(next);
      } catch (problem) { setError(problem.message); }
    }, 750);
    return () => clearInterval(timer);
  }, [job?.token, job?.status]);

  useEffect(() => {
    if (tab === "history") api("/api/history").then(setHistory).catch(problem => setError(problem.message));
    if (tab === "settings") api("/api/status").then(setStatus).catch(problem => setError(problem.message));
  }, [tab]);

  useEffect(() => {
    const timer = setInterval(() => api("/api/status").then(setStatus).catch(() => {}), 30000);
    return () => clearInterval(timer);
  }, []);

  async function upload(file) {
    if (!file) return;
    setPage(0);
    setSelected("");
    const data = new FormData();
    data.append("file", file, file.name || "ordine-demo.pdf");
    receive(await api("/api/jobs", {method: "POST", body: data}));
  }

  function changeRow(index, field, value) {
    setDraft(previous => ({...previous, rows: previous.rows.map((row, i) => i === index ? {...row, [field]: value} : row)}));
  }

  function focusRow(row) {
    setSelected(row.row_id);
    if (row.source_spans?.length && !row.source_spans.some(span => span.page_index === page)) setPage(row.source_spans[0].page_index);
  }

  async function save() {
    const next = await api("/api/jobs/" + job.token + "/draft", {method: "PUT", body: JSON.stringify(draft)});
    receive(next);
    return next;
  }

  async function exportOrder() {
    const approved = await api("/api/jobs/" + job.token + "/approve", {method: "POST", body: JSON.stringify({...draft, reviewed: true})});
    const request = await fetch("/api/jobs/" + job.token + "/csv");
    if (!request.ok) throw new Error("Download non riuscito.");
    const objectUrl = URL.createObjectURL(await request.blob());
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "ordine-" + approved.token.slice(0, 8) + ".csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 3000);
    receive(await api("/api/jobs/" + job.token));
  }

  const dirty = ready && (draft.number_style !== job.number_style || draft.currency !== job.currency || draft.rows !== job.rows);
  const selectedSpans = draft?.rows.find(row => row.row_id === selected)?.source_spans?.filter(span => span.page_index === page) || [];

  function captureEdge(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const vertical = capture === "top" || capture === "bottom";
    const fraction = vertical ? (event.clientY - bounds.top) / bounds.height : (event.clientX - bounds.left) / bounds.width;
    setMapping(current => current && {...current,[capture]:(100 * Math.max(0, Math.min(1, fraction))).toFixed(1)});
  }

  function openMapping(saved) {
    const edges = Object.fromEntries(manualEdges.map(([key]) => [key,key === "right" ? "100" : ""]));
    if (saved) {
      for (const [key,value] of Object.entries({...saved.columns, top:saved.top, right:saved.right})) {
        edges[key] = (value * 100).toFixed(1);
      }
    }
    setCapture(saved ? "bottom" : "top");
    setMapping(edges);
  }

  async function applyMapping() {
    const {top, bottom, right, ...positions} = mapping;
    const edges = Object.fromEntries(Object.entries(positions).filter(([,value]) => value !== "").map(([key,value]) => [key,Number(value) / 100]));
    const sorted = Object.values(edges).sort((a,b) => a - b);
    if (Number(top) >= Number(bottom) || sorted.at(-1) >= Number(right) / 100 ||
        sorted.some((edge,index) => index > 0 && edge - sorted[index - 1] < .001999999)) {
      throw new Error("Controlla i limiti della tabella e le posizioni delle colonne.");
    }
    const next = await api(`/api/jobs/${job.token}/pages/${page}/mapping`, {method:"PUT", body:JSON.stringify({
      revision:draft.revision, top:Number(top) / 100, bottom:Number(bottom) / 100, right:Number(right) / 100, columns:edges,
    })});
    receive(next);
    setSelected("");
    setMapping(null);
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><img className="brand-icon" src="/assets/logo.png" alt="" width="42" height="42"/><strong>Order<br/>Extractor</strong></div>
      <nav aria-label="Navigazione">
        {[["work", "file", "Estrazione"], ["history", "history", "Storico"], ["settings", "settings", "Impostazioni"]].map(([id, icon, text]) =>
          <button key={id} className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => {setTab(id); setError("");}}>
            <Icon name={icon}/><span>{text}</span>
          </button>)}
      </nav>
      <div className="sidebar-foot"><Icon name="shield"/><div><strong>Elaborazione locale</strong><small>I PDF restano sul dispositivo</small></div></div>
    </aside>
    <div className="application">
      <header className="topbar"><strong>{tab === "work" ? "Estrazione ordini" : tab === "history" ? "Storico" : "Impostazioni"}</strong>
        <span className={"license-pill " + (status?.license.active ? "valid" : "")}><i/>{status?.license.active ? "Licenza attiva" : "Licenza da verificare"}</span>
      </header>
      <main>
        {error && <p className="notice error" role="alert">{error}</p>}
        {!status && !error && <p role="status">Avvio di Order Extractor…</p>}
        {tab === "work" && status && <>
          <div className="page-heading"><div><h1>{ready ? "Rivedi l’ordine" : "Estrai un ordine"}</h1>
            <p>{ready ? "Confronta i campi con il PDF e correggi eventuali errori." : "Trasforma un PDF d’ordine in un CSV pronto per il gestionale."}</p></div>
            {job && <button className="button outline" disabled={!!busy} onClick={() => run("Chiusura", async () => {
              if (ready && job.status !== "exported" && !window.confirm("Chiudere l’ordine e scartare la revisione?")) return;
              await api("/api/jobs/" + job.token, {method: "DELETE"}); receive(null);
            })}><Icon name="close" size={16}/>{job.status === "running" ? "Annulla" : "Chiudi ordine"}</button>}
          </div>
          <ol className="steps" aria-label="Fasi dell’estrazione">{steps.map((text, i) => <li key={text} className={stage >= i ? "current" : ""} aria-current={stage === i ? "step" : undefined}>
            <span>{stage > i ? <Icon name="check" size={13}/> : i + 1}</span>{text}
          </li>)}</ol>
          {!status.license.active && !job && <section className="panel activation">
            <div className="feature-icon"><Icon name="shield" size={25}/></div>
            <h2>Attiva la licenza</h2><p>{status.license.message}</p>
            <form onSubmit={event => {event.preventDefault(); run("Attivazione", async () => {
              const license = await api("/api/activate", {method: "POST", body: JSON.stringify({serial, label})});
              setStatus({...status, license}); setSerial("");
            });}}>
              <label>Seriale della licenza<input required minLength={12} autoComplete="off" placeholder="OE-…" value={serial} onChange={event => setSerial(event.target.value)}/></label>
              <label>Nome postazione <small>(facoltativo)</small><input placeholder="Es. Ufficio acquisti" maxLength={80} value={label} onChange={event => setLabel(event.target.value)}/></label>
              <button className="button primary" disabled={!!busy}>{busy || "Attiva licenza"}<Icon name="right" size={16}/></button>
            </form>
          </section>}
          {status.license.active && !job && <section className="panel upload-panel">
            <div className={"dropzone " + (drag ? "drag" : "")} onDragOver={event => {event.preventDefault(); setDrag(true);}}
              onDragLeave={() => setDrag(false)} onDrop={event => {event.preventDefault(); setDrag(false); if (!busy) run("Caricamento", () => upload(event.dataTransfer.files[0]));}}>
              <div className="feature-icon"><Icon name="upload" size={25}/></div>
              <h2>Carica il PDF dell’ordine</h2>
              <p>Trascina qui un documento digitale o scansionato.</p>
              <input type="file" accept=".pdf,application/pdf" ref={fileInput} hidden onChange={event => run("Caricamento", () => upload(event.target.files[0]))}/>
              <button className="button primary" disabled={!!busy} onClick={() => fileInput.current.click()}><Icon name="file" size={17}/>{busy || "Scegli un PDF"}</button>
              <small>Un PDF alla volta · Fino a 50 MiB e 100 pagine</small>
            </div>
            {status.sample && <div className="sample-row"><span>Vuoi provare con un documento di esempio?</span><button className="text-button" disabled={!!busy} onClick={() => run("Caricamento", async () => {
              const response = await fetch("/sample.pdf");
              if (!response.ok) throw new Error("PDF dimostrativo non disponibile.");
              await upload(new File([await response.blob()], "ordine-demo.pdf", {type:"application/pdf"}));
            })}>Usa l’ordine demo <Icon name="right" size={15}/></button></div>}
          </section>}
          {job?.status === "running" && <section className="panel progress-panel"><div className="feature-icon"><Icon name="file"/></div><h2>Lettura del documento</h2>
            <p role="status">{job.progress.total ? `Pagina ${Math.min(job.progress.checked + 1, job.progress.total)} di ${job.progress.total}` : "Preparazione del PDF…"}</p>
            <progress max={job.progress.total || 1} value={job.progress.checked || 0}/></section>}
          {job?.status === "failed" && <p className="notice error" role="alert">{job.error}</p>}
          {ready && <>
            {job.duplicate && <p className="notice">Questo PDF è già stato elaborato. Puoi continuare la revisione.</p>}
            {job.notices?.length > 0 && <p className="notice">{job.notices.join(" ")}</p>}
            {job.status === "exported" && <p className="notice success" role="status">CSV scaricato. Puoi chiudere l’ordine o apportare altre correzioni.</p>}
            <div className="review">
              <section className="panel preview">
                <div className="panel-head"><h2>Documento</h2><div className="pager">
                  <button className="icon-button" aria-label="Pagina precedente" disabled={page === 0} onClick={() => setPage(page - 1)}><Icon name="left" size={15}/></button>
                  <span>{page + 1} / {job.page_count}</span>
                  <button className="icon-button" aria-label="Pagina successiva" disabled={page + 1 >= job.page_count} onClick={() => setPage(page + 1)}><Icon name="right" size={15}/></button>
                </div></div>
                <div className="preview-space"><div className={mapping ? "paper mapping-paper" : "paper"} onClick={mapping ? captureEdge : undefined}><img alt={`Anteprima pagina ${page + 1}`} src={`/api/jobs/${job.token}/pages/${page}`}/>
                  <svg className="marks" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
                    {selectedSpans.map((span, index) => <rect key={index} x={span.bbox[0]} y={span.bbox[1]} width={span.bbox[2]-span.bbox[0]} height={span.bbox[3]-span.bbox[1]} />)}
                    {mapping && manualEdges.filter(([key]) => mapping[key] !== "").map(([key]) => {
                      const point = Number(mapping[key]) / 100;
                      return key === "top" || key === "bottom" ? <line className="mapping-line" key={key} x1="0" x2="1" y1={point} y2={point}/> :
                        <line className="mapping-line" key={key} x1={point} x2={point} y1={Number(mapping.top || 0) / 100} y2={Number(mapping.bottom || 100) / 100}/>;
                    })}
                  </svg>
                </div></div>
                <div className="preview-foot"><span>{job.mapped_pages?.includes(page) ? "Mappatura manuale" : job.engines[page]}</span>{!mapping && job.mappable_pages?.includes(page) &&
                  <button className="button outline" disabled={!!busy} onClick={() => openMapping()}>Mappa colonne</button>}</div>
                {!mapping && savedColumns && <div className="preview-foot"><span>Profilo colonne <strong>{savedColumns.name}</strong></span>
                  <button className="button outline" disabled={!!busy} onClick={() => openMapping(savedColumns)}>Usa colonne salvate</button></div>}
                {mapping && <form className="manual-mapping" onSubmit={event => {event.preventDefault(); run("Mappatura", applyMapping);}}>
                  <h3>Mappa la tabella di pagina {page + 1}</h3>
                  <p>Seleziona un riferimento e fai clic sul PDF, oppure inserisci le posizioni in percentuale. Per ogni colonna indica il bordo sinistro.</p>
                  <label>Riferimento da segnare<select value={capture} onChange={event => setCapture(event.target.value)}>
                    {manualEdges.map(([key,label]) => <option value={key} key={key}>{label}</option>)}
                  </select></label>
                  <div className="manual-fields">{manualEdges.map(([key,label]) => <label key={key}>{label} (%)
                    <input type="number" min="0" max="100" step="0.1" required={requiredEdges.has(key)} value={mapping[key]}
                      onFocus={() => setCapture(key)} onChange={event => setMapping({...mapping,[key]:event.target.value})}/></label>)}</div>
                  {dirty && <p>Salva le correzioni della tabella prima di rileggere la pagina.</p>}
                  <div className="review-actions"><button type="button" className="button outline" onClick={() => setMapping(null)}>Chiudi mappatura</button>
                    <button className="button primary" disabled={!!busy || dirty}>Rileggi questa pagina</button></div>
                </form>}
              </section>
              <section className="panel table-panel">
                <div className="panel-head"><div><h2>Righe d’ordine</h2><small>{draft.rows.length} righe{!dirty && issueCount ? ` · ${issueCount} errori da correggere` : ""}</small></div>
                  <button className="button outline" onClick={() => setDraft({...draft, rows:[...draft.rows, {row_id: crypto.randomUUID(), article:"", lot:"", description:"", quantity:"", unit_price:"", source_row_number:"", line_total:"", currency:"", source_spans:[]}]})}><Icon name="plus" size={15}/>Aggiungi riga</button></div>
                {suggestedProfile && (draft.number_style !== suggestedProfile.style || draft.currency !== suggestedProfile.currency) &&
                  <div className="grid-settings" role="status"><span>Profilo suggerito per questo layout <strong>{suggestedProfile.name}</strong></span>
                    <button className="button outline" onClick={() => setDraft({...draft, number_style:suggestedProfile.style, currency:suggestedProfile.currency})}>Applica profilo suggerito</button></div>}
                <div className="grid-settings">
                  <label>Numeri<select value={draft.number_style} onChange={event => setDraft({...draft, number_style:event.target.value})}><option value="auto">Automatico</option><option value="it">Italiano · 1.234,56</option><option value="en">Inglese · 1,234.56</option></select></label>
                  <label>Valuta<select value={draft.currency} onChange={event => setDraft({...draft, currency:event.target.value})}><option value="">Non impostata</option>{["EUR","USD","GBP","CHF"].map(value => <option key={value}>{value}</option>)}</select></label>
                  {profiles.length > 0 && <label>Profilo<select defaultValue="" onChange={event => {const profile = profiles.find(item => item.id === event.target.value); if (profile) setDraft({...draft, number_style:profile.style, currency:profile.currency});}}><option value="">Seleziona</option>{profiles.map(profile => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label>}
                </div>
                <div className="table-scroll"><table><thead><tr><th scope="col">#</th>{columns.map(([field, title]) => <th scope="col" key={field}>{title}</th>)}<th scope="col"><span className="sr-only">Azioni</span></th></tr></thead>
                  <tbody>{draft.rows.map((row, index) => <tr key={row.row_id} className={selected === row.row_id ? "selected" : ""}>
                    <th scope="row" title={row.source_row_number ? "Riga originale " + row.source_row_number : ""}>{index + 1}</th>
                    {columns.map(([field, title]) => {
                      const issue = !dirty && job.issues?.find(item => item.row_id === row.row_id && item.field === field && item.severity === "blocking");
                      const alternative = row.ocr_alternatives?.[field];
                      const conflict = alternative && alternative !== row[field];
                      const describedBy = conflict ? `ocr-${index}-${field}` : undefined;
                      return <td key={field}><input aria-label={title + " riga " + (index + 1)} aria-invalid={issue?.severity === "blocking" || undefined}
                        aria-describedby={describedBy} data-ocr-review={conflict ? "true" : undefined}
                        title={issue?.message || (conflict ? "Lettura OCR alternativa: " + alternative : row[field])}
                        onFocus={() => focusRow(row)} onChange={event => changeRow(index, field, event.target.value)} value={row[field] || ""}/>
                        {conflict && <span className="sr-only" id={describedBy}>Lettura OCR alternativa: {alternative}. Verifica nel PDF.</span>}</td>;
                    })}
                    <td><button className="icon-button" aria-label={"Rimuovi riga " + (index + 1)} onClick={() => setDraft({...draft, rows:draft.rows.filter(other => other.row_id !== row.row_id)})}><Icon name="close" size={15}/></button></td>
                  </tr>)}</tbody></table>
                  {!draft.rows.length && <p className="empty">Nessuna riga disponibile. Aggiungi una riga dall’anteprima.</p>}
                </div>
                {!dirty && job.issues?.length > 0 && <ul className="issues">{job.issues.slice(0, 8).map((issue, index) => <li key={index}>{issue.row_id ? `Riga ${draft.rows.findIndex(row => row.row_id === issue.row_id) + 1} · ` : ""}{issue.message}</li>)}</ul>}
                <div className="review-actions">
                  <button className="button outline" disabled={!!busy || !dirty} onClick={() => run("Verifica", save)}>Salva e verifica</button>
                  <button className="button primary" disabled={!!busy || !status.license.active} onClick={() => run("Verifica", async () => {
                    const checked = dirty ? await save() : job;
                    if (checked.issues.some(issue => issue.severity === "blocking")) throw new Error("Correggi gli errori indicati prima di esportare.");
                    confirmation.current.showModal();
                  })}><Icon name="download" size={16}/>Esporta CSV</button>
                </div>
              </section>
            </div>
            <dialog ref={confirmation}><h2>Confermi l’esportazione?</h2><p>Scaricherai {draft.rows.length} righe nel CSV. Conferma di aver confrontato i dati con il PDF.</p>
              <form method="dialog" className="dialog-actions"><button className="button outline">Torna alla revisione</button>
              <button type="button" className="button primary" onClick={() => { confirmation.current.close(); run("Esportazione", exportOrder); }}>Conferma e scarica</button></form>
            </dialog>
          </>}
        </>}
        {tab === "history" && <><div className="page-heading"><div><h1>Storico locale</h1><p>Riepilogo delle ultime elaborazioni su questa postazione.</p></div></div>
          <section className="panel"><table className="history-table"><thead><tr><th>Data</th><th>Pagine</th><th>Righe</th><th>Stato</th></tr></thead>
            <tbody>{history.map(item => <tr key={item.id}><td>{date(Date.parse(item.created.replace(" ", "T") + "Z") / 1000)}</td><td>{item.pages}</td><td>{item.rows}</td><td>{statusNames[item.status] || item.status}</td></tr>)}</tbody></table>
            {!history.length && <p className="empty">Nessuna elaborazione registrata.</p>}</section></>}
        {tab === "settings" && status && <><div className="page-heading"><div><h1>Impostazioni</h1><p>Gestisci licenza e profili numerici della postazione.</p></div></div>
          <div className="setting-grid"><section className="panel settings-card"><h2>Licenza</h2><p>{status.license.message}</p>
            <dl><dt>Versione app</dt><dd>{status.version}</dd><dt>Ultima verifica</dt><dd>{date(status.license.verified)}</dd><dt>Scadenza offline</dt><dd>{date(status.license.expires)}</dd></dl>
            <button className="button outline" disabled={!!busy || !status.license.verified} onClick={() => run("Verifica", async () => setStatus({...status, license:await api("/api/license/refresh", {method:"POST",body:"{}"})}))}>Verifica licenza online</button>
            {status.license.verified && <button className="button outline" disabled={!!busy} onClick={() => {
              if (window.confirm("Disattivare questa postazione e liberare un posto? I profili e lo storico resteranno sul dispositivo.")) {
                run("Disattivazione", async () => {
                  try {
                    const next = await api("/api/license/deactivate", {method:"POST",body:"{}"});
                    setStatus(current => ({...current,license:next}));
                  } catch (problem) {
                    const latest = await api("/api/status").catch(() => null);
                    if (latest) setStatus(latest);
                    throw problem;
                  }
                });
              }
            }}>Disattiva postazione</button>}
          </section><section className="panel settings-card"><h2>Profili locali</h2><p>Salva il formato numerico e la valuta di un documento revisionato.</p>
            {profiles.map(profile => <div className="profile-item" key={profile.id}><span>{profile.name} · {profile.style.toUpperCase()} {profile.currency}{profile.layouts + profile.column_layouts ? ` · ${profile.layouts + profile.column_layouts} layout` : ""}</span>
              <button className="icon-button" aria-label={"Elimina " + profile.name} onClick={() => run("Eliminazione", async () => {if (window.confirm("Eliminare il profilo?")) setProfiles(await api("/api/profiles/" + profile.id, {method:"DELETE"}));})}><Icon name="close" size={15}/></button></div>)}
            {ready ? <form onSubmit={event => {event.preventDefault(); run("Salvataggio", async () => {
              const layoutToken = bindLayout && job.can_learn_layout && !dirty ? job.token : null;
              setProfiles(await api("/api/profiles", {method:"POST", body:JSON.stringify({name:profileName,number_style:draft.number_style,currency:draft.currency,layout_token:layoutToken})}));
              setProfileName(""); setBindLayout(false);
            });}}><label>Nome del profilo<input value={profileName} required maxLength={80} onChange={event => setProfileName(event.target.value)}/></label>
              {job.can_learn_layout && !dirty && <label className="privacy-toggle"><input type="checkbox" checked={bindLayout} onChange={event => setBindLayout(event.target.checked)}/>Suggerisci il profilo per ordini con questo layout</label>}
              <button className="button primary" disabled={!!busy}>Salva impostazioni correnti</button></form> : <p className="mute">Apri un ordine per salvare le sue impostazioni come profilo.</p>}
          </section><section className="panel settings-card"><h2>Privacy</h2>
            <p>La diagnostica facoltativa contiene codici evento, versione e conteggi. PDF, testo e CSV restano sul dispositivo.</p>
            <label className="privacy-toggle"><input type="checkbox" checked={status.diagnostics} disabled={!!busy}
              onChange={event => {const diagnostics = event.target.checked; run("Aggiornamento privacy", async () => {
                const next = await api("/api/privacy", {method:"PUT",body:JSON.stringify({diagnostics})});
                setStatus(current => ({...current,...next}));
              });}}/> Invia diagnostica facoltativa</label>
          </section>{status.updates && <section className="panel settings-card"><h2>Aggiornamenti</h2>
            {status.preview_updates && <p>Verifica le nuove anteprime su GitHub. L’installazione avviene alla riapertura dell’app.</p>}
            {status.update_feedback && !status.update_pending && <p role="status">{status.update_feedback}</p>}
            {status.update_pending && <p role="status">Aggiornamento confermato. Chiudi e riapri Order Extractor per installarlo.</p>}
            <button className="button outline" disabled={!!busy || !status.license.active} onClick={() => run("Verifica aggiornamenti", async () => {
              setRelease(undefined); setUpdateDownload("");
              setRelease((await api("/api/update/check", {method:"POST",body:"{}"})).release);
            })}>Verifica aggiornamenti</button>
            {release === null && <p role="status">Nessuna nuova versione disponibile nel tuo canale.</p>}
            {release && <><p><strong>Versione {release.version}</strong> · {release.channel} · {(release.size / 1048576).toFixed(1)} MiB</p>
              {release.notes && <p>{release.notes}</p>}
              {status.managed_updates ? <button className="button primary" disabled={!!busy || status.update_pending} onClick={() => {
                if (window.confirm(`Installare la versione ${release.version} al prossimo avvio?`)) run("Conferma aggiornamento", async () => {
                  await api("/api/update/schedule", {method:"POST",body:JSON.stringify({version:release.version})});
                  setStatus(current => ({...current,update_pending:true}));
                });
              }}>Installa al prossimo avvio</button> :
              updateDownload ? <a className="button primary" download href={updateDownload} onClick={() => setTimeout(() => setUpdateDownload(""), 0)}>Salva il pacchetto verificato</a> :
                <button className="button primary" disabled={!!busy} onClick={() => run("Download e verifica firma", async () => {
                  const next = await api("/api/update/prepare", {method:"POST",body:JSON.stringify({version:release.version})});
                  setUpdateDownload(next.download);
                })}>{busy === "Download e verifica firma" ? busy : "Prepara download"}</button>}
            </>}
          </section>}</div>
        </>}
      </main>
    </div>
  </div>;
}

createRoot(document.getElementById("root")).render(<App/>);
