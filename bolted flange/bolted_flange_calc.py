from __future__ import annotations

"""
Bolted Flange Calculator — NASA-STD-5020 / Shigley / VDI 2230

Implements:
  - Shigley Eq. 8-17 bolt stiffness, Eq. 8-20 frustum member stiffness
  - NASA-STD-5020 preload variation (App A.2), relaxation (Table 1),
    tension margins (Sec 6.2.1 / 6.3), separation (Sec 6.5),
    shear (App A.8), tension+shear interaction (Sec 6.2.3),
    fitting factor FF (App A.12)
  - VDI 2230 load introduction factor n
  - Goodman fatigue criterion
  - Thread stripping (Shigley 8-5)

Margin of safety convention (throughout): MS = Allow/(FF·FS·Applied) − 1
"""

from dataclasses import dataclass, field
import math
import re
from typing import List, Optional, Dict, Any, Tuple

from report_pdf import build_flange_pdf_report


# =============================================================================
# Helpers / unit utilities
# =============================================================================

def area_circle(d_mm: float) -> float:
    return math.pi * (d_mm ** 2) / 4.0


def area_ring(Do_mm: float, Di_mm: float) -> float:
    if Do_mm <= 0 or Di_mm < 0:
        raise ValueError("Invalid ring diameters.")
    if Do_mm <= Di_mm:
        raise ValueError("Need Do > Di for ring area.")
    return math.pi / 4.0 * (Do_mm * Do_mm - Di_mm * Di_mm)


def psi_to_MPa(psi: float) -> float:
    return psi * 0.006894757293168361


def in_to_mm(x_in: float) -> float:
    return x_in * 25.4


def subify(s: str) -> str:
    return re.sub(r"([A-Za-z]+)_([A-Za-z0-9]+)", r"\1<sub>\2</sub>", s)


# =============================================================================
# Calculation trace logger
# =============================================================================

@dataclass
class TraceEntry:
    """One calculation-trace step: plain text plus optional typeset LaTeX."""
    plain: str
    latex: Optional[str] = None


@dataclass
class CalcTrace:
    entries: List[TraceEntry] = field(default_factory=list)

    def add(self, plain: str, latex: Optional[str] = None) -> None:
        # Store the RAW trace string. subify() (which inserts HTML <sub> tags)
        # is applied only at PDF-generation time, not here, so that plain-text
        # consumers (e.g. a GUI trace pane) see clean text, not literal tags.
        self.entries.append(TraceEntry(plain=plain, latex=latex))

    @property
    def lines(self) -> List[str]:
        """Backward-compatible plain-text view of the trace (CLI, PDF, GUI fallback)."""
        return [e.plain for e in self.entries]


# =============================================================================
# Shigley / joint stiffness equations
# =============================================================================

def kb_shigley_8_17(At_mm2: float, Ad_mm2: float, E_MPa: float, lt_mm: float, ld_mm: float) -> float:
    """Bolt stiffness in clamped zone (N/mm), Shigley Eq. 8-17."""
    denom = (Ad_mm2 * lt_mm) + (At_mm2 * ld_mm)
    if denom <= 0:
        raise ValueError("Invalid l_t / l_d; denominator <= 0")
    return (Ad_mm2 * At_mm2 * E_MPa) / denom


def k_frustum_shigley_8_20(E_MPa: float, d_hole_mm: float, t_mm: float, D_interface_mm: float) -> float:
    """Stiffness of ONE conical frustum (N/mm), Shigley Eq. 8-20 for α=30°."""
    d = d_hole_mm
    D = D_interface_mm
    t = t_mm
    if d <= 0 or D <= 0 or t <= 0:
        raise ValueError("d, D, and t must be > 0")
    if D <= d:
        raise ValueError(f"Need D > d for frustum model. Got D={D:.3f} mm, d={d:.3f} mm")
    num = (1.155 * t + D - d) * (D + d)
    den = (1.155 * t + D + d) * (D - d)
    if num <= 0 or den <= 0:
        raise ValueError("Invalid geometry caused nonpositive log argument.")
    ln_term = math.log(num / den)
    if ln_term <= 0:
        raise ValueError("Log term <= 0; check geometry inputs (D, d, t).")
    return (0.5774 * math.pi * E_MPa * d) / ln_term


def km_series(frusta_k: List[float]) -> float:
    """Combine member frusta stiffnesses in series: 1/k_m = Σ(1/k_i)."""
    inv_sum = 0.0
    for k in frusta_k:
        if k <= 0:
            raise ValueError("All frusta stiffnesses must be > 0")
        inv_sum += 1.0 / k
    if inv_sum <= 0:
        raise ValueError("Invalid frusta list; inv_sum <= 0")
    return 1.0 / inv_sum


def bolt_group_deltaF_from_moment(M_Nmm: float, n_bolts: int, bolt_circle_diameter_mm: float) -> float:
    """Max bolt force from bending moment.

    For a linear (planar) force distribution over a bolt circle, the peak
    bolt force is F_max = M·r / Σ(r·cosθ_i)². With bolts evenly spaced,
    Σcos²θ_i = n/2, so F_max = M·r/(n·r²/2) = 2M/(n·r) = 4M/(n·D_bc),
    r = BCD/2.
    """
    if M_Nmm <= 0:
        return 0.0
    if n_bolts <= 0:
        raise ValueError("n_bolts must be > 0")
    r = bolt_circle_diameter_mm / 2.0
    if r <= 0:
        raise ValueError("bolt_circle_diameter_mm must be > 0")
    return 2.0 * M_Nmm / (n_bolts * r)


# =============================================================================
# Data models
# =============================================================================

@dataclass
class Layer:
    """One clamped member layer. Default: two identical frusta each t/2 thick."""
    name: str
    E_MPa: float
    thickness_mm: float
    D_interface_mm: float
    frusta_override: Optional[List[Tuple[float, float]]] = None


@dataclass
class MomentLoad:
    enabled: bool = False
    M_Nmm: float = 0.0
    bolt_circle_diameter_mm: float = 0.0
    prying_factor: float = 1.0
    note: str = ""


@dataclass
class ShearLoad:
    enabled: bool = False
    F_shear_per_bolt_N: float = 0.0
    at_threads: bool = False  # True = shear plane through threads; False = full shank


@dataclass
class ThermalLoad:
    enabled: bool = False
    alpha_bolt_per_C: float = 11.7e-6    # bolt CTE (1/°C); steel ~11.7e-6
    alpha_member_per_C: float = 23.1e-6  # member CTE (1/°C); aluminum ~23.1e-6
    dT_C: float = 0.0                    # temperature change (°C); positive = hot
    L_grip_mm: float = 0.0               # total grip length for thermal calc (mm)


@dataclass
class FatigueCheck:
    enabled: bool = False
    F_ext_max_N: float = 0.0   # max external cyclic load per bolt (N)
    F_ext_min_N: float = 0.0   # min external cyclic load per bolt (N)
    Se_MPa: float = 0.0        # bolt endurance limit (MPa)


@dataclass
class ThreadStrip:
    enabled: bool = False
    L_engage_mm: float = 0.0      # thread engagement length (mm)
    Sy_member_MPa: float = 0.0    # yield strength of internal thread member (MPa)


@dataclass
class Inputs:
    # ---- Separating load ----
    Pc_psi: float                  # Internal/chamber pressure used as separating load basis (psi)
    effective_diameter_in: float   # Effective pressure diameter (in)
    n_bolts: int                   # Number of bolts sharing the separating load

    # ---- Bolt geometry ----
    d_bolt_mm: float               # Nominal bolt diameter (mm)
    d_hole_mm: float               # Clearance hole diameter (mm) for frustum model
    At_mm2: float                  # Tensile stress area A_t (mm²)
    Ad_mm2: float                  # Shank area A_d (mm²)
    Eb_MPa: float                  # Bolt Young's modulus E_b (MPa)
    lt_mm: float                   # Threaded length within grip l_t (mm)
    ld_mm: float                   # Unthreaded length within grip l_d (mm)

    # ---- Strength allowables (all required for full aerospace analysis) ----
    Sp_MPa: float                  # Proof strength S_p (MPa)
    Sy_MPa: float                  # Yield strength S_y (MPa)
    Su_MPa: float                  # Ultimate tensile strength S_u (MPa)

    # ---- NASA preload ----
    Ppi_nom_N: float               # Nominal installed preload per bolt (N)

    # ---- Optional with defaults ----
    Gamma_p: float = 0.25          # Preload variation factor Γp; NASA App A.2 Eq A.2-1/2
    L_min_total_N: float = 0.0     # Minimum total joint clamp load required (gasket/friction) (N)
    target_sep_factor: float = 1.4 # Required FoS against separation (NASA Fig. 1 catastrophic)

    # NASA-STD-5001 factors of safety (unmanned spacecraft defaults)
    FSu: float = 1.4               # Ultimate factor of safety
    FSy: float = 1.25              # Yield factor of safety
    FF: float = 1.15               # Fitting factor (NASA App A.12)

    # Load introduction factor n (VDI 2230 §R3 / NASA App A.4)
    # 0 = load at bolt plane (max bolt load change); 1 = load at interface (reduced change)
    n_load_intro: float = 0.5      # Effective bolt load fraction = n·C

    # Short-term relaxation / embedment (NASA Table 1: 5% all-metal joints)
    Ppr_fraction: float = 0.05

    # Additional axial external force not from pressure (positive = separating)
    F_axial_extra_N: float = 0.0

    # Nut factor K for installation torque: T = K·d·Fi (set None to skip)
    K_nut: Optional[float] = None

    # Minimum minor-diameter thread area A_m (mm²), NASA Eq 6-13.
    # Required (must be > 0) only if shear.at_threads is True.
    Am_mm2: Optional[float] = None

    # Torque-tolerance factors for preload bounds, NASA Eqs 6-4 / 6-5a / 6-5b
    c_max: float = 1.0
    c_min: float = 1.0

    # True: joint is separation-critical, use Eq 6-5a for Pp_min in separation
    # checks. False: use the relaxed Eq 6-5b (Γ/√n_bolts) for separation only.
    separation_critical: bool = True

    # Optional load cases
    moment: MomentLoad = field(default_factory=MomentLoad)
    shear: ShearLoad = field(default_factory=ShearLoad)
    thermal: ThermalLoad = field(default_factory=ThermalLoad)
    fatigue: FatigueCheck = field(default_factory=FatigueCheck)
    thread_strip: ThreadStrip = field(default_factory=ThreadStrip)


@dataclass
class Results:
    # Separating load
    P_total_N: float
    P_per_bolt_N: float

    # Stiffness
    kb_N_per_mm: float
    km_N_per_mm: float
    C: float
    nC: float               # effective bolt load fraction = n_load_intro * C

    # Installed preload bounds
    Ppi_nom_N: float
    Ppi_min_N: float
    Ppi_max_N: float

    # Operating preload bounds (after relaxation + thermal)
    Ppr_N: float            # relaxation / embedment loss
    dPp_thermal_N: float    # thermal preload change (positive = increase)
    Pp_min_N: float         # minimum operating preload (Eq 6-5a)
    Pp_max_N: float         # maximum operating preload
    Pp_min_sep_N: float     # minimum operating preload used for separation checks
                             # (Eq 6-5a if separation_critical, else Eq 6-5b)

    # Moment
    dF_moment_nom_N: float  # nominal moment bolt force (before prying)
    dF_moment_N: float      # prying-amplified moment bolt force

    # Peak bolt tension (INFORMATIONAL peak-bolt-load / preload check only —
    # NOT a NASA-STD-5020 margin; factors of safety do not apply to preload
    # per §4.1). Used only for MS_proof below.
    Fb_max_N: float

    # Strength allowables
    F_proof_allow_N: float
    Ptu_allow_N: float
    Pty_allow_N: float

    # Tension margins
    MS_proof: float          # informational: F_proof_allow/Fb_max − 1 (peak bolt load incl. preload)
    MSu_tension: float       # NASA Sec 6.2.1 / 6.3 linear-theory margin (Eq 6-6/6-9/6-10/6-11)
    MSy_tension: float       # NASA Sec 6.3 linear-theory margin (Eq 6-19/6-21/6-22)

    # NASA linear-theory tension intermediates (Sec 6.2.1)
    P_sep_prime_N: float     # Eq 6-10: preload at which joint separates under applied load
    P_tu_prime_N: float      # Eq 6-9: applied load at which bolt ruptures
    sep_before_rupture: bool # True if separation occurs before rupture (governs MSu_tension)

    # Separation (informational Shigley check + governing NASA check, both use Pp_min_sep)
    Ppi_req_min_N: float    # min preload to prevent separation (Shigley/stiffness)
    FoS_sep: float          # Shigley stiffness-based FoS = Pp_min_sep/Ppi_req_min
    MS_sep: float           # informational: FoS_sep/target_sep_factor − 1
    MSsep_NASA: float       # NASA STD-5020 A.11 governing: Pp_min_sep/(FF·FSsep·F_sep) − 1

    # Helper: estimated bolt count to meet target separation factor
    required_bolts_for_target_sep: int

    # Optional outputs
    install_torque_Nmm: Optional[float] = None

    Psu_allow_N: Optional[float] = None
    MSu_shear: Optional[float] = None
    MS_interaction: Optional[float] = None

    sigma_a_MPa: Optional[float] = None
    sigma_m_MPa: Optional[float] = None
    fatigue_safety_factor: Optional[float] = None

    F_strip_N: Optional[float] = None
    MS_strip: Optional[float] = None


# =============================================================================
# Core computation
# =============================================================================

def compute(inp: Inputs, layers: List[Layer], trace: Optional[CalcTrace] = None) -> Results:
    if inp.n_bolts <= 0:
        raise ValueError("n_bolts must be > 0")
    if inp.Ppi_nom_N <= 0:
        raise ValueError("Ppi_nom_N must be > 0")
    if not (0.0 <= inp.Gamma_p < 1.0):
        raise ValueError("Gamma_p must be in [0, 1). Typical: 0.10–0.30")
    if inp.target_sep_factor <= 0:
        raise ValueError("target_sep_factor must be > 0")
    if inp.L_min_total_N < 0:
        raise ValueError("L_min_total_N must be >= 0")
    if not (0.0 <= inp.n_load_intro <= 1.0):
        raise ValueError("n_load_intro must be in [0, 1]")
    if not (0.0 <= inp.Ppr_fraction < 1.0):
        raise ValueError("Ppr_fraction must be in [0, 1)")
    if inp.Sy_MPa <= 0 or inp.Su_MPa <= 0 or inp.Sp_MPa <= 0:
        raise ValueError("Strength values (Sp, Sy, Su) must be > 0")
    if inp.At_mm2 <= 0:
        raise ValueError("At_mm2 must be > 0")
    if inp.Ad_mm2 <= 0:
        raise ValueError("Ad_mm2 must be > 0")
    if inp.d_bolt_mm <= 0:
        raise ValueError("d_bolt_mm must be > 0")
    if inp.Eb_MPa <= 0:
        raise ValueError("Eb_MPa must be > 0")
    if inp.lt_mm < 0 or inp.ld_mm < 0:
        raise ValueError("lt_mm and ld_mm must each be >= 0")
    if (inp.lt_mm + inp.ld_mm) <= 0:
        raise ValueError("lt_mm + ld_mm must be > 0")
    if inp.c_max <= 0 or inp.c_min <= 0:
        raise ValueError("c_max and c_min must be > 0")

    def tadd(s: str, latex: Optional[str] = None) -> None:
        if trace is not None:
            trace.add(s, latex)

    # -------------------------------------------------------------------------
    # 1) Separating load from pressure + optional extra axial
    # -------------------------------------------------------------------------
    Pc_MPa = psi_to_MPa(inp.Pc_psi)
    D_eff_mm = in_to_mm(inp.effective_diameter_in)
    A_mm2 = area_circle(D_eff_mm)
    P_pressure_N = Pc_MPa * A_mm2
    F_axial_total_N = P_pressure_N + inp.F_axial_extra_N
    P_per_bolt_N = F_axial_total_N / inp.n_bolts

    tadd(
        f"Pc = {inp.Pc_psi:.6g} psi -> Pc = {Pc_MPa:.6g} MPa",
        latex=(
            f"P_c = {inp.Pc_psi:.6g}\\ \\mathrm{{psi}} \\cdot 6.894757\\times10^{{-3}} "
            f"= {Pc_MPa:.6g}\\ \\mathrm{{MPa}}"
        ),
    )
    tadd(
        f"D_eff = {inp.effective_diameter_in:.6g} in -> D_eff = {D_eff_mm:.6g} mm",
        latex=(
            f"D_{{eff}} = {inp.effective_diameter_in:.6g}\\ \\mathrm{{in}} \\cdot 25.4 "
            f"= {D_eff_mm:.6g}\\ \\mathrm{{mm}}"
        ),
    )
    tadd(
        f"A = (π/4)·D_eff^2 = {A_mm2:.6g} mm^2",
        latex=(
            f"A = \\frac{{\\pi}}{{4}} D_{{eff}}^2 = \\frac{{\\pi}}{{4}}\\left({D_eff_mm:.6g}\\right)^2 "
            f"= {A_mm2:.6g}\\ \\mathrm{{mm^2}}"
        ),
    )
    tadd(
        f"P_pressure = Pc·A = {P_pressure_N:.6g} N",
        latex=f"P_{{pressure}} = P_c \\cdot A = {Pc_MPa:.6g} \\cdot {A_mm2:.6g} = {P_pressure_N:.6g}\\ \\mathrm{{N}}",
    )
    if inp.F_axial_extra_N != 0.0:
        tadd(
            f"F_axial_extra = {inp.F_axial_extra_N:.6g} N",
            latex=f"F_{{axial,extra}} = {inp.F_axial_extra_N:.6g}\\ \\mathrm{{N}}",
        )
    tadd(
        f"F_axial_total = {F_axial_total_N:.6g} N",
        latex=(
            f"F_{{axial,total}} = P_{{pressure}} + F_{{axial,extra}} = "
            f"{P_pressure_N:.6g} + {inp.F_axial_extra_N:.6g} = {F_axial_total_N:.6g}\\ \\mathrm{{N}}"
        ),
    )
    tadd(
        f"P_per_bolt = F_axial_total/N_b = {P_per_bolt_N:.6g} N",
        latex=(
            f"P_{{per\\text{{-}}bolt}} = \\frac{{F_{{axial,total}}}}{{N_b}} = "
            f"\\frac{{{F_axial_total_N:.6g}}}{{{inp.n_bolts}}} = {P_per_bolt_N:.6g}\\ \\mathrm{{N}}"
        ),
    )

    # -------------------------------------------------------------------------
    # 2) Joint stiffness split: C = kb/(kb+km), nC = n·C
    # -------------------------------------------------------------------------
    kb = kb_shigley_8_17(inp.At_mm2, inp.Ad_mm2, inp.Eb_MPa, inp.lt_mm, inp.ld_mm)
    tadd(
        f"k_b = (A_d·A_t·E_b)/(A_d·l_t + A_t·l_d) = "
        f"({inp.Ad_mm2:.6g}·{inp.At_mm2:.6g}·{inp.Eb_MPa:.6g})/"
        f"({inp.Ad_mm2:.6g}·{inp.lt_mm:.6g} + {inp.At_mm2:.6g}·{inp.ld_mm:.6g})"
        f" = {kb:.6g} N/mm",
        latex=(
            f"k_b = \\frac{{A_d A_t E_b}}{{A_d l_t + A_t l_d}} = "
            f"\\frac{{{inp.Ad_mm2:.6g} \\cdot {inp.At_mm2:.6g} \\cdot {inp.Eb_MPa:.6g}}}"
            f"{{{inp.Ad_mm2:.6g} \\cdot {inp.lt_mm:.6g} + {inp.At_mm2:.6g} \\cdot {inp.ld_mm:.6g}}} "
            f"= {kb:.6g}\\ \\mathrm{{N/mm}}"
        ),
    )

    frusta_ks: List[float] = []
    for layer in layers:
        if layer.frusta_override:
            for (t_i, D_i) in layer.frusta_override:
                k_i = k_frustum_shigley_8_20(layer.E_MPa, inp.d_hole_mm, t_i, D_i)
                frusta_ks.append(k_i)
                tadd(
                    f"k_frustum({layer.name}): t={t_i:.6g}, D={D_i:.6g} -> {k_i:.6g} N/mm",
                    latex=(
                        f"k_{{frustum,\\text{{{layer.name}}}}} = "
                        f"\\frac{{0.5774\\,\\pi E d}}"
                        f"{{\\ln\\left[\\frac{{(1.155t+D-d)(D+d)}}{{(1.155t+D+d)(D-d)}}\\right]}} "
                        f"\\Big|_{{t={t_i:.6g},\\,D={D_i:.6g}}} = {k_i:.6g}\\ \\mathrm{{N/mm}}"
                    ),
                )
        else:
            t_half = layer.thickness_mm / 2.0
            k_i = k_frustum_shigley_8_20(layer.E_MPa, inp.d_hole_mm, t_half, layer.D_interface_mm)
            frusta_ks.extend([k_i, k_i])
            tadd(
                f"k_frustum({layer.name}) x2: t={t_half:.6g}, D={layer.D_interface_mm:.6g} -> {k_i:.6g} N/mm each",
                latex=(
                    f"k_{{frustum,\\text{{{layer.name}}}}} \\times 2 = "
                    f"\\frac{{0.5774\\,\\pi E d}}"
                    f"{{\\ln\\left[\\frac{{(1.155t+D-d)(D+d)}}{{(1.155t+D+d)(D-d)}}\\right]}} "
                    f"\\Big|_{{t={t_half:.6g},\\,D={layer.D_interface_mm:.6g}}} = {k_i:.6g}\\ \\mathrm{{N/mm\\ each}}"
                ),
            )

    km = km_series(frusta_ks)
    tadd(
        f"k_m = 1/Σ(1/k_i) = {km:.6g} N/mm",
        latex=f"k_m = \\frac{{1}}{{\\sum_i \\frac{{1}}{{k_i}}}} = {km:.6g}\\ \\mathrm{{N/mm}}",
    )

    C = kb / (kb + km)
    nC = inp.n_load_intro * C
    tadd(
        f"C = k_b/(k_b+k_m) = {C:.6g}",
        latex=f"C = \\frac{{k_b}}{{k_b+k_m}} = \\frac{{{kb:.6g}}}{{{kb:.6g}+{km:.6g}}} = {C:.6g}",
    )
    tadd(
        f"n = {inp.n_load_intro:.6g}  (load introduction factor, VDI 2230)",
        latex=f"n = {inp.n_load_intro:.6g}\\ \\text{{(load introduction factor, VDI 2230)}}",
    )
    tadd(
        f"nC = n·C = {nC:.6g}  (effective bolt load fraction)",
        latex=f"nC = n \\cdot C = {inp.n_load_intro:.6g} \\cdot {C:.6g} = {nC:.6g}",
    )

    # -------------------------------------------------------------------------
    # 3) Moment-induced bolt force (bug fix: track nom and prying separately)
    # -------------------------------------------------------------------------
    dF_moment_nom = 0.0
    dF_moment = 0.0
    if inp.moment.enabled:
        if inp.moment.bolt_circle_diameter_mm <= 0:
            raise ValueError("Moment enabled but bolt_circle_diameter_mm <= 0")
        if inp.moment.prying_factor < 1.0:
            raise ValueError("prying_factor must be >= 1.0")
        dF_moment_nom = bolt_group_deltaF_from_moment(
            inp.moment.M_Nmm, inp.n_bolts, inp.moment.bolt_circle_diameter_mm
        )
        dF_moment = inp.moment.prying_factor * dF_moment_nom
        r = inp.moment.bolt_circle_diameter_mm / 2.0
        tadd(
            f"ΔF_moment_nom = 2M/(N_b·r) = 4M/(N_b·D_bc) = "
            f"2·{inp.moment.M_Nmm:.6g}/({inp.n_bolts}·{r:.6g}) = {dF_moment_nom:.6g} N",
            latex=(
                f"\\Delta F_{{moment,nom}} = \\frac{{2M}}{{N_b r}} = \\frac{{4M}}{{N_b D_{{bc}}}} = "
                f"\\frac{{2 \\cdot {inp.moment.M_Nmm:.6g}}}{{{inp.n_bolts} \\cdot {r:.6g}}} "
                f"= {dF_moment_nom:.6g}\\ \\mathrm{{N}}"
            ),
        )
        tadd(
            f"ΔF_moment = prying_factor·ΔF_nom = {inp.moment.prying_factor:.6g}·{dF_moment_nom:.6g} = {dF_moment:.6g} N",
            latex=(
                f"\\Delta F_{{moment}} = \\phi_{{pry}} \\cdot \\Delta F_{{moment,nom}} = "
                f"{inp.moment.prying_factor:.6g} \\cdot {dF_moment_nom:.6g} = {dF_moment:.6g}\\ \\mathrm{{N}}"
            ),
        )
    else:
        tadd("ΔF_moment = 0 (moment disabled)")

    # -------------------------------------------------------------------------
    # 4) NASA preload bounds: Ppi_min = c_min·Ppi(1−Γ) [Eq 6-5a],
    #    Ppi_max = c_max·Ppi(1+Γ) [Eq 6-4]
    # -------------------------------------------------------------------------
    Ppi_nom = inp.Ppi_nom_N
    Ppi_min = inp.c_min * Ppi_nom * (1.0 - inp.Gamma_p)
    Ppi_max = inp.c_max * Ppi_nom * (1.0 + inp.Gamma_p)
    tadd(
        f"Ppi_min = c_min·Ppi·(1−Γ_p) = {inp.c_min:.4g}·{Ppi_nom:.6g}·(1−{inp.Gamma_p:.4g}) = {Ppi_min:.6g} N  [Eq 6-5a]",
        latex=(
            f"P_{{pi,min}} = c_{{min}} P_{{pi}} (1-\\Gamma_p) = "
            f"{inp.c_min:.4g} \\cdot {Ppi_nom:.6g} \\cdot (1-{inp.Gamma_p:.4g}) "
            f"= {Ppi_min:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-5a]}}"
        ),
    )
    tadd(
        f"Ppi_max = c_max·Ppi·(1+Γ_p) = {inp.c_max:.4g}·{Ppi_nom:.6g}·(1+{inp.Gamma_p:.4g}) = {Ppi_max:.6g} N  [Eq 6-4]",
        latex=(
            f"P_{{pi,max}} = c_{{max}} P_{{pi}} (1+\\Gamma_p) = "
            f"{inp.c_max:.4g} \\cdot {Ppi_nom:.6g} \\cdot (1+{inp.Gamma_p:.4g}) "
            f"= {Ppi_max:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-4]}}"
        ),
    )

    # -------------------------------------------------------------------------
    # 5) Thermal preload change: ΔPp = k_eff·(α_m − α_b)·ΔT·L_grip
    # -------------------------------------------------------------------------
    dPp_thermal = 0.0
    if inp.thermal.enabled:
        if inp.thermal.L_grip_mm <= 0:
            raise ValueError("Thermal enabled but L_grip_mm <= 0")
        k_eff = (kb * km) / (kb + km)
        dPp_thermal = (
            k_eff
            * (inp.thermal.alpha_member_per_C - inp.thermal.alpha_bolt_per_C)
            * inp.thermal.dT_C
            * inp.thermal.L_grip_mm
        )
        tadd(
            f"k_eff = k_b·k_m/(k_b+k_m) = {k_eff:.6g} N/mm",
            latex=f"k_{{eff}} = \\frac{{k_b k_m}}{{k_b+k_m}} = \\frac{{{kb:.6g} \\cdot {km:.6g}}}{{{kb:.6g}+{km:.6g}}} = {k_eff:.6g}\\ \\mathrm{{N/mm}}",
        )
        tadd(
            f"ΔPp_thermal = k_eff·(α_m−α_b)·ΔT·L_grip = "
            f"{k_eff:.4g}·({inp.thermal.alpha_member_per_C:.3e}−{inp.thermal.alpha_bolt_per_C:.3e})"
            f"·{inp.thermal.dT_C:.4g}·{inp.thermal.L_grip_mm:.4g} = {dPp_thermal:.6g} N",
            latex=(
                f"\\Delta P_{{p,thermal}} = k_{{eff}} (\\alpha_m - \\alpha_b) \\Delta T\\, L_{{grip}} = "
                f"{k_eff:.4g} \\cdot ({inp.thermal.alpha_member_per_C:.3e} - {inp.thermal.alpha_bolt_per_C:.3e}) "
                f"\\cdot {inp.thermal.dT_C:.4g} \\cdot {inp.thermal.L_grip_mm:.4g} = {dPp_thermal:.6g}\\ \\mathrm{{N}}"
            ),
        )
    else:
        tadd("ΔPp_thermal = 0 (thermal disabled)")

    # -------------------------------------------------------------------------
    # 6) Short-term relaxation + operating preload bounds
    #    Ppr = Ppr_fraction·Ppi_min  (NASA Table 1: 5% all-metal)
    #    Pp_min = Ppi_min − Ppr + thermal if it lowers preload (conservative low)
    #    Pp_max = Ppi_max + thermal if it raises preload (conservative high)
    # -------------------------------------------------------------------------
    Ppr = inp.Ppr_fraction * Ppi_min
    Pp_min = Ppi_min - Ppr + min(0.0, dPp_thermal)
    Pp_max = Ppi_max + max(0.0, dPp_thermal)
    tadd(
        f"Ppr = Ppr_fraction·Ppi_min = {inp.Ppr_fraction:.4g}·{Ppi_min:.6g} = {Ppr:.6g} N",
        latex=f"P_{{pr}} = f_{{Ppr}} \\cdot P_{{pi,min}} = {inp.Ppr_fraction:.4g} \\cdot {Ppi_min:.6g} = {Ppr:.6g}\\ \\mathrm{{N}}",
    )
    tadd(
        f"Pp_min = Ppi_min − Ppr + min(0, ΔPp_thermal) = {Pp_min:.6g} N",
        latex=(
            f"P_{{p,min}} = P_{{pi,min}} - P_{{pr}} + \\min(0,\\Delta P_{{p,thermal}}) = "
            f"{Ppi_min:.6g} - {Ppr:.6g} + \\min(0,{dPp_thermal:.6g}) = {Pp_min:.6g}\\ \\mathrm{{N}}"
        ),
    )
    tadd(
        f"Pp_max = Ppi_max + max(0, ΔPp_thermal) = {Pp_max:.6g} N",
        latex=(
            f"P_{{p,max}} = P_{{pi,max}} + \\max(0,\\Delta P_{{p,thermal}}) = "
            f"{Ppi_max:.6g} + \\max(0,{dPp_thermal:.6g}) = {Pp_max:.6g}\\ \\mathrm{{N}}"
        ),
    )

    # -------------------------------------------------------------------------
    # 6b) Separation-specific minimum preload [NASA Eq 6-5b]
    #     If the joint is NOT separation-critical, a relaxed minimum preload
    #     (using Γ/√n_bolts instead of Γ) may be used for separation checks
    #     ONLY. Fatigue and all other margins keep using Pp_min (Eq 6-5a).
    # -------------------------------------------------------------------------
    if inp.separation_critical:
        Pp_min_sep = Pp_min
        tadd("separation_critical = True -> Pp_min_sep = Pp_min  [Eq 6-5a]")
    else:
        Ppi_min_sep = inp.c_min * Ppi_nom * (1.0 - inp.Gamma_p / math.sqrt(inp.n_bolts))
        Ppr_sep = inp.Ppr_fraction * Ppi_min_sep
        Pp_min_sep = Ppi_min_sep - Ppr_sep + min(0.0, dPp_thermal)
        tadd(
            f"Ppi_min_sep = c_min·Ppi·(1−Γ_p/√N_b) = "
            f"{inp.c_min:.4g}·{Ppi_nom:.6g}·(1−{inp.Gamma_p:.4g}/√{inp.n_bolts}) = {Ppi_min_sep:.6g} N  [Eq 6-5b]",
            latex=(
                f"P_{{pi,min\\text{{-}}sep}} = c_{{min}} P_{{pi}} \\left(1-\\frac{{\\Gamma_p}}{{\\sqrt{{N_b}}}}\\right) = "
                f"{inp.c_min:.4g} \\cdot {Ppi_nom:.6g} \\cdot \\left(1-\\frac{{{inp.Gamma_p:.4g}}}{{\\sqrt{{{inp.n_bolts}}}}}\\right) "
                f"= {Ppi_min_sep:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-5b]}}"
            ),
        )
        tadd(
            f"Pp_min_sep = Ppi_min_sep − Ppr_fraction·Ppi_min_sep + min(0, ΔPp_thermal) = {Pp_min_sep:.6g} N"
            f"  (separation checks only)",
            latex=(
                f"P_{{p,min\\text{{-}}sep}} = P_{{pi,min\\text{{-}}sep}} - f_{{Ppr}} P_{{pi,min\\text{{-}}sep}} "
                f"+ \\min(0,\\Delta P_{{p,thermal}}) = {Pp_min_sep:.6g}\\ \\mathrm{{N}}"
            ),
        )

    # -------------------------------------------------------------------------
    # 7) Peak bolt tension (most-loaded bolt) — INFORMATIONAL peak-bolt-load /
    #    preload check only. This is NOT a NASA-STD-5020 margin quantity
    #    (factors of safety do not apply to fastener preload per §4.1); it is
    #    used only to report MS_proof (an informational proof-load check).
    #    Fb_max = Pp_max + nC·P_per_bolt + ΔF_moment  (prying applied directly)
    # -------------------------------------------------------------------------
    Fb_max = Pp_max + nC * P_per_bolt_N + dF_moment
    tadd(
        f"F_b_max (informational peak bolt load) = Pp_max + nC·P_per_bolt + ΔF_moment = "
        f"{Pp_max:.6g} + {nC:.6g}·{P_per_bolt_N:.6g} + {dF_moment:.6g} = {Fb_max:.6g} N",
        latex=(
            f"F_{{b,max}} = P_{{p,max}} + nC \\cdot P_{{per\\text{{-}}bolt}} + \\Delta F_{{moment}} = "
            f"{Pp_max:.6g} + {nC:.6g} \\cdot {P_per_bolt_N:.6g} + {dF_moment:.6g} = {Fb_max:.6g}\\ \\mathrm{{N}}"
        ),
    )

    # -------------------------------------------------------------------------
    # 8) Strength allowables
    # -------------------------------------------------------------------------
    F_proof_allow = inp.Sp_MPa * inp.At_mm2
    Ptu_allow = inp.Su_MPa * inp.At_mm2
    Pty_allow = inp.Sy_MPa * inp.At_mm2
    tadd(
        f"F_proof_allow = S_p·A_t = {inp.Sp_MPa:.6g}·{inp.At_mm2:.6g} = {F_proof_allow:.6g} N",
        latex=f"F_{{proof,allow}} = S_p A_t = {inp.Sp_MPa:.6g} \\cdot {inp.At_mm2:.6g} = {F_proof_allow:.6g}\\ \\mathrm{{N}}",
    )
    tadd(
        f"Ptu_allow = S_u·A_t = {inp.Su_MPa:.6g}·{inp.At_mm2:.6g} = {Ptu_allow:.6g} N",
        latex=f"P_{{tu,allow}} = S_u A_t = {inp.Su_MPa:.6g} \\cdot {inp.At_mm2:.6g} = {Ptu_allow:.6g}\\ \\mathrm{{N}}",
    )
    tadd(
        f"Pty_allow = S_y·A_t = {inp.Sy_MPa:.6g}·{inp.At_mm2:.6g} = {Pty_allow:.6g} N",
        latex=f"P_{{ty,allow}} = S_y A_t = {inp.Sy_MPa:.6g} \\cdot {inp.At_mm2:.6g} = {Pty_allow:.6g}\\ \\mathrm{{N}}",
    )

    # -------------------------------------------------------------------------
    # 9) Tension margins of safety — NASA-STD-5020 linear-theory procedure
    #    [Sec 6.2.1 (ultimate), Sec 6.3 (yield)]. Factors of safety do NOT
    #    apply to preload (§4.1), so preload is separated from the applied
    #    load via the "prime" loads P_sep', P_tu' (and P_ty').
    #
    #    PtL = applied limit tensile load per bolt (no preload):
    #      PtL = P_per_bolt + ΔF_moment  (prying-amplified — conservative)
    #
    #    P_sep' = Pp_max/(1−nφ)                      [Eq 6-10]
    #    P_tu'  = (Ptu_allow − Pp_max)/nφ             [Eq 6-9]
    #    If P_sep' < P_tu'  -> separation precedes rupture:
    #      MSu_tension = Ptu_allow/(FF·FSu·PtL) − 1   [Eq 6-6]
    #    Else               -> rupture precedes separation:
    #      MSu_tension = P_tu'/(FF·FSu·PtL) − 1        [Eq 6-11]
    #
    #    P_ty' = (Pty_allow − Pp_max)/nφ, Pty_allow = Sy·At [Eq 6-19]
    #    If P_sep' < P_ty' -> MSy_tension = Pty_allow/(FF·FSy·PtL) − 1  [Eq 6-21]
    #    Else              -> MSy_tension = P_ty'/(FF·FSy·PtL) − 1      [Eq 6-22]
    # -------------------------------------------------------------------------
    nphi = nC
    PtL = P_per_bolt_N + dF_moment
    tadd(
        f"PtL = P_per_bolt + ΔF_moment = {P_per_bolt_N:.6g} + {dF_moment:.6g} = {PtL:.6g} N"
        f"  (applied limit tensile load per bolt, no preload, NASA Sec 6.2.1)",
        latex=f"P_{{tL}} = P_{{per\\text{{-}}bolt}} + \\Delta F_{{moment}} = {P_per_bolt_N:.6g} + {dF_moment:.6g} = {PtL:.6g}\\ \\mathrm{{N}}",
    )

    if nphi < 1.0:
        P_sep_prime = Pp_max / (1.0 - nphi)
    else:
        P_sep_prime = float("inf")
    tadd(
        f"P_sep' = Pp_max/(1−nφ) = {Pp_max:.6g}/(1−{nphi:.6g}) = {P_sep_prime:.6g} N  [Eq 6-10]",
        latex=(
            f"P'_{{sep}} = \\frac{{P_{{p,max}}}}{{1-n\\phi}} = \\frac{{{Pp_max:.6g}}}{{1-{nphi:.6g}}} "
            f"= {P_sep_prime:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-10]}}"
        ),
    )

    if nphi <= 0.0:
        P_tu_prime = float("inf")
    elif Ptu_allow <= Pp_max:
        P_tu_prime = 0.0
    else:
        P_tu_prime = (Ptu_allow - Pp_max) / nphi
    tadd(
        f"P_tu' = (Ptu_allow−Pp_max)/nφ = ({Ptu_allow:.6g}−{Pp_max:.6g})/{nphi:.6g} = {P_tu_prime:.6g} N  [Eq 6-9]",
        latex=(
            f"P'_{{tu}} = \\frac{{P_{{tu,allow}}-P_{{p,max}}}}{{n\\phi}} = "
            f"\\frac{{{Ptu_allow:.6g}-{Pp_max:.6g}}}{{{nphi:.6g}}} = {P_tu_prime:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-9]}}"
        ),
    )

    if nphi <= 0.0:
        P_ty_prime = float("inf")
    elif Pty_allow <= Pp_max:
        P_ty_prime = 0.0
    else:
        P_ty_prime = (Pty_allow - Pp_max) / nphi
    tadd(
        f"P_ty' = (Pty_allow−Pp_max)/nφ = ({Pty_allow:.6g}−{Pp_max:.6g})/{nphi:.6g} = {P_ty_prime:.6g} N  [Eq 6-19]",
        latex=(
            f"P'_{{ty}} = \\frac{{P_{{ty,allow}}-P_{{p,max}}}}{{n\\phi}} = "
            f"\\frac{{{Pty_allow:.6g}-{Pp_max:.6g}}}{{{nphi:.6g}}} = {P_ty_prime:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-19]}}"
        ),
    )

    sep_before_rupture = P_sep_prime < P_tu_prime

    MS_proof = (F_proof_allow / Fb_max) - 1.0 if Fb_max > 0 else float("inf")
    tadd(
        f"MS_proof (informational) = (F_proof_allow/F_b_max) − 1 = {MS_proof:.6g}",
        latex=f"MS_{{proof}} = \\frac{{F_{{proof,allow}}}}{{F_{{b,max}}}} - 1 = \\frac{{{F_proof_allow:.6g}}}{{{Fb_max:.6g}}} - 1 = {MS_proof:.6g}",
    )

    if PtL > 0:
        if sep_before_rupture:
            MSu_tension = (Ptu_allow / (inp.FF * inp.FSu * PtL)) - 1.0
            tadd(
                f"P_sep'={P_sep_prime:.6g} < P_tu'={P_tu_prime:.6g} -> separation precedes rupture: "
                f"MSu_tension = Ptu_allow/(FF·FSu·PtL) − 1 = "
                f"{Ptu_allow:.6g}/({inp.FF:.4g}·{inp.FSu:.4g}·{PtL:.6g}) − 1 = {MSu_tension:.6g}  [Eq 6-6]",
                latex=(
                    f"MS_{{u,tension}} = \\frac{{P_{{tu,allow}}}}{{FF \\cdot FS_u \\cdot P_{{tL}}}} - 1 = "
                    f"\\frac{{{Ptu_allow:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {PtL:.6g}}} - 1 "
                    f"= {MSu_tension:.6g}\\quad\\text{{[Eq 6-6]}}"
                ),
            )
        else:
            MSu_tension = (P_tu_prime / (inp.FF * inp.FSu * PtL)) - 1.0
            tadd(
                f"P_tu'={P_tu_prime:.6g} <= P_sep'={P_sep_prime:.6g} -> rupture precedes separation: "
                f"MSu_tension = P_tu'/(FF·FSu·PtL) − 1 = "
                f"{P_tu_prime:.6g}/({inp.FF:.4g}·{inp.FSu:.4g}·{PtL:.6g}) − 1 = {MSu_tension:.6g}  [Eq 6-11]",
                latex=(
                    f"MS_{{u,tension}} = \\frac{{P'_{{tu}}}}{{FF \\cdot FS_u \\cdot P_{{tL}}}} - 1 = "
                    f"\\frac{{{P_tu_prime:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {PtL:.6g}}} - 1 "
                    f"= {MSu_tension:.6g}\\quad\\text{{[Eq 6-11]}}"
                ),
            )

        if P_sep_prime < P_ty_prime:
            MSy_tension = (Pty_allow / (inp.FF * inp.FSy * PtL)) - 1.0
            tadd(
                f"P_sep'={P_sep_prime:.6g} < P_ty'={P_ty_prime:.6g} -> "
                f"MSy_tension = Pty_allow/(FF·FSy·PtL) − 1 = "
                f"{Pty_allow:.6g}/({inp.FF:.4g}·{inp.FSy:.4g}·{PtL:.6g}) − 1 = {MSy_tension:.6g}  [Eq 6-21]",
                latex=(
                    f"MS_{{y,tension}} = \\frac{{P_{{ty,allow}}}}{{FF \\cdot FS_y \\cdot P_{{tL}}}} - 1 = "
                    f"\\frac{{{Pty_allow:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSy:.4g} \\cdot {PtL:.6g}}} - 1 "
                    f"= {MSy_tension:.6g}\\quad\\text{{[Eq 6-21]}}"
                ),
            )
        else:
            MSy_tension = (P_ty_prime / (inp.FF * inp.FSy * PtL)) - 1.0
            tadd(
                f"P_ty'={P_ty_prime:.6g} <= P_sep'={P_sep_prime:.6g} -> "
                f"MSy_tension = P_ty'/(FF·FSy·PtL) − 1 = "
                f"{P_ty_prime:.6g}/({inp.FF:.4g}·{inp.FSy:.4g}·{PtL:.6g}) − 1 = {MSy_tension:.6g}  [Eq 6-22]",
                latex=(
                    f"MS_{{y,tension}} = \\frac{{P'_{{ty}}}}{{FF \\cdot FS_y \\cdot P_{{tL}}}} - 1 = "
                    f"\\frac{{{P_ty_prime:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSy:.4g} \\cdot {PtL:.6g}}} - 1 "
                    f"= {MSy_tension:.6g}\\quad\\text{{[Eq 6-22]}}"
                ),
            )
    else:
        MSu_tension = float("inf")
        MSy_tension = float("inf")
        tadd("PtL <= 0 -> MSu_tension = MSy_tension = inf")

    # -------------------------------------------------------------------------
    # 10) Separation checks. Both use Pp_min_sep (== Pp_min unless
    #     separation_critical is False).
    #
    #   External separating force per bolt:
    #     F_sep = P_per_bolt + ΔF_moment_nom  (nominal; no prying amplification)
    #
    #   Shigley/stiffness method (informational only):
    #     Ppi_req_min = (1 − nC)·F_sep + L_min_per_bolt
    #     FoS_sep     = Pp_min_sep / Ppi_req_min
    #     MS_sep      = FoS_sep/target_sep_factor − 1  (PASS means target FoS met)
    #
    #   NASA conservative — GOVERNING per NASA-STD-5020 A.11 / Sec 6.5:
    #     MSsep_NASA  = Pp_min_sep / (FF·FSsep·F_sep) − 1
    # -------------------------------------------------------------------------
    L_min_per_bolt = inp.L_min_total_N / inp.n_bolts
    F_sep_per_bolt = P_per_bolt_N + dF_moment_nom

    Ppi_req_min = (1.0 - nC) * F_sep_per_bolt + L_min_per_bolt
    FoS_sep = (Pp_min_sep / Ppi_req_min) if Ppi_req_min > 0 else float("inf")
    MS_sep = (FoS_sep / inp.target_sep_factor) - 1.0 if math.isfinite(FoS_sep) else float("inf")

    MSsep_NASA = (
        (Pp_min_sep / (inp.FF * inp.target_sep_factor * F_sep_per_bolt)) - 1.0
        if F_sep_per_bolt > 0 else float("inf")
    )

    tadd(
        f"F_sep_per_bolt = P_per_bolt + ΔF_moment_nom = {P_per_bolt_N:.6g} + {dF_moment_nom:.6g} = {F_sep_per_bolt:.6g} N",
        latex=f"F_{{sep,per\\text{{-}}bolt}} = P_{{per\\text{{-}}bolt}} + \\Delta F_{{moment,nom}} = {P_per_bolt_N:.6g} + {dF_moment_nom:.6g} = {F_sep_per_bolt:.6g}\\ \\mathrm{{N}}",
    )
    tadd(
        f"L_min_per_bolt = {L_min_per_bolt:.6g} N",
        latex=f"L_{{min,per\\text{{-}}bolt}} = \\frac{{L_{{min,total}}}}{{N_b}} = {L_min_per_bolt:.6g}\\ \\mathrm{{N}}",
    )
    tadd(
        f"Ppi_req_min = (1−nC)·F_sep + L_min = "
        f"(1−{nC:.6g})·{F_sep_per_bolt:.6g} + {L_min_per_bolt:.6g} = {Ppi_req_min:.6g} N",
        latex=(
            f"P_{{pi,req\\text{{-}}min}} = (1-nC) F_{{sep}} + L_{{min}} = "
            f"(1-{nC:.6g}) \\cdot {F_sep_per_bolt:.6g} + {L_min_per_bolt:.6g} = {Ppi_req_min:.6g}\\ \\mathrm{{N}}"
        ),
    )
    tadd(
        f"FoS_sep (Shigley, informational) = Pp_min_sep/Ppi_req_min = {Pp_min_sep:.6g}/{Ppi_req_min:.6g} = {FoS_sep:.6g}",
        latex=f"FoS_{{sep}} = \\frac{{P_{{p,min\\text{{-}}sep}}}}{{P_{{pi,req\\text{{-}}min}}}} = \\frac{{{Pp_min_sep:.6g}}}{{{Ppi_req_min:.6g}}} = {FoS_sep:.6g}",
    )
    tadd(
        f"MS_sep (Shigley, informational) = FoS_sep/target_sep_factor − 1 = {FoS_sep:.6g}/{inp.target_sep_factor:.4g} − 1 = {MS_sep:.6g}",
        latex=f"MS_{{sep}} = \\frac{{FoS_{{sep}}}}{{FS_{{sep,target}}}} - 1 = \\frac{{{FoS_sep:.6g}}}{{{inp.target_sep_factor:.4g}}} - 1 = {MS_sep:.6g}",
    )
    tadd(
        f"MSsep_NASA (governing, App A.11) = Pp_min_sep/(FF·FSsep·F_sep) − 1 = "
        f"{Pp_min_sep:.6g}/({inp.FF:.4g}·{inp.target_sep_factor:.4g}·{F_sep_per_bolt:.6g}) − 1 = {MSsep_NASA:.6g}",
        latex=(
            f"MS_{{sep,NASA}} = \\frac{{P_{{p,min\\text{{-}}sep}}}}{{FF \\cdot FS_{{sep}} \\cdot F_{{sep}}}} - 1 = "
            f"\\frac{{{Pp_min_sep:.6g}}}{{{inp.FF:.4g} \\cdot {inp.target_sep_factor:.4g} \\cdot {F_sep_per_bolt:.6g}}} - 1 "
            f"= {MSsep_NASA:.6g}"
        ),
    )

    # -------------------------------------------------------------------------
    # 11) Required bolt count estimate — NASA-based criterion: separation
    #     requires Pp_min_sep >= FF·FSsep·(P_total/n + 4M/(n·D_bc)) per bolt,
    #     i.e. n_bolts_required = ceil(FF·FSsep·(F_axial_total + 4M/D_bc) / Pp_min_sep)
    # -------------------------------------------------------------------------
    moment_term_total = 0.0
    if inp.moment.enabled:
        moment_term_total = 4.0 * inp.moment.M_Nmm / inp.moment.bolt_circle_diameter_mm
    required_bolts = (
        math.ceil(
            inp.FF * inp.target_sep_factor * (F_axial_total_N + moment_term_total) / Pp_min_sep
        )
        if Pp_min_sep > 0 else 10**9
    )
    required_bolts = max(1, required_bolts)

    # -------------------------------------------------------------------------
    # 12) Installation torque (optional): T = K·d·Fi
    # -------------------------------------------------------------------------
    install_torque: Optional[float] = None
    if inp.K_nut is not None:
        install_torque = inp.K_nut * inp.d_bolt_mm * inp.Ppi_nom_N
        tadd(
            f"T_install = K·d·F_i = {inp.K_nut:.4g}·{inp.d_bolt_mm:.6g}·{inp.Ppi_nom_N:.6g}"
            f" = {install_torque:.6g} N·mm  ({install_torque/1000:.4g} N·m)",
            latex=(
                f"T_{{install}} = K \\cdot d \\cdot F_i = {inp.K_nut:.4g} \\cdot {inp.d_bolt_mm:.6g} \\cdot {inp.Ppi_nom_N:.6g} "
                f"= {install_torque:.6g}\\ \\mathrm{{N \\cdot mm}} = {install_torque/1000:.4g}\\ \\mathrm{{N \\cdot m}}"
            ),
        )

    # -------------------------------------------------------------------------
    # 13) Shear analysis (optional) [NASA App A.8, Sec 6.2.3]
    # -------------------------------------------------------------------------
    Psu_allow: Optional[float] = None
    MSu_shear: Optional[float] = None
    MS_interaction: Optional[float] = None
    if inp.shear.enabled:
        if inp.shear.F_shear_per_bolt_N <= 0:
            raise ValueError("Shear enabled but F_shear_per_bolt_N <= 0")
        Fsu_MPa = inp.Su_MPa / math.sqrt(3.0)   # von Mises, NASA Eq A.8-5
        tadd(
            f"Fsu = S_u/√3 = {inp.Su_MPa:.6g}/√3 = {Fsu_MPa:.6g} MPa  [von Mises, NASA Eq A.8-5]",
            latex=f"F_{{su}} = \\frac{{S_u}}{{\\sqrt{{3}}}} = \\frac{{{inp.Su_MPa:.6g}}}{{\\sqrt{{3}}}} = {Fsu_MPa:.6g}\\ \\mathrm{{MPa}}\\quad\\text{{[Eq A.8-5]}}",
        )

        if inp.shear.at_threads:
            if inp.Am_mm2 is None or inp.Am_mm2 <= 0:
                raise ValueError("shear.at_threads is True but Am_mm2 is None or <= 0")
            Psu_allow = Fsu_MPa * inp.Am_mm2   # Eq 6-13
            tadd(
                f"Psu_allow = Fsu·A_m = {Fsu_MPa:.6g}·{inp.Am_mm2:.6g} = {Psu_allow:.6g} N  [Eq 6-13, shear at threads]",
                latex=f"P_{{su,allow}} = F_{{su}} A_m = {Fsu_MPa:.6g} \\cdot {inp.Am_mm2:.6g} = {Psu_allow:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-13]}}",
            )
        else:
            Psu_allow = Fsu_MPa * inp.Ad_mm2   # Eq 6-12
            tadd(
                f"Psu_allow = Fsu·A_d = {Fsu_MPa:.6g}·{inp.Ad_mm2:.6g} = {Psu_allow:.6g} N  [Eq 6-12, shear at body]",
                latex=f"P_{{su,allow}} = F_{{su}} A_d = {Fsu_MPa:.6g} \\cdot {inp.Ad_mm2:.6g} = {Psu_allow:.6g}\\ \\mathrm{{N}}\\quad\\text{{[Eq 6-12]}}",
            )

        PsL = inp.shear.F_shear_per_bolt_N
        MSu_shear = (Psu_allow / (inp.FF * inp.FSu * PsL)) - 1.0
        tadd(
            f"MSu_shear = Psu_allow/(FF·FSu·PsL) − 1 = "
            f"{Psu_allow:.6g}/({inp.FF:.4g}·{inp.FSu:.4g}·{PsL:.6g}) − 1 = {MSu_shear:.6g}",
            latex=(
                f"MS_{{u,shear}} = \\frac{{P_{{su,allow}}}}{{FF \\cdot FS_u \\cdot P_{{sL}}}} - 1 = "
                f"\\frac{{{Psu_allow:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {PsL:.6g}}} - 1 = {MSu_shear:.6g}"
            ),
        )

        # Tension+shear interaction [NASA Sec 6.2.3], ultimate DESIGN loads
        # (FF·FS applied, no preload). Bending term omitted (not modeled).
        Psu_design = inp.FF * inp.FSu * PsL
        Ptu_design = inp.FF * inp.FSu * PtL
        tadd(
            f"Psu_design = FF·FSu·PsL = {inp.FF:.4g}·{inp.FSu:.4g}·{PsL:.6g} = {Psu_design:.6g} N",
            latex=f"P_{{su,design}} = FF \\cdot FS_u \\cdot P_{{sL}} = {inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {PsL:.6g} = {Psu_design:.6g}\\ \\mathrm{{N}}",
        )
        tadd(
            f"Ptu_design = FF·FSu·PtL = {inp.FF:.4g}·{inp.FSu:.4g}·{PtL:.6g} = {Ptu_design:.6g} N",
            latex=f"P_{{tu,design}} = FF \\cdot FS_u \\cdot P_{{tL}} = {inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {PtL:.6g} = {Ptu_design:.6g}\\ \\mathrm{{N}}",
        )
        if inp.shear.at_threads:
            idx = (Psu_design / Psu_allow) ** 1.2 + (Ptu_design / Ptu_allow) ** 2.0   # Eq 6-17
        else:
            idx = (Psu_design / Psu_allow) ** 2.5 + (Ptu_design / Ptu_allow) ** 1.5   # Eq 6-15
        # MS_interaction = 1/idx − 1 is an approximation for these mixed
        # exponents; the standard's actual pass/fail criterion is idx <= 1.
        MS_interaction = (1.0 / idx) - 1.0 if idx > 0 else float("inf")

        mode = "threads" if inp.shear.at_threads else "full body"
        if inp.shear.at_threads:
            idx_latex = (
                f"\\left(\\frac{{{Psu_design:.6g}}}{{{Psu_allow:.6g}}}\\right)^{{1.2}} + "
                f"\\left(\\frac{{{Ptu_design:.6g}}}{{{Ptu_allow:.6g}}}\\right)^{{2.0}} = {idx:.6g}\\quad\\text{{[Eq 6-17]}}"
            )
        else:
            idx_latex = (
                f"\\left(\\frac{{{Psu_design:.6g}}}{{{Psu_allow:.6g}}}\\right)^{{2.5}} + "
                f"\\left(\\frac{{{Ptu_design:.6g}}}{{{Ptu_allow:.6g}}}\\right)^{{1.5}} = {idx:.6g}\\quad\\text{{[Eq 6-15]}}"
            )
        tadd(
            f"T+S interaction ({mode}): index = {idx:.6g}, MS_interaction (approx) = {MS_interaction:.6g}  [idx<=1 is the standard's pass/fail criterion]",
            latex=(
                f"\\text{{idx}}_{{T+S}} = {idx_latex}\\ \\Rightarrow\\ "
                f"MS_{{interaction}} \\approx \\frac{{1}}{{\\text{{idx}}}} - 1 = {MS_interaction:.6g}"
            ),
        )

    # -------------------------------------------------------------------------
    # 14a) Fatigue — Goodman criterion
    # -------------------------------------------------------------------------
    sigma_a: Optional[float] = None
    sigma_m: Optional[float] = None
    n_fatigue: Optional[float] = None
    if inp.fatigue.enabled:
        if inp.fatigue.Se_MPa <= 0:
            raise ValueError("Fatigue enabled but Se_MPa <= 0")
        F_range = abs(inp.fatigue.F_ext_max_N - inp.fatigue.F_ext_min_N)
        F_mean_ext = (inp.fatigue.F_ext_max_N + inp.fatigue.F_ext_min_N) / 2.0
        sigma_a = nC * F_range / (2.0 * inp.At_mm2)
        # NASA Eq 6-4 designates MAXIMUM preload for fatigue mean stress
        # (higher mean stress is more conservative under the Goodman line).
        sigma_m = (Pp_max + nC * F_mean_ext) / inp.At_mm2
        denom_gm = sigma_a / inp.fatigue.Se_MPa + sigma_m / inp.Su_MPa
        n_fatigue = (1.0 / denom_gm) if denom_gm > 0 else float("inf")
        tadd(
            f"σ_a = nC·(F_max−F_min)/(2·A_t) = {nC:.6g}·{F_range:.6g}/(2·{inp.At_mm2:.6g}) = {sigma_a:.6g} MPa",
            latex=(
                f"\\sigma_a = \\frac{{nC (F_{{max}}-F_{{min}})}}{{2 A_t}} = "
                f"\\frac{{{nC:.6g} \\cdot {F_range:.6g}}}{{2 \\cdot {inp.At_mm2:.6g}}} = {sigma_a:.6g}\\ \\mathrm{{MPa}}"
            ),
        )
        tadd(
            f"σ_m = (Pp_max + nC·F_mean)/A_t = ({Pp_max:.6g} + {nC:.6g}·{F_mean_ext:.6g})/{inp.At_mm2:.6g} = {sigma_m:.6g} MPa",
            latex=(
                f"\\sigma_m = \\frac{{P_{{p,max}} + nC \\cdot F_{{mean}}}}{{A_t}} = "
                f"\\frac{{{Pp_max:.6g} + {nC:.6g} \\cdot {F_mean_ext:.6g}}}{{{inp.At_mm2:.6g}}} = {sigma_m:.6g}\\ \\mathrm{{MPa}}"
            ),
        )
        tadd(
            f"Goodman: n_f = 1/(σ_a/Se + σ_m/Su) = {n_fatigue:.6g}",
            latex=(
                f"n_f = \\frac{{1}}{{\\frac{{\\sigma_a}}{{S_e}} + \\frac{{\\sigma_m}}{{S_u}}}} = "
                f"\\frac{{1}}{{\\frac{{{sigma_a:.6g}}}{{{inp.fatigue.Se_MPa:.6g}}} + \\frac{{{sigma_m:.6g}}}{{{inp.Su_MPa:.6g}}}}} "
                f"= {n_fatigue:.6g}"
            ),
        )

    # -------------------------------------------------------------------------
    # 14b) Thread stripping (Shigley 8-5 simplified)
    # -------------------------------------------------------------------------
    F_strip: Optional[float] = None
    MS_strip: Optional[float] = None
    if inp.thread_strip.enabled:
        if inp.thread_strip.L_engage_mm <= 0:
            raise ValueError("Thread strip enabled but L_engage_mm <= 0")
        if inp.thread_strip.Sy_member_MPa <= 0:
            raise ValueError("Thread strip enabled but Sy_member_MPa <= 0")
        A_strip = 0.5 * math.pi * inp.d_bolt_mm * inp.thread_strip.L_engage_mm
        F_strip = 0.577 * inp.thread_strip.Sy_member_MPa * A_strip
        MS_strip = (F_strip / (inp.FF * inp.FSu * Fb_max)) - 1.0 if Fb_max > 0 else float("inf")
        tadd(
            f"A_strip = 0.5·π·d·L_e = 0.5·π·{inp.d_bolt_mm:.6g}·{inp.thread_strip.L_engage_mm:.6g} = {A_strip:.6g} mm^2",
            latex=(
                f"A_{{strip}} = 0.5\\,\\pi\\, d\\, L_e = 0.5 \\cdot \\pi \\cdot {inp.d_bolt_mm:.6g} \\cdot "
                f"{inp.thread_strip.L_engage_mm:.6g} = {A_strip:.6g}\\ \\mathrm{{mm^2}}"
            ),
        )
        tadd(
            f"F_strip = 0.577·S_y_member·A_strip = 0.577·{inp.thread_strip.Sy_member_MPa:.6g}·{A_strip:.6g} = {F_strip:.6g} N",
            latex=(
                f"F_{{strip}} = 0.577\\, S_{{y,member}}\\, A_{{strip}} = 0.577 \\cdot {inp.thread_strip.Sy_member_MPa:.6g} "
                f"\\cdot {A_strip:.6g} = {F_strip:.6g}\\ \\mathrm{{N}}"
            ),
        )
        tadd(
            f"MS_strip = (F_strip/(FF·FSu·F_b_max)) − 1 = {MS_strip:.6g}",
            latex=f"MS_{{strip}} = \\frac{{F_{{strip}}}}{{FF \\cdot FS_u \\cdot F_{{b,max}}}} - 1 = \\frac{{{F_strip:.6g}}}{{{inp.FF:.4g} \\cdot {inp.FSu:.4g} \\cdot {Fb_max:.6g}}} - 1 = {MS_strip:.6g}",
        )

    return Results(
        P_total_N=F_axial_total_N,
        P_per_bolt_N=P_per_bolt_N,
        kb_N_per_mm=kb,
        km_N_per_mm=km,
        C=C,
        nC=nC,
        Ppi_nom_N=Ppi_nom,
        Ppi_min_N=Ppi_min,
        Ppi_max_N=Ppi_max,
        Ppr_N=Ppr,
        dPp_thermal_N=dPp_thermal,
        Pp_min_N=Pp_min,
        Pp_max_N=Pp_max,
        Pp_min_sep_N=Pp_min_sep,
        dF_moment_nom_N=dF_moment_nom,
        dF_moment_N=dF_moment,
        Fb_max_N=Fb_max,
        F_proof_allow_N=F_proof_allow,
        Ptu_allow_N=Ptu_allow,
        Pty_allow_N=Pty_allow,
        MS_proof=MS_proof,
        MSu_tension=MSu_tension,
        MSy_tension=MSy_tension,
        P_sep_prime_N=P_sep_prime,
        P_tu_prime_N=P_tu_prime,
        sep_before_rupture=sep_before_rupture,
        Ppi_req_min_N=Ppi_req_min,
        FoS_sep=FoS_sep,
        MS_sep=MS_sep,
        MSsep_NASA=MSsep_NASA,
        required_bolts_for_target_sep=required_bolts,
        install_torque_Nmm=install_torque,
        Psu_allow_N=Psu_allow,
        MSu_shear=MSu_shear,
        MS_interaction=MS_interaction,
        sigma_a_MPa=sigma_a,
        sigma_m_MPa=sigma_m,
        fatigue_safety_factor=n_fatigue,
        F_strip_N=F_strip,
        MS_strip=MS_strip,
    )


# =============================================================================
# Output formatting
# =============================================================================

def pretty_print(res: Results, inp: Inputs) -> None:
    def ms_str(ms: float) -> str:
        status = "PASS" if ms >= 0 else "FAIL"
        return f"{ms:.3f}  [{status}]"

    print("\n=== Separating load ===")
    print(f"P_total          : {res.P_total_N:,.1f} N  ({res.P_total_N/1000:.3f} kN)")
    print(f"P_per_bolt       : {res.P_per_bolt_N:,.1f} N  ({res.P_per_bolt_N/1000:.3f} kN)")

    print("\n=== Stiffness split ===")
    print(f"k_b              : {res.kb_N_per_mm:,.1f} N/mm")
    print(f"k_m              : {res.km_N_per_mm:,.1f} N/mm")
    print(f"C = kb/(kb+km)   : {res.C:.4f}")
    print(f"n (load intro)   : {inp.n_load_intro:.4f}")
    print(f"nC               : {res.nC:.4f}")
    if res.C > 0.6:
        print("  WARNING: C > 0.6 — may indicate low member stiffness or geometry issue")

    print("\n=== Installed preload bounds (per bolt) ===")
    print(f"Ppi_nom          : {res.Ppi_nom_N:,.1f} N")
    print(f"Ppi_min          : {res.Ppi_min_N:,.1f} N")
    print(f"Ppi_max          : {res.Ppi_max_N:,.1f} N")

    print("\n=== Operating preload bounds (after relaxation + thermal) ===")
    print(f"Ppr (relaxation) : {res.Ppr_N:,.1f} N  ({inp.Ppr_fraction*100:.1f}% of Ppi_min)")
    print(f"dPp_thermal      : {res.dPp_thermal_N:,.1f} N")
    print(f"Pp_min           : {res.Pp_min_N:,.1f} N")
    print(f"Pp_max           : {res.Pp_max_N:,.1f} N")
    print(f"Pp_min_sep       : {res.Pp_min_sep_N:,.1f} N"
          f"  (separation_critical={inp.separation_critical})")

    print("\n=== Peak bolt tension (informational) ===")
    print(f"dF_moment_nom    : {res.dF_moment_nom_N:,.1f} N")
    print(f"dF_moment (pry.) : {res.dF_moment_N:,.1f} N")
    print(f"F_b_max          : {res.Fb_max_N:,.1f} N  ({res.Fb_max_N/1000:.3f} kN)")

    print("\n=== Strength margins (NASA-STD-5020 §6.2.1/6.3) ===")
    print(f"F_proof_allow    : {res.F_proof_allow_N:,.1f} N")
    print(f"Ptu_allow        : {res.Ptu_allow_N:,.1f} N")
    print(f"Pty_allow        : {res.Pty_allow_N:,.1f} N")
    print(f"P_sep'           : {res.P_sep_prime_N:,.1f} N  [Eq 6-10]")
    print(f"P_tu'            : {res.P_tu_prime_N:,.1f} N  [Eq 6-9]")
    print(f"Sep before rupture: {res.sep_before_rupture}")
    print(f"MS_proof (info.) : {ms_str(res.MS_proof)}")
    print(f"MSu_tension      : {ms_str(res.MSu_tension)}  (FF={inp.FF}, FSu={inp.FSu})")
    print(f"MSy_tension      : {ms_str(res.MSy_tension)}  (FF={inp.FF}, FSy={inp.FSy})")

    print("\n=== Separation ===")
    print(f"Ppi_req_min      : {res.Ppi_req_min_N:,.1f} N")
    print(f"FoS_sep (Shigley): {res.FoS_sep:.3f}  [target >= {inp.target_sep_factor:.2f}]  (informational)")
    print(f"MS_sep (Shigley) : {ms_str(res.MS_sep)}  (informational)")
    print(f"MSsep_NASA       : {ms_str(res.MSsep_NASA)}  (FF={inp.FF}, FSsep={inp.target_sep_factor})  (governing, NASA A.11)")
    print(f"Required bolts   : {res.required_bolts_for_target_sep}")

    if res.install_torque_Nmm is not None:
        print("\n=== Installation torque ===")
        print(f"T_install        : {res.install_torque_Nmm:,.1f} N.mm  ({res.install_torque_Nmm/1000:.3f} N.m)")

    if res.MSu_shear is not None:
        print("\n=== Shear ===")
        print(f"Psu_allow        : {res.Psu_allow_N:,.1f} N")
        print(f"MSu_shear        : {ms_str(res.MSu_shear)}")
        print(f"MS_interaction   : {ms_str(res.MS_interaction)}")

    if res.fatigue_safety_factor is not None:
        print("\n=== Fatigue (Goodman) ===")
        print(f"σ_a              : {res.sigma_a_MPa:.3f} MPa")
        print(f"σ_m              : {res.sigma_m_MPa:.3f} MPa")
        print(f"n_f (safety)     : {res.fatigue_safety_factor:.3f}  [PASS if >= 1]")

    if res.MS_strip is not None:
        print("\n=== Thread stripping ===")
        print(f"F_strip          : {res.F_strip_N:,.1f} N")
        print(f"MS_strip         : {ms_str(res.MS_strip)}")


# =============================================================================
# PDF report
# =============================================================================

def make_pdf_report(
    inp: Inputs,
    layers: List[Layer],
    res: Results,
    filename: str = "flange_report.pdf",
    project_title: str = "Bolted Flange Joint",
    calculation_trace: Optional[List[str]] = None,
) -> str:
    inputs_dict: Dict[str, Any] = {
        "Pc (psi)": inp.Pc_psi,
        "Effective pressure diameter (in)": inp.effective_diameter_in,
        "Bolt count N_b": inp.n_bolts,
        "Bolt nominal diameter d (mm)": inp.d_bolt_mm,
        "Clearance hole diameter d_h (mm)": inp.d_hole_mm,
        "A_t (mm^2)": inp.At_mm2,
        "A_d (mm^2)": inp.Ad_mm2,
        "Bolt modulus E_b (MPa)": inp.Eb_MPa,
        "Threaded length l_t (mm)": inp.lt_mm,
        "Unthreaded length l_d (mm)": inp.ld_mm,
        "Proof strength S_p (MPa)": inp.Sp_MPa,
        "Yield strength S_y (MPa)": inp.Sy_MPa,
        "Ultimate strength S_u (MPa)": inp.Su_MPa,
        "Ppi_nom (N)": inp.Ppi_nom_N,
        "Gamma_p": inp.Gamma_p,
        "c_max": inp.c_max,
        "c_min": inp.c_min,
        "Separation-critical joint": inp.separation_critical,
        "Ppr_fraction": inp.Ppr_fraction,
        "L_min_total (N)": inp.L_min_total_N,
        "F_axial_extra (N)": inp.F_axial_extra_N,
        "Target separation FoS": inp.target_sep_factor,
        "FSu": inp.FSu,
        "FSy": inp.FSy,
        "FF (fitting factor)": inp.FF,
        "n_load_intro": inp.n_load_intro,
        "Member layers": ", ".join(
            [f"{ly.name} (t={ly.thickness_mm}mm, E={ly.E_MPa}MPa, D={ly.D_interface_mm}mm)"
             for ly in layers]
        ),
    }
    if inp.K_nut is not None:
        inputs_dict["K_nut (torque factor)"] = inp.K_nut
    if inp.moment.enabled:
        inputs_dict["Moment M (N·mm)"] = inp.moment.M_Nmm
        inputs_dict["Bolt circle diameter BCD (mm)"] = inp.moment.bolt_circle_diameter_mm
        inputs_dict["Prying factor"] = inp.moment.prying_factor
    if inp.thermal.enabled:
        inputs_dict["alpha_bolt (1/°C)"] = inp.thermal.alpha_bolt_per_C
        inputs_dict["alpha_member (1/°C)"] = inp.thermal.alpha_member_per_C
        inputs_dict["dT (°C)"] = inp.thermal.dT_C
        inputs_dict["L_grip for thermal (mm)"] = inp.thermal.L_grip_mm
    if inp.shear.enabled:
        inputs_dict["Shear per bolt (N)"] = inp.shear.F_shear_per_bolt_N
        inputs_dict["Shear at threads"] = inp.shear.at_threads
        if inp.shear.at_threads:
            inputs_dict["A_m (mm^2, minor-diameter thread area)"] = inp.Am_mm2
    if inp.fatigue.enabled:
        inputs_dict["Fatigue F_ext_max (N)"] = inp.fatigue.F_ext_max_N
        inputs_dict["Fatigue F_ext_min (N)"] = inp.fatigue.F_ext_min_N
        inputs_dict["Endurance limit Se (MPa)"] = inp.fatigue.Se_MPa
    if inp.thread_strip.enabled:
        inputs_dict["Thread engagement L_e (mm)"] = inp.thread_strip.L_engage_mm
        inputs_dict["Sy member (MPa)"] = inp.thread_strip.Sy_member_MPa

    inputs_dict = {subify(k): v for k, v in inputs_dict.items()}

    results_dict: Dict[str, Any] = {
        "P_total (N)": res.P_total_N,
        "P_per_bolt (N)": res.P_per_bolt_N,
        "k_b (N/mm)": res.kb_N_per_mm,
        "k_m (N/mm)": res.km_N_per_mm,
        "C = k_b/(k_b+k_m)": res.C,
        "nC = n·C": res.nC,
        "Ppi_nom (N)": res.Ppi_nom_N,
        "Ppi_min (N)": res.Ppi_min_N,
        "Ppi_max (N)": res.Ppi_max_N,
        "Ppr relaxation (N)": res.Ppr_N,
        "dPp_thermal (N)": res.dPp_thermal_N,
        "Pp_min operating (N)": res.Pp_min_N,
        "Pp_max operating (N)": res.Pp_max_N,
        "dF_moment_nom (N)": res.dF_moment_nom_N,
        "dF_moment prying (N)": res.dF_moment_N,
        "F_b_max (N, informational)": res.Fb_max_N,
        "F_proof_allow (N)": res.F_proof_allow_N,
        "Ptu_allow (N)": res.Ptu_allow_N,
        "Pty_allow (N)": res.Pty_allow_N,
        "P_sep' (N, Eq 6-10)": res.P_sep_prime_N,
        "P_tu' (N, Eq 6-9)": res.P_tu_prime_N,
        "Separation before rupture": res.sep_before_rupture,
        "MS_proof (informational)": res.MS_proof,
        "MSu_tension (NASA 6.2.1)": res.MSu_tension,
        "MSy_tension (NASA 6.3)": res.MSy_tension,
        "Ppi_req_min (N)": res.Ppi_req_min_N,
        "FoS_sep (Shigley, informational)": res.FoS_sep,
        "MS_sep (Shigley, informational)": res.MS_sep,
        "MSsep_NASA (governing, App A.11)": res.MSsep_NASA,
        "Required bolts (estimate)": res.required_bolts_for_target_sep,
    }
    if abs(res.Pp_min_sep_N - res.Pp_min_N) > 1e-9:
        results_dict["Pp_min_sep (N, separation checks only)"] = res.Pp_min_sep_N
    if res.install_torque_Nmm is not None:
        results_dict["T_install (N·mm)"] = res.install_torque_Nmm
    if res.Psu_allow_N is not None:
        results_dict["Psu_allow (N)"] = res.Psu_allow_N
        results_dict["MSu_shear"] = res.MSu_shear
        results_dict["MS_interaction"] = res.MS_interaction
    if res.sigma_a_MPa is not None:
        results_dict["sigma_a (MPa)"] = res.sigma_a_MPa
        results_dict["sigma_m (MPa)"] = res.sigma_m_MPa
        results_dict["n_f fatigue safety"] = res.fatigue_safety_factor
    if res.F_strip_N is not None:
        results_dict["F_strip (N)"] = res.F_strip_N
        results_dict["MS_strip"] = res.MS_strip

    results_dict = {subify(k): v for k, v in results_dict.items()}

    checks: Dict[str, bool] = {
        "Proof (MS_proof >= 0, informational)": res.MS_proof >= 0.0,
        "Ultimate tension (MSu_tension >= 0)": res.MSu_tension >= 0.0,
        "Yield tension (MSy_tension >= 0)": res.MSy_tension >= 0.0,
        "Separation Shigley (MS_sep >= 0, informational)": res.MS_sep >= 0.0,
        "Separation NASA (MSsep_NASA >= 0, governing per A.11)": res.MSsep_NASA >= 0.0,
        "Stiffness sanity (C <= 0.6)": res.C <= 0.6,
    }
    if res.MSu_shear is not None:
        checks["Shear ultimate (MSu_shear >= 0)"] = res.MSu_shear >= 0.0
        checks["T+S interaction (MS_interaction >= 0)"] = res.MS_interaction >= 0.0
    if res.fatigue_safety_factor is not None:
        checks["Fatigue Goodman (n_f >= 1)"] = res.fatigue_safety_factor >= 1.0
    if res.MS_strip is not None:
        checks["Thread strip (MS_strip >= 0)"] = res.MS_strip >= 0.0

    notes = (
        "Preload bounds: NASA-STD-5020 Eq 6-4 (Ppi_max), Eq 6-5a (Ppi_min), Eq 6-5b (Ppi_min_sep, "
        "non-separation-critical joints only). "
        "Relaxation: NASA Table 1. "
        "Tension margins: NASA-STD-5020 linear-theory procedure, Sec 6.2.1 (ultimate, Eq 6-6/6-9/6-10/6-11), "
        "Sec 6.3 (yield, Eq 6-19/6-21/6-22). Factors of safety do not apply to preload (Sec 4.1). "
        "Separation: NASA Sec 6.5 / App A.11 governs (MSsep_NASA); Shigley stiffness check is informational only. "
        "Shear: NASA App A.8 (von Mises Fsu=Su/√3), Eq 6-12 (body) / Eq 6-13 (threads). "
        "T+S interaction: NASA Sec 6.2.3, Eq 6-15 (body) / Eq 6-17 (threads), bending term omitted; "
        "criterion is idx <= 1, MS_interaction is an approximation. "
        "Load introduction factor n: VDI 2230."
    )

    # Apply subify (HTML <sub> tags) at PDF-generation time only; CalcTrace
    # stores raw plain-text lines so other consumers (e.g. a GUI trace pane)
    # aren't polluted with literal HTML.
    trace_for_pdf = [subify(line) for line in calculation_trace] if calculation_trace else calculation_trace

    return build_flange_pdf_report(
        filename=filename,
        project_title=project_title,
        inputs=inputs_dict,
        results=results_dict,
        checks=checks,
        notes=notes,
        warnings=[],
        calculation_trace=trace_for_pdf,
    )


# =============================================================================
# INPUTS (edit build_inputs to change joint configuration)
# =============================================================================

def build_inputs() -> Tuple[Inputs, List[Layer]]:
    inp = Inputs(
        # --- Pressure / separating load ---
        Pc_psi=300.0,
        effective_diameter_in=3.53,
        n_bolts=8,

        # --- Bolt geometry ---
        d_bolt_mm=8.0,
        d_hole_mm=8.5,
        At_mm2=36.6,
        Ad_mm2=50.3,
        Am_mm2=32.8,      # minor-diameter thread area; used only if shear.at_threads=True
        Eb_MPa=200_000.0,
        lt_mm=10.0,
        ld_mm=20.0,

        # --- Strength allowables (edit for your bolt grade) ---
        Sp_MPa=600.0,     # proof
        Sy_MPa=720.0,     # yield  (~1.2 × Sp for many alloy steels)
        Su_MPa=900.0,     # ultimate

        # --- Preload ---
        Ppi_nom_N=12_000.0,
        Gamma_p=0.25,
        c_max=1.0,
        c_min=1.0,
        separation_critical=True,

        # --- Optional minimum clamp ---
        L_min_total_N=0.0,

        # --- Factors of safety (NASA-STD-5001 unmanned defaults) ---
        FSu=1.4,
        FSy=1.25,
        FF=1.15,

        # --- Load introduction (0=bolt-plane loading, 1=interface loading) ---
        n_load_intro=0.5,

        # --- Relaxation (5% all-metal per NASA Table 1) ---
        Ppr_fraction=0.05,

        # --- Target separation FoS (NASA Fig.1 catastrophic) ---
        target_sep_factor=1.4,

        # --- Torque: set K_nut to activate torque output ---
        K_nut=None,    # e.g. 0.20 for lightly lubricated

        # --- Optional loads (set enabled=True and fill values) ---
        moment=MomentLoad(
            enabled=False,
            M_Nmm=0.0,
            bolt_circle_diameter_mm=0.0,
            prying_factor=1.0,
        ),
        shear=ShearLoad(enabled=False),
        thermal=ThermalLoad(enabled=False),
        fatigue=FatigueCheck(enabled=False),
        thread_strip=ThreadStrip(enabled=False),
    )

    dw = 1.5 * inp.d_bolt_mm
    layers = [
        Layer(
            name="Flange stack (symmetric)",
            E_MPa=71_000.0,
            thickness_mm=12.0,
            D_interface_mm=dw,
        )
    ]
    return inp, layers


# =============================================================================
# Main entry point
# =============================================================================

def main() -> None:
    inp, layers = build_inputs()
    trace = CalcTrace()
    res = compute(inp, layers, trace=trace)
    pretty_print(res, inp)
    actual_filename = make_pdf_report(
        inp, layers, res,
        filename="flange_report.pdf",
        project_title="Bolted Flange Joint (Example)",
        calculation_trace=trace.lines,
    )
    print(f"\nPDF generated: {actual_filename}")


if __name__ == "__main__":
    main()
