"""Génère les courbes de saturation à partir des résultats Locust (TP5).

Usage :
    python scripts/plot_saturation.py results/w1 results/w2
    python scripts/plot_saturation.py results/w1               # une seule config
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib.pyplot as plt

ROUTE = "/v1/predict"
SLO_MS = 200


def read_paliers(dossier: Path) -> list[dict]:
    rows = []
    for path in sorted(dossier.glob("u*_stats.csv"), key=lambda p: int(re.findall(r"\d+", p.stem)[0])):
        users = int(re.findall(r"\d+", path.stem)[0])
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row["Name"] == ROUTE:
                    rows.append(
                        {
                            "users": users,
                            "debit": float(row["Requests/s"]),
                            "p50": float(row["50%"]),
                            "p95": float(row["95%"]),
                            "p99": float(row["99%"]),
                        }
                    )
    return rows


def plot(configs: dict[str, list[dict]], out: Path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    for label, rows in configs.items():
        users = [r["users"] for r in rows]
        ax1.plot(users, [r["debit"] for r in rows], "o-", label=label)
        ax2.plot(users, [r["p95"] for r in rows], "o-", label=f"{label} p95")
        ax2.plot(users, [r["p50"] for r in rows], "s--", alpha=0.5, label=f"{label} p50")

    ax1.set_xlabel("Usagers simultanés")
    ax1.set_ylabel("Débit (req/s)")
    ax1.set_title("Débit selon la charge")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    ax2.axhline(y=SLO_MS, color="red", linestyle="--", linewidth=2, label=f"SLO {SLO_MS} ms")
    ax2.set_xlabel("Usagers simultanés")
    ax2.set_ylabel("Latence (ms)")
    ax2.set_title("Latence selon la charge")
    ax2.set_yscale("log")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    fig.suptitle("Courbe de saturation — API Vélo'v (mesure locale, 1 cœur par worker)", fontsize=13)
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Graphique enregistré : {out}")


def main():
    parser = argparse.ArgumentParser(description="Courbes de saturation Locust")
    parser.add_argument("dossiers", nargs="+", type=Path, help="Dossiers de résultats (ex: results/w1 results/w2)")
    parser.add_argument("--out", type=Path, default=Path("results/saturation.png"))
    args = parser.parse_args()

    configs = {}
    for d in args.dossiers:
        label = d.name
        rows = read_paliers(d)
        if rows:
            configs[label] = rows

    plot(configs, args.out)


if __name__ == "__main__":
    main()
