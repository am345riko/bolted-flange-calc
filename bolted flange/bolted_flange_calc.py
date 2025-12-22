from __future__ import annotations

# Inputs at 568

"""
Bolted Flange Calculator (NASA-STD-5020 styled outputs)

This script uses a joint stiffness + load split approach and formats the
“preload / separation / strength” logic in a NASA-STD-5020 style.

NASA-STD-5020 elements implemented:
- Uses installed preload variation factor Γp to compute:
    Ppi_min = Ppi * (1 − Γp)    and    Ppi_max = Ppi * (1 + Γp)
  Reference: NASA-STD-5020, Appendix A, A.2.1 (PDF p. 41) [Eq. (A.2-1), (A.2-2)]

- Computes the minimum initial preload per bolt required to prevent separation
  (and/or meet a minimum clamp load):
    Ppi_req_min = ((Fext_sep)*(1 − C) + L_min) / n
  Reference: NASA-STD-5020, Appendix A, A.2.1 (PDF p. 42) [Eq. (A.2-3b)]
  In this code, Fext_sep = P_total, n = N_bolts, so per bolt:
    Ppi_req_min_per_bolt = (1 − C)*P_per_bolt + (L_min_total / N_bolts)

Notes:
- The stiffness model (k_b, k_m, C) uses a Shigley frustum/joint approximation (not NASA-STD-5020).
  NASA-STD-5020 defines preload variation treatment and margin reporting; it does not mandate a single stiffness model.
- “Margin of Safety” style outputs are reported as:
    MS = (Allowable / Applied) − 1
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
    """Area of circle from diameter (mm^2)."""
    return math.pi * (d_mm ** 2) / 4.0


def area_ring(Do_mm: float, Di_mm: float) -> float:
    """Area of ring (mm^2)."""
    if Do_mm <= 0 or Di_mm < 0:
        raise ValueError("Invalid ring diameters.")
    if Do_mm <= Di_mm:
        raise ValueError("Need Do > Di for ring area.")
    return math.pi / 4.0 * (Do_mm * Do_mm - Di_mm * Di_mm)


def psi_to_MPa(psi: float) -> float:
    """psi -> MPa (N/mm^2)."""
    return psi * 0.006894757293168361


def in_to_mm(x_in: float) -> float:
    return x_in * 25.4


def subify(s: str) -> str:
    """
    Converts tokens like:
        l_t -> l<sub>t</sub>
        Fi_nom -> Fi<sub>nom</sub>
        k_b -> k<sub>b</sub>
    for ReportLab Paragraph rendering.
    """
    return re.sub(r"([A-Za-z]+)_([A-Za-z0-9]+)", r"\1<sub>\2</sub>", s)


# =============================================================================
# Calculation trace logger 
# =============================================================================

@dataclass
class CalcTrace:
    lines: List[str] = field(default_factory=list)

    def add(self, s: str) -> None:
        self.lines.append(subify(s))


# =============================================================================
# Shigley / joint equations (stiffness model)
# =============================================================================

def kb_shigley_8_17(At_mm2: float, Ad_mm2: float, E_MPa: float, lt_mm: float, ld_mm: float) -> float:
    """
    Bolt stiffness in the clamped zone (N/mm), Shigley Eq. (8–17):
        k_b = (A_d*A_t*E_b) / (A_d*l_t + A_t*l_d)
    """
    denom = (Ad_mm2 * lt_mm) + (At_mm2 * ld_mm)
    if denom <= 0:
        raise ValueError("Invalid l_t / l_d; denominator <= 0")
    return (Ad_mm2 * At_mm2 * E_MPa) / denom


def k_frustum_shigley_8_20(E_MPa: float, d_hole_mm: float, t_mm: float, D_interface_mm: float) -> float:
    """
    Stiffness of ONE conical frustum (N/mm), Shigley Eq. (8–20) for α≈30°:
        k = 0.5774 * π * E * d / ln( ((1.155 t + D - d)(D + d)) / ((1.155 t + D + d)(D - d)) )
    """
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
    """Combine multiple member frusta stiffnesses in series: 1/k_m = Σ (1/k_i)"""
    inv_sum = 0.0
    for k in frusta_k:
        if k <= 0:
            raise ValueError("All frusta stiffnesses must be > 0")
        inv_sum += 1.0 / k
    if inv_sum <= 0:
        raise ValueError("Invalid frusta list; inv_sum <= 0")
    return 1.0 / inv_sum


def bolt_group_deltaF_from_moment(M_Nmm: float, n_bolts: int, bolt_circle_diameter_mm: float) -> float:
    """
    Bolt-group approximation for bending moment about joint centroid:
        ΔF_max = M / (n * r),  r = BCD/2
    """
    if M_Nmm <= 0:
        return 0.0
    if n_bolts <= 0:
        raise ValueError("n_bolts must be > 0")
    r = bolt_circle_diameter_mm / 2.0
    if r <= 0:
        raise ValueError("bolt_circle_diameter_mm must be > 0")
    return M_Nmm / (n_bolts * r)


# =============================================================================
# Data models
# =============================================================================

@dataclass
class Layer:
    """
    One clamped member layer in the grip.

    Default behavior: model as TWO identical frusta, each thickness = t/2.
    """
    name: str
    E_MPa: float
    thickness_mm: float
    D_interface_mm: float
    frusta_override: Optional[List[Tuple[float, float]]] = None  # [(t_mm, D_interface_mm), ...]


@dataclass
class MomentLoad:
    enabled: bool = False
    M_Nmm: float = 0.0
    bolt_circle_diameter_mm: float = 0.0
    prying_factor: float = 1.0
    note: str = ""


@dataclass
class Inputs:
    # -------------------------------------------------------------------------
    # Separating load inputs (pressure)
    # -------------------------------------------------------------------------
    Pc_psi: float                 # Chamber/internal pressure used as separating load basis (psi)
    effective_diameter_in: float  # Effective diameter that pressure acts on (in)
    n_bolts: int                  # Number of bolts sharing separating load

    # -------------------------------------------------------------------------
    # Bolt geometry + stiffness inputs
    # -------------------------------------------------------------------------
    d_bolt_mm: float              # Nominal bolt diameter (mm)
    d_hole_mm: float              # Clearance hole diameter (mm) for frustum stiffness model
    At_mm2: float                 # Bolt tensile stress area A_t (mm^2)
    Ad_mm2: float                 # Bolt shank area A_d (mm^2)
    Eb_MPa: float                 # Bolt modulus E_b (MPa)

    lt_mm: float                  # Threaded length within grip l_t (mm)  -> uses A_t
    ld_mm: float                  # Unthreaded length within grip l_d (mm)-> uses A_d

    # -------------------------------------------------------------------------
    # Strength allowables
    # -------------------------------------------------------------------------
    Sp_MPa: float                 # Proof strength S_p (MPa). Proof load = S_p * A_t

    # -------------------------------------------------------------------------
    # NASA-STD-5020 preload model inputs (Appendix A)
    # -------------------------------------------------------------------------
    Ppi_nom_N: float              # Nominal installed preload PER BOLT (N)
    Gamma_p: float = 0.20         # Installed preload variation factor Γp (fraction)
                                 # Used in: Ppi_min = Ppi*(1-Γp), Ppi_max = Ppi*(1+Γp)
                                 # NASA-STD-5020 App. A, A.2.1 (PDF p.41), Eq (A.2-1),(A.2-2)

    # Optional minimum total clamp load requirement (gasket/seal/friction etc.)
    L_min_total_N: float = 0.0    # Total minimum clamp load required across the joint (N)
                                 # Used in separation requirement (PDF p.42), Eq (A.2-3b)

    # Design target (dimensionless): require FoS_sep >= target_sep_factor
    target_sep_factor: float = 2.5

    # Optional moment
    moment: MomentLoad = field(default_factory=MomentLoad)


@dataclass
class Results:
    # Separating load
    P_total_N: float
    P_per_bolt_N: float

    # Stiffness + load fraction
    kb_N_per_mm: float
    km_N_per_mm: float
    C: float

    # NASA-style preload min/max about nominal
    Ppi_nom_N: float
    Ppi_min_N: float
    Ppi_max_N: float

    # Bolt force (most-loaded bolt)
    dF_moment_N: float
    Fb_max_N: float

    # Allowables / margins (NASA-style)
    F_proof_allow_N: float
    MS_proof: float

    # Separation requirement and margin (NASA-style)
    Ppi_req_min_N: float
    FoS_sep: float
    MS_sep: float

    # Helper “what bolt count would satisfy target”
    required_bolts_for_target_sep: int


# =============================================================================
# Core computation
# =============================================================================

def compute(inp: Inputs, layers: List[Layer], trace: Optional[CalcTrace] = None) -> Results:
    if inp.n_bolts <= 0:
        raise ValueError("n_bolts must be > 0")
    if inp.Ppi_nom_N <= 0:
        raise ValueError("Ppi_nom_N must be > 0 (nominal installed preload per bolt)")
    if inp.Gamma_p < 0 or inp.Gamma_p >= 1:
        raise ValueError("Gamma_p must be in [0, 1). Typical: 0.10–0.30")
    if inp.target_sep_factor <= 0:
        raise ValueError("target_sep_factor must be > 0")
    if inp.L_min_total_N < 0:
        raise ValueError("L_min_total_N must be >= 0")

    def tadd(s: str) -> None:
        if trace is not None:
            trace.add(s)

    # -------------------------------------------------------------------------
    # 1) Separating load from pressure
    # -------------------------------------------------------------------------
    Pc_MPa = psi_to_MPa(inp.Pc_psi)            # MPa = N/mm^2
    D_eff_mm = in_to_mm(inp.effective_diameter_in)
    A_mm2 = area_circle(D_eff_mm)
    P_total_N = Pc_MPa * A_mm2
    P_per_bolt_N = P_total_N / inp.n_bolts

    tadd(f"Pc = {inp.Pc_psi:.6g} psi -> Pc = {Pc_MPa:.6g} MPa")
    tadd(f"D_eff = {inp.effective_diameter_in:.6g} in -> D_eff = {D_eff_mm:.6g} mm")
    tadd(f"A = (π/4)·D_eff^2 = (π/4)·({D_eff_mm:.6g})^2 = {A_mm2:.6g} mm^2")
    tadd(f"P_total = Pc·A = ({Pc_MPa:.6g})·({A_mm2:.6g}) = {P_total_N:.6g} N")
    tadd(f"P_per_bolt = P_total/N_b = ({P_total_N:.6g})/{inp.n_bolts} = {P_per_bolt_N:.6g} N")

    # -------------------------------------------------------------------------
    # 2) Joint stiffness split (Shigley model) -> C = kb/(kb+km)
    # -------------------------------------------------------------------------
    kb = kb_shigley_8_17(inp.At_mm2, inp.Ad_mm2, inp.Eb_MPa, inp.lt_mm, inp.ld_mm)
    tadd(
        "k_b = (A_d·A_t·E_b)/(A_d·l_t + A_t·l_d) = "
        f"({inp.Ad_mm2:.6g}·{inp.At_mm2:.6g}·{inp.Eb_MPa:.6g})/"
        f"({inp.Ad_mm2:.6g}·{inp.lt_mm:.6g} + {inp.At_mm2:.6g}·{inp.ld_mm:.6g})"
        f" = {kb:.6g} N/mm"
    )

    frusta_ks: List[float] = []
    for layer in layers:
        if layer.frusta_override:
            for (t_i, D_i) in layer.frusta_override:
                k_i = k_frustum_shigley_8_20(layer.E_MPa, inp.d_hole_mm, t_i, D_i)
                frusta_ks.append(k_i)
                tadd(f"k_frustum({layer.name}): t={t_i:.6g}, D={D_i:.6g} -> {k_i:.6g} N/mm")
        else:
            t_half = layer.thickness_mm / 2.0
            k_i = k_frustum_shigley_8_20(layer.E_MPa, inp.d_hole_mm, t_half, layer.D_interface_mm)
            frusta_ks.extend([k_i, k_i])
            tadd(f"k_frustum({layer.name}) x2: t={t_half:.6g}, D={layer.D_interface_mm:.6g} -> {k_i:.6g} N/mm each")

    km = km_series(frusta_ks)
    tadd("k_m = 1 / Σ(1/k_i)  (series combination of frusta)")
    tadd(f"k_m = {km:.6g} N/mm")

    C = kb / (kb + km)
    tadd(f"C = k_b/(k_b+k_m) = {kb:.6g}/({kb:.6g}+{km:.6g}) = {C:.6g}")

    # -------------------------------------------------------------------------
    # 3) NASA-STD-5020 preload min/max about nominal (Appendix A)
    #     Ppi_min = Ppi*(1-Γp), Ppi_max = Ppi*(1+Γp)
    #     Ref: NASA-STD-5020 App. A, A.2.1, PDF p.41 Eq (A.2-1),(A.2-2)
    # -------------------------------------------------------------------------
    Ppi_nom = inp.Ppi_nom_N
    Ppi_min = Ppi_nom * (1.0 - inp.Gamma_p)
    Ppi_max = Ppi_nom * (1.0 + inp.Gamma_p)

    tadd("NASA-STD-5020 App A preload bounds (PDF p.41):")
    tadd(f"Ppi_min = Ppi·(1−Γ_p) = {Ppi_nom:.6g}·(1−{inp.Gamma_p:.6g}) = {Ppi_min:.6g} N")
    tadd(f"Ppi_max = Ppi·(1+Γ_p) = {Ppi_nom:.6g}·(1+{inp.Gamma_p:.6g}) = {Ppi_max:.6g} N")

    # -------------------------------------------------------------------------
    # 4) Moment-induced additional bolt tension (optional)
    # -------------------------------------------------------------------------
    dF_moment = 0.0
    if inp.moment.enabled:
        if inp.moment.bolt_circle_diameter_mm <= 0:
            raise ValueError("Moment enabled but bolt_circle_diameter_mm <= 0")
        if inp.moment.prying_factor < 1.0:
            raise ValueError("prying_factor must be >= 1.0")

        dF_nom = bolt_group_deltaF_from_moment(inp.moment.M_Nmm, inp.n_bolts, inp.moment.bolt_circle_diameter_mm)
        dF_moment = inp.moment.prying_factor * dF_nom

        r = inp.moment.bolt_circle_diameter_mm / 2.0
        tadd(f"ΔF_moment_nom = M/(N_b·r), r=BCD/2 = {r:.6g} mm")
        tadd(f"ΔF_moment = prying_factor·ΔF_nom = {inp.moment.prying_factor:.6g}·{dF_nom:.6g} = {dF_moment:.6g} N")
    else:
        tadd("ΔF_moment = 0 (moment disabled)")

    # -------------------------------------------------------------------------
    # 5) Maximum bolt tension (most-loaded bolt)
    #    Conservative: Fb_max = Ppi_max + C*P_per_bolt + ΔF_moment
    # -------------------------------------------------------------------------
    Fb_max = Ppi_max + C * P_per_bolt_N + dF_moment
    tadd(f"F_b_max = Ppi_max + C·P_per_bolt + ΔF_moment = {Ppi_max:.6g} + {C:.6g}·{P_per_bolt_N:.6g} + {dF_moment:.6g} = {Fb_max:.6g} N")

    # -------------------------------------------------------------------------
    # 6) Proof check as NASA-style margin: MS = Allow/Applied − 1
    #    Allowable here is proof load: F_proof = S_p*A_t
    # -------------------------------------------------------------------------
    F_proof_allow = inp.Sp_MPa * inp.At_mm2
    MS_proof = (F_proof_allow / Fb_max) - 1.0 if Fb_max > 0 else float("inf")
    tadd(f"F_proof_allow = S_p·A_t = {inp.Sp_MPa:.6g}·{inp.At_mm2:.6g} = {F_proof_allow:.6g} N")
    tadd(f"MS_proof = (F_proof_allow/F_b_max) − 1 = ({F_proof_allow:.6g}/{Fb_max:.6g}) − 1 = {MS_proof:.6g}")

    # -------------------------------------------------------------------------
    # 7) Separation requirement (NASA-STD-5020 App. A, Eq A.2-3b, PDF p.42)
    #    Ppi_req_min = ((Fext_sep)*(1 − C) + L_min) / n
    #    Here: Fext_sep = P_total, n = N_bolts, so per bolt:
    #        Ppi_req_min_per_bolt = (1 − C)*P_per_bolt + (L_min_total/N_bolts)
    # -------------------------------------------------------------------------
    L_min_per_bolt = inp.L_min_total_N / inp.n_bolts
    Ppi_req_min = (1.0 - C) * P_per_bolt_N + dF_moment + L_min_per_bolt

    # A “factor of safety” on separation is naturally:
    #   FoS_sep = Ppi_min / Ppi_req_min
    # and MS_sep = FoS_sep − 1
    FoS_sep = (Ppi_min / Ppi_req_min) if Ppi_req_min > 0 else float("inf")
    MS_sep = FoS_sep - 1.0 if math.isfinite(FoS_sep) else float("inf")

    tadd("NASA-STD-5020 separation requirement (PDF p.42, Eq A.2-3b):")
    tadd(f"L_min_per_bolt = L_min_total/N_b = {inp.L_min_total_N:.6g}/{inp.n_bolts} = {L_min_per_bolt:.6g} N")
    tadd(f"Ppi_req_min = (1−C)·P_per_bolt + ΔF_moment + L_min_per_bolt = (1−{C:.6g})·{P_per_bolt_N:.6g} + {dF_moment:.6g} + {L_min_per_bolt:.6g} = {Ppi_req_min:.6g} N")
    tadd(f"FoS_sep = Ppi_min/Ppi_req_min = {Ppi_min:.6g}/{Ppi_req_min:.6g} = {FoS_sep:.6g}")
    tadd(f"MS_sep = FoS_sep − 1 = {MS_sep:.6g}")

    # -------------------------------------------------------------------------
    # 8) Required bolt count estimate for target separation factor
    #    Target requires: FoS_sep >= target_sep_factor
    #    A quick estimate assumes C is unchanged while bolt count varies.
    # -------------------------------------------------------------------------
    denom_total_like = (1.0 - C) * P_total_N + inp.L_min_total_N + dF_moment * inp.n_bolts
    required_bolts = math.ceil(inp.target_sep_factor * denom_total_like / Ppi_min) if Ppi_min > 0 else 10**9
    required_bolts = max(1, required_bolts)

    return Results(
        P_total_N=P_total_N,
        P_per_bolt_N=P_per_bolt_N,
        kb_N_per_mm=kb,
        km_N_per_mm=km,
        C=C,
        Ppi_nom_N=Ppi_nom,
        Ppi_min_N=Ppi_min,
        Ppi_max_N=Ppi_max,
        dF_moment_N=dF_moment,
        Fb_max_N=Fb_max,
        F_proof_allow_N=F_proof_allow,
        MS_proof=MS_proof,
        Ppi_req_min_N=Ppi_req_min,
        FoS_sep=FoS_sep,
        MS_sep=MS_sep,
        required_bolts_for_target_sep=required_bolts,
    )


# =============================================================================
# Output formatting
# =============================================================================

def pretty_print(res: Results, target_sep_factor: float) -> None:
    print("\n=== Separating load ===")
    print(f"P_total          : {res.P_total_N:,.1f} N   ({res.P_total_N/1000:.3f} kN)")
    print(f"P_per_bolt       : {res.P_per_bolt_N:,.1f} N   ({res.P_per_bolt_N/1000:.3f} kN)")

    print("\n=== Stiffness split ===")
    print(f"k_b               : {res.kb_N_per_mm:,.1f} N/mm")
    print(f"k_m               : {res.km_N_per_mm:,.1f} N/mm")
    print(f"C = k_b/(k_b+k_m) : {res.C:.4f}")
    if res.C > 0.6:
        print("WARNING: C is high (>0.6). Often indicates km too low or geometry assumptions are off.")

    print("\n=== NASA-STD-5020 preload bounds (per bolt) ===")
    print(f"Ppi_nom          : {res.Ppi_nom_N:,.1f} N")
    print(f"Ppi_min          : {res.Ppi_min_N:,.1f} N")
    print(f"Ppi_max          : {res.Ppi_max_N:,.1f} N")

    print("\n=== Peak bolt tension (most-loaded bolt) ===")
    print(f"ΔF_moment         : {res.dF_moment_N:,.1f} N")
    print(f"F_b_max           : {res.Fb_max_N:,.1f} N   ({res.Fb_max_N/1000:.3f} kN)")

    print("\n=== Strength (proof) ===")
    print(f"F_proof_allow     : {res.F_proof_allow_N:,.1f} N")
    print(f"MS_proof          : {res.MS_proof:.3f}  (PASS if >= 0)")

    print("\n=== Separation (NASA-style FoS + MS) ===")
    print(f"Ppi_req_min       : {res.Ppi_req_min_N:,.1f} N")
    print(f"FoS_sep           : {res.FoS_sep:.3f}  (PASS if >= {target_sep_factor:.2f})")
    print(f"MS_sep            : {res.MS_sep:.3f}")
    print(f"Required bolts for target sep (estimate): {res.required_bolts_for_target_sep:d}")


# =============================================================================
# PDF report
# =============================================================================

def make_pdf_report(
    inp: Inputs,
    layers: List[Layer],
    res: Results,
    filename: str = "flange_report.pdf",
    calculation_trace: Optional[List[str]] = None,
) -> None:
    inputs_dict: Dict[str, Any] = {
        "Pc (psi)": inp.Pc_psi,
        "Effective pressure diameter (in)": inp.effective_diameter_in,
        "Bolt count (N_b)": inp.n_bolts,

        "Bolt nominal diameter d (mm)": inp.d_bolt_mm,
        "Clearance hole diameter d_h (mm)": inp.d_hole_mm,
        "A_t (mm^2)": inp.At_mm2,
        "A_d (mm^2)": inp.Ad_mm2,
        "Bolt modulus E_b (MPa)": inp.Eb_MPa,
        "Threaded length l_t (mm)": inp.lt_mm,
        "Unthreaded length l_d (mm)": inp.ld_mm,

        "Proof strength S_p (MPa)": inp.Sp_MPa,

        "Ppi_nom (N) (per bolt)": inp.Ppi_nom_N,
        "Γ_p (preload variation factor)": inp.Gamma_p,
        "L_min_total (N)": inp.L_min_total_N,
        "Target separation factor": inp.target_sep_factor,

        "Member layers": ", ".join(
            [f"{ly.name} (t={ly.thickness_mm}mm, E={ly.E_MPa}MPa, D={ly.D_interface_mm}mm)" for ly in layers]
        ),
    }

    if inp.moment.enabled:
        inputs_dict["Moment M (N·mm)"] = inp.moment.M_Nmm
        inputs_dict["Bolt circle diameter BCD (mm)"] = inp.moment.bolt_circle_diameter_mm
        inputs_dict["Prying factor"] = inp.moment.prying_factor

    inputs_dict = {subify(k): v for k, v in inputs_dict.items()}

    results_dict: Dict[str, Any] = {
        "P_total (N)": res.P_total_N,
        "P_per_bolt (N)": res.P_per_bolt_N,

        "k_b (N/mm)": res.kb_N_per_mm,
        "k_m (N/mm)": res.km_N_per_mm,
        "C = k_b/(k_b+k_m)": res.C,

        "Ppi_nom (N)": res.Ppi_nom_N,
        "Ppi_min (N)": res.Ppi_min_N,
        "Ppi_max (N)": res.Ppi_max_N,

        "ΔF_moment (N)": res.dF_moment_N,
        "F_b_max (N)": res.Fb_max_N,

        "F_proof_allow (N) = S_p·A_t": res.F_proof_allow_N,
        "MS_proof = (Allow/Applied) − 1": res.MS_proof,

        "Ppi_req_min (N)": res.Ppi_req_min_N,
        "FoS_sep = Ppi_min/Ppi_req_min": res.FoS_sep,
        "MS_sep = FoS_sep − 1": res.MS_sep,

        "Required bolts for target separation (estimate)": res.required_bolts_for_target_sep,
    }
    results_dict = {subify(k): v for k, v in results_dict.items()}

    checks: Dict[str, bool] = {
        "Proof (MS_proof >= 0)": res.MS_proof >= 0.0,
        f"Separation (FoS_sep >= target={inp.target_sep_factor:.2f})": res.FoS_sep >= inp.target_sep_factor,
        "Sanity: C <= 0.6": res.C <= 0.6,
    }

    notes = (
        "Preload bounds and separation requirement follow NASA-STD-5020 Appendix A (A.2.1): "
        "Ppi_min/Ppi_max per Eq. (A.2-1)/(A.2-2) and Ppi_req_min per Eq. (A.2-3b). "
        "Joint stiffness split (C) uses a Shigley-style stiffness model."
    )

    build_flange_pdf_report(
        filename=filename,
        project_title="SPARK-2 MCC–Injector/Faceplate Flange Joint",
        inputs=inputs_dict,
        results=results_dict,
        checks=checks,
        notes=notes,
        warnings=[],
        calculation_trace=calculation_trace,
    )


# =============================================================================
# INPUTS HERE (edit values)
# =============================================================================

def build_inputs() -> Tuple[Inputs, List[Layer]]:
    """
    Edit only this function to change joint configuration.
    Everything else uses these objects.
    """

    inp = Inputs(
        # --- Pressure / separating load ---
        Pc_psi=300.0,                 # Chamber/internal pressure used for the separating load (psi)
        effective_diameter_in=3.53,   # Effective pressure diameter (in) that pressure acts over
        n_bolts=8,                    # Number of bolts sharing the separating load

        # --- Bolt geometry + material stiffness inputs ---
        d_bolt_mm=8.0,                # Nominal bolt diameter (mm)
        d_hole_mm=8.5,                # Clearance hole diameter (mm) used in member frustum stiffness model
        At_mm2=36.6,                  # Tensile stress area A_t (mm^2) (threaded section area)
        Ad_mm2=50.3,                  # Shank area A_d (mm^2) (unthreaded body area)
        Eb_MPa=200_000.0,             # Bolt modulus E_b (MPa). Steel ~ 200k, Ti ~ 110k

        # --- Grip / stretch lengths for bolt stiffness split ---
        lt_mm=10.0,                   # Threaded length within the clamped grip (mm) -> uses A_t
        ld_mm=20.0,                   # Unthreaded length within the clamped grip (mm) -> uses A_d

        # --- Strength allowable ---
        Sp_MPa=600.0,                 # Proof strength S_p (MPa). Proof load = S_p * A_t

        # --- NASA-STD-5020 preload model inputs (Appendix A) ---
        Ppi_nom_N=12_000.0,           # Nominal installed preload PER BOLT (N)
        Gamma_p=0.20,                 # Installed preload variation factor Γ_p (fraction)
                                      # NASA-STD-5020 Appendix A (PDF p.41) Eq (A.2-1),(A.2-2)

        # --- Optional minimum total clamp requirement across the joint ---
        L_min_total_N=0.0,            # Total minimum clamp load required (gasket/seal/friction), N
                                      # NASA-STD-5020 (PDF p.42) Eq (A.2-3b)

        # --- Design target on separation ---
        target_sep_factor=2.5,        # Require FoS_sep >= this value (dimensionless)

        moment=MomentLoad(
            enabled=False,
            M_Nmm=0.0,
            bolt_circle_diameter_mm=0.0,
            prying_factor=1.0,
            note="Set enabled=True and fill in values if bending effects are required."
        ),
    )

    # ---- MEMBER STACK ----
    # Replace D_interface_mm with a washer OD / under-head bearing diameter
    dw_guess = 1.5 * inp.d_bolt_mm

    layers = [
        Layer(
            name="Flange stack (symmetric)",
            E_MPa=71_000.0,           # Example: Aluminum ~ 69–71 GPa => 69,000–71,000 MPa
            thickness_mm=12.0,        # Total clamped thickness for this layer (mm)
            D_interface_mm=dw_guess,  # Bearing/washer diameter at interface (mm)
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

    pretty_print(res, target_sep_factor=inp.target_sep_factor)
    make_pdf_report(inp, layers, res, filename="flange_report.pdf", calculation_trace=trace.lines)

    print("\nPDF generated: flange_report.pdf")


if __name__ == "__main__":
    main()
