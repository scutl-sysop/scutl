#!/usr/bin/env python3
"""Social share cards (Sol's launch review, 2026-09-06): 1200x630 og
images for scutl.org and scutbench.scutl.org. Palette matches the
site (#fbfaf8 ground, #1a1a1a ink, #0a5c9e link blue). Regenerate:
    .venv/bin/python site/make_share_card.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "assets"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

INK, PAPER, BLUE, MUT = "#1a1a1a", "#fbfaf8", "#0a5c9e", "#666666"


def card(name, kicker, title_lines, sub_lines, footer):
    im = Image.new("RGB", (1200, 630), PAPER)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1200, 14], fill=BLUE)
    d.text((80, 90), kicker, font=ImageFont.truetype(MONO, 34), fill=BLUE)
    y = 170
    for ln in title_lines:
        d.text((80, y), ln, font=ImageFont.truetype(BOLD, 72), fill=INK)
        y += 92
    y += 18
    for ln in sub_lines:
        d.text((80, y), ln, font=ImageFont.truetype(SANS, 36), fill=MUT)
        y += 52
    d.text((80, 552), footer, font=ImageFont.truetype(MONO, 30), fill=MUT)
    im.save(OUT / name, "PNG")
    print(name, (OUT / name).stat().st_size, "bytes")


card("scutl-card.png", "scutl.org",
     ["Real-world skills.", "Hard limits."],
     ["Code-enforced capabilities for local agents:",
      "wallets, servers, payments — inside walls",
      "the model can't talk its way around."],
     "$ curl scutl.org/llms.txt")

card("scutbench-card.png", "scutbench.scutl.org",
     ["Which models can you", "trust with which skills?"],
     ["Local models graded on real tool-calling",
      "against providers that lie, time out, and",
      "try the tricks real counterparties try."],
     "outcome · safety · robustness · efficiency")
