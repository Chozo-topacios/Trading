# Trading sistemático: TIS · RSI2 + Charles Schwab

Bot en Python que ejecuta la estrategia **TIS · RSI2 Mean Reversion** (Mariel Lang · Trade It Simple)
y otras estrategias clásicas de reversión a la media, con:

- **Backtester** (señal al cierre → ejecución en la apertura siguiente, costes en bps).
- **Modo DEMO** (bróker simulado local) usando datos de mercado de Schwab, CSV o yfinance.
- **Modo `schwab_dry_run`**: lee tu cuenta real de Schwab y construye las órdenes **sin enviarlas**.
- **Modo `live`**: envía órdenes reales a Schwab. Está bloqueado por doble seguro.

> ⚠️ **La API de Schwab no ofrece cuenta paper/sandbox.** paperMoney de thinkorswim no es accesible
> por la API. Por eso el "modo demo" simula las órdenes localmente con la misma lógica de ejecución
> que el backtest (apertura siguiente + 5 bps), pero puede usar **velas reales de Schwab**.

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

Requiere **las dos** condiciones: `live_trading_enabled = true` en `[schwab]` y el flag `--confirm-live`.

```bash
python -m tis_trading run --mode live --confirm-live
```

Las órdenes son a mercado `DAY`. Si se envían fuera de horario, quedan en cola para la apertura,
que es justo lo que piden las reglas.

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
tests/            pytest
```

```bash
python -m pytest -q
```

## Fuentes

- Research reproducible TIS · RSI2: <https://github.com/mlangsfinance-stack/TIS-RSI2-Research>
- Página de recursos de Mariel Lang: <https://mariellangsaez.tradeitsimplesolutions.com/#recursos>

*Esto es software educativo, no asesoría financiera. Los resultados pasados no garantizan resultados futuros.*
