#!/usr/bin/env python3
"""Keep the 3D towers and the activity radar on the skyline SVGs.

The pinned action's normal chart always adds a pie, a radar, and a count
overlay. Its settings have no switch for towers plus radar only, so this
script drops the other two groups and tightens the viewBox around what stays.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "profile-3d-contrib" / "profile-skyline-dark.svg",
    ROOT / "profile-3d-contrib" / "profile-skyline-light.svg",
]
SVG = "http://www.w3.org/2000/svg"
ALLOWED_TEXT = re.compile(r"^(?:Commit|Issue|PullReq|Review|Repo|[0-9]+K?)$")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ops(transform: str) -> list[tuple[str, list[float]]]:
    ops = []
    for name, args in re.findall(r"([A-Za-z]+)\(([^)]*)\)", transform or ""):
        nums = [float(item) for item in re.split(r"[,\s]+", args.strip()) if item]
        ops.append((name, nums))
    return ops


def apply_op(name: str, nums: list[float], x: float, y: float) -> tuple[float, float]:
    if name == "translate":
        return x + nums[0], y + (nums[1] if len(nums) > 1 else 0.0)
    if name == "scale":
        sy = nums[1] if len(nums) > 1 else nums[0]
        return x * nums[0], y * sy
    if name == "skewX":
        return x + y * math.tan(math.radians(nums[0])), y
    if name == "skewY":
        return x, y + x * math.tan(math.radians(nums[0]))
    if name == "matrix" and len(nums) >= 6:
        a, b, c, d, e, f = nums[:6]
        return a * x + c * y + e, b * x + d * y + f
    return x, y


def map_point(transforms: list[str], x: float, y: float) -> tuple[float, float]:
    for transform in reversed(transforms):
        for name, nums in reversed(parse_ops(transform)):
            x, y = apply_op(name, nums, x, y)
    return x, y


def has_class(element: ET.Element, name: str) -> bool:
    for node in element.iter():
        classes = (node.get("class") or "").split()
        if name in classes or any(item.startswith(name) for item in classes):
            return True
    return False


def is_radar(element: ET.Element) -> bool:
    return has_class(element, "radar")


def is_towers(element: ET.Element) -> bool:
    return has_class(element, "cont-")


def collect_points(element: ET.Element) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    def walk(node: ET.Element, transforms: list[str]) -> None:
        here = transforms + ([node.get("transform")] if node.get("transform") else [])
        kind = local(node.tag)
        if kind == "rect" and (node.get("class") or "").startswith("cont-"):
            width = float(node.get("width") or 0)
            height = float(node.get("height") or 0)
            for corner in ((0, 0), (width, 0), (0, height), (width, height)):
                points.append(map_point(here, *corner))
        elif kind == "polygon":
            raw = node.get("points") or ""
            nums = [float(item) for item in re.split(r"[,\s]+", raw.strip()) if item]
            for index in range(0, len(nums) - 1, 2):
                points.append(map_point(here, nums[index], nums[index + 1]))
        elif kind == "line":
            points.append(map_point(here, float(node.get("x1") or 0), float(node.get("y1") or 0)))
            points.append(map_point(here, float(node.get("x2") or 0), float(node.get("y2") or 0)))
        elif kind == "text":
            x = float(node.get("x") or 0)
            y = float(node.get("y") or 0)
            style = node.get("style") or ""
            match = re.search(r"font-size:\s*([0-9.]+)", style)
            size = float(match.group(1)) if match else float(node.get("font-size") or 16)
            label = node.text or ""
            anchor = node.get("text-anchor") or "start"
            width = max(size, len(label) * size * 0.62)
            left = width / 2 if anchor == "middle" else (width if anchor == "end" else 0)
            right = width / 2 if anchor == "middle" else (0 if anchor == "end" else width)
            for corner in ((x - left, y - size), (x + right, y + size * 0.4)):
                points.append(map_point(here, *corner))
        for child in list(node):
            walk(child, here)

    walk(element, [])
    return points


def crop_viewbox(root: ET.Element, kept: list[ET.Element]) -> None:
    points: list[tuple[float, float]] = []
    for element in kept:
        points.extend(collect_points(element))
    if not points:
        raise SystemExit("skyline has no geometry to crop to")
    pad = 16.0
    min_x = min(point[0] for point in points) - pad
    min_y = min(point[1] for point in points) - pad
    max_x = max(point[0] for point in points) + pad
    max_y = max(point[1] for point in points) + pad
    width = max_x - min_x
    height = max_y - min_y
    root.set("viewBox", f"{min_x:.2f} {min_y:.2f} {width:.2f} {height:.2f}")
    root.set("width", f"{width:.0f}")
    root.set("height", f"{height:.0f}")
    for node in root.iter():
        if local(node.tag) == "rect" and node.get("class") == "fill-bg":
            node.set("x", f"{min_x:.2f}")
            node.set("y", f"{min_y:.2f}")
            node.set("width", f"{width:.2f}")
            node.set("height", f"{height:.2f}")


def strip(path: Path) -> None:
    ET.register_namespace("", SVG)
    tree = ET.parse(path)
    root = tree.getroot()
    removed: list[str] = []
    for child in list(root):
        if local(child.tag) != "g":
            continue
        if is_towers(child) or is_radar(child):
            continue
        root.remove(child)
        removed.append(child.get("transform") or "info overlay")
    kept = [child for child in list(root) if local(child.tag) == "g"]
    if not any(is_towers(child) for child in kept):
        raise SystemExit(f"{path} has no 3D contribution towers")
    if not any(is_radar(child) for child in kept):
        raise SystemExit(f"{path} has no activity radar")
    crop_viewbox(root, kept)
    tree.write(path, encoding="unicode", xml_declaration=False)
    text = path.read_text()
    if not text.startswith("<svg"):
        text = '<?xml version="1.0" encoding="UTF-8"?>\n' + text
        path.write_text(text)
    for node in ET.parse(path).getroot().iter():
        if local(node.tag) == "text" and node.text and not ALLOWED_TEXT.match(node.text.strip()):
            raise SystemExit(f"{path} still has unexpected text {node.text!r}")
    for label in ("Commit", "Issue", "PullReq", "Review", "Repo"):
        if label not in text:
            raise SystemExit(f"{path} lost radar label {label}")
    if "cont-top-0" not in text or 'class="radar"' not in text:
        raise SystemExit(f"{path} lost the towers or the radar")
    print(
        f"{path.relative_to(ROOT)}: removed {len(removed)} overlay group(s); "
        f"viewBox {root.get('viewBox')}"
    )


def main() -> int:
    missing = [str(path) for path in TARGETS if not path.is_file()]
    if missing:
        raise SystemExit("missing skyline svg: " + ", ".join(missing))
    for path in TARGETS:
        strip(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
