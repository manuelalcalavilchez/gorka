/* Visor de resultados: mapa, panel de indicadores, ajuste de parámetros y descargas. */
const ID = location.pathname.split("/").filter(Boolean).pop();
const BASE = `/api/estudios/${ID}`;
const $ = (id) => document.getElementById(id);

let cfg = null;
let mapa = null;
let control = null;
let capasActivas = [];
let rasteres = {};
let graficos = {};
let leyenda = null;
let encuadrado = false;
let ultimaVersion = null;
let datos = null;

// ------------------------------------------------------------------ mapa
function iniciarMapa() {
  mapa = L.map("mapa", { zoomControl: true, preferCanvas: false }).setView([40.2, -3.7], 6);
  const pnoa = L.tileLayer.wms(cfg.wms.pnoa, {
    layers: cfg.wms.pnoa_capa, format: "image/jpeg", maxZoom: 20, attribution: "PNOA © IGN",
  });
  const ign = L.tileLayer.wms(cfg.wms.ign, {
    layers: cfg.wms.ign_capa, format: "image/png", maxZoom: 20, attribution: "Mapa base © IGN",
  });
  const blanco = L.tileLayer("", { attribution: "" });
  pnoa.addTo(mapa);
  control = L.control.layers({ "Ortofoto PNOA": pnoa, "Mapa IGN": ign, "Sin fondo": blanco }, {},
    { collapsed: window.innerWidth < 1100 }).addTo(mapa);
  L.control.scale({ imperial: false }).addTo(mapa);

  // Control de opacidad de los rásteres
  const Opac = L.Control.extend({
    onAdd() {
      const d = L.DomUtil.create("div", "leaflet-control opacidad");
      d.innerHTML = `Opacidad rásteres <input type="range" min="0" max="100" value="85" style="width:90px;vertical-align:middle">`;
      L.DomEvent.disableClickPropagation(d);
      d.querySelector("input").oninput = (e) => Object.values(rasteres).forEach((r) => r.setOpacity(e.target.value / 100));
      return d;
    },
  });
  new Opac({ position: "bottomleft" }).addTo(mapa);
  leyenda = L.control({ position: "bottomright" });
  leyenda.onAdd = () => L.DomUtil.create("div", "leaflet-control leyenda");
  leyenda.addTo(mapa);
  mapa.on("overlayadd overlayremove", actualizarLeyenda);
  herramientaPerfil();
}

function limpiarCapas() {
  capasActivas.forEach((c) => { control.removeLayer(c); mapa.removeLayer(c); });
  capasActivas = [];
  rasteres = {};
}

function anadir(capa, nombre, visible = true) {
  control.addOverlay(capa, nombre);
  if (visible) capa.addTo(mapa);
  capasActivas.push(capa);
  return capa;
}

async function geo(url) {
  const r = await fetch(`${BASE}/fichero/${url}`, { cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}

function estiloAfeccion(f) {
  const p = f.properties;
  if (p.tipo === "zona") {
    const c = (SEM[p.semaforo] || SEM.gris).color;
    return { color: c, weight: p.afecta ? 1.5 : 0.8, fillColor: c, fillOpacity: p.afecta ? 0.28 : 0.12, dashArray: p.afecta ? null : "4 4" };
  }
  return { color: "#102A43", weight: 2, fillOpacity: 0.04 };
}

async function pintarMapa(r) {
  limpiarCapas();
  const v = r.visor || { raster: {}, vector: {} };
  const nombresRaster = { sombreado: "Sombreado del relieve", pendientes: "Pendientes", corte_relleno: "Desmonte / terraplén" };
  const visibles = { sombreado: false, pendientes: false, corte_relleno: true };
  for (const [k, info] of Object.entries(v.raster || {})) {
    const capa = L.imageOverlay(`${BASE}/fichero/${info.url}?v=${r.version}`, info.bounds, { opacity: 0.85 });
    capa._clave = k;
    rasteres[k] = anadir(capa, nombresRaster[k] || k, visibles[k]);
  }
  // Afecciones (una capa por fuente)
  for (const af of r.afecciones) {
    const url = (v.vector || {})[`capa:${af.id}`];
    if (!url) continue;
    const gj = await geo(url);
    if (!gj || !gj.features.length) continue;
    const capa = L.geoJSON(gj, {
      style: estiloAfeccion,
      pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color: "#102A43", weight: 1.5, fillColor: "#1E88E5", fillOpacity: .8 }),
      onEachFeature: (f, l) => l.bindPopup(`<b>${esc(af.nombre)}</b><br>${esc(f.properties.etiqueta || "")}`),
    });
    capa._afeccion = af.id;
    anadir(capa, `<span class="punto" style="background:${(SEM[af.semaforo] || SEM.gris).color}"></span> ${esc(af.nombre)}`,
      af.estado === "afecta");
  }
  if (v.vector && v.vector.ocupacion) {
    const gj = await geo(v.vector.ocupacion);
    if (gj) anadir(L.geoJSON(gj, { style: { color: "#FFFFFF", weight: 1.5, dashArray: "6 4", fillOpacity: 0 } }),
      r.modo === "vial" ? "Corredor ocupado" : "Ocupación con taludes");
  }
  if (v.vector && v.vector.eje) {
    const gj = await geo(v.vector.eje);
    if (gj) anadir(L.geoJSON(gj, {
      style: (f) => ({ color: f.properties.h > 0.2 ? "#C62828" : f.properties.h < -0.2 ? "#1565C0" : "#FFEB3B", weight: 5 }),
      onEachFeature: (f, l) => l.bindPopup(f.properties.h > 0 ? `Desmonte medio ${nf(f.properties.h, 1)} m` : `Terraplén medio ${nf(-f.properties.h, 1)} m`),
    }), "Eje (desmonte / terraplén)");
  }
  const amb = await geo("ambito.geojson");
  if (amb) {
    const capa = anadir(L.geoJSON(amb, { style: { color: "#FFEB3B", weight: 3, fillOpacity: 0 } }), "Ámbito");
    if (!encuadrado) { mapa.fitBounds(capa.getBounds().pad(0.35)); encuadrado = true; }
  }
  actualizarLeyenda();
}

function actualizarLeyenda() {
  const d = leyenda.getContainer();
  if (!d || !datos) return;
  let h = `<b>Leyenda</b><br><i style="border:2px solid #FFEB3B"></i>Ámbito<br>`;
  if (rasteres.pendientes && mapa.hasLayer(rasteres.pendientes)) {
    const c = ["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"], e = ["0–3 %", "3–10 %", "10–20 %", "20–30 %", "> 30 %"];
    h += `<div style="margin-top:4px"><b>Pendiente</b><br>${c.map((x, i) => `<i style="background:${x}"></i>${e[i]}`).join("<br>")}</div>`;
  }
  if (rasteres.corte_relleno && mapa.hasLayer(rasteres.corte_relleno)) {
    const lim = datos.visor.raster.corte_relleno.lim;
    h += `<div style="margin-top:4px"><b>Movimiento de tierras</b><br><i style="background:#B2182B"></i>Desmonte (hasta ${nf(lim, 1)} m)<br>
          <i style="background:#2166AC"></i>Terraplén (hasta ${nf(lim, 1)} m)</div>`;
  }
  h += `<div style="margin-top:4px"><b>Condicionantes</b><br>${["rojo", "ambar", "info"].map((s) => `<i style="background:${SEM[s].color};opacity:.6"></i>${SEM[s].txt}`).join("<br>")}</div>`;
  d.innerHTML = h;
}

// ------------------------------------------------------------------ perfil libre
function herramientaPerfil() {
  let activo = false, puntos = [], linea = null;
  const Btn = L.Control.extend({
    onAdd() {
      const d = L.DomUtil.create("div", "leaflet-bar leaflet-control herramienta");
      d.innerHTML = `<a href="#" title="Dibujar un perfil del terreno">Perfil</a>`;
      L.DomEvent.disableClickPropagation(d);
      d.querySelector("a").onclick = (e) => {
        e.preventDefault();
        if (activo && puntos.length >= 2) return terminar();
        activo = !activo; puntos = [];
        if (linea) { mapa.removeLayer(linea); linea = null; }
        e.target.textContent = activo ? "Terminar perfil" : "Perfil";
        mapa.getContainer().style.cursor = activo ? "crosshair" : "";
      };
      this._a = d.querySelector("a");
      return d;
    },
  });
  const btn = new Btn({ position: "topleft" }).addTo(mapa);
  mapa.on("click", (e) => {
    if (!activo) return;
    puntos.push(e.latlng);
    if (!linea) linea = L.polyline(puntos, { color: "#00E5FF", weight: 3, dashArray: "6 4" }).addTo(mapa);
    else linea.setLatLngs(puntos);
  });
  mapa.on("dblclick", (e) => { if (activo && puntos.length >= 2) { L.DomEvent.stop(e); terminar(); } });
  async function terminar() {
    activo = false;
    btn._a.textContent = "Perfil";
    mapa.getContainer().style.cursor = "";
    const coords = puntos.map((p) => `${p.lng.toFixed(6)},${p.lat.toFixed(6)}`).join(";");
    try {
      const pf = await api(`${BASE}/perfil?coords=${encodeURIComponent(coords)}`);
      $("perfil").classList.add("abierto");
      const sets = [{ label: "Terreno", data: pf.terreno, borderColor: "#6D4C41", pointRadius: 0, borderWidth: 1.5 }];
      if (pf.diseno && pf.diseno.some((v) => v !== null))
        sets.push({ label: "Diseño", data: pf.diseno, borderColor: "#0F7B6C", pointRadius: 0, borderWidth: 2, spanGaps: false });
      $("perfil-titulo").textContent = `Perfil del terreno · ${nf(pf.distancia[pf.distancia.length - 1])} m`;
      grafico("perfil_libre", "g-perfil-libre", {
        type: "line", data: { labels: pf.distancia.map((d) => nf(d)), datasets: sets },
        options: opcionesLinea("Distancia (m)", "Cota (m)"),
      });
    } catch (e) { alert(e.message); }
  }
  $("perfil-cerrar").onclick = () => { $("perfil").classList.remove("abierto"); if (linea) { mapa.removeLayer(linea); linea = null; } };
}

// ------------------------------------------------------------------ gráficos
function grafico(clave, canvasId, conf) {
  if (graficos[clave]) graficos[clave].destroy();
  const c = $(canvasId);
  if (!c) return;
  graficos[clave] = new Chart(c, conf);
}
function opcionesLinea(x, y) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
    scales: { x: { title: { display: true, text: x }, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
              y: { title: { display: true, text: y }, ticks: { font: { size: 10 } } } },
  };
}

// ------------------------------------------------------------------ panel
function kpi(et, v) { return `<div class="kpi"><small>${esc(et)}</small><b>${v}</b></div>`; }
function filas(pares) { return `<table class="tabla">${pares.map(([a, b]) => `<tr><td>${esc(a)}</td><td class="num">${b}</td></tr>`).join("")}</table>`; }

function pintarResumen(r) {
  const g = r.global, a = r.ambito, v = r.volumenes || {}, st = (r.terreno || {}).estadisticas || {};
  const terr = r.territorio || {};
  let h = `<div class="semaforo s-${g.semaforo}"><div class="dot"></div><div><b>${SEM[g.semaforo].txt.toUpperCase()}</b><span>${esc(g.texto)}</span></div></div>`;
  h += `<div class="kpis">`;
  h += a.tipo === "poligono" ? kpi("Superficie del ámbito", `${nf(a.area_m2 / 10000, 2)} ha`) : kpi("Longitud del eje", `${nf(a.longitud_m)} m`);
  h += kpi("Superficie de obra", `${nf(a.area_obra_m2 / 10000, 2)} ha`);
  if (v.volumen_desmonte_m3 !== undefined) {
    h += kpi("Desmonte", `${nf(v.volumen_desmonte_m3)} m³`) + kpi("Terraplén", `${nf(v.volumen_terraplen_m3)} m³`);
    h += kpi("Balance", `${nf(v.balance_m3)} m³`) + kpi(v.excedente_esponjado_m3 > 0 ? "Excedente esponjado" : "Déficit", `${nf(v.excedente_esponjado_m3 > 0 ? v.excedente_esponjado_m3 : v.deficit_m3)} m³`);
  }
  if (st.pendiente_media !== undefined) h += kpi("Pendiente media", `${nf(st.pendiente_media, 1)} %`) + kpi("Desnivel", `${nf(st.desnivel, 1)} m`);
  h += `</div>`;
  if (g.motivos.length) h += `<p><b>Condicionantes principales:</b> ${g.motivos.map(esc).join(", ")}</p>`;
  h += filas([
    ["Comunidad", esc((terr.ccaa || []).map((c) => c.nombre).join(", ") || "—")],
    ["Municipios", esc((terr.municipios || []).map((m) => `${m.nombre} (${nf(m.pct, 1)} %)`).join(", ") || "—")],
    ["Tipo de obra", esc(r.modo_nombre)],
    ["Modelo del terreno", esc(r.terreno.fuente ? r.terreno.fuente.nombre : "Sin MDT")],
  ]);
  (r.avisos || []).forEach((x) => { h += `<div class="aviso">${esc(x)}</div>`; });
  if (g.capas_cobertura_parcial.length) h += `<div class="aviso">Cobertura parcial (la ausencia de afección no es concluyente): ${g.capas_cobertura_parcial.map(esc).join(", ")}.</div>`;
  if (g.capas_sin_datos.length) h += `<p class="nota">Sin datos: ${g.capas_sin_datos.map(esc).join(", ")}.</p>`;
  h += `<p class="nota">Revisa el mapa y los parámetros en «Movimiento». Cuando el resultado sea correcto, descarga el informe.</p>`;
  $("s-resumen").innerHTML = h;
}

function pintarTerreno(r) {
  const t = r.terreno, st = t.estadisticas || {};
  if (!t.fuente) { $("s-terreno").innerHTML = `<div class="aviso">No hay modelo del terreno para este ámbito.</div>`; return; }
  let h = filas([["Modelo", esc(t.fuente.nombre)], ["Paso de trabajo", `${nf(t.fuente.res_trabajo_m, 1)} m`],
    ["Fiable para volúmenes", t.fiable ? "Sí" : "<b style='color:#C62828'>No</b>"]]);
  if (st.cota_min !== undefined) {
    h += filas([["Cota mínima", `${nf(st.cota_min, 1)} m`], ["Cota media", `${nf(st.cota_media, 1)} m`], ["Cota máxima", `${nf(st.cota_max, 1)} m`],
      ["Desnivel", `${nf(st.desnivel, 1)} m`], ["Pendiente media", `${nf(st.pendiente_media, 1)} %`],
      ["Pendiente P90", `${nf(st.pendiente_p90, 1)} %`], ["Pendiente máxima", `${nf(st.pendiente_max, 1)} %`]]);
    h += `<div class="grafico"><canvas id="g-pend"></canvas></div>`;
  }
  h += `<p class="nota">Fuente: ${esc(t.fuente.fuente)} · ${esc((t.fuente.ficheros || []).slice(0, 3).join(", "))}</p>`;
  $("s-terreno").innerHTML = h;
  if (st.clases_pendiente) grafico("pend", "g-pend", {
    type: "bar",
    data: { labels: st.clases_pendiente.map((c) => c.clase), datasets: [{ label: "% del ámbito", data: st.clases_pendiente.map((c) => c.pct),
      backgroundColor: ["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"] }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
      scales: { y: { title: { display: true, text: "% del ámbito" } } } },
  });
}

const ETQ = {
  cota_plataforma: ["Cota de plataforma", "m", 2], cota_final: ["Cota final", "m", 2], cota_fondo: ["Cota de fondo", "m", 2],
  capacidad_m3: ["Capacidad", "m³", 0], volumen_extraible_m3: ["Volumen extraíble", "m³", 0],
  volumen_desmonte_m3: ["Desmonte", "m³", 0], volumen_terraplen_m3: ["Terraplén", "m³", 0], balance_m3: ["Balance", "m³", 0],
  excedente_esponjado_m3: ["Excedente esponjado", "m³", 0], deficit_m3: ["Déficit", "m³", 0],
  viajes_camion: ["Viajes de camión", "", 0], viajes_camion_aporte: ["Viajes de camión (aporte)", "", 0],
  altura_max_desmonte_m: ["Altura máx. desmonte", "m", 2], altura_max_terraplen_m: ["Altura máx. terraplén", "m", 2],
  profundidad_max_m: ["Profundidad máx.", "m", 2], superficie_ocupada_m2: ["Superficie ocupada", "m²", 0],
  superficie_taludes_m2: ["Superficie de taludes", "m²", 0], superficie_fondo_m2: ["Superficie de fondo", "m²", 0],
  superficie_restauracion_m2: ["Superficie a restaurar", "m²", 0], longitud_m: ["Longitud", "m", 0],
  pendiente_max_rasante_pct: ["Pendiente máx. rasante", "%", 2], pendiente_max_terreno_pct: ["Pendiente máx. terreno", "%", 2],
  ancho_ocupacion_max_m: ["Ancho máx. ocupación", "m", 1],
};

function pintarObra(r) {
  const v = r.volumenes || {};
  let h = `<h2>Parámetros</h2><form class="form-params" id="f-params">${camposHTML(r.modo, r.parametros, (cfg.defectos || {})[r.modo] || {})}</form>
    <div style="display:flex;gap:8px;margin-bottom:16px"><button class="btn" id="recalcular">Recalcular</button>
    <button class="btn sec" id="restaurar">Valores por defecto</button></div>`;
  if (!r.volumenes) { $("s-obra").innerHTML = h + `<div class="aviso">Sin modelo del terreno no se calculan volúmenes.</div>`; enlazarParams(r); return; }
  h += `<h2>Resultado</h2>`;
  const pares = Object.entries(ETQ).filter(([k]) => v[k] !== undefined && v[k] !== null).map(([k, [et, u, d]]) => [et, `${nf(v[k], d)} ${u}`]);
  if (v.cota_compensada) pares.unshift(["Cota calculada por compensación", "Sí"]);
  h += filas(pares);
  h += `<div class="grafico"><canvas id="g-balance"></canvas></div>`;
  if (r.vial) {
    h += `<h2>Perfil longitudinal</h2><div class="grafico"><canvas id="g-long"></canvas></div>
          <h2>Diagrama de masas</h2><div class="grafico" style="height:170px"><canvas id="g-masa"></canvas></div>`;
    h += `<table class="tabla"><tr><th>PK</th><th>Desmonte</th><th>Terraplén</th><th>Pend.</th></tr>${r.vial.tramos.map((t) =>
      `<tr><td>${nf(t.pk_ini)}–${nf(t.pk_fin)}</td><td class="num">${nf(t.desmonte_m3)}</td><td class="num">${nf(t.terraplen_m3)}</td><td class="num">${nf(t.pendiente_media_pct, 1)} %</td></tr>`).join("")}</table>`;
  }
  h += `<p class="nota">Volúmenes en banco sobre la malla del MDT. Estimación de prefactibilidad.</p>`;
  $("s-obra").innerHTML = h;
  enlazarParams(r);
  grafico("balance", "g-balance", {
    type: "bar",
    data: { labels: ["Desmonte", "Terraplén"], datasets: [{ data: [v.volumen_desmonte_m3, v.volumen_terraplen_m3], backgroundColor: ["#C62828", "#1565C0"] }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, indexAxis: "y", plugins: { legend: { display: false } },
      scales: { x: { title: { display: true, text: "m³" } } } },
  });
  if (r.vial) {
    const p = r.vial.perfil;
    grafico("long", "g-long", { type: "line", data: { labels: p.pk.map((x) => nf(x)), datasets: [
      { label: "Terreno", data: p.terreno, borderColor: "#6D4C41", pointRadius: 0, borderWidth: 1.4 },
      { label: "Rasante", data: p.rasante, borderColor: "#0F7B6C", pointRadius: 0, borderWidth: 2 }] },
      options: opcionesLinea("PK (m)", "Cota (m)") });
    grafico("masa", "g-masa", { type: "line", data: { labels: p.pk.map((x) => nf(x)), datasets: [
      { label: "Masa acumulada (m³)", data: p.masa, borderColor: "#2E7D32", pointRadius: 0, borderWidth: 1.6, fill: { target: "origin", above: "rgba(198,40,40,.12)", below: "rgba(21,101,192,.12)" } }] },
      options: opcionesLinea("PK (m)", "m³") });
  }
}

function enlazarParams(r) {
  $("recalcular").onclick = async () => {
    try {
      await api(`${BASE}/recalcular`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parametros: leerCampos($("f-params")) }) });
      vigilar();
    } catch (e) { alert(e.message); }
  };
  $("restaurar").onclick = () => { $("f-params").innerHTML = camposHTML(r.modo, {}, (cfg.defectos || {})[r.modo] || {}); };
}

function pintarAfecciones(r) {
  const temas = {};
  r.afecciones.forEach((a) => (temas[a.tema] = temas[a.tema] || []).push(a));
  const textoEstado = { afecta: "", sin_afeccion: "", no_instalada: "Capa no instalada", sin_cobertura: "Sin cobertura en la comunidad", error: "Error" };
  let h = "";
  for (const [tema, lista] of Object.entries(temas)) {
    h += `<div class="grupo-tema">${esc(tema)}</div>`;
    for (const a of lista) {
      const s = SEM[a.semaforo] || SEM.gris;
      let det = "";
      const zonas = (a.zonas || []).filter((z) => z.afecta);
      if (zonas.length) det += `<table class="tabla"><tr><th>Zona</th><th>m²</th><th>%</th></tr>${zonas.map((z) =>
        `<tr><td><span class="punto" style="background:${SEM[z.semaforo].color}"></span> ${esc(z.etiqueta)}${z.elementos && z.elementos.length ? `<br><span class="nota">${z.elementos.slice(0, 5).map(esc).join(", ")}</span>` : ""}</td>
         <td class="num">${nf(z.area_m2)}</td><td class="num">${nf(z.pct, 1)}</td></tr>`).join("")}</table>`;
      if (a.distancia_min_m !== null && a.distancia_min_m !== undefined && !zonas.length) det += `<p>Elemento más cercano a <b>${nf(a.distancia_min_m)} m</b>.</p>`;
      if (textoEstado[a.estado]) det += `<p>${textoEstado[a.estado]}</p>`;
      if (a.cobertura_parcial && a.estado !== "afecta") det += `<div class="aviso">Cobertura parcial: la ausencia de afección no es concluyente.</div>`;
      (a.valores || []).forEach((v) => { det += `<p class="nota">${esc(Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(" · "))}</p>`; });
      (a.avisos || []).forEach((x) => { det += `<div class="aviso">${esc(x)}</div>`; });
      det += `<p class="nota">${esc(a.fuente)}${a.fecha ? ` · ${esc(a.fecha)}` : ""}${a.notas ? `<br>${esc(a.notas)}` : ""}</p>`;
      h += `<details class="afeccion" data-id="${a.id}" ${a.estado === "afecta" ? "open" : ""}>
        <summary><span class="nombre">${esc(a.nombre)}</span><span class="etiqueta" style="background:${s.color}">${s.txt}</span></summary>
        <div class="det">${det}</div></details>`;
    }
  }
  $("s-afecciones").innerHTML = h;
  document.querySelectorAll(".afeccion").forEach((d) => d.addEventListener("toggle", () => {
    if (!d.open) return;
    const capa = capasActivas.find((c) => c._afeccion === d.dataset.id);
    if (capa) { capa.addTo(mapa); try { mapa.fitBounds(capa.getBounds().pad(0.2)); } catch (_) { /* sin extensión */ } }
  }));
}

function pintarTramitacion(r) {
  const col = { alto: "#C62828", medio: "#EF8F00", bajo: "#5B6475" };
  $("s-tramitacion").innerHTML = r.tramitacion.map((t) =>
    `<div class="afeccion"><div class="det" style="padding-top:10px"><span class="etiqueta" style="background:${col[t.nivel]}">${esc(t.nivel)}</span>
      <b style="margin-left:6px">${esc(t.tramite)}</b><p style="margin:6px 0 0">${esc(t.motivo)}</p></div></div>`).join("")
    + `<p class="nota">Orientación no vinculante: confirmar con los órganos competentes y la normativa autonómica y municipal.</p>`;
}

async function pintar(r) {
  datos = r;
  $("titulo").innerHTML = `${esc(r.nombre)}<small id="subtitulo">${esc(r.modo_nombre)} · versión ${r.version} · ${new Date(r.fecha).toLocaleString("es-ES")}</small>`;
  document.title = `${r.nombre} · Prefactibilidad`;
  pintarResumen(r); pintarTerreno(r); pintarObra(r); pintarAfecciones(r); pintarTramitacion(r);
  await pintarMapa(r);
  ["pdf", "xlsx", "zip"].forEach((t) => { $(`d-${t}`).disabled = false; });
}

// ------------------------------------------------------------------ estado / sondeo
let sondeo = null;
function vigilar() {
  $("cargando").style.display = "flex";
  $("c-error").innerHTML = "";
  ["pdf", "xlsx", "zip"].forEach((t) => { $(`d-${t}`).disabled = true; });
  clearTimeout(sondeo);
  const paso = async () => {
    let r;
    try { r = await api(BASE); } catch (e) { $("c-mensaje").textContent = e.message; sondeo = setTimeout(paso, 3000); return; }
    const e = r.estado;
    $("c-barra").style.width = `${Math.round((e.progreso || 0) * 100)}%`;
    $("c-mensaje").textContent = e.mensaje || "";
    if (e.estado === "listo" && r.resultado) {
      $("cargando").style.display = "none";
      if (ultimaVersion !== r.resultado.version) { ultimaVersion = r.resultado.version; await pintar(r.resultado); }
    } else if (e.estado === "error") {
      $("c-titulo").textContent = "No se ha podido completar el estudio";
      $("c-error").innerHTML = `<div class="error">${esc(e.mensaje)}</div><a class="btn sec" href="/">Volver</a>`
        + (datos ? ` <button class="btn sec" onclick="document.getElementById('cargando').style.display='none'">Ver versión anterior</button>` : "");
    } else {
      sondeo = setTimeout(paso, 1200);
    }
  };
  paso();
}

function descargar(tipo) {
  const b = $(`d-${tipo}`);
  const txt = b.textContent;
  b.disabled = true; b.textContent = "Generando…";
  fetch(`${BASE}/descargar/${tipo}`).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).detail || `Error ${r.status}`);
    const nombre = (r.headers.get("Content-Disposition") || "").match(/filename\*?=(?:UTF-8'')?"?([^";]+)/);
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = nombre ? decodeURIComponent(nombre[1]) : `estudio.${tipo}`;
    a.click();
    URL.revokeObjectURL(a.href);
  }).catch((e) => alert(e.message)).finally(() => { b.disabled = false; b.textContent = txt; });
}

document.querySelectorAll("#pestanas button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#pestanas button").forEach((x) => x.classList.toggle("activo", x === b));
  document.querySelectorAll(".seccion").forEach((s) => s.classList.toggle("activo", s.id === `s-${b.dataset.s}`));
  Object.values(graficos).forEach((g) => g.resize());
});
["pdf", "xlsx", "zip"].forEach((t) => { $(`d-${t}`).onclick = () => descargar(t); });

(async () => {
  cfg = await api("/api/config");
  iniciarMapa();
  vigilar();
})();
