# Bolted Flange Calculator

Sizes and checks a preloaded bolted flange joint (for example a pressurized flange on a small rocket engine or pressure vessel) against the criteria of **NASA-STD-5020** (Requirements for Threaded Fastening Systems in Spaceflight Hardware), using **Shigley** joint-stiffness theory and the **VDI 2230** load-introduction factor. It reports a margin of safety for every failure mode, shows the full calculation trace with typeset equations, and exports a PDF report.

## Installation

Requires Python 3.10 or newer.

```
git clone https://github.com/am345riko/bolted-flange-calc.git
cd bolted-flange-calc
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running it

**GUI (recommended):**

```
python "bolted flange/bolted_flange_calc_v2.py"
```

**Command line:** edit the `build_inputs()` function at the bottom of `bolted flange/bolted_flange_calc.py` with your joint data, then:

```
python "bolted flange/bolted_flange_calc.py"
```

**Standalone app (no Python needed):** build a Windows executable with PyInstaller:

```
pip install pyinstaller
cd "bolted flange"
pyinstaller --noconfirm --windowed --name BoltedFlangeCalculator --add-data "assets;assets" bolted_flange_calc_v2.py
```

The app is created in `bolted flange/dist/BoltedFlangeCalculator/` — run `BoltedFlangeCalculator.exe` from that folder (the folder must stay together; zip it to share it).

## How to use it

1. Enter the joint data in the left panel. **Units are metric (mm, N, MPa) except chamber pressure (psi) and effective diameter (inches).**
2. Tick the optional analyses you need: installation torque, moment load, shear, thermal, fatigue, thread stripping.
3. Define the clamped member stack in the Layers table (one row per clamped part: modulus, thickness, and the frustum start diameter, normally 1.5× the bolt diameter).
4. Click **Calculate**. The Summary tab shows every margin with PASS/FAIL. The Calculation Trace tab shows every equation with the actual numbers substituted, so each result can be checked by hand.
5. **Generate PDF Report** saves the inputs, results, checks, and the full trace to a PDF. If the file name already exists, a time-stamped version is created instead of overwriting.

A margin of safety (MS) is acceptable when **MS ≥ 0**. Negative means the check fails.

## Theory and equations

The calculation runs in the order below. Section and equation numbers refer to NASA-STD-5020 (2012).

### 1. Separating load

The internal pressure pushes the flange faces apart. The total separating force is the pressure acting on the area enclosed by the seal:

$$P = p_c \cdot \frac{\pi}{4} D_{eff}^2$$

where $D_{eff}$ is the effective seal diameter (for an O-ring, the diameter at which the seal sits). Each of the $n$ bolts carries $P/n$, plus any extra applied axial force.

### 2. Joint stiffness

A preloaded bolt does not feel the full external load. The bolt and the clamped flanges act as springs in parallel: when an external load is applied, it splits between stretching the bolt further and relieving the compression in the flanges, in proportion to their stiffnesses.

Bolt stiffness (Shigley Eq. 8-17) treats the shank and the threaded portion inside the grip as springs in series:

$$k_b = \frac{A_d A_t E_b}{A_d l_t + A_t l_d}$$

Member stiffness models the compressed material in each flange as 30° cones ("frusta", Shigley Eq. 8-20), one frustum per half-layer, combined in series ($1/k_m = \sum 1/k_i$):

$$k_i = \frac{0.5774\,\pi E d}{\ln\dfrac{(1.155t + D - d)(D + d)}{(1.155t + D + d)(D - d)}}$$

The stiffness factor is the fraction of external load the bolt would take if the load were introduced at the bolt head and nut:

$$C = \frac{k_b}{k_b + k_m}$$

In reality the load enters the joint somewhere inside the clamped stack, which reduces the bolt's share further. The load-introduction factor $n$ (VDI 2230, NASA App. A.4) accounts for this: the bolt sees $nC$ of the external load, and the flange interface loses $(1-nC)$ of it. $n = 0.5$ is the usual assumption for loading planes at mid-thickness.

### 3. Preload and its uncertainty

Torque-controlled installation produces a scattered preload. NASA-STD-5020 brackets it (Eqs. 6-4, 6-5a):

$$P_{pi\text{-}max} = c_{max}(1+\Gamma)P_{pi\text{-}nom} \qquad P_{pi\text{-}min} = c_{min}(1-\Gamma)P_{pi\text{-}nom}$$

$\Gamma$ is the preload variation: 0.25 for bolts lubricated at assembly, 0.35 dry, unless test data says otherwise. $c_{max}/c_{min}$ cover the torque-wrench tolerance (a ±5 % torque spec gives 1.05/0.95). For joints that are **not** separation-critical, Eq. 6-5b allows the minimum-preload scatter to be relaxed by $\Gamma/\sqrt{n_{bolts}}$, because the scatter of the whole bolt pattern averages out.

The operating preload window then accounts for (Table 1):

- **Short-term relaxation**: 5 % of minimum preload is lost to embedment of surface asperities in all-metal joints.
- **Thermal change**: if bolt and flange expand differently, preload changes by $\Delta P_p = k_{eff}(\alpha_m - \alpha_b)\,\Delta T\, L_{grip}$, with $k_{eff} = k_b k_m/(k_b+k_m)$. Only the unfavorable direction is applied to each bound.

$$P_{p\text{-}max} = P_{pi\text{-}max} + \Delta P_{t\text{-}max} \qquad P_{p\text{-}min} = P_{pi\text{-}min} - P_{pr} - \Delta P_{t\text{-}min}$$

### 4. Moment load on the bolt pattern

An overturning moment $M$ on the flange loads the bolts linearly with distance from the neutral axis of the bolt circle. The most-loaded bolt sees:

$$\Delta F = \frac{4M}{n\,D_{bc}}$$

A prying factor ≥ 1 can amplify this if the flange lifts and levers on its outer edge.

### 5. Peak bolt load

The highest tension any bolt sees at limit load (reported for information and used in the thread-stripping check):

$$F_{b\text{-}max} = P_{p\text{-}max} + nC \cdot P_{bolt} + \Delta F_{moment}$$

### 6. Margins of safety

All NASA margins follow one form (Eq. 6-1):

$$MS = \frac{\text{allowable load}}{FF \cdot FS \cdot \text{limit load}} - 1$$

$FS$ is the factor of safety (defaults here: 1.4 ultimate, 1.25 yield per NASA-STD-5001) and $FF$ = 1.15 is a fitting factor covering load-path uncertainty. **Factors of safety are applied to the external applied load only — never to preload** (§4.1). That rule shapes the tension check below.

### 7. Ultimate and yield tension (§6.2.1, §6.3)

Whether preload matters for bolt rupture depends on which happens first as the applied load grows:

- Linearly projected separation load: $P'_{sep} = \dfrac{P_{p\text{-}max}}{1 - nC}$ (Eq. 6-10)
- Applied load at which the bolt reaches its ultimate allowable: $P'_{tu} = \dfrac{P_{tu\text{-}allow} - P_{p\text{-}max}}{nC}$ (Eq. 6-9)

If the joint separates first ($P'_{sep} < P'_{tu}$), the bolt takes the full applied load at rupture and preload drops out — the margin is simply $MS_u = \frac{P_{tu\text{-}allow}}{FF \cdot FS_u \cdot P_{tL}} - 1$ (Eq. 6-6). If the bolt would rupture first, the margin uses $P'_{tu}$ instead (Eq. 6-11). The yield check works the same way with $P_{ty\text{-}allow} = S_y A_t$ (Eqs. 6-19 to 6-22). Allowable loads come from the stress area: $P_{tu\text{-}allow} = S_u A_t$.

A separate informational check compares the peak bolt load against proof strength ($S_p A_t$) with no factors, to confirm installation preload does not yield the bolt.

### 8. Joint separation (§6.5)

A flange with a seal must never gap. NASA sets the separation load equal to the minimum preload, because stiffness-based predictions of the true separation load are not conservative (App. A.11):

$$MS_{sep} = \frac{P_{p\text{-}min}}{FF \cdot FS_{sep} \cdot P_{tL}} - 1 \qquad \text{(Eq. 6-23)}$$

$FS_{sep}$ = 1.4 for separation-critical joints where failure is catastrophic (Figure 1 of the standard). The calculator also shows the classical Shigley stiffness-based separation factor for information; the NASA check governs.

### 9. Shear and combined loading (§6.2.2, §6.2.3)

The shear allowable uses the von Mises relation $F_{su} = S_u/\sqrt{3}$ on the area actually in the shear plane: the minor-diameter thread area $A_m$ if threads are in the plane (Eq. 6-13), the full body area if not (Eq. 6-12). Friction is not credited, and preload is omitted (App. A.7 — test data show preload does not change the rupture load in shear).

Combined tension + shear must satisfy an interaction criterion using factored design loads:

$$\left(\frac{P_{su}}{P_{su\text{-}allow}}\right)^{2.5} + \left(\frac{P_{tu}}{P_{tu\text{-}allow}}\right)^{1.5} \le 1 \;\text{(body in plane, Eq. 6-15)} \qquad \left(\frac{P_{su}}{P_{su\text{-}allow}}\right)^{1.2} + \left(\frac{P_{tu}}{P_{tu\text{-}allow}}\right)^{2} \le 1 \;\text{(threads in plane, Eq. 6-17)}$$

### 10. Installation torque

The torque needed to reach the nominal preload uses the nut-factor relation $T = K \cdot D \cdot P_{pi}$ (App. A.2). $K$ ≈ 0.20 for dry steel, lower when lubricated; it should be measured for flight hardware.

### 11. Fatigue (Goodman)

For cyclic external loads, the bolt's alternating and mean stresses are:

$$\sigma_a = \frac{nC\,(F_{max} - F_{min})}{2A_t} \qquad \sigma_m = \frac{P_{p\text{-}max} + nC\,\bar{F}}{A_t}$$

Only the fraction $nC$ of the load cycle reaches the bolt — this is the main reason preloaded joints survive vibration. The Goodman safety factor is $n_f = \left(\sigma_a/S_e + \sigma_m/S_u\right)^{-1}$, acceptable when ≥ 1. $S_e$ must be the endurance limit of the *threaded* fastener (thread notch included), not the raw material value. Maximum preload is used for the mean stress because higher mean stress is worse.

### 12. Thread stripping

When a bolt threads into a tapped hole, the internal threads can shear out before the bolt breaks. The check uses the approximate engaged shear area $A_{strip} = 0.5\,\pi d L_e$ and the shear yield of the weaker member:

$$F_{strip} = 0.577\, S_{y,member} \cdot A_{strip} \ge FF \cdot FS_u \cdot F_{b\text{-}max}$$

Engagement length $L_e$ should be chosen so the bolt fails in tension before the threads strip (§5.6.1).

## Limitations

- Preload must be torque-controlled; the $\Gamma$ values assume that method.
- The default member model uses two identical frusta per layer. For stacks of several different layers, the frusta should expand through the stack; use the `frusta_override` input on `Layer` for those cases.
- The thread-stripping area is an approximation; use FED-STD-H28 shear areas for flight hardware.
- Bolt-to-bolt load distribution (e.g., from moments) uses a linear elastic bolt-circle model; unusual geometries need a finite-element load distribution.
- This tool supports analysis to NASA-STD-5020 but does not replace it. Read the standard before relying on the numbers.

## References

- NASA-STD-5020, *Requirements for Threaded Fastening Systems in Spaceflight Hardware*, 2012.
- NASA-STD-5001, *Structural Design and Test Factors of Safety for Spaceflight Hardware*.
- Shigley's Mechanical Engineering Design (joint stiffness, Eqs. 8-17 and 8-20).
- VDI 2230, *Systematic Calculation of High Duty Bolted Joints*.
