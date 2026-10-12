"""Corrida diaria: busca, guarda historia, imprime reporte y (opcional) avisa por Telegram.

Uso:  python -m hunter.main
"""

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from hunter import buy
from hunter.hunt import run
from hunter.source import Source

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
HISTORY = DATA / "history.json"
LATEST = DATA / "latest.json"


def gflights_link(orig, dest, dep, ret=None):
    q = f"Flights from {orig} to {dest} on {dep}" + (f" returning {ret}" if ret else " one way")
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": q, "curr": "USD"})


def add_links(result, cfg):
    """Links de compra a Google Flights (uno por billete)."""
    t = cfg["trip"]
    o, d = t["origin"][0], t["destination"][0]
    for opt in result["single"]["options"]:
        opt["links"] = [gflights_link(o, d, opt["dep"], opt["ret"])]
    for opt in result["split"]:
        hub_air = opt["out"]["legs"][-2]["to"]  # aeropuerto donde aterriza el tramo largo
        out_short, back_short = opt["out"]["legs"][-1], opt["back"]["legs"][0]
        opt["links"] = [
            gflights_link(o, hub_air, opt["dep"], opt["ret"]),
            gflights_link(hub_air, out_short["to"], out_short["dep"][:10]),
            gflights_link(back_short["from"], hub_air, back_short["dep"][:10]),
        ]


def fmt_leg(l):
    return f"{l['from']} ➡️ {l['to']} | {l['flight']} | {l['dep'][5:].replace('T', ' ')} → {l['arr'][5:].replace('T', ' ')}"


def report(result, prev_best, cfg):
    """Texto en el formato del prompt original (🚨 o modo silencioso)."""
    t = cfg["trip"]
    w = result["winner"]
    route = f"{'/'.join(t['origin'])} 🔁 {'/'.join(t['destination'])}"
    if not w:
        return f"Monitoreo activo para {route}. Sin resultados hoy."
    label = f"vía {w['hub']} (combinada)" if w["type"] == "split" else f"billete único ({w['out']['legs'][0]['airline']})"
    is_new_min = prev_best is None or w["price"] < prev_best
    if not is_new_min:
        return f"Monitoreo activo para {route}. Mejor opción actual: ${w['price']:.0f} USD {label} (con equipaje)."

    lines = [
        "🚨 [OFERTA DETECTADA: BILLETE ÚNICO vs. RUTA COMBINADA]",
        f"Precio total (ida + vuelta): ${w['price']:.0f} USD (tasas + carry-on + 23kg incluidos)",
        f"Estrategia: {label} | {w['dep']} → {w['ret']}",
        "Ida:",
        *[f"  {fmt_leg(l)}" for l in w["out"]["legs"]],
        "Vuelta:",
        *[f"  {fmt_leg(l)}" for l in w["back"]["legs"]],
    ]
    bs, bp = result["best_single"], result["best_split"]
    if bs and bp:
        lines.append(f"Comparativa: único ${bs['price']:.0f} vs. combinada vía {bp['hub']} ${bp['price']:.0f} "
                     f"(ahorro ${result['split_saving']:.0f}, umbral ${result['split_threshold']})")
    if prev_best:
        lines.append(f"Mínimo anterior: ${prev_best:.0f} → nuevo: ${w['price']:.0f}")
    return "\n".join(lines)


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
    except Exception as e:
        logging.warning("Telegram falló: %s", e)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    src = Source(cfg)
    result = run(src, cfg)
    add_links(result, cfg)

    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    today_utc = datetime.now(timezone.utc).date().isoformat()
    prev = [h["winner_price"] for h in history if h.get("winner_price") and h["run_at"][:10] != today_utc]
    prev_best = min(prev) if prev else None

    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    w, bs, bp = result["winner"], result["best_single"], result["best_split"]
    signal = buy.evaluate(cfg, w and w["price"], prev_best) if "purchase" in cfg else None
    prev_color = history[-1].get("signal") if history else None
    # Una fila por día: si se corre a mano dos veces el mismo día, gana la última.
    history = [h for h in history if h["run_at"][:10] != now[:10]]
    history.append({
        "signal": signal and signal["color"],
        "run_at": now,
        "winner_price": w and w["price"],
        "winner_label": w and (f"vía {w['hub']}" if w["type"] == "split" else "billete único"),
        "single_price": bs and bs["price"],
        "split_price": bp and bp["price"],
        "split_hub": bp and bp["hub"],
        "by_hub": {o["hub"]: o["price"] for o in result["split"]},
    })

    text = report(result, prev_best, cfg)
    if signal:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[signal["color"]]
        text += f"\n\n{icon} Semáforo: {signal['label']} — " + " ".join(signal["reasons"])
        text += f"\nFaltan {signal['days_to_deadline']} días para la fecha límite de compra ({signal['deadline']})."
    print(text)
    turned_green = signal and signal["color"] == "green" and prev_color != "green"
    if prev_best is None or (w and w["price"] < prev_best) or turned_green:
        telegram(text)

    DATA.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps({"run_at": now, "trip": cfg["trip"], "split_cfg": cfg["split"],
                                  "requests": src.calls, "report": text, "buy": signal, **result},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    logging.info("Listo. %d requests a Google.", src.calls)
    return 0 if w else 1


if __name__ == "__main__":
    sys.exit(main())
