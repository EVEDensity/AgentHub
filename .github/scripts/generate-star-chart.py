"""Fetch stargazer data from GitHub API and generate an SVG star chart.

Usage:
    python .github/scripts/generate-star-chart.py

Requires GITHUB_TOKEN env var (or GITHUB_TOKEN from Actions context).
Outputs to .github/star-chart.svg
"""

import json
import os
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone


REPO = "EVEDensity/AgentHub"
OUTPUT = ".github/star-chart.svg"

CHART_W = 720
CHART_H = 300
PAD_L = 60
PAD_R = 30
PAD_T = 30
PAD_B = 50


def fetch_stargazers(token):
    """Fetch all stargazer timestamps via paginated API."""
    stars = []
    page = 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/stargazers?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github.v3.star+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "AgentHub-star-chart",
            },
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        if not data:
            break
        for entry in data:
            stars.append(entry["starred_at"])
        page += 1
    return stars


def bin_by_month(stars):
    """Bin star timestamps into monthly buckets, return sorted list of (month_label, count)."""
    monthly = OrderedDict()
    for ts in stars:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        key = dt.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + 1

    cumulative = 0
    bins = []
    for month in sorted(monthly):
        cumulative += monthly[month]
        bins.append((month, cumulative))
    return bins


def generate_svg(bins):
    """Generate an SVG line chart of cumulative stars."""
    n = len(bins)
    if n < 2:
        return _empty_svg()

    max_stars = max(c for _, c in bins)
    if max_stars == 0:
        return _empty_svg()

    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B

    # Data points
    points = []
    for i, (month, count) in enumerate(bins):
        x = PAD_L + (i / (n - 1)) * plot_w
        y = PAD_T + plot_h - (count / max_stars) * plot_h
        points.append((x, y))

    # Grid lines (4 horizontal)
    grid_lines = ""
    y_labels = ""
    for i in range(5):
        y_val = int(max_stars * i / 4)
        y_pos = PAD_T + plot_h - (y_val / max_stars) * plot_h
        grid_lines += f'<line x1="{PAD_L}" y1="{y_pos}" x2="{CHART_W - PAD_R}" y2="{y_pos}" stroke="#e5e7eb" stroke-width="1"/>\n'
        y_labels += f'<text x="{PAD_L - 8}" y="{y_pos + 4}" text-anchor="end" fill="#9ca3af" font-size="11">{y_val}</text>\n'

    # X labels (show every ~6 months)
    x_labels = ""
    for i, (month, _) in enumerate(bins):
        if i % max(1, n // 8) == 0 or i == n - 1:
            x_pos = PAD_L + (i / (n - 1)) * plot_w
            label = month[2:] if n > 12 else month
            x_labels += f'<text x="{x_pos}" y="{CHART_H - PAD_B + 20}" text-anchor="middle" fill="#9ca3af" font-size="11">{label}</text>\n'

    # Area fill
    area_parts = [f"M{points[0][0]},{PAD_T + plot_h}"]
    for x, y in points:
        area_parts.append(f"L{x},{y}")
    area_parts.append(f"L{points[-1][0]},{PAD_T + plot_h}Z")
    area_d = " ".join(area_parts)

    # Line path
    line_parts = [f"M{points[0][0]},{points[0][1]}"]
    for x, y in points[1:]:
        line_parts.append(f"L{x},{y}")
    line_d = " ".join(line_parts)

    # Dots (fewer dots for readability)
    dots = ""
    dot_interval = max(1, n // 15)
    for i, (x, y) in enumerate(points):
        if i == 0 or i == n - 1 or i % dot_interval == 0:
            dots += f'<circle cx="{x}" cy="{y}" r="3" fill="#6366f1" stroke="#fff" stroke-width="1.5"/>\n'

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_W}" height="{CHART_H}" viewBox="0 0 {CHART_W} {CHART_H}">
  <rect width="{CHART_W}" height="{CHART_H}" fill="#ffffff" rx="8"/>
  {grid_lines}
  {y_labels}
  <path d="{area_d}" fill="url(#grad)" opacity="0.15"/>
  <defs>
    <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#6366f1"/>
      <stop offset="100%" stop-color="#6366f1" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <path d="{line_d}" fill="none" stroke="#6366f1" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  {dots}
  {x_labels}
  <text x="{CHART_W / 2}" y="18" text-anchor="middle" fill="#374151" font-size="14" font-weight="bold">Star History — {REPO}</text>
</svg>"""
    return svg


def _empty_svg():
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_W}" height="{CHART_H}">
  <rect width="{CHART_W}" height="{CHART_H}" fill="#ffffff" rx="8"/>
  <text x="{CHART_W / 2}" y="{CHART_H / 2}" text-anchor="middle" fill="#9ca3af" font-size="14">
    Not enough data to generate star chart yet.
  </text>
</svg>"""


def main():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN or GH_TOKEN env var required")
        exit(1)

    print(f"Fetching stargazers for {REPO}...")
    stars = fetch_stargazers(token)
    print(f"Found {len(stars)} stargazers")

    if not stars:
        print("No stargazers found, generating empty chart")
        svg = _empty_svg()
    else:
        bins = bin_by_month(stars)
        print(f"Data spans {len(bins)} months, latest: {bins[-1][0]} ({bins[-1][1]} stars)")
        svg = generate_svg(bins)

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as f:
        f.write(svg)
    print(f"Chart written to {OUTPUT}")


if __name__ == "__main__":
    main()
