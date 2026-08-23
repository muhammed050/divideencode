import csv
import io
import io as _io
import math
import os
import random
import struct
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "samples")

WORDS = ("the of and to a in that is was he for it with as his on be at by i "
         "this had not are but from or have an they which one you were her all "
         "she there would their we him been has when who will more no if out "
         "so said what up its about into than them can only other new some "
         "could time these two may then do first any my now such like our over "
         "man me even most made after also did many before must through back "
         "years where much your way well down should because each just those "
         "people mr how too little state good very make world still own see "
         "men work long get here between both life being under never day "
         "same another know while last might us great old year off come since "
         "against go came right used take three").split()

CODE_IDENTS = ("process handle compute parse validate transform encode decode "
               "fetch store update render dispatch resolve execute evaluate "
               "serialize deserialize register cleanup initialize configure "
               "measure aggregate filter reduce merge split normalize verify "
               "compress restore scan index buffer stream cache flush sync").split()


def write(name, data):
    path = os.path.join(SAMPLES_DIR, name)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    size = os.path.getsize(path)
    print("%-22s %8d bytes" % (name, size))


def gen_text(seed=1):
    rng = random.Random(seed)
    parts = []
    for p in range(60):
        para = []
        for s in range(rng.randint(4, 9)):
            n = rng.randint(8, 26)
            words = [rng.choice(WORDS) for _ in range(n)]
            sent = " ".join(words).capitalize() + "."
            para.append(sent)
        parts.append(" ".join(para))
    return "\n\n".join(parts).encode()


def gen_json(seed=2):
    rng = random.Random(seed)
    recs = []
    for i in range(1500):
        recs.append({
            "id": i,
            "uuid": "%08x-%04x-%04x" % (rng.getrandbits(32),
                                        rng.getrandbits(16), i & 0xFFFF),
            "name": "record_%s_%d" % (rng.choice(CODE_IDENTS), i),
            "score": round(rng.random() * 100, 3),
            "active": rng.random() < 0.5,
            "tags": [rng.choice(WORDS) for _ in range(rng.randint(0, 4))],
            "meta": {"created": "2026-0%d-%02dT%02d:00:00Z"
                     % (rng.randint(1, 9), rng.randint(1, 28),
                        rng.randint(0, 23)),
                     "views": rng.randint(0, 99999)},
        })
    import json
    return json.dumps(recs, indent=2).encode()


def gen_big_json(seed=12):
    rng = random.Random(seed)
    rows = []
    for i in range(6000):
        rows.append({"t": 1700000000 + i * 37,
                     "sensor": "S%03d" % (i % 64),
                     "v1": round(math.sin(i / 97) * 500 + rng.gauss(0, 4), 2),
                     "v2": round(math.cos(i / 53) * 250 + rng.gauss(0, 3), 2),
                     "flag": i % 7 == 0})
    import json
    return json.dumps(rows, separators=(",", ":")).encode()


def gen_csv(seed=3):
    rng = random.Random(seed)
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["id", "timestamp", "device", "metric_name", "value",
                 "unit", "quality"])
    for i in range(4000):
        wr.writerow([i,
                     "2026-07-%02d %02d:%02d:%02d.%03d"
                     % (1 + i // 24 % 28, i % 24, i % 60, i % 60, i % 1000),
                     "dev-%03d" % (i % 128),
                     rng.choice(["temperature", "pressure", "humidity",
                                 "voltage", "current"]),
                     "%.2f" % (rng.random() * 1000),
                     rng.choice(["C", "kPa", "%", "V", "A"]),
                     rng.choice(["GOOD", "GOOD", "GOOD", "UNCERTAIN"])])
    return buf.getvalue().encode()


def _html_page(title, body_lines):
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>%s</title>\n<style>body{font-family:sans-serif;margin:2em}"
            ".item{padding:.4em;border-bottom:1px solid #ddd}</style>\n</head>\n"
            "<body>\n<h1>%s</h1>\n%s</body>\n</html>\n"
            % (title, title, "\n".join(body_lines))).encode()


def gen_html():
    lines = []
    for i in range(700):
        w = WORDS[i % len(WORDS)]
        lines.append("<div class=\"item\" id=\"row-%d\"><span>%d</span>"
                     "<a href=\"/detail/%d\">Article about %s #%d</a>"
                     "<em>updated 2026-08-%02d</em></div>"
                     % (i, i, i, w, i, 1 + i % 28))
    return _html_page("Sample Catalog", lines)


def gen_xml():
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<inventory generated="2026-08-22">']
    for i in range(900):
        parts.append('  <item id="%d" sku="SKU-%06d" stock="%d">'
                     % (i, i * 7919 % 1000000, (i * 31) % 500))
        parts.append("    <description>%s component revision %d</description>"
                     % (CODE_IDENTS[i % len(CODE_IDENTS)].capitalize(), i % 9))
        parts.append("    <price currency=\"USD\">%.2f</price>" % ((i * 13.37) % 900))
        if i % 5 == 0:
            parts.append("    <note>requires review before shipment</note>")
        parts.append("  </item>")
    parts.append("</inventory>")
    return ("\n".join(parts)).encode()


def gen_markdown():
    out = ["# Project Documentation", "",
           "## Overview", ""]
    for sec in range(30):
        out.append("## Section %d: %s pipeline" % (sec, CODE_IDENTS[sec % len(CODE_IDENTS)]))
        out.append("")
        for s in range(4):
            words = " ".join(WORDS[(sec * 7 + s * 13 + k) % len(WORDS)]
                             for k in range(24))
            out.append(words.capitalize() + ".")
        out.append("")
        out.append("- bullet point number %d with `code_snippet_%d`" % (sec, sec))
        out.append("- second bullet referencing [docs](https://example.com/%d)" % sec)
        out.append("")
        out.append("```python")
        out.append("def handler_%d(request):" % sec)
        out.append("    return process_%d(request.payload)" % (sec % len(CODE_IDENTS)))
        out.append("```")
        out.append("")
    return "\n".join(out).encode()


def _code_file(lang, count, seed):
    rng = random.Random(seed)
    lines = []
    ident = lambda: rng.choice(CODE_IDENTS) + "_" + str(rng.randint(0, 99))
    num = lambda: str(rng.randint(0, 65535))
    for i in range(count):
        fn = ident()
        if lang == "py":
            body = [
                "",
                "def %s(self, value, options=None):" % fn,
                '    """Generated routine %d."""' % i,
                "    result = []",
                "    for item in self.items:",
                "        if item.valid and item.weight > %s:" % num(),
                "            result.append(transform(item, %s))" % num(),
                "        else:",
                "            self.skipped += 1",
                "    self.log('%s processed %%d items' %% len(result))" % fn,
                "    return result",
                "",
            ]
        elif lang == "js":
            body = [
                "",
                "export function %s(value, options = null) {" % fn,
                "  const results = [];",
                "  for (const item of this.items) {",
                "    if (item.valid && item.weight > %s) {" % num(),
                "      results.push(transform(item, %s));" % num(),
                "    } else {",
                "      this.skipped++;",
                "    }",
                "  }",
                "  console.log(`%s processed ${results.length} items`);",
                "  return results;",
                "}",
                "",
            ]
        elif lang == "ts":
            body = [
                "",
                "export function %s(value: number, options?: Options): number[] {" % fn,
                "  const results: number[] = [];",
                "  for (const item of this.items) {",
                "    if (item.isValid() && item.weight > %s) {" % num(),
                "      results.push(transform(item, %s));" % num(),
                "    } else {",
                "      this.skipped += 1;",
                "    }",
                "  }",
                "  logger.info('%s', results.length);",
                "  return results;",
                "}",
                "",
            ]
        elif lang == "c":
            body = [
                "",
                "int %s(ctx_t *ctx, uint32_t value, uint32_t flags) {" % fn,
                "    int result = 0;",
                "    for (size_t j = 0; j < ctx->count; j++) {",
                "        entry_t *e = &ctx->entries[j];",
                "        if (e->valid && e->weight > %sU) {" % num(),
                "            result += transform(e, %sU);" % num(),
                "        } else {",
                "            ctx->skipped++;",
                "        }",
                "    }",
                "    LOG(\"%s processed %d entries\", result);",
                "    return result;",
                "}",
                "",
            ]
        elif lang == "cpp":
            body = [
                "",
                "int Processor::%s(uint32_t value, const Options& opts) {" % fn,
                "    int result = 0;",
                "    for (const auto& e : items_) {",
                "        if (e.valid() && e.weight() > %su) {" % num(),
                "            result += transform(e, %su);" % num(),
                "        } else {",
                "            skipped_++;",
                "        }",
                "    }",
                "    spdlog::info(\"%s: {} entries\", result);",
                "    return result;",
                "}",
                "",
            ]
        elif lang == "java":
            body = [
                "",
                "    public int %s(int value, Options options) {" % fn,
                "        int result = 0;",
                "        for (Item item : this.items) {",
                "            if (item.isValid() && item.getWeight() > %s) {" % num(),
                "                result += transform(item, %s);" % num(),
                "            } else {",
                "                this.skipped++;",
                "            }",
                "        }",
                "        LOGGER.info(\"%s processed \" + result);",
                "        return result;",
                "    }",
                "",
            ]
        elif lang == "rs":
            body = [
                "",
                "pub fn %s(&mut self, value: u32, opts: &Options) -> Vec<u32> {" % fn,
                "    let mut result = Vec::new();",
                "    for item in &self.items {",
                "        if item.valid && item.weight > %s {" % num(),
                "            result.push(transform(item, %s));" % num(),
                "        } else {",
                "            self.skipped += 1;",
                "        }",
                "    }",
                "    info!(\"%s processed {} items\", result.len());",
                "    result",
                "}",
                "",
            ]
        else:
            raise ValueError(lang)
        lines.extend(body)
    header = {
        "py": ["#!/usr/bin/env python3", "\"\"\"Generated benchmark source.\"\"\"", ""],
        "js": ["// Generated benchmark source", ""],
        "ts": ["// Generated benchmark source", "import { Options } from './types';", ""],
        "c": ["#include <stdint.h>", "#include <stddef.h>", "", "typedef struct { int valid; unsigned weight; } entry_t;", "typedef struct { entry_t *entries; size_t count; int skipped; } ctx_t;", "#define LOG(...) 0", "int transform(entry_t *e, unsigned k);", ""],
        "cpp": ["#include <cstdint>", "#include <vector>", "#include \"options.h\"", "", "class Processor { public: std::vector<Entry> items_; int skipped_{}; int transform(const Entry&, unsigned);"],
        "java": ["import java.util.*;", "import java.util.logging.*;", "", "public class Generated {", "    private static final Logger LOGGER = Logger.getLogger(\"gen\");", "    private List<Item> items = new ArrayList<>();", "    private int skipped = 0;"],
        "rs": ["use log::info;", "", "pub struct Engine { pub items: Vec<Item>, pub skipped: usize }"],
    }[lang]
    return ("\n".join(header + lines) +
            ("\n}\n" if lang == "cpp" else "\n" if lang == "java" else "")).encode()


def gen_log(seed=5):
    rng = random.Random(seed)
    levels = ["INFO", "INFO", "INFO", "WARN", "ERROR", "DEBUG"]
    mods = ["http.server", "db.pool", "auth.service", "queue.worker",
            "cache.redis", "scheduler.core"]
    msgs = [
        lambda r: "request completed status=%d duration=%dms"
                  % (r.choice([200, 201, 204, 301, 404, 500]), r.randint(1, 5000)),
        lambda r: "connection acquired pool_size=%d wait=%dms"
                  % (r.randint(1, 64), r.randint(1, 500)),
        lambda r: "user authenticated method=%s latency=%dms"
                  % (r.choice(["password", "oauth2", "token"]), r.randint(1, 900)),
        lambda r: "job dispatched queue=%s attempt=%d"
                  % (r.choice(["email", "render", "export", "billing"]),
                     r.randint(1, 5)),
        lambda r: "cache miss key_hash=%08x backing=db" % r.getrandbits(32),
        lambda r: "health check ok uptime=%ds rps=%.1f"
                  % (r.randint(1, 999999), r.random() * 1000),
    ]
    lines = []
    t = 1755800000
    for i in range(2400):
        t += rng.randint(10, 400)
        lvl = rng.choice(levels)
        mod = rng.choice(mods)
        msg = rng.choice(msgs)
        detail = msg(rng)
        lines.append("%s %-5s [%s] %s" % (
            "2026-08-%02d %02d:%02d:%02d,%03d"
            % (1 + i // 24 % 28, (t // 3600) % 24, (t // 60) % 60, t % 60,
               rng.randint(0, 999)),
            lvl, mod, detail))
    return ("\n".join(lines) + "\n").encode()


def gen_sql(seed=6):
    rng = random.Random(seed)
    lines = ["-- benchmark database export", "BEGIN TRANSACTION;"]
    for i in range(2500):
        lines.append(
            "INSERT INTO orders (id, customer_id, product_sku, quantity, "
            "unit_price, status, created_at) VALUES (%d, %d, '%s-%05d', %d, "
            "%.2f, '%s', '2026-07-%02d %02d:%02d:00');"
            % (i, rng.randint(1, 50000), rng.choice(["SKU", "PRD", "ITM"]),
               rng.randint(0, 99999), rng.randint(1, 42), rng.random() * 500,
               rng.choice(["pending", "shipped", "delivered", "cancelled"]),
               1 + i % 28, i % 24, i % 60))
    lines.append("COMMIT;")
    return ("\n".join(lines) + "\n").encode()


def gen_sensor_i16(seed=7, n=65536):
    rng = random.Random(seed)
    out = bytearray()
    phase = 0.0
    v1, v2 = 0.0, 0.0
    for i in range(n):
        base = 18000 + 9000 * math.sin(i / 2100.0) \
               + 3500 * math.sin(i / 310.0) \
               + 800 * math.sin(i / 41.0)
        v1 = 0.92 * v1 + rng.gauss(0, 60)
        v2 = 0.85 * v2 + rng.gauss(0, 25)
        val = int(base + v1 + v2)
        val = max(-32768, min(32767, val))
        out += struct.pack("<h", val)
    return bytes(out)


def gen_counters_u32(n=40000):
    out = bytearray()
    counters = [100000 + i * 17 for i in range(10)]
    for step in range(n // 10):
        for c in range(10):
            counters[c] += 17 + (step % 3)
            out += struct.pack("<I", counters[c])
    return bytes(out)


def gen_images():
    from PIL import Image
    w, h = 480, 480
    img = Image.new("RGB", (w, h))
    px = img.load()
    rng = random.Random(11)
    for y in range(h):
        for x in range(w):
            r = int(127 + 127 * math.sin(x / 37.0))
            g = int(127 + 127 * math.sin(y / 23.0 + 1.3))
            b = int(127 + 127 * math.sin((x + y) / 51.0 + 2.1))
            noise = rng.randint(-14, 14)
            px[x, y] = (max(0, min(255, r + noise)),
                        max(0, min(255, g + noise)),
                        max(0, min(255, b + noise)))
    write("photo.png", _img_bytes(img, "PNG"))
    write("photo.jpg", _img_bytes(img, "JPEG", quality=85))
    write("photo.webp", _img_bytes(img, "WEBP", quality=85))


def _img_bytes(img, fmt, **kw):
    buf = _io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def gen_pdf():
    objs = []
    content_lines = []
    for i in range(400):
        content_lines.append(
            "BT /F1 10 Tf 50 %d Td (DivideEncode benchmark document line %04d "
            "- sample text for compression testing) Tj ET" % (760 - i * 2, i))
    stream = "\n".join(content_lines).encode()
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode() + o + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos)).encode()
    return bytes(out)


def gen_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", b"This archive contains benchmark payloads.\n" * 40)
        zf.writestr("data.txt", gen_text(seed=21))
        zf.writestr("numbers.csv", gen_csv(seed=22))
        zf.writestr("noise.bin", bytes(bytearray((i * 97 + 13) % 256
                                                 for i in range(4096))))
    return buf.getvalue()


def main():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    print("generating samples -> %s" % SAMPLES_DIR)
    write("text_en.txt", gen_text())
    write("data.json", gen_json())
    write("big.json", gen_big_json())
    write("table.csv", gen_csv())
    write("page.html", gen_html())
    write("feed.xml", gen_xml())
    write("notes.md", gen_markdown())
    write("app.js", _code_file("js", 260, 31))
    write("app.ts", _code_file("ts", 260, 32))
    write("main.py", _code_file("py", 260, 33))
    write("program.c", _code_file("c", 260, 34))
    write("engine.cpp", _code_file("cpp", 260, 35))
    write("App.java", _code_file("java", 260, 36))
    write("lib.rs", _code_file("rs", 260, 37))
    write("server.log", gen_log())
    write("dump.sql", gen_sql())
    write("sensor_i16.bin", gen_sensor_i16())
    write("counters_u32.bin", gen_counters_u32())
    gen_images()
    write("doc.pdf", gen_pdf())
    write("archive.zip", gen_zip())
    write("random_os.bin", os.urandom(262144))
    write("random_prng.bin", bytes(bytearray(
        (i * 1103515245 + 12345) >> 16 & 0xFF for i in range(262144))))
    print("done")


if __name__ == "__main__":
    main()
