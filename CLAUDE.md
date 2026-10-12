# Trip — Flight Deal & Hub Hunter

Tracker diario de vuelos: compara **billete único** vs. **ruta combinada por hub** (split-ticketing) y muestra todo en un dashboard estático.

- Viaje activo: BUE ⇄ Washington, salida mar/abr 2027, carry-on + 23kg, umbral split USD 150. Todo en `config.yaml`.
- Fuente: Google Flights vía librería `fli` (`flights` en pip, github.com/punitarani/fli). Sin API key. `BagsFilter` hace que Google sume el equipaje; low-cost sin dato → recargo manual en `config.yaml`.
- Código: `hunter/source.py` (único archivo que toca `fli`), `hunter/hunt.py` (lógica), `hunter/buy.py` (semáforo de compra: parada óptima con precio de reserva, reglas en `config.yaml > purchase`), `hunter/main.py` (orquesta, historia 1 fila/día, Telegram).
- El calendario de Google ignora el filtro de escala (con filtro devuelve ~1 fecha): se usa sin filtro y la regla se valida en el detalle.
- No correr dos `hunter.main` en paralelo: Google limita y los hubs vuelven vacíos (`hub_status: "error"`).
- Salida: `docs/data/latest.json` + `history.json` → `docs/index.html` (GitHub Pages).
- Runner activo: Programador de tareas de Windows, tarea "Trip Hub Hunter" 08:00 diaria (StartWhenAvailable) → `run_hunt.bat` → log en `logs/hunt.log`. Solo corre con la PC prendida.
- `.github/workflows/daily.yml` existe pero NO está activo (la carpeta no es repo git aún). ~100–200 requests a Google por corrida, ~10 min.
- Correr local: `.venv/Scripts/python -m hunter.main`; dashboard: `python -m http.server -d docs`.
