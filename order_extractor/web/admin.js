const byId = id => document.getElementById(id);
let token = "", resource = "";
const columns = {
  customers:[["name","Ragione sociale"],["status","Stato"]],
  licenses:[["prefix","Prefisso"],["customer_id","Cliente"],["status","Stato"],["seats","Posti"],["offline_days","Giorni offline"],["channel","Canale"],["valid_until","Scadenza"]],
  installations:[["label","Postazione"],["license_id","Licenza"],["status","Stato"],["app_version","Versione"],["last_seen","Ultimo contatto"]],
  audit:[["recorded","Data"],["action","Azione"],["actor","Attore"],["target","Oggetto"]],
  events:[["recorded","Data"],["code","Evento"],["severity","Severità"],["app_version","Versione"],["page_count","Pagine"],["row_count","Righe"]]
};
const timeFields = new Set(["valid_until", "last_seen", "recorded"]);

async function api(path, method="GET", data) {
  const response = await fetch("/api/v1/admin/" + path, {method,
    headers:{"Authorization":"Bearer " + token,"Content-Type":"application/json"},
    body: method === "GET" ? undefined : JSON.stringify(data || {})});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).message || "Operazione non riuscita.");
  return response.status === 204 ? null : response.json();
}

async function run(action) {
  byId("error").hidden = true;
  try { await action(); }
  catch(error) { byId("error").textContent = error.message; byId("error").hidden = false; }
}

function node(name, text, parent) {
  const element = document.createElement(name);
  if (text !== undefined) element.textContent = text;
  parent.append(element);
  return element;
}

function button(parent, label, update) {
  const result = node("button", label, parent);
  result.className = "button outline";
  result.onclick = () => run(async () => { await update(); await load(resource); });
}

async function load(name) {
  const list = await api(name);
  const customers = name === "licenses" ? await api("customers") : [];
  const labels = new Map(customers.map(item => [item.id,item.name]));
  resource = name;
  byId("customer").hidden = name !== "customers";
  byId("license").hidden = name !== "licenses";
  if (name === "licenses") {
    const select = byId("license").querySelector("select");
    select.replaceChildren();
    for (const item of customers) node("option", item.name, select).value = item.id;
  }
  for (const item of document.querySelectorAll("[data-resource]")) {
    item.setAttribute("aria-current", item.dataset.resource === name ? "page" : "false");
  }
  const result = byId("results");
  result.replaceChildren();
  if (!list.length) {
    node("p", "Nessun risultato.", result).className = "empty";
    return;
  }
  const table = node("table", undefined, result);
  table.className = "history-table";
  const head = node("tr", undefined, node("thead", undefined, table));
  for (const [,label] of columns[name]) node("th", label, head).scope = "col";
  if (!["audit","events"].includes(name)) node("th", "Azioni", head).scope = "col";
  const body = node("tbody", undefined, table);
  for (const item of list) {
    const tr = node("tr", undefined, body);
    for (const [field] of columns[name]) {
      const value = item[field];
      const rendered = name === "installations" && field === "status" && value === "released" ? "disattivata" : value;
      node("td", rendered == null || rendered === "" ? "—" : timeFields.has(field)
        ? new Date(rendered * 1000).toLocaleString("it-IT") : field === "customer_id" ? labels.get(rendered) || rendered : rendered, tr);
    }
    if (["audit","events"].includes(name)) continue;
    const actions = node("td", undefined, tr);
    if (name === "customers") button(actions, item.status === "active" ? "Sospendi" : "Riattiva", () => {
      if (confirm("Modificare lo stato del cliente?")) return api("customers/" + item.id, "PATCH", {status:item.status === "active" ? "suspended" : "active"});
    });
    if (name === "installations" && item.status !== "revoked") button(actions, "Revoca", () => {
      if (confirm("Revocare la postazione? Per riattivarla servirà un intervento amministrativo.")) return api("installations/" + item.id + "/revoke", "POST");
    });
    if (name === "licenses" && item.status !== "revoked") {
      button(actions, item.status === "active" ? "Sospendi" : "Riattiva", () => {
        if (confirm("Modificare lo stato della licenza?")) return api("licenses/" + item.id, "PATCH", {
          status:item.status === "active" ? "suspended" : "active", seats:item.seats,
          offline_days:item.offline_days,channel:item.channel,valid_until:item.valid_until});
      });
      button(actions, "Revoca", () => {
        if (confirm("Revocare la licenza?")) return api("licenses/" + item.id, "PATCH", {
          status:"revoked", seats:item.seats, offline_days:item.offline_days,
          channel:item.channel,valid_until:item.valid_until});
      });
    }
  }
}

byId("session").onsubmit = event => {
  event.preventDefault();
  token = byId("token").value.trim();
  byId("token").value = "";
  run(async () => {
    await load("customers"); byId("tools").hidden = false; byId("exit").hidden = false;
  });
};
byId("exit").onclick = () => {
  token = ""; byId("tools").hidden = true; byId("exit").hidden = true;
  byId("serial").hidden = true; byId("serial").textContent = ""; byId("results").replaceChildren();
};
for (const item of document.querySelectorAll("[data-resource]")) item.onclick = () => run(async () => {
  byId("serial").textContent = ""; byId("serial").hidden = true;
  await load(item.dataset.resource);
});
byId("customer").querySelector("form").onsubmit = event => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => { await api("customers", "POST", Object.fromEntries(new FormData(form))); form.reset(); await load(resource); });
};
byId("license").querySelector("form").onsubmit = event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  run(async () => {
    const issued = await api("licenses","POST",{...data,seats:Number(data.seats),offline_days:Number(data.offline_days)});
    byId("serial").textContent = "Copia il seriale ora\n" + issued.serial; byId("serial").hidden = false;
    await load(resource);
  });
};
