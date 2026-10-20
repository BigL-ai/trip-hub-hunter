# Trip — Flight Deal & Hub Hunter

Tracker diario de vuelos: compara **billete único** vs. **ruta combinada por hub** (split-ticketing) y muestra todo en un dashboard estático.

- Viaje activo: BUE ⇄ Washington, salida mar/abr 2027, carry-on + 23kg, umbral split USD 150. Todo en `config.yaml`.
- Fuente: Google Flights vía librería `fli` (`flights` en pip, github.com/punitarani/fli). Sin API key. `BagsFilter` hace que Google sume el equipaje; low-cost sin dato → recargo manual en `config.yaml`.
- Código: `hunter/source.py` (único archivo que toca `fli`), `hunter/hunt.py` (lógica), `hunter/buy.py` (semáforo de compra: parada óptima con precio de reserva, reglas en `config.yaml > purchase`), `hunter/main.py` (orquesta, historia 1 fila/día, Telegram).
- "Price insights" de Google (`Source.price_insight`; en runners de GitHub: idioma es-419 obligatorio y reintentos con espera larga, Google frena el bloque tras ráfagas): fli no lo expone, se lee de `inner[5]` crudo ([1] actual, [4]/[5] rango típico, [10][0] historia ~60 días). Google SOLO lo da para solo-ida SIN valija → es termómetro de mercado, no nuestro precio. Semáforo: verde si nivel "bajo".
- El calendario de Google ignora el filtro de escala (con filtro devuelve ~1 fecha): se usa sin filtro y la regla se valida en el detalle.
- No correr dos `hunter.main` en paralelo: Google limita y los hubs vuelven vacíos (`hub_status: "error"`).
- Guarda de calidad: si el calendario del billete único vuelve vacío (Google limitó), `main` sale con código 2 sin escribir datos. No disparar muchas corridas seguidas: Google frena la IP de los runners.
- `ErrorResponse 13` de Google = freno por exceso de pedidos (por IP). El price insight es lo primero que se corta → se pide al INICIO (tras el calendario), no al final. Evitar muchas corridas locales seguidas.
- Detección de freno: `_ThrottleSpy` envuelve el cliente de fli y cuenta las respuestas "13"; un vacío por freno cuenta como error (hub → "⚠ sin dato"), no como "sin vuelos". Pausa entre hubs: `search.hub_pause_seconds` (20s). Si el calendario del billete único vuelve vacío, `run` corta antes de los hubs.
- Tope de tiempo: `search.time_budget_minutes` (20). Pasado el tope `_call` devuelve vacío sin buscar, los hubs restantes quedan "sin tiempo" y se publica igual (el workflow corta a los 30 min y si corta no se guarda nada: así se perdieron 15–18/10).
- Salida: `docs/data/latest.json` + `history.json` → `docs/index.html` (GitHub Pages).
- Runner: GitHub Actions `.github/workflows/daily.yml` 08:00 ART en repo público github.com/BigL-ai/trip-hub-hunter; el bot commitea `docs/data` ("hunt: YYYY-MM-DD") → hacer `git pull` antes de tocar algo local. ~125 requests a Google, ~10 min.
- Dashboard online: https://bigl-ai.github.io/trip-hub-hunter/ (GitHub Pages sobre `/docs`).
- La tarea local de Windows fue borrada; `run_hunt.bat` queda solo para correr a mano.
- Correr local: `.venv/Scripts/python -m hunter.main`; dashboard: `python -m http.server -d docs`.
