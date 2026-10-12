"""Fuente de datos: Google Flights vía la librería `fli` (github.com/punitarani/fli).

Todo lo que sale de acá está "normalizado" a dicts simples (no objetos de fli),
así el resto del código no depende de la librería. Si mañana fli se rompe,
se reemplaza solo este archivo.
"""

import logging
import time
from datetime import date, timedelta

from fli.models import (
    Airport,
    BagsFilter,
    DateSearchFilters,
    FlightSearchFilters,
    FlightSegment,
    LayoverRestrictions,
    MaxStops,
    PassengerInfo,
    SortBy,
    TripType,
)
from fli.search import SearchDates, SearchFlights

log = logging.getLogger(__name__)

STOPS = {0: MaxStops.NON_STOP, 1: MaxStops.ONE_STOP_OR_FEWER}


class Source:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.flights = SearchFlights()
        self.dates = SearchDates()
        self.calls = 0          # contador de requests a Google (para no abusar)
        self.errors = 0         # búsquedas que fallaron por error (≠ "no hay vuelos")
        self._cache = {}        # misma búsqueda en la misma corrida = no se repite

    # ---------- helpers internos ----------

    def _bags(self):
        b = self.cfg["baggage"]
        return BagsFilter(checked_bags=b["checked_bags"], carry_on=b["carry_on"])

    def _seg(self, orig, dest, day):
        return FlightSegment(
            departure_airport=[[Airport[a], 0] for a in orig],
            arrival_airport=[[Airport[a], 0] for a in dest],
            travel_date=str(day),
        )

    def _layover(self, min_m, max_m):
        if min_m is None and max_m is None:
            return None
        return LayoverRestrictions(min_duration=min_m, max_duration=max_m)

    def _call(self, fn, *args, **kwargs):
        """Llama a Google con reintento: a veces devuelve vacío sin motivo."""
        s = self.cfg["search"]
        errored = False
        for attempt in range(3):
            self.calls += 1
            try:
                r = fn(*args, currency=s["currency"], country=s["country"], **kwargs)
                if r:
                    return r
            except Exception as e:  # red, parseo, rate limit...
                errored = True
                log.warning("Error en búsqueda (intento %d): %s", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
        self.errors += errored
        return []

    def _fee(self, legs):
        """Recargo manual de equipaje para low-cost sin dato en Google."""
        fees = self.cfg["baggage"].get("manual_fee_per_leg_usd", {}) or {}
        return sum(fees.get(l["airline"], 0) for l in legs)

    @staticmethod
    def _itin(fr):
        """FlightResult de fli → dict simple."""
        legs = [
            {
                "airline": l.airline.name.removeprefix("_"),
                "flight": f"{l.airline.name.removeprefix('_')}{l.flight_number}",
                "from": l.departure_airport.name,
                "to": l.arrival_airport.name,
                "dep": l.departure_datetime.isoformat(timespec="minutes"),
                "arr": l.arrival_datetime.isoformat(timespec="minutes"),
            }
            for l in fr.legs
        ]
        return {
            "legs": legs,
            "stops": fr.stops,
            "duration_min": fr.duration,
            "layovers_min": [ly.duration for ly in (fr.layovers or [])],
            "self_transfer": bool(fr.self_transfer),
        }

    # ---------- búsquedas públicas ----------

    def calendar_rt(self, orig, dest, window, duration, max_stops, layover_min=None, layover_max=None):
        """Precio ida+vuelta más barato por fecha de salida, para una duración fija.
        Devuelve [{dep, ret, price}] (1 request a Google por ventana de ≤61 días)."""
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        start = max(start, date.today() + timedelta(days=1))
        f = DateSearchFilters(
            trip_type=TripType.ROUND_TRIP,
            passenger_info=PassengerInfo(adults=self.cfg["trip"]["adults"]),
            flight_segments=[
                self._seg(orig, dest, start),
                self._seg(dest, orig, start + timedelta(days=duration)),
            ],
            stops=STOPS[max_stops],
            layover_restrictions=self._layover(layover_min, layover_max),
            bags=self._bags(),
            from_date=str(start),
            to_date=str(end),
            duration=duration,
        )
        out = []
        for dp in self._call(self.dates.search, f):
            dep = dp.date[0].date()
            out.append({"dep": str(dep), "ret": str(dep + timedelta(days=duration)), "price": dp.price})
        return out

    def round_trip(self, orig, dest, dep, ret, max_stops, layover_min=None, layover_max=None):
        """Ida+vuelta en UN billete. Devuelve [{out, back, price}] ordenado por precio."""
        key = ("rt", tuple(orig), tuple(dest), dep, ret, max_stops, layover_min, layover_max)
        if key in self._cache:
            return self._cache[key]
        f = FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP,
            passenger_info=PassengerInfo(adults=self.cfg["trip"]["adults"]),
            flight_segments=[self._seg(orig, dest, dep), self._seg(dest, orig, ret)],
            stops=STOPS[max_stops],
            layover_restrictions=self._layover(layover_min, layover_max),
            sort_by=SortBy.CHEAPEST,
            bags=self._bags(),
        )
        top_n = self.cfg["search"]["top_n_outbound"]
        res = []
        for combo in self._call(self.flights.search, f, top_n=top_n):
            out_fr, back_fr = combo
            # En ida+vuelta, Google pone el precio TOTAL del viaje en el tramo de vuelta.
            if back_fr.price is None:
                continue
            out, back = self._itin(out_fr), self._itin(back_fr)
            fee = self._fee(out["legs"] + back["legs"])
            res.append({"out": out, "back": back, "price": back_fr.price + fee})
        res.sort(key=lambda x: x["price"])
        self._cache[key] = res
        return res

    def price_insight(self, orig, dest, day):
        """'Price insights' de Google: rango de precio típico + historia de ~60 días.

        fli no lo expone: viene escondido en inner[5] de la respuesta cruda.
        Google SOLO lo devuelve para búsquedas solo-ida SIN equipaje, así que es
        un termómetro del mercado de la ruta, no nuestro precio exacto (ida+vuelta con valija).
        Estructura (verificada contra el cartel "Prices are currently high" de la web, oct-2026):
          [1] precio actual · [4] / [5] rango típico (bajo / alto) · [10][0] = [[timestamp_ms, precio], ...]
        """
        from fli.search._urls import with_locale_params
        from fli.search.flights import parse_first_wrb_payload

        f = FlightSearchFilters(
            trip_type=TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=self.cfg["trip"]["adults"]),
            flight_segments=[self._seg(orig, dest, day)],
            stops=STOPS[self.cfg["single_ticket"]["max_stops"]],
            sort_by=SortBy.BEST,
        )
        s = self.cfg["search"]
        # Idioma es-419 obligatorio: desde los servidores de GitHub (EE.UU.), con "en" o sin idioma
        # Google responde ErrorResponse 13 para este bloque (verificado oct-2026).
        url = with_locale_params(SearchFlights.BASE_URL, s["currency"], "es-419", s["country"])
        # Después de una ráfaga de búsquedas, Google frena este bloque un rato (ErrorResponse 13):
        # por eso las esperas entre intentos son largas (verificado en GitHub, oct-2026).
        for attempt, wait in enumerate([0, 10, 20, 40, 60]):
            time.sleep(wait)
            self.calls += 1
            try:
                r = self.flights.client.post(url=url, data=f"f.req={f.encode()}", impersonate="chrome")
                pi = parse_first_wrb_payload(r.text)[5]
                if pi:
                    hist = pi[10][0] if len(pi) > 10 and pi[10] else []
                    current, low, high = pi[1][1], pi[4][1], pi[5][1]
                    return {
                        "route": f"{'/'.join(orig)} → {'/'.join(dest)}",
                        "date": str(day),
                        "current": current,
                        "typical_low": low,
                        "typical_high": high,
                        "level": "bajo" if current < low else "alto" if current > high else "típico",
                        "history": [
                            {"date": str(date.fromtimestamp(ms / 1000)), "price": p} for ms, p in hist
                        ],
                    }
                log.warning("Price insight sin datos (intento %d): %s", attempt + 1, r.text[:90].replace(chr(10), " "))
            except Exception as e:  # el bloque es no oficial: si cambia, no rompe la corrida
                log.warning("Price insight falló (intento %d): %s", attempt + 1, e)
        return None

    def one_way(self, orig, dest, day, max_stops=0):
        """Solo ida. Devuelve [{itin, price}] ordenado por precio."""
        key = ("ow", tuple(orig), tuple(dest), str(day), max_stops)
        if key in self._cache:
            return self._cache[key]
        f = FlightSearchFilters(
            trip_type=TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=self.cfg["trip"]["adults"]),
            flight_segments=[self._seg(orig, dest, day)],
            stops=STOPS[max_stops],
            sort_by=SortBy.CHEAPEST,
            bags=self._bags(),
        )
        res = []
        for fr in self._call(self.flights.search, f):
            if fr.price is None:
                continue
            it = self._itin(fr)
            res.append({"itin": it, "price": fr.price + self._fee(it["legs"])})
        res.sort(key=lambda x: x["price"])
        self._cache[key] = res
        return res
