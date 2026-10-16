#!/usr/bin/env python3
"""IRQ 42 (VIC1 bit 10, SDIO) patch for the 2007.mobile core.

  python3 irq_patch.py                         # ../re-core/emu.wasm -> out/emu-irq.wasm
  python3 irq_patch.py --wat ../patch/out/emu-net.wat --out-name emu-net-irq
                                               # stack on top of the emu-net patch

Edits (each anchor must match EXACTLY once, otherwise nothing is written):
  1. new global $sdio_irq_level right after original global 0 (so no original
     global index changes; the module has only global 0)
  2. func 19 (device tick): one call that re-drives VIC1 RAW bit 10 from
     $sdio_irq_level, inserted just before the CPU IRQ/FIQ line computation
  3. append irq_patch.wat (before the table): $vic_irq_reeval, $sdio_set_irq,
     $sdio_raise_irq, $sdio_lower_irq, $sdio_irq_state
  4. exports: sdio_raise_irq, sdio_lower_irq, vic_irq_reeval, sdio_irq_state
Then wat2wasm (default feature set only) + wasm-validate.
The polled idle table at data 22576 / func 208 / func 160 is NOT touched
(see IRQ_NOTES.md for why it is not needed).
"""
import argparse, hashlib, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORIG_SHA256 = "6c168674625059f974a11ef808bcf06185ddbb11a1ce553f9bd22f3bc29d52c6"

EXPORTS = [("sdio_raise_irq", "sdio_raise_irq"), ("sdio_lower_irq", "sdio_lower_irq"),
           ("vic_irq_reeval", "vic_irq_reeval"), ("sdio_irq_state", "sdio_irq_state")]


def die(msg):
    sys.exit("irq_patch.py: " + msg)


def run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        die("%s failed:\n%s%s" % (cmd[0], p.stdout, p.stderr))
    return p.stdout


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        die("anchor for %s matched %d times (expected 1)" % (what, n))
    return text.replace(old, new)


def func_span(wat, idx):
    m = re.search(r"^  \(func \(;%d;\)" % idx, wat, re.M)
    if not m:
        die("func %d not found" % idx)
    n = re.compile(r"^  \((?:func|table)\b", re.M).search(wat, m.end())
    return m.start(), n.start()


def edit_func(wat, idx, old, new, what):
    s, e = func_span(wat, idx)
    body = replace_once(wat[s:e], old, new, what)
    return wat[:s] + body + wat[e:]


# Tail of func 19: IRQ = any(EN&(RAW|SOFT)&~SEL), FIQ = ...&SEL, local 7 = machine+84200 (VIC0).
TAIL19 = ("      local.get 7\n      i32.load offset=8\n      local.tee 8\n      local.get 7\n"
          "      i32.load offset=4\n      local.get 7\n      i32.load offset=12\n      local.get 7\n"
          "      i32.load\n      i32.or\n      i32.and\n      local.tee 3\n      i32.and\n"
          "      i32.const 0\n      i32.ne\n      local.set 2\n      local.get 0\n      i32.const 84216\n"
          "      i32.add\n      local.tee 1\n")
HOOK19 = ("      ;; re-irq: drive line 42 (VIC1 RAW bit 10) from the SDIO device level\n"
          "      local.get 7\n      i32.const 16\n      i32.add\n      i32.const 10\n"
          "      global.get $sdio_irq_level\n      call 20\n")


def apply_func19_hook(wat):
    """func 19: re-drive VIC1 RAW bit 10 from $sdio_irq_level every device tick."""
    return edit_func(wat, 19, TAIL19, HOOK19 + TAIL19, "func19 line-42 hook")


def patch_wat(wat):
    # --- sanity checks on the assumptions the new code hard-codes ----------
    if not re.search(r"^  \(func \(;20;\) \(type 2\) \(param i32 i32 i32\)\n    local.get 1\n    i32.const 31\n"
                     r"    i32.le_u\n", wat, re.M):
        die("func 20 is not the VIC RAW bit setter")
    s, e = func_span(wat, 19)
    body19 = wat[s:e]
    for need in ("      i32.const 84200\n      i32.add\n      local.tee 7\n", "i32.store8 offset=272\n",
                 "i32.store8 offset=273\n"):
        if body19.count(need) != 1:
            die("func 19 layout differs (%r)" % need.strip())
    s, e = func_span(wat, 233)
    if "i32.const 23560\n" not in wat[s:e]:
        die("machine struct is not at 23560")
    if "$sdio_irq_level" in wat:
        die("input is already irq-patched")
    # an emu-net build made with patch/sdio_irq_stub.wat has empty placeholders
    for stub in ("  (func $sdio_raise_irq)\n", "  (func $sdio_lower_irq)\n"):
        if wat.count(stub) == 1:
            wat = wat.replace(stub, "")
            print("removed placeholder", stub.strip())
    funcs = open(os.path.join(HERE, "irq_patch.wat")).read()
    # 1) globals declared in irq_patch.wat move right after original global 0
    gl = re.compile(r"^  \(global \$.*\n", re.M)
    globs = "".join(gl.findall(funcs))
    funcs = gl.sub("", funcs)
    if "$sdio_irq_level" not in globs:
        die("irq_patch.wat does not declare $sdio_irq_level")
    wat = replace_once(wat, "  (global (;0;) (mut i32) (i32.const 5247344))\n",
                       "  (global (;0;) (mut i32) (i32.const 5247344))\n" + globs, "irq global")
    # 2) func 19 hook
    wat = apply_func19_hook(wat)
    # 3) functions
    wat = replace_once(wat, "  (table (;0;) 58 funcref)\n", funcs + "\n  (table (;0;) 58 funcref)\n",
                       "irq funcs insertion")
    # 4) exports
    for e, _ in EXPORTS:
        if '(export "%s"' % e in wat:
            die("export %s already exists" % e)
    exp = "".join('  (export "%s" (func $%s))\n' % (e, f) for e, f in EXPORTS)
    m = re.search(r"^  \(elem \(;0;\)", wat, re.M)
    if not m:
        die("elem segment not found")
    return wat[:m.start()] + exp + wat[m.start():]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wasm", default=os.path.join(HERE, "..", "re-core", "emu.wasm"))
    ap.add_argument("--wat", help="patch this .wat instead of wasm2wat(--wasm), e.g. ../patch/out/emu-net.wat")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--out-name", default="emu-irq")
    a = ap.parse_args()
    if a.wat:
        wat = open(a.wat).read()
        print("input", a.wat)
    else:
        got = hashlib.sha256(open(a.wasm, "rb").read()).hexdigest()
        if got != ORIG_SHA256:
            die("sha256 mismatch: got %s expected %s" % (got, ORIG_SHA256))
        print("sha256 ok", got)
        wat = run("wasm2wat", a.wasm)
    new = patch_wat(wat)
    os.makedirs(a.out, exist_ok=True)
    wat_path = os.path.join(a.out, a.out_name + ".wat")
    wasm_path = os.path.join(a.out, a.out_name + ".wasm")
    with open(wat_path, "w") as f:
        f.write(new)
    run("wat2wasm", wat_path, "-o", wasm_path)
    run("wasm-validate", wasm_path)
    print("%s: %d bytes, validated, sha256 %s" % (wasm_path, os.path.getsize(wasm_path),
                                                  hashlib.sha256(open(wasm_path, "rb").read()).hexdigest()))


if __name__ == "__main__":
    main()
