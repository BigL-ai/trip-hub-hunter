"""Semáforo de compra: ¿compro hoy o espero?

Modelo: parada óptima con precio de reserva.
  🟢 COMPRAR  → el precio de hoy toca el precio de reserva del período,
               o Google dice que la ruta está por DEBAJO de su rango típico (activo desde el día 1),
               o (pasada la fase de observación) bate el mínimo histórico,
               o llegamos a la fecha límite.
  🟡 ATENTO   → el precio está cerca del de reserva, o hay un evento de ofertas en curso/inminente.
  🔴 ESPERAR  → nada de lo anterior.
"""

from datetime import date


def _d(s):
    return date.fromisoformat(s)


def evaluate(cfg, price, prev_min, today=None, insight=None):
    p = cfg["purchase"]
    today = today or date.today()
    deadline = _d(p["deadline"])
    observe_until = _d(p["observe_until"])

    rule = next((r for r in p["reserve_prices"] if today <= _d(r["until"])), None)
    reserve = rule["max_usd"] if rule else None

    active = [e for e in p["events"] if _d(e["from"]) <= today <= _d(e["to"])]
    upcoming = sorted((e for e in p["events"] if _d(e["from"]) > today), key=lambda e: e["from"])
    soon = [e for e in upcoming if (_d(e["from"]) - today).days <= 7]
    avoid = [a for a in p.get("avoid", []) if _d(a["from"]) <= today <= _d(a["to"])]

    reasons, color = [], "red"
    if price is None:
        reasons.append("Sin precio hoy.")
    elif today >= deadline:
        color = "green"
        reasons.append(f"Pasó la fecha límite ({p['deadline']}): comprá lo mejor disponible.")
    elif reserve and price <= reserve:
        color = "green"
        reasons.append(f"${price:.0f} ≤ precio de reserva ${reserve} del período.")
    elif insight and insight["level"] == "bajo":
        color = "green"
        reasons.append(f"Google: la ida está a ${insight['current']:.0f}, debajo de su rango típico "
                       f"(${insight['typical_low']:.0f}–${insight['typical_high']:.0f}) → mercado barato.")
    elif today > observe_until and prev_min and price < prev_min:
        color = "green"
        reasons.append(f"Fase de observación terminada y ${price:.0f} bate el mínimo histórico (${prev_min:.0f}).")
    else:
        if reserve:
            gap = (price - reserve) / reserve * 100
            reasons.append(f"${price:.0f} está {gap:.0f}% sobre el precio de reserva (${reserve}).")
            if gap <= p.get("yellow_margin_pct", 5):
                color = "yellow"
        if active:
            color = "yellow"
            reasons.append(f"Evento en curso: {active[0]['name']} — mirá promos de aerolíneas/agencias.")
        elif soon:
            color = "yellow"
            reasons.append(f"Se viene {soon[0]['name']} ({soon[0]['from']}): conviene esperar a ese pico de ofertas.")
        if insight:
            reasons.append(f"Google: ida a ${insight['current']:.0f}, rango típico "
                           f"${insight['typical_low']:.0f}–${insight['typical_high']:.0f} → precio {insight['level']}.")
        if today <= observe_until:
            reasons.append(f"Fase de observación hasta {p['observe_until']}: armando baseline.")
        if avoid:
            reasons.append(f"Período {avoid[0]['name']}: históricamente pocas ofertas.")

    return {
        "color": color,
        "label": {"green": "COMPRAR", "yellow": "ATENTO", "red": "ESPERAR"}[color],
        "reasons": reasons,
        "price": price,
        "reserve": reserve,
        "prev_min": prev_min,
        "days_to_deadline": (deadline - today).days,
        "deadline": p["deadline"],
        "next_event": upcoming[0] if upcoming else None,
        "events": p["events"],
        "avoid": p.get("avoid", []),
        "reserve_prices": p["reserve_prices"],
        "observe_until": p["observe_until"],
    }
