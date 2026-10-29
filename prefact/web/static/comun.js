// Utilidades compartidas entre la página de inicio y el visor.
const MODOS = {
  explanacion: { nombre: "Explanación", desc: "Plataforma para nave, planta solar o parcela.", geom: "polígono" },
  relleno: { nombre: "Relleno / depósito", desc: "Depósito de tierras o relleno de vaguada.", geom: "polígono" },
  extraccion: { nombre: "Extracción", desc: "Cantera, gravera o préstamo.", geom: "polígono" },
  vial: { nombre: "Vial / camino", desc: "Eje de camino o acceso.", geom: "línea (eje)" },
};

// Campos de parámetros por modo: [clave, etiqueta, ayuda, paso]
const CAMPOS = {
  explanacion: [
    ["cota", "Cota de plataforma (m)", "Vacío = cota que compensa desmonte y terraplén", 0.1],
    ["talud_desmonte", "Talud de desmonte (H:V)", "1,5 → 1,5 m horizontales por 1 m vertical", 0.1],
    ["talud_terraplen", "Talud de terraplén (H:V)", "", 0.1],
    ["pendiente_pct", "Pendiente de plataforma (%)", "0 = horizontal", 0.1],
    ["azimut", "Sentido de bajada (°)", "0 norte · 90 este · 180 sur", 1],
    ["esponjamiento", "Esponjamiento", "Para el excedente a transportar", 0.05],
  ],
  relleno: [
    ["cota", "Cota final (m)", "Vacío = punto más alto del ámbito", 0.1],
    ["volumen_objetivo", "Volumen a depositar (m³)", "Si se indica, calcula la cota final", 100],
    ["talud_terraplen", "Talud exterior (H:V)", "", 0.1],
    ["esponjamiento", "Esponjamiento", "", 0.05],
  ],
  extraccion: [
    ["cota_fondo", "Cota de fondo (m)", "Si se indica, sustituye a la profundidad", 0.1],
    ["profundidad", "Profundidad bajo la cota media (m)", "", 0.5],
    ["talud_interior", "Talud del hueco (H:V)", "", 0.1],
    ["esponjamiento", "Esponjamiento", "", 0.05],
  ],
  vial: [
    ["ancho", "Ancho de plataforma (m)", "", 0.5],
    ["pendiente_max_pct", "Pendiente máxima (%)", "", 0.5],
    ["talud_desmonte", "Talud de desmonte (H:V)", "", 0.1],
    ["talud_terraplen", "Talud de terraplén (H:V)", "", 0.1],
    ["suavizado_m", "Suavizado de rasante (m)", "Ventana de suavizado del terreno", 10],
    ["esponjamiento", "Esponjamiento", "", 0.05],
  ],
};

const SEM = {
  rojo: { txt: "Excluyente", color: "#C62828" },
  ambar: { txt: "Condicionante", color: "#EF8F00" },
  verde: { txt: "Sin afección", color: "#2E7D32" },
  info: { txt: "Informativo", color: "#1E88E5" },
  gris: { txt: "Sin datos", color: "#9E9E9E" },
};

const nf = (v, d = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" :
  Number(v).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function camposHTML(modo, valores = {}, defectos = {}) {
  return (CAMPOS[modo] || []).map(([k, et, ayuda, paso]) => {
    const v = valores[k] ?? "";
    const ph = defectos[k] === null || defectos[k] === undefined ? "auto" : defectos[k];
    return `<div><label for="p_${k}">${esc(et)}</label>
      <input type="number" step="${paso}" id="p_${k}" name="${k}" value="${v === null ? "" : esc(v)}" placeholder="${esc(ph)}">
      ${ayuda ? `<div class="ayuda">${esc(ayuda)}</div>` : ""}</div>`;
  }).join("");
}

function leerCampos(contenedor) {
  const p = {};
  contenedor.querySelectorAll("input[type=number]").forEach((i) => {
    if (i.value !== "") p[i.name] = Number(i.value);
  });
  return p;
}

async function api(url, opciones = {}) {
  const r = await fetch(url, opciones);
  let cuerpo = null;
  try { cuerpo = await r.json(); } catch (_) { /* sin cuerpo JSON */ }
  if (!r.ok) throw new Error((cuerpo && cuerpo.detail) || `Error ${r.status}`);
  return cuerpo;
}
