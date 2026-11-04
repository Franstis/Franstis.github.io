"""Place the nodes of a small network for drawing (no dependencies).

A force layout: linked nodes pull together (more for heavier links), all
nodes push apart, a weak pull keeps them near the middle, and a last pass
separates circles that overlap. The same input always gives the same picture.
"""
import math
import random


def layout(nodes, links, width, height, iterations=600, seed=1, pin=None, anchors=None):
    """nodes: [{"id", "r"}] (optional "space": room to keep around the circle,
    for its name); links: [{"source", "target", "weight"}].
    Returns {id: (x, y)} inside width x height, keeping each circle inside.
    pin: id of a node to keep in the middle (for example the author themself).
    anchors: {id: (x, y)} in -0.5..0.5, points that pull nodes gently (groups)."""
    rng = random.Random(seed)
    ids = [n["id"] for n in nodes]
    radius = {n["id"]: n["r"] for n in nodes}
    space = {n["id"]: n.get("space", n["r"]) for n in nodes}
    anchors = anchors or {}
    count = len(ids)
    if not count:
        return {}
    # Start on a circle, in a fixed shuffled order
    order = ids[:]
    rng.shuffle(order)
    pos = {}
    for i, node in enumerate(order):
        angle = 2 * math.pi * i / count
        ax, ay = anchors.get(node, (math.cos(angle) * 0.4, math.sin(angle) * 0.4))
        pos[node] = [ax + rng.uniform(-0.05, 0.05), ay + rng.uniform(-0.05, 0.05)]
    if pin in pos:
        pos[pin] = [0.0, 0.0]
    edges = [(l["source"], l["target"], l.get("weight", 1)) for l in links
             if l["source"] in pos and l["target"] in pos and l["source"] != l["target"]]
    top = max((w for _, _, w in edges), default=1)
    k = 0.9 / math.sqrt(count)  # ideal distance
    temperature = 0.1
    for step in range(iterations):
        move = {node: [0.0, 0.0] for node in ids}
        for i in range(count):
            a = ids[i]
            for j in range(i + 1, count):
                b = ids[j]
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d2 = dx * dx + dy * dy or 1e-6
                force = k * k / d2
                move[a][0] += dx * force; move[a][1] += dy * force
                move[b][0] -= dx * force; move[b][1] -= dy * force
        for a, b, w in edges:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 1e-6
            force = d / k * (0.3 + 0.7 * w / top)
            move[a][0] -= dx * force; move[a][1] -= dy * force
            move[b][0] += dx * force; move[b][1] += dy * force
        for node in ids:
            ax, ay = anchors.get(node, (0.0, 0.0))
            pull = 1.2 if node in anchors else 0.6
            move[node][0] -= (pos[node][0] - ax) * pull
            move[node][1] -= (pos[node][1] - ay) * pull
            if node == pin:
                continue
            d = math.hypot(*move[node]) or 1e-6
            pos[node][0] += move[node][0] / d * min(d, temperature)
            pos[node][1] += move[node][1] / d * min(d, temperature)
        temperature = max(0.002, temperature * 0.992)
    # Scale into the box, keeping the aspect ratio of the layout
    xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
    pad = max(space.values()) + 6
    span_x = (max(xs) - min(xs)) or 1; span_y = (max(ys) - min(ys)) or 1
    scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    out = {node: [width / 2 + (p[0] - cx) * scale, height / 2 + (p[1] - cy) * scale] for node, p in pos.items()}
    # Push apart circles that overlap, then keep them inside the box
    for _ in range(200):
        moved = False
        for i in range(count):
            a = ids[i]
            for j in range(i + 1, count):
                b = ids[j]
                dx, dy = out[a][0] - out[b][0], out[a][1] - out[b][1]
                d = math.hypot(dx, dy) or 1e-6
                gap = space[a] + space[b] + 2 - d
                if gap > 0:
                    moved = True
                    ux, uy = dx / d, dy / d
                    out[a][0] += ux * gap / 2; out[a][1] += uy * gap / 2
                    out[b][0] -= ux * gap / 2; out[b][1] -= uy * gap / 2
        for node in ids:
            r = radius[node] + 14
            out[node][0] = min(max(out[node][0], r), width - r)
            out[node][1] = min(max(out[node][1], r), height - r)
        if not moved:
            break
    return {node: (round(p[0], 1), round(p[1], 1)) for node, p in out.items()}
