(function () {
  "use strict";

  var STATUS_COLOR = {
    alive: "#0ca30c",
    missing_fate_unknown: "#ec835a",
    dead: "#d03b3b",
    unknown: "#898781",
  };

  var STATUS_LABEL = {
    alive: "Alive, transmitting",
    missing_fate_unknown: "Missing, fate unknown",
    dead: "Dead",
    unknown: "Status unknown",
  };

  function statusColor(status) {
    return STATUS_COLOR[status] || STATUS_COLOR.unknown;
  }

  function fmtDate(iso) {
    if (!iso) return "unknown date";
    return iso.length > 10 ? iso.slice(0, 10) : iso;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function popupHtml(props) {
    var name = props.bird_name || props.bird_id;
    var lines = [];
    lines.push("<div class=\"hh-popup\">");
    lines.push("<h3>" + escapeHtml(name) + " (" + escapeHtml(props.bird_id) + ")</h3>");
    lines.push(
      "<p class=\"status-line\"><strong>" +
        escapeHtml(STATUS_LABEL[props.status] || props.status) +
        "</strong> &middot; " +
        escapeHtml(fmtDate(props.event_date)) +
        "</p>"
    );
    if (props.region_text) {
      lines.push("<p class=\"meta\">Region: " + escapeHtml(props.region_text) + "</p>");
    }
    if (props.habitat_context) {
      lines.push("<p class=\"habitat-context\">" + escapeHtml(props.habitat_context) + "</p>");
      if (props.confirmed_at) {
        lines.push("<p class=\"meta\">Confirmed by human review: " + escapeHtml(fmtDate(props.confirmed_at)) + "</p>");
      }
    }
    lines.push(
      "<p class=\"meta\">Source: <a href=\"" +
        escapeHtml(props.source_url || "#") +
        "\" target=\"_blank\" rel=\"noopener\">" +
        escapeHtml(props.source_name || "Natural England") +
        "</a>, " +
        escapeHtml(props.source_update_label || "") +
        "</p>"
    );
    lines.push("</div>");
    return lines.join("");
  }

  function pointToLayer(feature, latlng) {
    var props = feature.properties;
    var confirmed = props.publish_tier === "confirmed-located";
    return L.circleMarker(latlng, {
      radius: confirmed ? 9 : 7,
      fillColor: statusColor(props.status),
      fillOpacity: 0.85,
      color: confirmed ? "#0b0b0b" : statusColor(props.status),
      weight: confirmed ? 3 : 1,
    });
  }

  function renderRedactedList(features) {
    var list = document.getElementById("redacted-list");
    var withoutGeometry = features.filter(function (f) {
      return !f.geometry;
    });
    if (withoutGeometry.length === 0) {
      list.innerHTML = '<li class="empty">None currently.</li>';
      return;
    }
    list.innerHTML = withoutGeometry
      .map(function (f) {
        var p = f.properties;
        return (
          "<li><span class=\"entry-title\">" +
          escapeHtml(p.bird_name || p.bird_id) +
          "</span><span class=\"entry-meta\">" +
          escapeHtml(STATUS_LABEL[p.status] || p.status) +
          " &middot; " +
          escapeHtml(p.region_text || "region not stated") +
          " &middot; " +
          escapeHtml(fmtDate(p.event_date)) +
          "</span></li>"
        );
      })
      .join("");
  }

  function renderUnconfirmedList(reports) {
    var list = document.getElementById("unconfirmed-list");
    if (!reports || reports.length === 0) {
      list.innerHTML = '<li class="empty">No unconfirmed reports currently.</li>';
      return;
    }
    list.innerHTML = reports
      .map(function (r) {
        return (
          "<li><a class=\"entry-title\" href=\"" +
          escapeHtml(r.link || "#") +
          "\" target=\"_blank\" rel=\"noopener\">" +
          escapeHtml(r.title) +
          "</a><span class=\"entry-meta\">" +
          escapeHtml(r.source_name || "") +
          " &middot; " +
          escapeHtml(fmtDate(r.published_at)) +
          "</span></li>"
        );
      })
      .join("");
  }

  function renderSourceStatus(statusDoc) {
    var list = document.getElementById("source-status-list");
    var sources = (statusDoc && statusDoc.sources) || {};
    var keys = Object.keys(sources);
    if (keys.length === 0) {
      list.innerHTML = '<li class="empty">No fetch history yet.</li>';
      return;
    }
    list.innerHTML = keys
      .sort()
      .map(function (key) {
        var s = sources[key];
        var cls = s.ok ? "ok" : "failed";
        var label = s.ok ? "last refreshed" : "last attempt FAILED";
        var when = fmtDate(s.ok ? s.last_success_at : s.last_attempt_at);
        return (
          "<li><strong>" +
          escapeHtml(key.replace(/_/g, " ")) +
          "</strong>: <span class=\"" +
          cls +
          "\">" +
          label +
          " " +
          when +
          "</span></li>"
        );
      })
      .join("");
  }

  function renderStatTiles(stats) {
    var container = document.getElementById("stat-tiles");
    if (!stats) {
      container.innerHTML = "";
      return;
    }
    var sinceLabel = stats.tracking_since_year ? "Hen harriers tracked since " + stats.tracking_since_year : "Hen harriers tracked";
    var tiles = [
      { value: stats.total_tracked, label: sinceLabel },
      { value: stats.dead_or_missing, label: "Now dead or missing" },
      { value: stats.confirmed_near_habitat, label: "Confirmed near mapped habitat" },
    ];
    container.innerHTML = tiles
      .map(function (t) {
        return (
          '<div class="stat-tile"><span class="stat-value">' +
          escapeHtml(t.value == null ? "—" : t.value) +
          '</span><span class="stat-label">' +
          escapeHtml(t.label) +
          "</span></div>"
        );
      })
      .join("");
  }

  function init(incidents, unconfirmed, sourceStatus, stats) {
    var map = L.map("map").setView([54.8, -2.5], 6);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    var geoLayer = L.geoJSON(incidents, {
      filter: function (feature) {
        return !!feature.geometry;
      },
      pointToLayer: pointToLayer,
      onEachFeature: function (feature, layer) {
        layer.bindPopup(popupHtml(feature.properties));
      },
    }).addTo(map);

    try {
      var bounds = geoLayer.getBounds();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
    } catch (e) {
      /* no features with geometry yet - keep default view */
    }

    renderRedactedList(incidents.features || []);
    renderUnconfirmedList((unconfirmed && unconfirmed.reports) || []);
    renderSourceStatus(sourceStatus);
    renderStatTiles(stats);
  }

  Promise.all([
    fetch("data/incidents.geojson").then(function (r) { return r.json(); }),
    fetch("data/unconfirmed_reports.json").then(function (r) { return r.json(); }),
    fetch("data/source_status.json").then(function (r) { return r.json(); }),
    fetch("data/site_stats.json").then(function (r) { return r.json(); }),
  ])
    .then(function (results) {
      init(results[0], results[1], results[2], results[3]);
    })
    .catch(function (err) {
      document.getElementById("map").innerHTML =
        '<p style="padding:1rem;color:#d03b3b;">Failed to load site data: ' + escapeHtml(err.message) + "</p>";
      console.error(err);
    });
})();
