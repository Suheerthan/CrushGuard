# CrushGuard

**Clamp-on crowd-pressure sensors that warn a control room before a crowd crush happens.**
Built for Bit N Build '26 under **PSN022: Open Innovation for Disaster Management**.

Stampedes keep happening in India: Hathras, the Maha Kumbh, New Delhi railway station, Bengaluru,
Karur, and others. In every case the barricades were there, but nobody knew how hard the crowd was
pushing against them until people were already dying. CCTV shows how many people there are. It does
not show **how much force** they are under, and force is what kills.

CrushGuard puts a cheap sensor node on the barricades India already uses (bamboo poles, pipe
railings, rope lines, temple queue rails). Each node measures:

- **push force** on the barricade (strain-gauge load cell in a 3D-printed clamp)
- **how fast that force is rising**, so it can predict "critical in 12 s"
- **surging**: rhythmic back-and-forth waves in the crowd, the pattern that comes before a crush

The nodes form their own radio network (ESP-NOW, no mobile internet needed; phone networks
usually jam in big crowds). A control-room dashboard shows every barricade segment as
green / amber / red, raises alerts with a clear action ("stop entry at E1, open G2"), and plays a
PA announcement in English, Hindi or a regional language. Every node also has its own beacon and
buzzer and keeps working if the control room goes offline.

---

## Control room features

| View | What it does |
|---|---|
| **Venue** | Live barricade map with glowing pressure zones, alerts (critical first), per-segment detail with a **30-second pressure forecast**, sensor health, stewards on the ground, simulator |
| **Stewards (phones)** | Stewards scan the QR code on the Venue view (same Wi-Fi, no internet). They pick their barricades; the phone **vibrates and beeps** when their zone goes amber/red, tells them what to do, and has **I'm on it** and **Need backup** buttons. The control room sees who is responding where. |
| **National** | Map of India with every venue reporting to one state / national control room, sorted by risk. The main venue is real; the others are simulated for the demo. |
| **Replay** | Every session is logged. Pick one, press play (1×/5×/20×) or drag the timeline, and see exactly what the control room saw: pressure, alerts, operator actions and steward responses. A summary shows how early the warning came and how fast stewards and the control room responded. |
| **Incident report** | One click in Replay opens a printable report (Save as PDF): plain-language summary, pressure chart, every incident with warning time, steward response and first action, per-barricade exposure, and the full timeline, with signature lines for police / safety officer. |
| **Plan** | Before the event: enter area, expected crowd, front-of-stage share, exits and entries. CrushGuard shows the crowd density (comfortable → crush risk), time to empty the ground, entry queue, how many sensors are needed and what to change. |

**Barricade collapse detection.** If the crowd was pushing hard and the force suddenly disappears while the
barricade tilts over (motion sensor), CrushGuard raises **BARRICADE DOWN**: the most dangerous moment,
because people fall forward. The node flashes red/amber with a siren, the control room gets a critical alert,
and steward phones show what to do. Press **Barricade back up** once it is fixed.

**Heat stress.** An optional DHT22 sensor on the gateway measures temperature and humidity. CrushGuard computes
the heat index (US National Weather Service formula) and, when it is hot and humid, treats the same push as more
dangerous (limits lowered 5–30 % by category) and raises a heat alert with actions (water, shade, slow entry).

The forecast is `f(t) = f + rate·τ·(1 − e^(−t/τ))` with τ = 15 s: it follows the current rise at
first and then levels off, so it does not predict impossible straight-line growth.

---

## What is in this repo

```
firmware/
  node/        ESP32 barricade node: HX711 load cell + MPU6050, beacon, ESP-NOW  (node.ino)
               crush_metrics.h  force / rate / surge maths (unit-tested on a laptop)
  gateway/     ESP32 plugged into the laptop: radio <-> USB serial               (gateway.ino)
server/
  app.py       control-room server + open REST/WebSocket API
  crushguard/  risk engine + forecast, simulator, stewards, replay, national network, serial bridge
  static/      control room (index.html), steward phone page (steward.html), India map
  national.json  venues shown in the National view
  config.json  venue layout, segments, gates, thresholds, PA messages
tests/         C++ and Python tests (including C++/Python parity)
```

## Run it now (no hardware needed)

```bash
cd server
pip install -r requirements.txt
python app.py --sim
```

Open **http://localhost:8000**. The terminal also prints the steward link
(`http://<laptop-ip>:8000/steward`) for phones on the same Wi-Fi. If a phone cannot open it,
allow Python through the Windows firewall (private networks) when Windows asks.

The **Simulator** panel at the bottom lets you:

| Button | What happens |
|---|---|
| Crowd build-up | people keep arriving and press toward the middle of the stage; amber comes ~15–20 s before red, with a countdown |
| Crowd surge wave | a pressure wave travels sideways along the barricade. CrushGuard flags a **crowd wave** and goes red *before* any single segment is overloaded |
| Simulate push here | a sharp push on the selected segment (stays local, recovers) |
| Stop entry / Open G1 / Open G2 | the response actions: watch the pressure fall |
| Take node offline | shows sensor health monitoring and the offline alert |
| Barricade collapse at S… | the selected barricade gives way: force vanishes, it tilts 35°, BARRICADE DOWN |
| Weather: Mild / Hot & humid / Heatwave | changes the heat index; watch the limits tighten and the heat alert |

The **Response** buttons (Stop entry, Open G1/G2) work in both modes. With real sensors they are the
operator's action log, so the incident report can show how fast the control room acted.

Every simulated node runs **the same maths as the real firmware** (tests/test_parity.py checks
this), and produces the same messages as the real gateway, so the dashboard and server do not
change when you switch to hardware.

Run the tests:

```bash
python -m pytest -q tests                                   # from the repo root
g++ -std=c++17 tests/test_metrics.cpp -o t && ./t           # firmware maths on a laptop
```

## Switch to real hardware

1. Flash `firmware/gateway/gateway.ino` onto one ESP32 and plug it into the laptop.
2. Flash `firmware/node/node.ino` onto every node (same firmware for all).
3. Open each node's serial monitor (115200) and type `id 1`, `id 2`, … to match `config.json`.
4. Calibrate each clamp (see below).
5. `python app.py --serial COM5` (Windows) or `--serial /dev/ttyUSB0` (Linux/Mac).

Arduino IDE: install the **esp32 by Espressif** board package (2.x or 3.x both work). Board:
*ESP32 Dev Module*. No other libraries are needed; the HX711 and MPU6050 are driven directly.

## Hardware (per node)

| Part | Qty | Approx. ₹ |
|---|---|---|
| ESP32 DevKit V1 | 1 | 350–450 |
| Bar load cell, 20 kg or 50 kg (TAL220 type) | 1 | 150–250 |
| HX711 load-cell amplifier | 1 | 60–90 |
| MPU6050 accelerometer | 1 | 120–180 |
| LEDs green / amber / red + 220 Ω resistors | 3 | 20 |
| Active buzzer 5 V + BC547 transistor | 1 | 25 |
| Power: USB power bank for the demo (or 18650 + TP4056 + HT7333 LDO) | 1 | 150–300 |
| 3D-printed clamp + M4 bolts + rubber strip | 1 | 100 |
| *Optional, gateway only:* DHT22 temperature/humidity sensor + 10 kΩ resistor | 1 | 150–250 |
| **Total** | | **~₹1,000–1,400** |

Plus one ESP32 for the gateway. Compare: imported barrier load systems cost many lakhs per venue.

### Wiring

| From | To (ESP32) |
|---|---|
| HX711 DOUT / SCK | GPIO16 / GPIO4 |
| HX711 VCC / GND | 3V3 / GND |
| Load cell red / black / white / green | HX711 E+ / E− / A− / A+ (swap A± if force reads negative) |
| MPU6050 SDA / SCL | GPIO21 / GPIO22 |
| LED green / amber / red (via 220 Ω) | GPIO25 / GPIO26 / GPIO27 |
| Buzzer (via transistor) | GPIO14 |
| Battery + → 100 k → **GPIO35** → 100 k → GND | battery monitor |
| *Gateway:* DHT22 DATA → **GPIO15** (10 kΩ pull-up to 3V3), VCC → 3V3 | heat stress (optional) |

### The clamp (mechanical design)

The clamp is the core engineering part. It fits a 40–75 mm pole or pipe:

- A two-part clamp body bolts around the pole, with a rubber strip so it does not slip or rotate.
- The bar load cell is **cantilevered**: one end bolted to the clamp body, the other end bolted
  to a **push plate** that faces the crowd. When people push the plate, the cell bends and the
  HX711 reads it.
- For **rope-line barricades**, use an S-type load cell *in line with the rope* instead. It
  measures rope tension, which rises when the crowd leans on the rope.
- Keep the push plate 5–10 mm clear of the pole so it can deflect, and put a mechanical end stop
  behind it so a very large push cannot overload the cell (a 20 kg cell survives ~150% of rated load).

### Calibration (5 minutes per node)

In the node's serial monitor:

```
tare                  (nothing touching the plate)
cal 98.1              (after hanging / pressing a known 10 kg = 98.1 N on the plate)
thr 250 450           (amber and red limits in newtons)
info                  (check readings)
```

Values are saved in flash and survive power cycles.

> **About thresholds:** 250 N / 450 N are demo values for a push on a table-top rail. For a real
> deployment the red limit must come from the barricade's rated load and field testing, set per
> barricade type. Do not present the demo numbers as safety limits.

## How the risk logic works

Each node (and the server) computes:

- **force**: smoothed push force (N)
- **rate**: slope of force over the last 2 s (N/s)
- **surging**: std-dev of force around its trend over the last 5 s (N). A steady lean ≈ 0, waves are large.
- **effective load** = force + 3 s × rate (if rising) + 1.5 × surging
- **risk score** = 100 × effective load / red limit. Amber from the amber limit, red at 100, with hysteresis so beacons do not flicker.
- **time to critical** = (red limit − force) / rate, shown as a countdown.
- **crowd wave**: 3 or more neighbouring segments surging at the same time → the whole zone goes red, and the server pushes red to those nodes' beacons even if each one alone looks fine.

If the gateway or laptop fails, every node keeps judging its own segment and drives its own beacon.

## Open API (for police / temple / railway control rooms)

Interactive docs at **/docs** when the server runs.

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | live risk for every segment + alerts |
| `GET /api/history` | last 2 minutes of force per segment |
| `GET /api/alerts?active_only=true` | alert log |
| `POST /api/alerts/{id}/ack` | acknowledge |
| `GET /api/pa?segment=S3&lang=hi` | announcement text |
| `POST /api/nodes/{n}/identify` | blink a node's beacon to find it on site |
| `POST /api/stewards` · `POST /api/alerts/{id}/respond` · `POST /api/stewards/{id}/help` | steward check-in, "I'm on it", backup request |
| `GET /api/national` | every venue's status for a state / national control room |
| `GET /api/sessions` · `GET /api/replay/{name}` | logged sessions, rebuilt timeline and response statistics |
| `POST /api/action` | log an operator action (stop entry, open gate, note) |
| `POST /api/segments/{id}/restore` | barricade put back up after a collapse |
| `POST /api/plan` | pre-event capacity plan |
| `GET /report?session=…` | printable incident report |
| `WS /ws` | 5 Hz live stream |

Every session is logged to `server/logs/session-*.csv` for post-event review and inquiries.

## Demo script (3 minutes)

1. **Hook (20 s):** "Most crowd-crush deaths are caused by pressure, not by the number of people. CCTV can count people, but it can't see pressure."
2. **Live hardware (60 s):** two teammates lean on the railing slowly. The dashboard goes amber with a countdown, then red; the beacon flashes; the PA plays in Hindi.
3. **Surge (40 s):** switch to the simulator "Crowd surge wave". The zone goes red even though no single barricade is overloaded yet. That early warning is what makes it useful.
4. **Response (30 s):** a teammate's phone (steward page) buzzes; they tap **I'm on it** and the control room shows them responding. Press Stop entry + Open gates. Pressure drops and alerts clear.
5. **Scale (30 s):** switch to **National**: many venues on one screen. ~₹1,000 per node, clamps onto existing barricades, no internet needed, open API for any state's control room. Targets: Nashik Kumbh 2027, railway footbridges, temple queue complexes.
6. **Collapse (20 s):** press **Barricade collapse**: BARRICADE DOWN on the big screen, the node siren and every steward phone.
7. **Accountability (30 s):** open **Replay**, then **Incident report**: "CrushGuard warned 13 s before critical, steward responded, entry stopped 3 s after."
8. **Prevention (20 s, if asked):** the **Plan** tab shows the same venue was unsafe before the gates even opened.

## Planning figures used

- Standing crowd density: up to 2 people/m² comfortable (UK Purple Guide planning figure), 5 people/m² the upper
  limit for standing crowds, above 6 people/m² crowds become unstable ([G. Keith Still](https://www.gkstill.com/Support/crowd-density/CrowdDensity-1.html)).
- Flow through exits: 82 people per metre per minute on level ground, 66 on steps or slopes ([InCrowd Safety summary of UK guidance](https://incrowdsafety.co.uk/flow-rates-densities-and-the-maths/)).
- Heat index: US National Weather Service formula and categories. The risk multipliers per category are a CrushGuard design choice.

The planner is guidance for a first check, not a replacement for a professional crowd safety plan or local rules
(for example NDMA crowd-management guidelines).

## Credits

India map outline: [DataMeet India community](https://github.com/datameet/maps)
(`Country/india-composite.geojson`), CC BY 4.0, simplified for offline use.

## Honest positioning

Barrier load monitoring already exists abroad for steel concert barriers (e.g. Mojo Barriers,
EXE Flexa, Crowd Cushion). CrushGuard's contribution is making it **clamp-on, low-cost, and made
for India's improvised barricades and rope lines**, with surge-wave detection and multilingual
auto-response built in.

## Known limits / next steps

- Hindi and Tamil PA text in `config.json` should be checked by a native speaker.
- The simulator's crowd model is illustrative, not a validated crowd-dynamics model.
- Next: LoRa for kilometre-long routes (roadshows, Kumbh ghats), solar nodes, integration with
  city Integrated Command and Control Centres, camera density fusion.
