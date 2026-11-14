"""Renders captured terminal output of scribd-downloader.py as a PNG for the README.

Usage: python extension/tools/render_terminal.py OUTPUT.txt assets/python-terminal.png "command line"
"""
import sys

from PIL import Image, ImageDraw, ImageFont

text_path, out_path, command = sys.argv[1], sys.argv[2], sys.argv[3]
lines = [f"> {command}"] + open(text_path, encoding="utf-8").read().rstrip().split("\n")

SCALE = 2
font = ImageFont.truetype("consola.ttf", 14 * SCALE)
bold = ImageFont.truetype("consolab.ttf", 14 * SCALE)
line_height = 20 * SCALE
pad = 18 * SCALE
bar = 34 * SCALE
width = int(max(font.getlength(line) for line in lines)) + 2 * pad
height = bar + 2 * pad + line_height * len(lines)

image = Image.new("RGB", (width, height), (20, 20, 20))
draw = ImageDraw.Draw(image)
draw.rectangle((0, 0, width, bar), fill=(42, 42, 41))
draw.text((pad, bar // 2), "Terminal", font=font, fill=(207, 201, 187), anchor="lm")

y = bar + pad
for index, line in enumerate(lines):
    done = "saved successfully" in line
    color = (251, 248, 241) if index == 0 or done else (190, 186, 176)
    draw.text((pad, y), line, font=bold if index == 0 or done else font, fill=color)
    y += line_height

image.save(out_path)
print("wrote", out_path, image.size)
