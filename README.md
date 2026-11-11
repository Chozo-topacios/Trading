# Trading sistemático: TIS · RSI2 + Schwab, Interactive Brokers y MetaTrader 5

Bot en Python que ejecuta la estrategia **TIS · RSI2 Mean Reversion** (Mariel Lang · Trade It Simple)
y otras estrategias clásicas de reversión a la media, con:

- **Backtester** (señal al cierre → ejecución en la apertura siguiente, costes en bps).
- **Tres brókers**: Charles Schwab, Interactive Brokers (IBKR) y MetaTrader 5 (Exness u otro).
- **Modos demo**, y modos reales bloqueados por doble seguro.

| Modo | Bróker | ¿Quién ejecuta las órdenes? | Dinero real |
|---|---|---|---|
| `paper` | ninguno | simulador local del bot (apertura siguiente + 5 bps) | No |
| `schwab_dry_run` | Schwab | nadie: se construyen y registran, no se envían | No |
| `ibkr_paper` | IBKR | **simulador de IBKR** (cuenta paper `DU…`) | No |
| `mt5_demo` | MT5 / Exness | **servidor demo del bróker** | No |
| `schwab_live`, `ibkr_live`, `mt5_live` | el que corresponda | el bróker | **Sí** |

> ⚠️ **La API de Schwab no ofrece cuenta paper/sandbox** (paperMoney de thinkorswim no es accesible
> por la API). Para Schwab el demo es el simulador local `paper`, que puede usar velas reales de Schwab.
> IBKR y MT5 sí tienen cuentas demo reales accesibles por API.

## Estrategias

| Clave | Origen | Filtro | Entrada (al cierre) | Salida (al cierre → venta en la apertura siguiente) |
|---|---|---|---|---|
| `tis_rsi2` | **Trade It Simple #1** (Mariel Lang) | Close > SMA(200) | RSI(2) < 15 | 2 velas verdes consecutivas (Close > Open) en posición |
| `connors_rsi2` | Larry Connors (RSI2 original) | Close > SMA(200) | RSI(2) < 5 | Close > SMA(5) |
| `double7` | Connors, *Double 7's* | Close > SMA(200) | Cierre = mínimo de 7 días | Cierre = máximo de 7 días |
| `ibs` | Internal Bar Strength | Close > SMA(200) | IBS < 0.2 | IBS > 0.8 o Close > máximo anterior |

Todas son **solo largos, diarias**. El activo de referencia de TIS es **QQQ**.
Todos los parámetros se pueden cambiar (`params` en la configuración o `--params` en el backtest),
y `max_hold_days` añade un stop temporal opcional a cualquier estrategia.

**Gestión de riesgo** (como el robot TIS): arriesga el 1 % del capital contra un stop *virtual* de
2 × ATR(14). Ese stop solo calcula el tamaño de la posición: las reglas TIS no usan stop de precio
(`use_stop = false`). También tiene un tope de exposición por posición (`max_position_pct`).

## Instalación

```bash
pip install -r requirements.txt
```

```bash
cp config.example.toml config.toml
```

## 1. Backtest

```bash
pip install yfinance
```

```bash
python -m tis_trading download QQQ
```

```bash
python -m tis_trading backtest QQQ --strategy all --out resultados
```

```bash
python -m tis_trading backtest QQQ --strategy tis_rsi2 --params '{"rsi_entry": 10}'
```

También acepta tus propios CSV en `data/<SYMBOL>_D1.csv` con las columnas `Date,Open,High,Low,Close`.

## 2. Conectar Charles Schwab

1. Crea una app en <https://developer.schwab.com> con los productos **Accounts and Trading Production**
   y **Market Data Production**. Callback URL: `https://127.0.0.1:8182`. La aprobación tarda unos días.
2. Exporta las credenciales (nunca las pongas en el repositorio):

```bash
export SCHWAB_APP_KEY="..." SCHWAB_APP_SECRET="..." SCHWAB_CALLBACK_URL="https://127.0.0.1:8182"
```

3. Autoriza (abre la URL, inicia sesión y pega la URL de redirección). El *refresh token* dura
   **7 días**: cuando caduque, repite este paso.

```bash
python -m tis_trading auth
```

## 3. Modo DEMO (recomendado al empezar)

En `config.toml`: `mode = "paper"` y `data_source = "schwab"` (o `csv`/`yfinance`).

```bash
python -m tis_trading run
```

```bash
python -m tis_trading status
```

Cada ejecución: (1) llena las órdenes demo pendientes en la apertura de la vela siguiente,
(2) evalúa las señales sobre la **última vela diaria completa** (la de hoy se ignora antes de las 16:00 ET),
y (3) encola las nuevas órdenes. Si lo ejecutas dos veces sobre la misma vela, no duplica órdenes.
El estado se guarda en `state/` (ignorado por git).

Prográmalo una vez al día después del cierre, por ejemplo con cron a las 16:15 ET de lunes a viernes:

```bash
CRON_TZ=America/New_York
15 16 * * 1-5 cd /ruta/Trading && python -m tis_trading run >> state/run.log 2>&1
```

## 4. Dry-run contra tu cuenta real

```bash
python -m tis_trading run --mode schwab_dry_run
```

Lee el saldo y las posiciones reales y registra las órdenes que **enviaría** en
`state/schwab_orders.jsonl`, sin enviarlas.

## 5. Real (solo cuando el demo esté validado)

Requiere **las dos** condiciones: `live_trading_enabled = true` en la sección del bróker (`[schwab]`, `[ibkr]` o `[mt5]`) y el flag `--confirm-live`.

```bash
python -m tis_trading run --mode schwab_live --confirm-live
```

Las órdenes son a mercado `DAY`. Si se envían fuera de horario, quedan en cola para la apertura,
que es justo lo que piden las reglas.

## 6. Interactive Brokers (cuenta paper)

1. Activa la cuenta paper en el Client Portal: *Settings → Paper Trading Account*. Tendrá usuario
   propio y un número de cuenta que empieza por `DU`.
2. Instala TWS o IB Gateway, inicia sesión **con el usuario paper** y activa la API:
   *Configure → API → Settings → Enable ActiveX and Socket Clients*.
3. (Opcional) Para tener datos en tiempo real en paper, comparte las suscripciones de tu cuenta real
   (*Settings → Paper Trading Account → Share real-time market data*). Sin eso hay datos con retraso,
   suficiente para una estrategia diaria.
4. Instala la librería y configura `[ibkr]` (puerto 7497 en TWS o 4002 en Gateway):

```bash
pip install ib_async
```

5. En `config.toml`: `mode = "ibkr_paper"`, `data_source = "ibkr"` y `symbol = "QQQ"`. Luego:

```bash
python -m tis_trading run --mode ibkr_paper
```

- Las órdenes son **Market-on-Open** (`MKT` con `tif=OPG`): se ejecutan en la subasta de apertura,
  como piden las reglas. Hay que enviarlas antes de las ~9:28 ET; programar el bot a las 16:15 ET cumple.
- El bot **se niega a operar** en `ibkr_paper` si la cuenta conectada no es `DU…`.
- IB Gateway se desconecta una vez al día. Para dejarlo desatendido usa
  [IBC](https://github.com/IbcAlpha/IBC), que inicia sesión automáticamente.

## 7. MetaTrader 5 / Exness (cuenta demo)

1. Abre una cuenta **demo MT5** en Exness (gratis, sin depósito) e instala el terminal MetaTrader 5.
2. En MT5: *Herramientas → Opciones → Asesores Expertos → Permitir trading algorítmico*.
3. La librería oficial **solo funciona en Windows**, con el terminal abierto (en Mac o Linux necesitas
   Wine o una máquina virtual con Windows):

```bash
pip install MetaTrader5
```

4. Credenciales en variables de entorno (o deja el terminal con la sesión iniciada):

```bash
set MT5_LOGIN=12345678
set MT5_PASSWORD=...
set MT5_SERVER=Exness-MT5Trial
```

5. En `config.toml`: `mode = "mt5_demo"`, `data_source = "mt5"` y usa **el símbolo del bróker**,
   por ejemplo `symbol = "USTEC"` (Nasdaq 100; en cuentas Standard puede ser `USTECm`). Luego:

```bash
python -m tis_trading run --mode mt5_demo
```

- El bot **se niega a operar** en `mt5_demo` si la cuenta conectada no es demo.
- El tamaño se calcula en **lotes** con el valor del tick de cada símbolo, se redondea al paso de
  volumen y se reduce si no hay margen libre suficiente.
- Solo gestiona las posiciones abiertas con su número mágico (`magic`): no toca tus operaciones manuales.
- Las órdenes se ejecutan **al momento** (el CFD cotiza casi 23 h). Ejecuta el bot justo después del
  cambio de vela diaria del servidor.

**Diferencias con QQQ que debes conocer:** operas CFDs, no el ETF. Las velas diarias se cierran
según la hora del servidor, así que las señales no coinciden exactamente con las del research de TIS.
Además hay apalancamiento, spread y **swap** por cada noche con la posición abierta. Haz el backtest con
datos del propio bróker antes de fiarte de los resultados de QQQ.

> Alternativa sin Python: el research de Mariel incluye el robot `TIS_RSI2_MeanReversion.mq5`, que
> puedes cargar directamente en MT5 y probar con su Strategy Tester.

## Estructura

```
tis_trading/
  indicators.py   RSI de Wilder, SMA, ATR, IBS
  strategies.py   tis_rsi2, connors_rsi2, double7, ibs
  backtest.py     backtester + métricas (CAGR, DD, Sharpe, PF, win rate, exposición)
  risk.py         sizing 1 % vs 2×ATR(14)
  engine.py       ciclo diario + configuración + bloqueo del modo live
  brokers/paper.py   bróker demo local
  brokers/schwab.py  OAuth, market data, cuentas y órdenes de Schwab
  brokers/ibkr.py    Interactive Brokers (ib_async): datos, cuenta, órdenes MOO
  brokers/mt5.py     MetaTrader 5 / Exness: datos, cuenta, lotes, órdenes
tests/            pytest
```

```bash
python -m pytest -q
```

## Fuentes

- Research reproducible TIS · RSI2: <https://github.com/mlangsfinance-stack/TIS-RSI2-Research>
- Página de recursos de Mariel Lang: <https://mariellangsaez.tradeitsimplesolutions.com/#recursos>

*Esto es software educativo, no asesoría financiera. Los resultados pasados no garantizan resultados futuros.*
