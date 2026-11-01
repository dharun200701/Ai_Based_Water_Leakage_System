"""SI-unit hydraulic calculations and pipeline network validation."""

from __future__ import annotations

import math
from typing import Any

G = 9.80665
RHO_WATER = 998.2
MU_WATER = 1.002e-3
MATERIAL_ROUGHNESS_MM = {
    "pvc": 0.0015,
    "hdpe": 0.007,
    "commercial_steel": 0.045,
    "galvanized_iron": 0.15,
    "cast_iron": 0.26,
    "concrete": 0.3,
    "custom": 0.15,
}


def _number(
    value: Any,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number.") from error
    if (
        not math.isfinite(result)
        or (minimum is not None and result < minimum)
        or (maximum is not None and result > maximum)
    ):
        limits = []
        if minimum is not None:
            limits.append(f"at least {minimum}")
        if maximum is not None:
            limits.append(f"no more than {maximum}")
        raise ValueError(f"{label} must be finite and " + " and ".join(limits) + ".")
    return result


def friction_factor(reynolds: float, relative_roughness: float) -> float:
    if reynolds <= 0:
        return 0.0
    if reynolds < 2300:
        return 64 / reynolds
    turbulent = 0.25 / (
        math.log10(relative_roughness / 3.7 + 5.74 / reynolds**0.9) ** 2
    )
    if reynolds >= 4000:
        return turbulent
    laminar = 64 / 2300
    ratio = (reynolds - 2300) / (4000 - 2300)
    return laminar + ratio * (turbulent - laminar)


def assess_network(network: dict[str, Any]) -> dict[str, Any]:
    """Validate a junction/pipe network and calculate per-pipe hydraulic estimates.

    Pipe flow and junction demand are entered in L/s, dimensions in mm/m, and
    elevations in metres. Minor-loss coefficients are optional.
    """
    if not isinstance(network, dict):
        raise ValueError("Network must be an object.")
    name = str(network.get("name", "")).strip()
    if not name or len(name) > 120:
        raise ValueError("Provide a network name no longer than 120 characters.")
    junctions = network.get("junctions")
    pipes = network.get("pipes")
    if not isinstance(junctions, list) or not junctions:
        raise ValueError("Add at least one junction.")
    if not isinstance(pipes, list):
        raise ValueError("Pipes must be a list.")
    if len(junctions) > 200 or len(pipes) > 200:
        raise ValueError("Assessments are limited to 200 junctions and 200 pipe segments.")

    junction_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(junctions, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Junction {index} must be an object.")
        junction_id = str(item.get("id", "")).strip()
        if not junction_id or junction_id in junction_map:
            raise ValueError("Junction IDs must be present and unique.")
        junction_map[junction_id] = {
            "id": junction_id,
            "name": str(item.get("name", junction_id)).strip() or junction_id,
            "elevation_m": _number(item.get("elevation_m", 0), f"{junction_id} elevation", -12000, 10000),
            "demand_l_s": _number(item.get("demand_l_s", 0), f"{junction_id} demand", 0, 100000),
            "pressure_kpa": (
                _number(item["pressure_kpa"], f"{junction_id} pressure", -1000, 100000)
                if item.get("pressure_kpa") not in (None, "") else None
            ),
        }

    pipe_ids: set[str] = set()
    adjacency = {junction_id: set() for junction_id in junction_map}
    balances = {junction_id: -item["demand_l_s"] for junction_id, item in junction_map.items()}
    results = []
    warnings: list[str] = []
    for index, item in enumerate(pipes, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Pipe {index} must be an object.")
        pipe_id = str(item.get("id", "")).strip()
        if not pipe_id or pipe_id in pipe_ids:
            raise ValueError("Pipe IDs must be present and unique.")
        pipe_ids.add(pipe_id)
        upstream = str(item.get("upstream", "")).strip()
        downstream = str(item.get("downstream", "")).strip()
        if upstream not in junction_map or downstream not in junction_map or upstream == downstream:
            raise ValueError(f"Pipe {pipe_id} must connect two different defined junctions.")
        adjacency[upstream].add(downstream)
        adjacency[downstream].add(upstream)
        length = _number(item.get("length_m"), f"{pipe_id} length", 0, 1_000_000)
        diameter_mm = _number(item.get("diameter_mm"), f"{pipe_id} diameter", 1, 5000)
        if length == 0 or diameter_mm == 0:
            raise ValueError(f"Pipe {pipe_id} length and diameter must be greater than zero.")
        flow_l_s = _number(item.get("flow_l_s", 0), f"{pipe_id} flow", 0, 100000)
        material = str(item.get("material", "custom")).strip().lower()
        if material not in MATERIAL_ROUGHNESS_MM:
            raise ValueError(f"{pipe_id} material must be one of: {', '.join(MATERIAL_ROUGHNESS_MM)}.")
        roughness_mm = _number(
            item.get("roughness_mm", MATERIAL_ROUGHNESS_MM[material]),
            f"{pipe_id} roughness", 0, 100,
        )
        if roughness_mm >= diameter_mm:
            raise ValueError(f"{pipe_id} absolute roughness must be smaller than its internal diameter.")
        minor_k = _number(item.get("minor_k", 0), f"{pipe_id} minor-loss K", 0, 10000)
        diameter = diameter_mm / 1000
        area = math.pi * diameter**2 / 4
        flow = flow_l_s / 1000
        velocity = flow / area
        if velocity > 30:
            raise ValueError(f"{pipe_id} flow implies an implausible velocity above 30 m/s.")
        reynolds = RHO_WATER * velocity * diameter / MU_WATER
        factor = friction_factor(reynolds, roughness_mm / diameter_mm)
        friction_head = factor * (length / diameter) * velocity**2 / (2 * G)
        minor_head = minor_k * velocity**2 / (2 * G)
        results.append({
            "id": pipe_id, "upstream": upstream, "downstream": downstream,
            "length_m": length, "diameter_mm": diameter_mm, "flow_l_s": flow_l_s,
            "material": material, "roughness_mm": roughness_mm,
            "area_m2": area, "velocity_m_s": velocity, "reynolds": reynolds,
            "friction_factor": factor, "friction_head_m": friction_head,
            "minor_head_m": minor_head, "total_head_loss_m": friction_head + minor_head,
        })
        balances[upstream] -= flow_l_s
        balances[downstream] += flow_l_s

    if pipes and len(junction_map) > 1:
        visited: set[str] = set()
        stack = [next(iter(junction_map))]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(adjacency[current] - visited)
        disconnected = sorted(set(junction_map) - visited)
        if disconnected:
            warnings.append("Disconnected junctions: " + ", ".join(disconnected))
    elif len(junction_map) > 1:
        warnings.append("No pipes connect the defined junctions.")

    if not pipes:
        warnings.append("Add pipe segments for hydraulic calculations.")
    if any(abs(value) > 0.01 for value in balances.values()):
        warnings.append("Flow-balance residual exceeds 0.01 L/s at one or more junctions; supplied flows/demands may be incomplete.")

    for junction_id, junction in junction_map.items():
        junction["flow_balance_l_s"] = balances[junction_id]

    total_head_loss = sum(row["total_head_loss_m"] for row in results)
    inspection_priorities = []
    for pipe in results:
        reasons = []
        if pipe["velocity_m_s"] > 2.5:
            reasons.append("estimated velocity exceeds 2.5 m/s")
        if pipe["total_head_loss_m"] > 1:
            reasons.append("estimated segment head loss exceeds 1 m")
        if reasons:
            inspection_priorities.append({
                "pipe_id": pipe["id"],
                "junctions": [pipe["upstream"], pipe["downstream"]],
                "reason": "; ".join(reasons),
            })
    start_id = str(network.get("start_junction_id", "")).strip()
    end_id = str(network.get("end_junction_id", "")).strip()
    elevation_result: dict[str, Any] = {}
    if start_id or end_id:
        if start_id not in junction_map or end_id not in junction_map:
            raise ValueError("Both start and end elevation references must be defined junction IDs.")
        elevation_difference = (
            junction_map[end_id]["elevation_m"] - junction_map[start_id]["elevation_m"]
        )
        elevation_result = {
            "start_junction_id": start_id,
            "end_junction_id": end_id,
            "difference_m": elevation_difference,
            "pressure_difference_kpa": RHO_WATER * G * elevation_difference / 1000,
        }
    pump = network.get("pump", {})
    if not isinstance(pump, dict):
        raise ValueError("Pump assumptions must be an object.")
    pump_result: dict[str, Any] = {}
    pump_junction = str(pump.get("junction_id", "")).strip()
    if pump_junction:
        if pump_junction not in junction_map:
            raise ValueError("Pump location must match a defined junction ID.")
        pump_result["junction_id"] = pump_junction
    pump_rpm = pump.get("rpm")
    reference_rpm = pump.get("reference_rpm")
    if pump_rpm not in (None, ""):
        pump_result["rpm"] = _number(pump_rpm, "Pump RPM", 0, 100000)
    if reference_rpm not in (None, ""):
        reference = _number(reference_rpm, "Reference pump RPM", 0, 100000)
        if reference == 0:
            raise ValueError("Reference pump RPM must be greater than zero.")
        if "rpm" not in pump_result or pump_result["rpm"] == 0:
            raise ValueError("Provide positive current and reference pump RPM for affinity-law estimates.")
        ratio = pump_result["rpm"] / reference
        pump_result["affinity_estimates"] = {
            "flow_ratio": ratio, "head_ratio": ratio**2, "power_ratio": ratio**3,
        }
    pump_head = pump.get("head_m")
    pump_flow = pump.get("flow_l_s")
    if pump_head not in (None, "") and pump_flow not in (None, ""):
        head = _number(pump_head, "Pump head", 0, 10000)
        flow = _number(pump_flow, "Pump flow", 0, 100000) / 1000
        hydraulic_power = RHO_WATER * G * flow * head
        pump_result["head_m"] = head
        pump_result["flow_l_s"] = flow * 1000
        pump_result["hydraulic_power_w"] = hydraulic_power
        efficiency = pump.get("efficiency")
        if efficiency not in (None, ""):
            efficiency = _number(efficiency, "Pump efficiency", 0)
            if not 0 < efficiency <= 1:
                raise ValueError("Pump efficiency must be greater than 0 and no more than 1 (fraction).")
            pump_result["estimated_input_power_w"] = hydraulic_power / efficiency
    elif pump_head not in (None, "") or pump_flow not in (None, ""):
        warnings.append("Pump hydraulic power requires both pump head and pump flow.")
    return {
        "name": name,
        "junctions": list(junction_map.values()),
        "pipes": results,
        "warnings": warnings,
        "inspection_priorities": inspection_priorities,
        "total_head_loss_m": total_head_loss,
        "pressure_equivalent_kpa": RHO_WATER * G * total_head_loss / 1000,
        "elevation_change": elevation_result,
        "pump": pump_result,
        "assumptions": [
            "Steady, incompressible water flow; water properties approximated at 20 °C.",
            "Darcy–Weisbach with Swamee–Jain turbulent friction factor; linear estimate in transition.",
            "Minor losses are included only when a pipe minor-loss K value is supplied.",
            "Supplied flows are not a solved network solution; nodal residuals are validation indicators.",
            "Pressure equivalent is based only on summed estimated loss; it is not a predicted junction pressure.",
            "Pump affinity-law ratios apply only to the same pump near comparable operating conditions.",
            "Hydraulic review priorities use screening thresholds of velocity >2.5 m/s or segment head loss >1 m; they do not identify or confirm leaks.",
        ],
    }
