/* Saarland Live — Leaflet frontend with SSE live updates */
"use strict";

// --- Map -------------------------------------------------------------------
const map = L.map("map", { zoomControl: true }).setView([49.24, 6.95], 10);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

// dark-ish overlay for HUD contrast is handled via CSS backgrounds

// --- State -----------------------------------------------------------------
const markers = new Map(); // vehicle id -> {marker, polyline}
let stopsLayer = L.layerGroup().addTo(map);
let currentLineFilter = "";
let currentMode = "";
let lastSnapshot = null;

function modeMatches(v) {
  if (!currentMode) return true;
  if (v.mode === currentMode) return true;
  // AST (Anruf-Sammel-Taxi) counts as bus
  if (currentMode === "bus" && v.mode === "ast") return true;
  return false;
}

// --- Helpers ---------------------------------------------------------------
function delayColor(delayS) {
  if (delayS === null || delayS === undefined) return "#868e96";
  if (delayS >= 60) return "#e5484d";
  if (delayS <= -30) return "#f59f00";
  return "#2f9e44";
}

function fmtDelay(delayS) {
  if (delayS === null || delayS === undefined) return "";
  const m = Math.round(delayS / 60);
  if (m === 0) return `<span class="ontime">pünktlich</span>`;
  if (m > 0) return `<span class="delay">+${m} min</span>`;
  return `<span class="ontime">${m} min</span>`;
}

function fmtTime(epochS) {
  if (!epochS) return "–";
  return new Date(epochS * 1000).toLocaleTimeString("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// --- Stops (loaded once) ---------------------------------------------------
async function loadStops() {
  try {
    const res = await fetch("/api/stops");
    const stops = await res.json();
    stopsLayer.clearLayers();
    for (const s of stops) {
      const m = L.circleMarker([s.lat, s.lon], {
        radius: 3,
        color: "#74c0fc",
        weight: 1,
        fillColor: "#74c0fc",
        fillOpacity: 0.7,
      });
      m.bindTooltip(escapeHtml(s.name), { direction: "top", offset: [0, -3] });
      stopsLayer.addLayer(m);
    }
    console.log(`loaded ${stops.length} stops`);
  } catch (e) {
    console.warn("stops load failed", e);
  }
}

// --- Vehicles --------------------------------------------------------------
function vehiclePopup(v) {
  const parts = [];
  parts.push(`<div class="line">${escapeHtml(v.line)} <span class="muted">${escapeHtml(v.name)}</span></div>`);
  if (v.destination) parts.push(`→ ${escapeHtml(v.destination)}`);
  parts.push(`Status: ${fmtDelay(v.delay_s) || '<span class="muted">unbekannt</span>'}`);
  if (v.rt === false) {
    parts.push(`<span class="muted">Fahrplan-Position – keine Echtzeitmeldung</span>`);
  }
  if (v.next_stop) parts.push(`<span class="muted">nächster Halt:</span> ${escapeHtml(v.next_stop)}`);
  parts.push(`<span class="muted">Stand: ${fmtTime(v.ts || lastSnapshot?.radar_updated_at)}</span>`);
  return parts.join("<br>");
}

function renderVehicles(vehicles) {
  const seen = new Set();

  for (const v of vehicles) {
    if (!modeMatches(v)) continue;
    if (currentLineFilter && v.line !== currentLineFilter) continue;
    seen.add(v.id);

    const noRt = v.rt === false;
    const color = noRt ? "#868e96" : delayColor(v.delay_s);
    const html = `<div class="badge${noRt ? " no-rt" : ""}" style="background:${color}">${escapeHtml(v.line)}</div>`;
    const polyStyle = { color, weight: 3, opacity: 0.55, dashArray: noRt ? "6 4" : null };

    let entry = markers.get(v.id);
    if (!entry) {
      const marker = L.marker([v.lat, v.lon], {
        icon: L.divIcon({ className: "train-marker", html, iconSize: null }),
        zIndexOffset: 1000,
      }).addTo(map);
      marker.bindPopup(() => vehiclePopup(v));
      const poly = L.polyline(
        (v.polyline || []).map((p) => [p.lat, p.lon]),
        polyStyle
      ).addTo(map);
      entry = { marker, poly };
      markers.set(v.id, entry);
    } else {
      // smooth-ish move
      entry.marker.setLatLng([v.lat, v.lon]);
      entry.marker.setIcon(L.divIcon({ className: "train-marker", html, iconSize: null }));
      entry.marker.setPopupContent(vehiclePopup(v));
      if (v.polyline && v.polyline.length > 1) {
        entry.poly.setLatLngs(v.polyline.map((p) => [p.lat, p.lon]));
        entry.poly.setStyle(polyStyle);
      }
    }
    // rebind popup data closure
    entry.marker.off("popupopen");
    entry.marker.on("popupopen", () => entry.marker.setPopupContent(vehiclePopup(v)));
  }

  // remove stale
  for (const [id, entry] of markers) {
    if (!seen.has(id)) {
      map.removeLayer(entry.marker);
      map.removeLayer(entry.poly);
      markers.delete(id);
    }
  }

  document.getElementById("vehicle-count").textContent = String(seen.size);
}

// --- Line filter dropdown --------------------------------------------------
function updateLineFilter(vehicles) {
  const sel = document.getElementById("line-filter");
  const lines = [...new Set(vehicles.filter(modeMatches).map((v) => v.line))].sort((a, b) =>
    a.localeCompare(b, "de", { numeric: true })
  );
  const current = sel.value;
  sel.innerHTML = '<option value="">alle</option>';
  for (const l of lines) {
    const o = document.createElement("option");
    o.value = l;
    o.textContent = l;
    sel.appendChild(o);
  }
  sel.value = lines.includes(current) ? current : "";
  currentLineFilter = sel.value;
}

document.getElementById("line-filter").addEventListener("change", (e) => {
  currentLineFilter = e.target.value;
  if (lastSnapshot) renderVehicles(lastSnapshot.vehicles || []);
});

// --- Mode chips ------------------------------------------------------------
document.querySelectorAll("#modes .chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#modes .chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentMode = btn.dataset.mode || "";
    if (lastSnapshot) {
      updateLineFilter(lastSnapshot.vehicles || []);
      renderVehicles(lastSnapshot.vehicles || []);
    }
  });
});

// --- Status ----------------------------------------------------------------
function setStatus(ok, text) {
  const dot = document.getElementById("status-dot");
  const txt = document.getElementById("status-text");
  dot.className = "dot " + (ok ? "ok" : "err");
  txt.textContent = text;
}

function renderErrors(snap) {
  const el = document.getElementById("errors");
  const errs = [];
  if (snap.radar_error) errs.push("Radar: " + snap.radar_error);
  if (snap.gtfs_rt_error) errs.push("GTFS-RT: " + snap.gtfs_rt_error);
  if (locationError) errs.push(locationError);
  el.textContent = errs.join("\n");
}

function handleSnapshot(snap) {
  lastSnapshot = snap;
  const vehicles = snap.vehicles || [];
  updateLineFilter(vehicles);
  renderVehicles(vehicles);
  document.getElementById("updated-at").textContent = fmtTime(snap.radar_updated_at);

  const ds = snap.delay_stats || {};
  const delayed = ds.delayed_ge_2min;
  document.getElementById("delay-summary").textContent =
    typeof delayed === "number"
      ? `${delayed} verspätet`
      : "–";

  renderErrors(snap);

  const hasRadar = snap.radar_updated_at > 0 && !snap.radar_error;
  const hasRt = snap.gtfs_rt_updated_at > 0 && !snap.gtfs_rt_error;
  if (hasRadar || hasRt) {
    setStatus(true, "live");
  } else {
    setStatus(false, "keine Daten");
  }
}

// --- SSE -------------------------------------------------------------------
function connect() {
  const es = new EventSource("/api/stream");

  es.onopen = () => setStatus(true, "verbunden");
  es.onmessage = (ev) => {
    try {
      handleSnapshot(JSON.parse(ev.data));
    } catch (e) {
      console.error("bad snapshot", e);
    }
  };
  es.onerror = () => {
    setStatus(false, "Verbindung verloren – neuer Versuch…");
    es.close();
    setTimeout(connect, 3000);
  };
}

// --- Current location -------------------------------------------------------
let userMarker = null;
let userAccuracyCircle = null;
let locationError = null;

function locateUser() {
  const btn = document.getElementById("locate-btn");
  if (!navigator.geolocation) {
    locationError = "Standort: Browser-Unterstützung fehlt";
    if (lastSnapshot) renderErrors(lastSnapshot);
    return;
  }
  btn.disabled = true;
  btn.textContent = "Standort wird ermittelt…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      locationError = null;
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      if (!userMarker) {
        userMarker = L.marker([lat, lon], {
          icon: L.divIcon({ className: "user-dot", iconSize: [14, 14] }),
          zIndexOffset: 2000,
        })
          .addTo(map)
          .bindPopup("Mein Standort");
        userAccuracyCircle = L.circle([lat, lon], {
          radius: accuracy,
          color: "#1971c2",
          weight: 1,
          fillColor: "#1971c2",
          fillOpacity: 0.12,
        }).addTo(map);
      } else {
        userMarker.setLatLng([lat, lon]);
        userAccuracyCircle.setLatLng([lat, lon]);
        userAccuracyCircle.setRadius(accuracy);
      }
      map.setView([lat, lon], Math.max(map.getZoom(), 14));
      btn.disabled = false;
      btn.textContent = "Mein Standort";
      if (lastSnapshot) renderErrors(lastSnapshot);
    },
    (err) => {
      locationError = "Standort: " + (err.message || `Fehler ${err.code}`);
      btn.disabled = false;
      btn.textContent = "Mein Standort";
      if (lastSnapshot) renderErrors(lastSnapshot);
    },
    { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 }
  );
}

document.getElementById("locate-btn").addEventListener("click", locateUser);

// --- Boot ------------------------------------------------------------------
loadStops();
connect();
