"""Pages for the document generator: a page is a list of drawing operations in points (A4, origin at the top left).

One page can be written two ways: as a PDF with a real text layer (no packages needed), or drawn as a picture and then
damaged like a scan or a phone photo (needs Pillow and numpy). Nothing here reads the platform's code.
Pictures are drawn with the installed Arial or DejaVu font, so a picture's bytes are the same for the same seed on the
same machine, not across machines.
"""

import io
import zlib
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

PAGE_W, PAGE_H = 595.0, 842.0

# Helvetica character widths (1/1000 em) for ASCII 32..126.
_W = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
    556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556,
    556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]  # fmt: skip


def text_width(s: str, size: float, bold: bool = False) -> float:
    total = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in s)
    return total * size / 1000 * (1.04 if bold else 1.0)


def latin1(s: str) -> str:
    return s.encode("latin-1", "replace").decode("latin-1")


@dataclass
class Text:
    x: float
    y: float  # baseline, from the top
    s: str
    size: float = 9.0
    bold: bool = False
    align: str = "l"  # l, r or c
    gray: float = 0.0  # 0 black .. 1 white


@dataclass
class Rule:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.5


@dataclass
class Picture:
    x: float
    y: float  # top edge
    w: float
    h: float
    pixels: Any  # a 2-D numpy array of uint8, 0 black .. 255 white


Page = list[Text | Rule | Picture]


def _left(t: Text) -> float:
    w = text_width(t.s, t.size, t.bold)
    return t.x - w if t.align == "r" else t.x - w / 2 if t.align == "c" else t.x


# ---- PDF ----------------------------------------------------------------------------------------


def _pdf_string(s: str) -> str:
    return latin1(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(page: Page, images: list[tuple[bytes, int, int, bool]]) -> bytes:
    """The page's drawing commands. Pictures are appended to `images` and called /Im<n>."""
    out: list[str] = []
    for op in page:
        if isinstance(op, Text):
            font = "F2" if op.bold else "F1"
            out.append(
                f"BT /{font} {op.size:g} Tf {op.gray:g} g {_left(op):.2f} {PAGE_H - op.y:.2f} Td ({_pdf_string(op.s)}) Tj ET"
            )
        elif isinstance(op, Rule):
            out.append(
                f"{op.width:g} w 0 G {op.x1:.2f} {PAGE_H - op.y1:.2f} m {op.x2:.2f} {PAGE_H - op.y2:.2f} l S"
            )
        else:
            h, w = op.pixels.shape
            images.append((zlib.compress(op.pixels.tobytes(), 9), w, h, False))
            out.append(f"q {op.w:.2f} 0 0 {op.h:.2f} {op.x:.2f} {PAGE_H - op.y - op.h:.2f} cm /Im{len(images)} Do Q")
    return "\n".join(out).encode("latin-1")


def pdf_from_pages(pages: list[Page]) -> bytes:
    """A PDF with a real text layer. Page 1 of the list is the first page."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    add(b"")  # 1: catalog, filled in below
    add(b"")  # 2: pages
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids: list[int] = []
    for page in pages:
        images: list[tuple[bytes, int, int, bool]] = []
        stream = _content(page, images)
        image_ids = []
        for packed, w, h, _ in images:
            image_ids.append(
                add(
                    f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 "
                    f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n".encode()
                    + packed
                    + b"\nendstream"
                )
            )
        content = add(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        xobj = (
            " /XObject << " + " ".join(f"/Im{i} {n} 0 R" for i, n in enumerate(image_ids, 1)) + " >>" if image_ids else ""
        )
        kids.append(
            add(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:g} {PAGE_H:g}] /Contents {content} 0 R "
                f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >>{xobj} >> >>".encode()
            )
        )
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    return _write_pdf(objs)


def pdf_from_pictures(jpegs: list[tuple[bytes, int, int]]) -> bytes:
    """A PDF that is only pictures of pages (a scan): no text layer at all. Each item is (JPEG bytes, width, height)."""
    objs: list[bytes] = [b"", b""]
    kids: list[int] = []
    for data, w, h in jpegs:
        img = len(objs) + 1
        objs.append(
            f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 "
            f"/Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )
        stream = f"q {PAGE_W:g} 0 0 {PAGE_H:g} 0 0 cm /Im1 Do Q".encode()
        content = len(objs) + 1
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        kids.append(len(objs) + 1)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:g} {PAGE_H:g}] /Contents {content} 0 R "
            f"/Resources << /XObject << /Im1 {img} 0 R >> >> >>".encode()
        )
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    return _write_pdf(objs)


def _write_pdf(objs: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---- pictures -----------------------------------------------------------------------------------


def _font(size: float, bold: bool) -> Any:
    from PIL import ImageFont

    names = (
        ("C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    for candidate in names:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, max(int(round(size)), 6))
    return ImageFont.load_default(max(int(round(size)), 6))


def render(page: Page, dpi: int = 150) -> Any:
    """The page drawn as a grey picture (PIL 'L' image)."""
    from PIL import Image, ImageDraw

    k = dpi / 72
    img = Image.new("L", (int(PAGE_W * k), int(PAGE_H * k)), 255)
    draw = ImageDraw.Draw(img)
    for op in page:
        if isinstance(op, Text):
            font = _font(op.size * k, op.bold)
            # Our widths are Helvetica's; drawing is left aligned from the same left edge so the two outputs agree.
            draw.text((_left(op) * k, op.y * k), latin1(op.s), fill=int(op.gray * 255), font=font, anchor="ls")
        elif isinstance(op, Rule):
            draw.line([(op.x1 * k, op.y1 * k), (op.x2 * k, op.y2 * k)], fill=0, width=max(int(op.width * k), 1))
        else:
            pic = Image.fromarray(op.pixels).resize((int(op.w * k), int(op.h * k)), Image.NEAREST)
            img.paste(pic, (int(op.x * k), int(op.y * k)))
    return img


def to_jpeg(img: Any, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def to_png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def qr_pixels(text: str, module_px: int = 4) -> Any:
    """A QR code as a grey array. Needs zxing-cpp."""
    import numpy as np
    import zxingcpp

    code = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    return np.array(zxingcpp.write_barcode_to_image(code, scale=module_px))


# ---- damage -------------------------------------------------------------------------------------


def _noise(img: Any, rng: Random, sigma: float) -> Any:
    import numpy as np
    from PIL import Image

    gen = np.random.default_rng(rng.getrandbits(32))
    arr = np.asarray(img, dtype=np.float32) + gen.normal(0, sigma, (img.height, img.width)).astype(np.float32)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def scan(img: Any, rng: Random) -> Any:
    """A flatbed scan: a hair off straight, a little grain."""
    from PIL import ImageFilter

    out = img.rotate(rng.uniform(-0.5, 0.5), resample=2, fillcolor=255)
    return _noise(out.filter(ImageFilter.GaussianBlur(0.5)), rng, 5)


def photo(img: Any, rng: Random) -> Any:
    """A phone photo: tilted, uneven light, a little soft."""
    import numpy as np
    from PIL import Image, ImageFilter

    out = img.rotate(rng.uniform(-2.0, 2.0), resample=2, fillcolor=235)
    w, h = out.size
    shift = 0.012 * w
    coeffs = (1, rng.uniform(-0.01, 0.01), rng.uniform(-shift, shift), rng.uniform(-0.01, 0.01), 1, rng.uniform(-shift, shift),
              rng.uniform(-2e-5, 2e-5), rng.uniform(-2e-5, 2e-5))  # fmt: skip
    out = out.transform((w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=225)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    angle = rng.uniform(0, 6.28)
    light = 0.82 + 0.18 * (0.5 + 0.5 * np.sin(angle) * (yy / h - 0.5) * 2 + 0.5 * np.cos(angle) * (xx / w - 0.5) * 2)
    out = Image.fromarray(np.clip(np.asarray(out, dtype=np.float32) * light, 0, 255).astype(np.uint8))
    return _noise(out.filter(ImageFilter.GaussianBlur(0.8)), rng, 6)


def skewed(img: Any, rng: Random) -> Any:
    """A scan put in crooked: several degrees off."""
    angle = rng.choice([-1, 1]) * rng.uniform(4.0, 6.5)
    return _noise(img.rotate(angle, resample=2, expand=True, fillcolor=255), rng, 4)


def low_contrast(img: Any, rng: Random) -> Any:
    """Faded print on grey paper."""
    import numpy as np
    from PIL import Image, ImageFilter

    arr = np.asarray(img, dtype=np.float32) / 255
    faded = 105 + arr * 105  # black prints at 105, paper at 210
    out = Image.fromarray(faded.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5))
    return _noise(out, rng, 5)
