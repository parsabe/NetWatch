"""All the math from the slides, as small functions."""
import math
from collections import Counter

from scipy.stats import beta


def asr(successes, n):
    """Step 2a: empirical ASR  p_hat = s / n."""
    return successes / n


def clopper_pearson_upper(successes, n, alpha=0.05):
    """Step 2b: one-sided (1-alpha) upper bound p_bar."""
    if successes >= n:
        return 1.0
    return float(beta.ppf(1 - alpha, successes + 1, n - successes))


def dro_worst_case(p, rho):
    """Step 2c: g_rho(p) = min{1, p + sqrt(2 rho p (1-p))}.

    Valid when rho <= (1-p)/(2p); beyond that we return the conservative value 1.
    """
    if p <= 0:
        return 0.0
    if rho > (1 - p) / (2 * p):
        return 1.0
    return min(1.0, p + math.sqrt(2 * rho * p * (1 - p)))


def rho_between_mixes(test_categories, other_categories):
    """Chi-square distance D(Q||P) = 1/2 * sum_c P(c) * (Q(c)/P(c) - 1)^2.

    test_categories / other_categories: lists of attack-type labels.
    P = mix in your test set, Q = mix in another dataset.
    Categories present in Q but absent from P make the distance infinite
    (your test set never saw them) -> returned as math.inf.
    """
    P, Q = Counter(test_categories), Counter(other_categories)
    nP, nQ = sum(P.values()), sum(Q.values())
    if any(c not in P for c in Q):
        return math.inf
    total = 0.0
    for c in P:
        p_c, q_c = P[c] / nP, Q.get(c, 0) / nQ
        total += p_c * (q_c / p_c - 1) ** 2
    return 0.5 * total


def forging_probability(keyspace_size, attempts=1):
    """Step 1: P_forge <= q / |K|."""
    return min(1.0, attempts / keyspace_size)


def combined_risk(keyspace_size, attempts, p_bar, p_adp_bar, rho):
    """Step 4: R = min{1, q/|K| + max(g_rho(p_bar), p_adp_bar)}."""
    return min(1.0, forging_probability(keyspace_size, attempts)
               + max(dro_worst_case(p_bar, rho), p_adp_bar))


def choose_defense(results, eta=0.05, baseline="none"):
    """Step 5: argmin risk subject to utility >= U_none - eta.

    results: dict name -> {"risk": float, "utility": float}
    """
    u0 = results[baseline]["utility"]
    ok = {k: v for k, v in results.items() if v["utility"] >= u0 - eta}
    return min(ok, key=lambda k: ok[k]["risk"]) if ok else None


if __name__ == "__main__":
    # Reproduces the numbers from the slides / chat.
    p_bar = clopper_pearson_upper(20, 1000)
    print(f"p_hat = {asr(20, 1000):.3f}, p_bar = {p_bar:.4f}")       # 0.020, ~0.029
    print(f"g_0.1(p_bar) = {dro_worst_case(p_bar, 0.1):.4f}")        # ~0.104
    p_adp = clopper_pearson_upper(60, 500)
    print(f"p_adp_bar = {p_adp:.4f}")                                 # ~0.147
    print(f"risk = {combined_risk(math.factorial(26), 1000, p_bar, p_adp, 0.1):.4f}")

    # Toy rho example: test mix 90/10, real mix 70/30 -> rho ~ 0.22
    test = ["polite"] * 900 + ["roleplay"] * 100
    real = ["polite"] * 700 + ["roleplay"] * 300
    print(f"rho(90/10 -> 70/30) = {rho_between_mixes(test, real):.3f}")
