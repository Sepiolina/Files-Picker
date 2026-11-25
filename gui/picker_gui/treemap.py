"""Squarified treemap layout (Bruls, Huizing & van Wijk) - no Qt, shared by the Space tab and reports."""


def squarify(values, x, y, w, h):
    """
    values: positive numbers sorted from largest to smallest.
    Returns one (x, y, w, h) rectangle per value, filling the box x, y, w, h,
    with areas proportional to the values and tiles as square as possible.
    """
    n = len(values)
    total = float(sum(values))
    if n == 0 or total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0.0, 0.0)] * n
    scale = w * h / total
    areas = [v * scale for v in values]
    rects = []

    def worst(row, side):
        s = sum(row)
        return max(max(side * side * a / (s * s), (s * s) / (side * side * a)) for a in row)

    i = 0
    while i < n:
        side = min(w, h)
        row = [areas[i]]
        j = i + 1
        while j < n and worst(row + [areas[j]], side) <= worst(row, side):
            row.append(areas[j])
            j += 1
        s = sum(row)
        if w >= h:                      # a column along the left edge
            cw = s / h
            yy = y
            for a in row:
                rects.append((x, yy, cw, a / cw))
                yy += a / cw
            x, w = x + cw, w - cw
        else:                           # a row along the top edge
            rh = s / w
            xx = x
            for a in row:
                rects.append((xx, y, a / rh, rh))
                xx += a / rh
            y, h = y + rh, h - rh
        i = j
    return rects
