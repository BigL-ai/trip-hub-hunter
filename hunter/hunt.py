"""Motor: billete único vs. ruta combinada por hub (split-ticketing).

Flujo:
  1. Calendario BUE⇄DESTINO  → fechas más baratas → detalle → mejor BILLETE ÚNICO.
  2. Por cada hub:
       calendario BUE⇄HUB (directo) → fechas baratas →
       ida+vuelta BUE⇄HUB  +  solo-ida HUB→DESTINO  +  solo-ida DESTINO→HUB
       → se arman combos que respeten el margen de conexión → mejor COMBO del hub.
  3. Se compara el mejor combo contra el billete único usando el umbral de ahorro.
"""

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)


def _dt(s):
    return datetime.fromisoformat(s)


def _hours(a, b):
    """Horas entre dos timestamps locales del MISMO aeropuerto (hub)."""
    return (_dt(b) - _dt(a)).total_seconds() / 3600


def best_date_pairs(src, orig, dest, window, durations, max_stops, k, min_gap_days=3):
    """Top-k combinaciones (salida, regreso) más baratas sumando todas las duraciones.
    Ojo: el calendario de Google NO respeta el filtro de escalas largas/cortas, así que
    su precio es un "desde"; la regla de escala se aplica después, en el detalle.
    Como muchas fechas empatan en precio, se eligen fechas separadas ≥ min_gap_days
    para no gastar las k búsquedas en días consecutivos.
    Devuelve (top_k, calendario_completo)."""
    pairs = []
    for d in durations:
        for p in src.calendar_rt(orig, dest, window, d, max_stops):
            pairs.append({**p, "days": d})
    picked = []
    for p in sorted(pairs, key=lambda p: p["price"]):
        dep = date.fromisoformat(p["dep"])
        if all(abs((dep - date.fromisoformat(q["dep"])).days) >= min_gap_days or q["days"] != p["days"]
               for q in picked):
            picked.append(p)
        if len(picked) == k:
            break
    return picked, pairs


# ---------------------------------------------------------------- billete único

def hunt_single(src, cfg):
    t, st = cfg["trip"], cfg["single_ticket"]
    pairs, calendar = best_date_pairs(
        src, t["origin"], t["destination"], t["departure_window"], t["durations"],
        st["max_stops"], cfg["search"]["top_date_pairs"],
    )
    log.info("Billete único — fechas candidatas: %s", pairs)
    options = []
    for p in pairs:
        res = src.round_trip(
            t["origin"], t["destination"], p["dep"], p["ret"], st["max_stops"],
            st["layover_min_minutes"], st["layover_max_minutes"],
        )
        if res:
            r = res[0]
            options.append({"type": "single", "dep": p["dep"], "ret": p["ret"],
                            "price": r["price"], "out": r["out"], "back": r["back"],
                            "calendar_price": p["price"]})
    options.sort(key=lambda o: o["price"])
    return {"date_pairs": pairs, "calendar": calendar, "options": options}


# ---------------------------------------------------------------- ruta combinada

def _connect_out(src, dest, arrival, cfg):
    """Mejor vuelo HUB→DESTINO que sale del MISMO aeropuerto donde aterrizamos,
    con margen dentro de [min, max] horas. Busca el día de llegada y el siguiente."""
    sp = cfg["split"]
    arr_airport, arr_time = arrival["to"], arrival["arr"]
    day = _dt(arr_time).date()
    best = None
    for d in (day, day + timedelta(days=1)):
        for f in src.one_way([arr_airport], dest, d, max_stops=0):
            leg = f["itin"]["legs"][0]
            wait = _hours(arr_time, leg["dep"])
            if sp["min_connection_hours"] <= wait <= sp["max_connection_hours"]:
                if best is None or f["price"] < best["price"]:
                    best = {**f, "wait_h": round(wait, 1)}
    return best


def _connect_back(src, dest, departure, cfg):
    """Mejor vuelo DESTINO→HUB que llega al MISMO aeropuerto del que sale
    el vuelo al origen, con margen [min, max] horas. Busca ese día y el anterior."""
    sp = cfg["split"]
    dep_airport, dep_time = departure["from"], departure["dep"]
    day = _dt(dep_time).date()
    best = None
    for d in (day - timedelta(days=1), day):
        if d <= date.today():
            continue
        for f in src.one_way(dest, [dep_airport], d, max_stops=0):
            leg = f["itin"]["legs"][-1]
            wait = _hours(leg["arr"], dep_time)
            if sp["min_connection_hours"] <= wait <= sp["max_connection_hours"]:
                if best is None or f["price"] < best["price"]:
                    best = {**f, "wait_h": round(wait, 1)}
    return best


def hunt_hub(src, cfg, hub, hub_airports):
    t = cfg["trip"]
    pairs, _ = best_date_pairs(
        src, t["origin"], hub_airports, t["departure_window"], t["durations"],
        0, cfg["search"]["top_date_pairs_per_hub"],
    )
    if not pairs:
        log.info("Hub %s: sin vuelos directos desde el origen", hub)
        return None
    best = None
    for p in pairs:
        # Tramo largo: BUE⇄HUB ida+vuelta directo (un billete)
        for rt in src.round_trip(t["origin"], hub_airports, p["dep"], p["ret"], 0)[:6]:
            out_conn = _connect_out(src, t["destination"], rt["out"]["legs"][-1], cfg)
            if not out_conn:
                continue
            back_conn = _connect_back(src, t["destination"], rt["back"]["legs"][0], cfg)
            if not back_conn:
                continue
            total = rt["price"] + out_conn["price"] + back_conn["price"]
            if best is None or total < best["price"]:
                best = {
                    "type": "split", "hub": hub, "dep": p["dep"], "ret": p["ret"],
                    "price": round(total, 2),
                    "tickets": [
                        {"label": f"Origen ⇄ {hub} (ida y vuelta)", "price": rt["price"]},
                        {"label": f"{hub} → Destino (solo ida)", "price": out_conn["price"]},
                        {"label": f"Destino → {hub} (solo ida)", "price": back_conn["price"]},
                    ],
                    "out": {"legs": rt["out"]["legs"] + out_conn["itin"]["legs"],
                            "wait_h": out_conn["wait_h"]},
                    "back": {"legs": back_conn["itin"]["legs"] + rt["back"]["legs"],
                             "wait_h": back_conn["wait_h"]},
                }
    log.info("Hub %s: %s", hub, best and best["price"])
    return best


def hunt_split(src, cfg):
    """Devuelve (combos ordenados por precio, estado por hub).
    Estado: "ok" | "sin vuelos" (no hay combinación válida) | "error" (Google falló:
    el hub NO se descartó por precio, simplemente hoy no hay dato)."""
    out, status = [], {}
    for hub, airports in cfg["split"]["hubs"].items():
        errors_before = src.errors
        try:
            r = hunt_hub(src, cfg, hub, airports)
        except Exception as e:
            log.warning("Hub %s falló: %s", hub, e)
            r = None
            src.errors += 1
        if r:
            out.append(r)
            status[hub] = "ok"
        else:
            status[hub] = "error" if src.errors > errors_before else "sin vuelos"
    out.sort(key=lambda o: o["price"])
    return out, status


# ---------------------------------------------------------------- veredicto

def run(src, cfg):
    single = hunt_single(src, cfg)
    split, hub_status = hunt_split(src, cfg)
    best_single = single["options"][0] if single["options"] else None
    best_split = split[0] if split else None

    threshold = cfg["split"]["min_saving_usd"]
    saving = None
    winner = best_single
    if best_single and best_split:
        saving = round(best_single["price"] - best_split["price"], 2)
        if saving >= threshold:
            winner = best_split
    elif best_split:
        winner = best_split

    return {
        "single": single,
        "split": split,
        "hub_status": hub_status,
        "best_single": best_single,
        "best_split": best_split,
        "split_saving": saving,
        "split_threshold": threshold,
        "winner": winner,
    }
