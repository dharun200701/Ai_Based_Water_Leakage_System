document.addEventListener("DOMContentLoaded", () => {
    setupPrediction();
    setupCivilForm();
});

function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

function setupPrediction() {
    const form = document.getElementById("prediction-form");
    if (!form) return;

    document.getElementById("demo-normal")?.addEventListener("click", () => {
        fillReadings([60, 80, 25, 3, 2000, 5480], "demo");
    });
    document.getElementById("demo-leak")?.addEventListener("click", () => {
        fillReadings([18, 245, 24, 4.8, 1700, 3400], "demo");
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const output = document.getElementById("prediction-result");
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        output.textContent = "Analyzing readings…";
        output.className = "result-box";
        const payload = {
            pressure: Number(document.getElementById("pressure").value),
            flow: Number(document.getElementById("flow").value),
            temperature: Number(document.getElementById("temperature").value),
            vibration: Number(document.getElementById("vibration").value),
            rpm: Number(document.getElementById("rpm").value),
            hours: Number(document.getElementById("hours").value),
            source_mode: document.getElementById("source-mode").value,
        };
        try {
            const response = await fetch("/predict", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken(),
                    "Idempotency-Key": crypto.randomUUID(),
                },
                body: JSON.stringify(payload),
            });
            if (response.status === 401) {
                window.location.href = "/login";
                return;
            }
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Analysis failed.");
            renderPrediction(output, result);
        } catch (error) {
            output.className = "result-box result-error";
            output.textContent = error.message || "Could not complete the analysis.";
        } finally {
            button.disabled = false;
        }
    });
}

function fillReadings(values, source) {
    ["pressure", "flow", "temperature", "vibration", "rpm", "hours"].forEach((id, index) => {
        document.getElementById(id).value = values[index];
    });
    document.getElementById("source-mode").value = source;
}

function renderPrediction(container, result) {
    const leak = result.status === "danger";
    const probability = result.probability === null
        ? "Not available"
        : `${Number(result.probability).toFixed(2)}%`;
    const alert = result.alert
        ? `Alert #${result.alert.id}: ${result.alert.status} — ${result.alert.status_message}`
        : "No email alert was required for this normal prediction.";
    container.className = `result-box ${leak ? "result-warning" : "result-good"}`;
    container.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = `${result.prediction} · Analysis #${result.analysis_id}`;
    const details = document.createElement("p");
    details.textContent = `Model score: ${probability}. ${result.probability_note || ""}`;
    const source = document.createElement("p");
    source.textContent = `Source: ${result.source_mode === "demo" ? "simulated demonstration values" : "user-entered readings"} · ${result.timestamp}`;
    const caution = document.createElement("p");
    caution.textContent = "A model prediction is not proof of a physical leak. Verify readings and inspect the pipeline.";
    const notice = document.createElement("p");
    notice.textContent = alert;
    container.append(heading, details, source, caution, notice);
}

function setupCivilForm() {
    const form = document.getElementById("civil-form");
    if (!form) return;
    const junctionRows = document.getElementById("junction-rows");
    const pipeRows = document.getElementById("pipe-rows");
    const addRow = (templateId, parent) => {
        const row = document.getElementById(templateId).content.cloneNode(true);
        parent.append(row);
    };
    document.getElementById("add-junction").addEventListener("click", () => addRow("junction-row-template", junctionRows));
    document.getElementById("add-pipe").addEventListener("click", () => addRow("pipe-row-template", pipeRows));
    document.addEventListener("click", (event) => {
        if (event.target.matches(".remove-row")) event.target.closest("tr").remove();
    });
    document.addEventListener("change", (event) => {
        if (!event.target.matches('#pipe-rows select[data-field="material"]')) return;
        const roughness = {
            pvc: 0.0015, hdpe: 0.007, commercial_steel: 0.045,
            galvanized_iron: 0.15, cast_iron: 0.26, concrete: 0.3, custom: 0.15,
        };
        event.target.closest("tr").querySelector('[data-field="roughness_mm"]').value = roughness[event.target.value];
    });

    document.getElementById("load-example-network").addEventListener("click", () => {
        document.getElementById("network-name").value = "Illustrative campus branch (demo)";
        document.getElementById("start-junction").value = "J1";
        document.getElementById("end-junction").value = "J3";
        junctionRows.replaceChildren();
        pipeRows.replaceChildren();
        const junctions = [
            {id: "J1", name: "Supply", elevation_m: 12, demand_l_s: 0},
            {id: "J2", name: "Branch", elevation_m: 10, demand_l_s: 0.5},
            {id: "J3", name: "Outlet", elevation_m: 8, demand_l_s: 0.5},
        ];
        const pipes = [
            {id: "P1", upstream: "J1", downstream: "J2", length_m: 120, diameter_mm: 100, flow_l_s: 1, material: "pvc", roughness_mm: 0.0015, minor_k: 1.5},
            {id: "P2", upstream: "J2", downstream: "J3", length_m: 80, diameter_mm: 80, flow_l_s: 0.5, material: "pvc", roughness_mm: 0.0015, minor_k: 1},
        ];
        junctions.forEach((value) => addObjectRow("junction-row-template", junctionRows, value));
        pipes.forEach((value) => addObjectRow("pipe-row-template", pipeRows, value));
        ["pump-junction", "pump-rpm", "reference-rpm", "pump-head", "pump-flow", "pump-efficiency"]
            .forEach((id) => { document.getElementById(id).value = ""; });
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const result = document.getElementById("civil-result");
        const readRows = (parent) => [...parent.querySelectorAll("tr")].map((row) => {
            const values = {};
            row.querySelectorAll("[data-field]").forEach((input) => {
                values[input.dataset.field] = input.type === "number"
                    ? (input.value === "" ? null : Number(input.value))
                    : input.value.trim();
            });
            return values;
        });
        const payload = {
            name: document.getElementById("network-name").value.trim(),
            start_junction_id: document.getElementById("start-junction").value.trim(),
            end_junction_id: document.getElementById("end-junction").value.trim(),
            junctions: readRows(junctionRows),
            pipes: readRows(pipeRows),
            pump: {
                junction_id: document.getElementById("pump-junction").value.trim(),
                rpm: optionalNumber("pump-rpm"),
                reference_rpm: optionalNumber("reference-rpm"),
                head_m: optionalNumber("pump-head"),
                flow_l_s: optionalNumber("pump-flow"),
                efficiency: optionalNumber("pump-efficiency"),
            },
        };
        result.textContent = "Validating network and calculating estimates…";
        try {
            const response = await fetch("/civil", {
                method: "POST",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken()},
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Assessment failed.");
            renderCivilResult(result, data);
        } catch (error) {
            result.className = "panel result-box result-error";
            result.textContent = error.message || "Could not calculate the assessment.";
        }
    });
}

function addObjectRow(templateId, parent, values) {
    const fragment = document.getElementById(templateId).content.cloneNode(true);
    fragment.querySelectorAll("[data-field]").forEach((input) => {
        if (values[input.dataset.field] !== undefined) input.value = values[input.dataset.field];
    });
    parent.append(fragment);
}

function optionalNumber(id) {
    const value = document.getElementById(id).value;
    return value === "" ? null : Number(value);
}

function renderCivilResult(container, data) {
    container.className = "panel result-box result-good";
    container.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = `Assessment #${data.assessment_id}: ${data.name}`;
    container.append(heading);
    const summary = document.createElement("p");
    summary.textContent = `Estimated summed pipe head loss: ${data.total_head_loss_m.toFixed(4)} m (${data.pressure_equivalent_kpa.toFixed(2)} kPa equivalent; not a junction pressure).`;
    container.append(summary);
    if (data.elevation_change.difference_m !== undefined) {
        const elevation = document.createElement("p");
        elevation.textContent = `Elevation change ${data.elevation_change.start_junction_id} → ${data.elevation_change.end_junction_id}: ${data.elevation_change.difference_m.toFixed(2)} m (static pressure-head equivalent ${data.elevation_change.pressure_difference_kpa.toFixed(2)} kPa; excludes friction and boundary pressure).`;
        container.append(elevation);
    }
    if (data.pump.hydraulic_power_w !== undefined) {
        const power = document.createElement("p");
        power.textContent = `Estimated hydraulic power: ${data.pump.hydraulic_power_w.toFixed(1)} W${data.pump.estimated_input_power_w !== undefined ? ` · estimated input power at entered efficiency: ${data.pump.estimated_input_power_w.toFixed(1)} W` : ""}`;
        container.append(power);
    }
    if (data.pump.affinity_estimates) {
        const affinity = document.createElement("p");
        affinity.textContent = `Same-pump affinity-law ratios (estimates): flow ×${data.pump.affinity_estimates.flow_ratio.toFixed(3)}, head ×${data.pump.affinity_estimates.head_ratio.toFixed(3)}, power ×${data.pump.affinity_estimates.power_ratio.toFixed(3)}.`;
        container.append(affinity);
    }

    const diagram = document.createElement("div");
    diagram.className = "network-diagram";
    diagram.setAttribute("aria-label", "Pipe network flow direction");
    data.pipes.forEach((pipe) => {
        const segment = document.createElement("span");
        segment.textContent = `${pipe.upstream} ── ${pipe.id} → ${pipe.downstream} · ${pipe.length_m} m · Ø${pipe.diameter_mm} mm`;
        diagram.append(segment);
    });
    container.append(diagram);

    const junctionTitle = document.createElement("h3");
    junctionTitle.textContent = "Junction inventory and flow balance";
    container.append(junctionTitle);
    const junctionTable = document.createElement("table");
    junctionTable.innerHTML = "<thead><tr><th>Junction</th><th>Elevation (m)</th><th>Demand (L/s)</th><th>Measured pressure (kPa)</th><th>Net inflow residual (L/s)</th></tr></thead>";
    const junctionBody = document.createElement("tbody");
    data.junctions.forEach((junction) => {
        const row = document.createElement("tr");
        [junction.id, junction.elevation_m, junction.demand_l_s,
            junction.pressure_kpa === null ? "Not supplied" : junction.pressure_kpa,
            junction.flow_balance_l_s.toFixed(3)].forEach((value) => {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.append(cell);
            });
        junctionBody.append(row);
    });
    junctionTable.append(junctionBody);
    container.append(junctionTable);

    const table = document.createElement("div");
    table.className = "table-scroll";
    const resultTable = document.createElement("table");
    resultTable.innerHTML = "<thead><tr><th>Pipe</th><th>Velocity (m/s)</th><th>Reynolds</th><th>Friction factor</th><th>Friction loss (m)</th><th>Minor loss (m)</th><th>Total (m)</th></tr></thead>";
    const body = document.createElement("tbody");
    data.pipes.forEach((pipe) => {
        const row = document.createElement("tr");
        [pipe.id, pipe.velocity_m_s.toFixed(3), pipe.reynolds.toFixed(0),
            pipe.friction_factor.toFixed(5), pipe.friction_head_m.toFixed(4),
            pipe.minor_head_m.toFixed(4), pipe.total_head_loss_m.toFixed(4)]
            .forEach((value) => {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.append(cell);
            });
        body.append(row);
    });
    resultTable.append(body);
    table.append(resultTable);
    container.append(table);

    const warnings = document.createElement("div");
    warnings.className = data.warnings.length ? "notice notice-warning" : "notice notice-success";
    warnings.textContent = data.warnings.length ? data.warnings.join(" ") : "No connectivity or flow-balance warnings detected.";
    container.append(warnings);
    const priorities = document.createElement("p");
    priorities.textContent = data.inspection_priorities.length
        ? `Hydraulic review priorities (not leak locations): ${data.inspection_priorities.map((item) => `${item.pipe_id} at ${item.junctions.join(" / ")} — ${item.reason}`).join("; ")}`
        : "No pipe exceeded the stated hydraulic review screening thresholds.";
    container.append(priorities);
    const assumptions = document.createElement("ul");
    assumptions.className = "plain-list";
    data.assumptions.forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        assumptions.append(item);
    });
    container.append(assumptions);
    const exportLink = document.createElement("a");
    exportLink.className = "button button-secondary";
    exportLink.href = `/civil/${data.assessment_id}/export.csv`;
    exportLink.textContent = "Download CSV report";
    container.append(exportLink);
}
