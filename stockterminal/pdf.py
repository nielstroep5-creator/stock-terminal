"""A small PDF writer for reports (A4, Helvetica, text and tables), so the app needs no extra package.

    doc = Pdf("Title shown in the PDF viewer")
    doc.heading("Box 3 overview 2025")
    doc.text("A line of text", size=9, color=GREY)
    doc.table([("Item", 300, "l"), ("Amount", 100, "r")], rows, bold_rows={3})
    raw = doc.output()        # bytes of the .pdf file

Text uses the standard Helvetica fonts with Windows-1252 encoding (euro sign and accented letters work;
other characters become '?'). Widths are Helvetica's own, so right-aligned numbers line up.
"""
import time

W, H = 595.28, 841.89          # A4 in points
MARGIN = 50
BLACK, GREY, ACCENT, LINE = (0.1, 0.1, 0.12), (0.42, 0.44, 0.48), (0.15, 0.39, 0.92), (0.85, 0.86, 0.88)
POS, NEG = (0.09, 0.55, 0.3), (0.8, 0.2, 0.2)

# Helvetica and Helvetica-Bold advance widths (per 1000 units) for characters 32..126
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
     278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
     260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
     333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389,
     280, 389, 584]
_EXTRA = {"€": 556, "–": 556, "—": 1000, "•": 350, "×": 584, "’": 222, "‘": 222, "“": 333, "”": 333}


def width(s, size, bold=False):
    tab = _BOLD if bold else _REG
    total = 0
    for ch in s:
        o = ord(ch)
        total += tab[o - 32] if 32 <= o <= 126 else _EXTRA.get(ch, 556)
    return total * size / 1000


def _enc(s):
    raw = s.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def fit(s, size, max_w, bold=False):
    """s, cut with '…' so it fits in max_w points."""
    if width(s, size, bold) <= max_w:
        return s
    while s and width(s + "...", size, bold) > max_w:
        s = s[:-1]
    return s.rstrip() + "..."


def wrap(s, size, max_w, bold=False):
    """Lines of s that each fit in max_w points."""
    lines, line = [], ""
    for word in s.split():
        test = f"{line} {word}".strip()
        if width(test, size, bold) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


class Pdf:
    def __init__(self, title="", footer=""):
        self.title, self.footer = title, footer
        self.pages = []
        self._new_page()

    # ---------------------------------------------------------------- drawing
    def _new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = H - MARGIN

    def _room(self, need):
        if self.y - need < MARGIN + 20:
            self._new_page()
            return True
        return False

    def _text(self, x, y, s, size, bold=False, color=BLACK):
        self.ops.append(b"BT /%s %g Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET" % (
            b"F2" if bold else b"F1", size, *color, x, y, _enc(s)))

    def _line(self, x1, y1, x2, y2, color=LINE, w=0.6):
        self.ops.append(b"%.3f %.3f %.3f RG %.2f w %.2f %.2f m %.2f %.2f l S" % (*color, w, x1, y1, x2, y2))

    def _rect(self, x, y, w, h, color):
        self.ops.append(b"%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f" % (*color, x, y, w, h))

    # ---------------------------------------------------------------- building blocks
    def heading(self, s, size=18):
        self._room(size + 10)
        self.y -= size
        self._text(MARGIN, self.y, s, size, bold=True)
        self.y -= 8

    def section(self, s):
        lines = wrap(s, 12, W - 2 * MARGIN, True)
        self._room(48 + 15 * len(lines))
        self.y -= 16
        for line in lines:
            self._text(MARGIN, self.y - 11, line, 12, bold=True)
            self.y -= 15
        self.y -= 3

    def text(self, s, size=9.5, color=BLACK, bold=False, gap=3):
        for line in wrap(s, size, W - 2 * MARGIN, bold):
            self._room(size + gap)
            self.y -= size + gap
            self._text(MARGIN, self.y, line, size, bold, color)

    def badge(self, s, color):
        size = 8.5
        w = width(s, size, True) + 12
        self._room(18)
        self._rect(MARGIN, self.y - 16, w, 14, tuple(1 - (1 - c) * 0.15 for c in color))
        self._text(MARGIN + 6, self.y - 12.2, s, size, True, color)
        self.y -= 20

    def space(self, h=8):
        self.y -= h

    def table(self, cols, rows, bold_rows=(), colors=None, size=9):
        """cols: [(header, width, 'l'|'r')]; rows: lists of strings. Widths are scaled to the page width."""
        scale = (W - 2 * MARGIN) / sum(c[1] for c in cols)
        cols = [(h, w * scale, a) for h, w, a in cols]
        rh = size + 6.5

        def pad(j):                     # a left-aligned column right after a right-aligned one gets some room
            return 5 if j and cols[j - 1][2] == "r" else 0

        def header():
            x = MARGIN
            self.y -= rh
            for j, (h, w, a) in enumerate(cols):
                s = fit(h, size - 0.5, w - 6, True)
                tx = x + w - width(s, size - 0.5, True) if a == "r" else x + pad(j)
                self._text(tx, self.y + 2, s, size - 0.5, True, GREY)
                x += w + 0
            self._line(MARGIN, self.y - 2, W - MARGIN, self.y - 2, GREY, 0.7)
            self.y -= 2

        if any(h for h, _, _ in cols):
            self._room(rh * 3)
            header()
        for i, row in enumerate(rows):
            if self._room(rh) and any(h for h, _, _ in cols):
                header()
            self.y -= rh
            bold = i in bold_rows
            x = MARGIN
            for j, (cell, (_, w, a)) in enumerate(zip(row, cols)):
                s = fit(str(cell if cell is not None else ""), size, w - (6 if a == "l" else 0), bold)
                color = (colors or {}).get((i, j), BLACK)
                tx = x + w - width(s, size, bold) if a == "r" else x + pad(j)
                self._text(tx, self.y + 2, s, size, bold, color)
                x += w
            self._line(MARGIN, self.y - 2.5, W - MARGIN, self.y - 2.5)
            self.y -= 2

    # ---------------------------------------------------------------- the file
    def output(self):
        n = len(self.pages)
        for i, ops in enumerate(self.pages):
            foot = f"{self.footer}   ·   page {i + 1} of {n}" if self.footer else f"page {i + 1} of {n}"
            x = W - MARGIN - width(foot, 7.5)
            ops.append(b"BT /F1 7.5 Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET" % (*GREY, x, MARGIN - 18, _enc(foot)))
        objs = []                                           # object number = index + 1

        def add(body):
            objs.append(body)
            return len(objs)

        add(b"")                                            # 1: catalog (filled in below)
        add(b"")                                            # 2: pages
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        for ops in self.pages:
            stream = b"\n".join(ops)
            c = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            kids.append(add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>" % (W, H, c, f1, f2)))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
        info = add(b"<< /Title (%s) /Producer (Stock Terminal) /CreationDate (D:%s) >>" % (
            _enc(self.title), time.strftime("%Y%m%d%H%M%S").encode()))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (i + 1, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info, xref)
        return bytes(out)
